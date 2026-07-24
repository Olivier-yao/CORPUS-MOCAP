"""Mapping des landmarks MediaPipe Hand Landmarker (21 points par main)
vers les bones de doigts + poignet du rig — voir tools/generate_test_hands.py
pour la convention de nommage (<doigt>.<01|02|03>.<L|R>, doigts parentés à
hand.L/hand.R).

Même méthode de retargeting que les membres du corps (aim simplifié,
sans torsion — voir addon/bone_mapping.py) pour les doigts. Pas de gel sur
confiance basse : MediaPipe Hand Landmarker ne fournit pas de score de
visibilité par point comme MediaPipe Pose ; une main est soit détectée
entièrement, soit absente (voir capture_server/server.py: extract_hands).

Le poignet (hand.L/R) utilise une rotation complète à 3 degrés de liberté
(comme face_mapping.apply_head_rotation) pour capter la pronation/
supination (tourner la paume vers le haut/bas) que le simple aim des
doigts ne capte pas. Convention d'axes empirique — à vérifier/ajuster si
le poignet tourne dans le mauvais sens lors des premiers tests. Contrairement
à la torsion du buste (abandonnée, voir bone_mapping.py), les doigts ne
dépendent pas du poignet pour leur calcul (ils visent directement leur
propre cible dans l'espace monde), donc une éventuelle instabilité du
poignet ne devrait pas les faire déraper en cascade."""

from __future__ import annotations

import bpy
from mathutils import Matrix, Vector

from .bone_mapping import _aim_bone, _apply_full_rotation, _landmark_to_vector, resolve_bone_name

# (nom du doigt, indices des 4 landmarks de sa chaîne : base, 2 jointures, bout)
FINGER_LANDMARKS = [
    ("thumb", (1, 2, 3, 4)),
    ("index", (5, 6, 7, 8)),
    ("middle", (9, 10, 11, 12)),
    ("ring", (13, 14, 15, 16)),
    ("pinky", (17, 18, 19, 20)),
]

# Landmarks de repère pour l'orientation du poignet.
WRIST = 0
MIDDLE_MCP = 9
INDEX_MCP = 5
PINKY_MCP = 17

# Même logique que SPINE_DEPTH_DAMPING (bone_mapping.py) : atténue l'axe
# de profondeur, le plus bruité en mono-caméra RGB.
HAND_DEPTH_DAMPING = 0.4

# Idem pour les segments de doigts : ce sont des os très courts, donc le
# même niveau de bruit sur l'axe de profondeur produit une erreur
# angulaire bien plus grande que sur un os long (bras, jambe) — d'où un
# amortissement plus fort ici.
FINGER_DEPTH_DAMPING = 0.25


def _hand_orientation_matrix(wrist: Vector, middle_mcp: Vector, index_mcp: Vector, pinky_mcp: Vector) -> Matrix | None:
    """Matrice de rotation 3x3 (axes du rig : X droite, Y devant soi, Z
    haut) représentant l'orientation de la paume, à partir du poignet et
    des bases d'index/majeur/auriculaire."""
    up = middle_mcp - wrist
    if up.length_squared < 1e-8:
        return None
    up = Vector((up.x, up.y * HAND_DEPTH_DAMPING, up.z)).normalized()

    # right_raw n'était pas amorti sur l'axe de profondeur jusqu'ici — même
    # trou que celui trouvé sur les membres du corps (upper_arm/forearm),
    # cause probable du chaos observé lors du premier essai de rotation
    # du poignet.
    right_raw = index_mcp - pinky_mcp
    right_raw = Vector((right_raw.x, right_raw.y * HAND_DEPTH_DAMPING, right_raw.z))
    if right_raw.length_squared < 1e-8:
        return None
    right = right_raw - up * right_raw.dot(up)
    if right.length_squared < 1e-8:
        return None
    right = right.normalized()

    forward = right.cross(up)

    return Matrix((
        (right.x, forward.x, up.x),
        (right.y, forward.y, up.y),
        (right.z, forward.z, up.z),
    ))


def apply_hand(
    armature_obj: bpy.types.Object, landmarks: list[dict], side: str, prefix: str = "", suffix: str = ""
) -> None:
    """side : "L" ou "R". landmarks : 21 dicts {x, y, z} d'une main
    MediaPipe (coordonnées normalisées, même convention que le corps).
    `prefix`/`suffix` : voir bone_mapping.resolve_bone_name."""
    pose_bones = armature_obj.pose.bones

    def lm(i: int) -> Vector:
        return _landmark_to_vector(landmarks[i])

    def bone(name: str):
        return pose_bones.get(resolve_bone_name(name, prefix, suffix))

    # Réactivé après ajout de l'amortissement de profondeur manquant sur
    # right_raw (cause probable du chaos précédent, par analogie avec le
    # même trou trouvé sur upper_arm/forearm) — à revalider en conditions
    # réelles.
    hand_bone = bone(f"hand.{side}")
    if hand_bone is not None:
        orientation = _hand_orientation_matrix(lm(WRIST), lm(MIDDLE_MCP), lm(INDEX_MCP), lm(PINKY_MCP))
        if orientation is not None:
            _apply_full_rotation(hand_bone, orientation, armature_obj)
            bpy.context.view_layer.update()

    for finger_name, indices in FINGER_LANDMARKS:
        for seg_index in range(3):
            pose_bone = bone(f"{finger_name}.{seg_index + 1:02d}.{side}")
            if pose_bone is None:
                continue
            start = lm(indices[seg_index])
            end = lm(indices[seg_index + 1])
            direction = end - start
            direction.y *= FINGER_DEPTH_DAMPING
            _aim_bone(pose_bone, direction, armature_obj)
            bpy.context.view_layer.update()


def get_animated_bone_names(side: str, prefix: str = "", suffix: str = "") -> list[str]:
    """Noms résolus des bones (poignet + doigts) affectés par apply_hand
    pour un côté donné ("L" ou "R"), pour l'insertion de keyframes."""
    names = [f"hand.{side}"]
    for finger_name, _ in FINGER_LANDMARKS:
        for seg_index in range(1, 4):
            names.append(f"{finger_name}.{seg_index:02d}.{side}")
    return [resolve_bone_name(name, prefix, suffix) for name in names]
