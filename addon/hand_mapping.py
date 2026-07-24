"""Mapping des landmarks MediaPipe Hand Landmarker (21 points par main)
vers les bones de doigts + poignet du rig — voir tools/generate_test_hands.py
pour la convention de nommage (<doigt>.<01|02|03>.<L|R>, doigts parentés à
hand.L/hand.R).

Même méthode de retargeting que les membres du corps (aim simplifié,
sans torsion — voir addon/bone_mapping.py) pour les doigts. Pas de gel sur
confiance basse : MediaPipe Hand Landmarker ne fournit pas de score de
visibilité par point comme MediaPipe Pose ; une main est soit détectée
entièrement, soit absente (voir capture_server/server.py: extract_hands).

Le poignet (hand.L/R) capte la pronation/supination (tourner la paume
vers le haut/bas) via une TORSION SEULE autour de l'axe de visée actuel
de l'avant-bras — cet axe n'est jamais recalculé à partir des landmarks
de la main, il reste toujours exactement celui de l'avant-bras (via
`bone_mapping.bone_rest_world_rot`), garantissant que la main continue
toujours dans son prolongement (jamais de "décrochage" visuel). Seule la
torsion (rotation autour de cet axe fixe) est dérivée des landmarks de
la main (index/auriculaire). Une première version calculait une
orientation complète à 3 degrés de liberté à partir des landmarks de la
main seuls, indépendamment de l'avant-bras — ça causait un décrochage
visuel (deux modèles MediaPipe différents, sans garantie de cohérence
entre eux). Contrairement à la torsion du buste (abandonnée, voir
bone_mapping.py), les doigts ne dépendent pas du poignet pour leur
calcul (ils visent directement leur propre cible dans l'espace monde),
donc une éventuelle imprécision de la torsion du poignet ne les fait
pas dérailler en cascade."""

from __future__ import annotations

import bpy
from mathutils import Quaternion, Vector

from .bone_mapping import _aim_bone, bone_rest_world_rot, _landmark_to_vector, resolve_bone_name

# (nom du doigt, indices des 4 landmarks de sa chaîne : base, 2 jointures, bout)
FINGER_LANDMARKS = [
    ("thumb", (1, 2, 3, 4)),
    ("index", (5, 6, 7, 8)),
    ("middle", (9, 10, 11, 12)),
    ("ring", (13, 14, 15, 16)),
    ("pinky", (17, 18, 19, 20)),
]

# Landmarks de repère pour la torsion du poignet.
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


def _wrist_twist_quaternion(
    hand_bone: bpy.types.PoseBone, armature_obj: bpy.types.Object, index_mcp: Vector, pinky_mcp: Vector
) -> Quaternion | None:
    """Quaternion de torsion PURE (rotation uniquement autour de l'axe Y
    local du bone, qui reste fixé sur la direction actuelle de l'avant-
    bras) à appliquer directement à `hand_bone.rotation_quaternion` — pas
    de conjugaison nécessaire, ce quaternion est déjà dans le bon repère.

    Principe : l'axe de visée (up) n'est JAMAIS recalculé à partir des
    landmarks de la main — il reste exactement celui de l'orientation de
    repos actuelle du bone (donc dans le prolongement de l'avant-bras).
    Seule la torsion autour de cet axe fixe est dérivée de la direction
    index->auriculaire, projetée perpendiculairement à cet axe (donc,
    par construction, la rotation résultante ne peut être qu'une torsion
    pure — la preuve : les deux vecteurs comparés via rotation_difference
    sont tous deux perpendiculaires à l'axe Y local, la rotation minimale
    entre eux est donc nécessairement une rotation autour de cet axe)."""
    try:
        rest_world_rot = bone_rest_world_rot(hand_bone, armature_obj)
    except ValueError:
        print(f"[CORPUS-MOCAP] Matrice de repos non-inversible pour l'os '{hand_bone.bone.name}' — gelé cette trame.")
        return None

    up_world = rest_world_rot.col[1]  # axe Y local (visée), figé tel quel

    right_raw = index_mcp - pinky_mcp
    right_raw = Vector((right_raw.x, right_raw.y * HAND_DEPTH_DAMPING, right_raw.z))
    right_world = right_raw - up_world * right_raw.dot(up_world)  # perpendiculaire à l'axe fixe
    if right_world.length_squared < 1e-8:
        return None
    right_world.normalize()

    try:
        right_local = (rest_world_rot.inverted() @ right_world).normalized()
    except ValueError:
        return None

    return Vector((1.0, 0.0, 0.0)).rotation_difference(right_local)


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

    hand_bone = bone(f"hand.{side}")
    if hand_bone is not None:
        twist = _wrist_twist_quaternion(hand_bone, armature_obj, lm(INDEX_MCP), lm(PINKY_MCP))
        if twist is not None:
            hand_bone.rotation_mode = "QUATERNION"
            hand_bone.rotation_quaternion = twist
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
