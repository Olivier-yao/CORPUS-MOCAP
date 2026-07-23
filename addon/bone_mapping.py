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
from mathutils import Matrix, Vector

LANDMARK_INDEX = {
    "left_shoulder": 11, "right_shoulder": 12,
    "left_elbow": 13, "right_elbow": 14,
    "left_wrist": 15, "right_wrist": 16,
    "left_hip": 23, "right_hip": 24,
    "left_knee": 25, "right_knee": 26,
    "left_ankle": 27, "right_ankle": 28,
}

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

# En dessous de ce seuil de confiance MediaPipe (0-1), un landmark est
# considéré "non fiable" (souvent hors cadre) : le membre concerné est
# gelé (on ne touche pas à sa rotation/position) plutôt que de suivre une
# position devinée par le modèle, qui donne un mouvement erratique/figé
# sans rapport avec le geste réel.
VISIBILITY_THRESHOLD = 0.5


def _visible(landmarks: list[dict], name: str) -> bool:
    return landmarks[LANDMARK_INDEX[name]]["visibility"] >= VISIBILITY_THRESHOLD


def _landmark_to_vector(landmark: dict) -> Vector:
    """Convertit un landmark MediaPipe (image space : x droite, y bas,
    z profondeur) en vecteur dans l'espace du rig (Z up, Y devant soi)."""
    x = landmark["x"] - 0.5
    y = -landmark["z"]
    z = -(landmark["y"] - 0.5)
    return Vector((x, y, z))


def _aim_bone(pose_bone: bpy.types.PoseBone, target_dir_world: Vector, armature_obj: bpy.types.Object) -> None:
    """Oriente `pose_bone` pour que son axe de repos (Y local) pointe vers
    `target_dir_world`, exprimé dans l'espace du rig. Utilise la matrice
    monde *actuelle* du parent (`pose_bone.parent.matrix`), donc l'appelant
    doit avoir rafraîchi le depsgraph (`view_layer.update()`) si le parent
    vient d'être modifié dans la même trame."""
    bone = pose_bone.bone
    if pose_bone.parent is not None:
        parent_world_rot = pose_bone.parent.matrix.to_3x3()
        rest_local_rot = (pose_bone.parent.bone.matrix_local.inverted() @ bone.matrix_local).to_3x3()
    else:
        parent_world_rot = armature_obj.matrix_world.to_3x3()
        rest_local_rot = bone.matrix_local.to_3x3()

    rest_world_rot = parent_world_rot @ rest_local_rot

    target = target_dir_world.normalized()
    if target.length_squared < 1e-8:
        return

    local_target = (rest_world_rot.inverted() @ target).normalized()
    quat = Vector((0.0, 1.0, 0.0)).rotation_difference(local_target)

    pose_bone.rotation_mode = "QUATERNION"
    pose_bone.rotation_quaternion = quat


def _apply_full_rotation(pose_bone: bpy.types.PoseBone, world_target_rot: Matrix, armature_obj: bpy.types.Object) -> None:
    """Applique une rotation complète (3 degrés de liberté, avec torsion)
    à `pose_bone`, à partir d'une matrice de rotation cible exprimée en
    axes du rig (X droite, Y devant soi, Z haut ; identité = pose neutre
    debout face caméra). Même principe de conjugaison par l'orientation
    de repos que `face_mapping.apply_head_rotation` (contrairement à
    `_aim_bone` qui ne contraint que 2 degrés de liberté, la direction,
    en laissant la torsion libre)."""
    bone = pose_bone.bone
    if pose_bone.parent is not None:
        parent_world_rot = pose_bone.parent.matrix.to_3x3()
        rest_local_rot = (pose_bone.parent.bone.matrix_local.inverted() @ bone.matrix_local).to_3x3()
    else:
        parent_world_rot = armature_obj.matrix_world.to_3x3()
        rest_local_rot = bone.matrix_local.to_3x3()
    rest_world_rot = parent_world_rot @ rest_local_rot

    local_rot = rest_world_rot.inverted() @ world_target_rot @ rest_world_rot
    pose_bone.rotation_mode = "QUATERNION"
    pose_bone.rotation_quaternion = local_rot.to_quaternion()


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


def apply_pose(armature_obj: bpy.types.Object, landmarks: list[dict], initial_hip_center: Vector | None) -> Vector:
    """Applique une trame de landmarks sur `armature_obj`.

    Retourne le centre de bassin (espace rig) de cette trame ; l'appelant
    le repasse en `initial_hip_center` à l'appel suivant pour calculer une
    translation relative du bassin (position de la 1ère trame = origine)."""
    pose_bones = armature_obj.pose.bones

    def lm(name: str) -> Vector:
        return _landmark_to_vector(landmarks[LANDMARK_INDEX[name]])

    hip_center = (lm("left_hip") + lm("right_hip")) / 2.0
    shoulder_center = (lm("left_shoulder") + lm("right_shoulder")) / 2.0

    hips_visible = _visible(landmarks, "left_hip") and _visible(landmarks, "right_hip")
    shoulders_visible = _visible(landmarks, "left_shoulder") and _visible(landmarks, "right_shoulder")

    hips_bone = pose_bones.get("hips")
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
        if shoulders_visible:
            hip_orientation = _torso_orientation_matrix(
                hip_center, shoulder_center, lm("left_hip"), lm("right_hip")
            )
            if hip_orientation is not None:
                _apply_full_rotation(hips_bone, hip_orientation, armature_obj)
                bpy.context.view_layer.update()

    spine_bone = pose_bones.get("spine")
    if spine_bone is not None and hips_visible and shoulders_visible:
        orientation = _torso_orientation_matrix(
            hip_center, shoulder_center, lm("left_shoulder"), lm("right_shoulder")
        )
        if orientation is not None:
            _apply_full_rotation(spine_bone, orientation, armature_obj)
            bpy.context.view_layer.update()

    for bone_name, start_name, end_name in LIMB_SEGMENTS:
        pose_bone = pose_bones.get(bone_name)
        if pose_bone is None:
            continue
        if not (_visible(landmarks, start_name) and _visible(landmarks, end_name)):
            continue  # membre non fiable (souvent hors cadre) : on le gèle
        _aim_bone(pose_bone, lm(end_name) - lm(start_name), armature_obj)
        bpy.context.view_layer.update()

    return hip_center


def get_animated_bone_names() -> list[str]:
    """Noms des os affectés par apply_pose, pour l'insertion de keyframes."""
    names = ["hips", "spine"]
    names.extend(bone_name for bone_name, _, _ in LIMB_SEGMENTS)
    return names
