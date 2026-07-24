"""Mapping des landmarks MediaPipe Pose vers les os du rig (Phase 1, corps
uniquement : épaules, coudes, poignets, hanches, genoux, chevilles, colonne).

Convention de nommage des os attendue (voir tools/generate_test_rig.py) :
hips, spine, chest, neck, head,
shoulder.L/R, upper_arm.L/R, forearm.L/R, hand.L/R,
thigh.L/R, shin.L/R, foot.L/R

Le mapping est en dur pour cette phase de validation (un seul rig de test).
Le rendre configurable par personnage (cahier des charges §7) est prévu
pour une itération ultérieure.

Méthode de retargeting : "aim" simplifié pour les membres (on aligne
l'axe de repos de chaque os sur la direction landmark_départ ->
landmark_arrivée), sans gestion du twist/roll — suffisant pour valider
le pipeline de bout en bout, à raffiner plus tard si le rendu manque de
naturel. La colonne (spine) utilise une orientation complète à 3 degrés
de liberté (épaules gauche/droite comme référence de torsion), pour
capter la rotation du buste sur lui-même (pivoter sans se pencher).
"""

from __future__ import annotations

import bpy
from mathutils import Matrix, Quaternion, Vector

LANDMARK_INDEX = {
    "left_shoulder": 11, "right_shoulder": 12,
    "left_elbow": 13, "right_elbow": 14,
    "left_wrist": 15, "right_wrist": 16,
    "left_hip": 23, "right_hip": 24,
    "left_knee": 25, "right_knee": 26,
    "left_ankle": 27, "right_ankle": 28,
}

# (bone épaule/clavicule, landmark épaule) — visé depuis le centre des
# épaules (shoulder_center) vers le landmark épaule correspondant, pour
# capter le mouvement de l'omoplate (hausser/baisser, avancer/reculer)
# indépendamment de la rotation du bras (upper_arm) qui part du même point.
CLAVICLE_SEGMENTS = [
    ("shoulder.L", "left_shoulder"),
    ("shoulder.R", "right_shoulder"),
]

# (bone, landmark de départ, landmark d'arrivée)
LIMB_SEGMENTS = [
    ("upper_arm.L", "left_shoulder", "left_elbow"),
    ("forearm.L", "left_elbow", "left_wrist"),
    ("upper_arm.R", "right_shoulder", "right_elbow"),
    ("forearm.R", "right_elbow", "right_wrist"),
    ("thigh.L", "left_hip", "left_knee"),
    ("shin.L", "left_knee", "left_ankle"),
    ("thigh.R", "right_hip", "right_knee"),
    ("shin.R", "right_knee", "right_ankle"),
]

# Les coordonnées MediaPipe sont normalisées, sans échelle réelle connue :
# facteurs choisis empiriquement pour une amplitude de déplacement du
# bassin plausible sur le rig de test. L'axe de profondeur (Y, dérivé du
# "z" MediaPipe) est nettement plus bruité que x/y en mono-caméra RGB : on
# l'amortit fortement pour éviter que tout le rig ait l'air de "glisser"
# au moindre mouvement du buste.
ROOT_TRANSLATION_SCALE_LATERAL = 1.5  # X (gauche/droite) et Z (haut/bas)
ROOT_TRANSLATION_SCALE_DEPTH = 0.3    # Y (profondeur) — axe bruité

# Même logique que ROOT_TRANSLATION_SCALE_DEPTH, appliquée à la direction
# bassin->épaules du buste : un léger biais de profondeur entre les deux
# centres (même debout bien droit) suffit à faire pencher tout le buste,
# le Y (profondeur) étant l'axe le plus bruité en mono-caméra RGB.
SPINE_DEPTH_DAMPING = 0.3

# Même logique, appliquée à la direction de chaque membre (bras/jambe).
# Jamais amortie jusqu'ici contrairement au bassin/buste/mains — un bruit
# de profondeur sur epaule/coude/poignet peut suffire à faire pointer tout
# le membre dans une direction très éloignée du mouvement réel.
LIMB_DEPTH_DAMPING = 0.4

# Amortissement plus fort pour l'épaule/clavicule (comme les doigts) : le
# déplacement épaule<->centre des épaules est petit/subtil (hausser les
# épaules, etc.), donc particulièrement sensible au bruit de profondeur.
CLAVICLE_DEPTH_DAMPING = 0.25

# En dessous de ce seuil de confiance MediaPipe (0-1), un landmark est
# considéré "non fiable" (souvent hors cadre) : le membre concerné est
# gelé (on ne touche pas à sa rotation/position) plutôt que de suivre une
# position devinée par le modèle, qui donne un mouvement erratique/figé
# sans rapport avec le geste réel.
VISIBILITY_THRESHOLD = 0.5

# Amortissement de la torsion buste/bassin (0 = aucune torsion, 1 =
# pleine sensibilité) — réduit les faux positifs dus au mouvement des
# bras sans réduire la réactivité de l'inclinaison/direction.
TORSO_TWIST_DAMPING = 0.5


def resolve_bone_name(base_name: str, prefix: str = "", suffix: str = "") -> str:
    """Applique le préfixe/suffixe configurable (cahier des charges §7)
    au nom d'os attendu par convention, ex. resolve_bone_name("hips", "DEF-")
    -> "DEF-hips" — pour les rigs auto-générés (ex. Rigify) dont les os
    de déformation suivent un préfixe/suffixe cohérent mais différent de
    notre convention par défaut."""
    return f"{prefix}{base_name}{suffix}"


# Traductions pour l'outil "Associer les os par clic" (addon/operators.py) —
# aide à la compréhension des noms d'os anglais de la convention CORPUS-MOCAP.
# (label, féminin ?) — pour l'accord de "droit/droite" ("gauche" est invariable.
_ROLE_BASE_TRANSLATIONS = {
    "hips": ("Bassin", False),
    "spine": ("Colonne / buste", True),
    "upper_arm": ("Bras (haut)", False),
    "forearm": ("Avant-bras", False),
    "thigh": ("Cuisse", True),
    "shin": ("Tibia", False),
    "head": ("Tête", True),
    "hand": ("Main", True),
    "thumb": ("Pouce", False),
    "index": ("Index", False),
    "middle": ("Majeur", False),
    "ring": ("Annulaire", False),
    "pinky": ("Auriculaire", False),
}
_ROLE_SEGMENT_TRANSLATIONS = {"01": "base", "02": "milieu", "03": "bout"}


def _side_label(side_code: str, feminine: bool) -> str:
    if side_code == "L":
        return "gauche"
    if side_code == "R":
        return "droite" if feminine else "droit"
    return side_code


def translate_role_name(role: str) -> str:
    """Traduction française indicative d'un nom d'os canonique, ex.
    "thumb.01.L" -> "Pouce gauche - base". Purement informatif (affichage),
    ne remplace pas le nom anglais utilisé pour la recherche/le mapping."""
    parts = role.split(".")
    label, feminine = _ROLE_BASE_TRANSLATIONS.get(parts[0], (parts[0], False))
    if len(parts) == 1:
        return label
    if len(parts) == 2:
        side = _side_label(parts[1], feminine)
        return f"{label} {side}"
    if len(parts) == 3:
        segment = _ROLE_SEGMENT_TRANSLATIONS.get(parts[1], parts[1])
        side = _side_label(parts[2], feminine)
        return f"{label} {side} - {segment}"
    return label


def _visible(landmarks: list[dict], name: str) -> bool:
    return landmarks[LANDMARK_INDEX[name]]["visibility"] >= VISIBILITY_THRESHOLD


def _landmark_to_vector(landmark: dict) -> Vector:
    """Convertit un landmark MediaPipe (image space : x droite, y bas,
    z profondeur) en vecteur dans l'espace du rig (Z up, Y devant soi)."""
    x = landmark["x"] - 0.5
    y = -landmark["z"]
    z = -(landmark["y"] - 0.5)
    return Vector((x, y, z))


def bone_rest_world_rot(pose_bone: bpy.types.PoseBone, armature_obj: bpy.types.Object) -> Matrix:
    """Orientation de repos de `pose_bone` exprimée dans l'espace du rig,
    en tenant compte de la matrice *courante* du parent (`pose_bone.parent.matrix`,
    pas sa propre pose de repos) — donc l'appelant doit avoir rafraîchi le
    depsgraph (`view_layer.update()`) si le parent vient d'être modifié
    dans la même trame. Factorisé pour être réutilisé par _aim_bone,
    _apply_full_rotation, et le calcul de torsion du poignet
    (hand_mapping.py) qui a besoin de connaître cette orientation sans
    en changer l'axe de visée. Peut lever ValueError si la matrice de
    repos est non-inversible (bone à configuration dégénérée)."""
    bone = pose_bone.bone
    if pose_bone.parent is not None:
        parent_world_rot = pose_bone.parent.matrix.to_3x3()
        rest_local_rot = (pose_bone.parent.bone.matrix_local.inverted() @ bone.matrix_local).to_3x3()
    else:
        parent_world_rot = armature_obj.matrix_world.to_3x3()
        rest_local_rot = bone.matrix_local.to_3x3()
    return parent_world_rot @ rest_local_rot


def _aim_bone(pose_bone: bpy.types.PoseBone, target_dir_world: Vector, armature_obj: bpy.types.Object) -> None:
    """Oriente `pose_bone` pour que son axe de repos (Y local) pointe vers
    `target_dir_world`, exprimé dans l'espace du rig.

    Gèle silencieusement (ne touche pas à la rotation) si une matrice de
    repos s'avère non-inversible (bone à l'échelle/configuration
    dégénérée dans le rig cible) plutôt que de planter toute la capture —
    voir aussi print de diagnostic pour repérer l'os fautif."""
    bone = pose_bone.bone
    try:
        rest_world_rot = bone_rest_world_rot(pose_bone, armature_obj)

        target = target_dir_world.normalized()
        if target.length_squared < 1e-8:
            return

        local_target = (rest_world_rot.inverted() @ target).normalized()
    except ValueError:
        print(f"[CORPUS-MOCAP] Matrice de repos non-inversible pour l'os '{bone.name}' — gelé cette trame.")
        return

    quat = Vector((0.0, 1.0, 0.0)).rotation_difference(local_target)

    pose_bone.rotation_mode = "QUATERNION"
    pose_bone.rotation_quaternion = quat


def _apply_full_rotation(
    pose_bone: bpy.types.PoseBone,
    world_target_rot: Matrix,
    armature_obj: bpy.types.Object,
    twist_damping: float = 1.0,
) -> None:
    """Applique une rotation complète (3 degrés de liberté, avec torsion)
    à `pose_bone`, à partir d'une matrice de rotation cible exprimée en
    axes du rig (X droite, Y devant soi, Z haut ; identité = pose neutre
    debout face caméra). Même principe de conjugaison par l'orientation
    de repos que `face_mapping.apply_head_rotation` (contrairement à
    `_aim_bone` qui ne contraint que 2 degrés de liberté, la direction,
    en laissant la torsion libre).

    `twist_damping` (0-1) réduit la composante de torsion (rotation
    autour de l'axe Y local du bone) sans toucher à l'inclinaison/
    direction — utile car la torsion du buste est sensible à des
    mouvements qui n'en sont pas vraiment (lever un bras déplace un peu
    l'épaule correspondante, ce qui peut être interprété à tort comme une
    rotation du buste)."""
    bone = pose_bone.bone
    try:
        rest_world_rot = bone_rest_world_rot(pose_bone, armature_obj)
        local_rot = rest_world_rot.inverted() @ world_target_rot @ rest_world_rot
    except ValueError:
        print(f"[CORPUS-MOCAP] Matrice de repos non-inversible pour l'os '{bone.name}' — gelé cette trame.")
        return
    quat = local_rot.to_quaternion()

    if twist_damping < 1.0:
        # Décomposition swing-twist autour de l'axe Y local du bone.
        twist = Quaternion((quat.w, 0.0, quat.y, 0.0))
        if twist.magnitude > 1e-6:
            twist.normalize()
            swing = quat @ twist.inverted()
            twist = Quaternion((1.0, 0.0, 0.0, 0.0)).slerp(twist, max(0.0, twist_damping))
            quat = swing @ twist

    pose_bone.rotation_mode = "QUATERNION"
    pose_bone.rotation_quaternion = quat


def _torso_orientation_matrix(
    hip_center: Vector, shoulder_center: Vector, left_ref: Vector, right_ref: Vector
) -> Matrix | None:
    """Construit une matrice de rotation 3x3 (axes du rig : X droite, Y
    devant soi, Z haut) représentant une orientation complète, torsion
    comprise — contrairement au simple "aim" bassin->épaules qui ne capte
    pas la rotation sur soi-même (pivoter sans se pencher). `left_ref` /
    `right_ref` servent de référence de torsion (épaules pour le buste,
    hanches pour le bassin) ; `hip_center`/`shoulder_center` définissent
    l'axe "haut". Convention empirique (comme pour la tête) : à ajuster
    si le sens de rotation est inversé lors des premiers tests."""
    up = shoulder_center - hip_center
    if up.length_squared < 1e-8:
        return None
    up = up.normalized()

    # left_ref -> right_ref (pas l'inverse) : dans notre convention
    # (_landmark_to_vector sans inversion de x, et
    # tools/generate_test_rig.py où les os ".L" sont côté +X), le point
    # "left_*" de MediaPipe se retrouve côté +X du rig au repos — il faut
    # donc partir de left_ref pour que "right" pointe bien vers +X
    # (identité) en pose neutre face caméra.
    right_raw = left_ref - right_ref
    if right_raw.length_squared < 1e-8:
        return None
    right = right_raw - up * right_raw.dot(up)  # orthogonalisation (Gram-Schmidt)
    if right.length_squared < 1e-8:
        return None
    right = right.normalized()

    forward = right.cross(up)

    return Matrix((
        (right.x, forward.x, up.x),
        (right.y, forward.y, up.y),
        (right.z, forward.z, up.z),
    ))


def apply_pose(
    armature_obj: bpy.types.Object,
    landmarks: list[dict],
    initial_hip_center: Vector | None,
    prefix: str = "",
    suffix: str = "",
) -> Vector:
    """Applique une trame de landmarks sur `armature_obj`.

    Retourne le centre de bassin (espace rig) de cette trame ; l'appelant
    le repasse en `initial_hip_center` à l'appel suivant pour calculer une
    translation relative du bassin (position de la 1ère trame = origine).

    `prefix`/`suffix` : voir resolve_bone_name — pour un rig dont les os
    ne sont pas nommés exactement selon la convention par défaut."""
    pose_bones = armature_obj.pose.bones

    def bone(name: str):
        return pose_bones.get(resolve_bone_name(name, prefix, suffix))

    def lm(name: str) -> Vector:
        return _landmark_to_vector(landmarks[LANDMARK_INDEX[name]])

    hip_center = (lm("left_hip") + lm("right_hip")) / 2.0
    shoulder_center = (lm("left_shoulder") + lm("right_shoulder")) / 2.0

    hips_visible = _visible(landmarks, "left_hip") and _visible(landmarks, "right_hip")
    shoulders_visible = _visible(landmarks, "left_shoulder") and _visible(landmarks, "right_shoulder")

    hips_bone = bone("hips")
    if hips_bone is not None and hips_visible:
        if initial_hip_center is None:
            hips_bone.location = Vector((0.0, 0.0, 0.0))
        else:
            delta = hip_center - initial_hip_center
            hips_bone.location = Vector((
                delta.x * ROOT_TRANSLATION_SCALE_LATERAL,
                delta.y * ROOT_TRANSLATION_SCALE_DEPTH,
                delta.z * ROOT_TRANSLATION_SCALE_LATERAL,
            ))
        # Pas de rotation sur "hips" : les cuisses en sont enfants dans le
        # rig, et la moindre instabilité de la rotation du bassin se
        # répercute en cascade sur le calcul des jambes (_aim_bone lit la
        # matrice *courante* du parent). Le buste (spine) capte déjà
        # l'essentiel de la torsion du corps ; le risque de régression sur
        # les jambes ne vaut pas le gain ici.

    spine_bone = bone("spine")
    if spine_bone is not None and hips_visible and shoulders_visible:
        # Retour au simple "aim" (direction bassin->épaules, sans torsion) :
        # la version à 3 degrés de liberté (_torso_orientation_matrix,
        # toujours définie plus haut) a produit plusieurs régressions
        # (position anormale au neutre, rig qui part de travers) malgré
        # plusieurs correctifs successifs, sans pouvoir être validée en
        # conditions réelles. À reprendre plus tard avec plus de recul —
        # potentiellement avec de meilleures données de profondeur
        # (Phase 5, multi-caméra).
        spine_dir = shoulder_center - hip_center
        spine_dir.y *= SPINE_DEPTH_DAMPING
        _aim_bone(spine_bone, spine_dir, armature_obj)
        bpy.context.view_layer.update()

    if shoulders_visible:
        for clavicle_bone_name, shoulder_landmark_name in CLAVICLE_SEGMENTS:
            clavicle_bone = bone(clavicle_bone_name)
            if clavicle_bone is None or not _visible(landmarks, shoulder_landmark_name):
                continue
            clavicle_dir = lm(shoulder_landmark_name) - shoulder_center
            clavicle_dir.y *= CLAVICLE_DEPTH_DAMPING
            _aim_bone(clavicle_bone, clavicle_dir, armature_obj)
            bpy.context.view_layer.update()

    for bone_name, start_name, end_name in LIMB_SEGMENTS:
        pose_bone = bone(bone_name)
        if pose_bone is None:
            continue
        if not (_visible(landmarks, start_name) and _visible(landmarks, end_name)):
            continue  # membre non fiable (souvent hors cadre) : on le gèle
        limb_dir = lm(end_name) - lm(start_name)
        limb_dir.y *= LIMB_DEPTH_DAMPING
        _aim_bone(pose_bone, limb_dir, armature_obj)
        bpy.context.view_layer.update()

    return hip_center


def get_animated_bone_names(prefix: str = "", suffix: str = "") -> list[str]:
    """Noms résolus des os affectés par apply_pose, pour l'insertion de keyframes."""
    names = ["hips", "spine"]
    names.extend(bone_name for bone_name, _ in CLAVICLE_SEGMENTS)
    names.extend(bone_name for bone_name, _, _ in LIMB_SEGMENTS)
    return [resolve_bone_name(name, prefix, suffix) for name in names]
