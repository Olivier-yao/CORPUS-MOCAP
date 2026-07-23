"""Mapping des landmarks MediaPipe Hand Landmarker (21 points par main)
vers les bones de doigts du rig — voir tools/generate_test_hands.py pour
la convention de nommage (<doigt>.<01|02|03>.<L|R>, doigts parentés à
hand.L/hand.R).

Même méthode de retargeting que les membres du corps (aim simplifié,
sans torsion — voir addon/bone_mapping.py). Pas de gel sur confiance
basse : MediaPipe Hand Landmarker ne fournit pas de score de visibilité
par point comme MediaPipe Pose ; une main est soit détectée entièrement,
soit absente (voir capture_server/server.py: extract_hands)."""

from __future__ import annotations

import bpy
from mathutils import Vector

from .bone_mapping import _aim_bone, _landmark_to_vector

# (nom du doigt, indices des 4 landmarks de sa chaîne : base, 2 jointures, bout)
FINGER_LANDMARKS = [
    ("thumb", (1, 2, 3, 4)),
    ("index", (5, 6, 7, 8)),
    ("middle", (9, 10, 11, 12)),
    ("ring", (13, 14, 15, 16)),
    ("pinky", (17, 18, 19, 20)),
]


def apply_hand(armature_obj: bpy.types.Object, landmarks: list[dict], side: str) -> None:
    """side : "L" ou "R". landmarks : 21 dicts {x, y, z} d'une main
    MediaPipe (coordonnées normalisées, même convention que le corps)."""
    pose_bones = armature_obj.pose.bones

    def lm(i: int) -> Vector:
        return _landmark_to_vector(landmarks[i])

    for finger_name, indices in FINGER_LANDMARKS:
        for seg_index in range(3):
            bone_name = f"{finger_name}.{seg_index + 1:02d}.{side}"
            pose_bone = pose_bones.get(bone_name)
            if pose_bone is None:
                continue
            start = lm(indices[seg_index])
            end = lm(indices[seg_index + 1])
            _aim_bone(pose_bone, end - start, armature_obj)
            bpy.context.view_layer.update()


def get_animated_bone_names(side: str) -> list[str]:
    """Noms des bones de doigts affectés par apply_hand pour un côté
    donné ("L" ou "R"), pour l'insertion de keyframes."""
    names = []
    for finger_name, _ in FINGER_LANDMARKS:
        for seg_index in range(1, 4):
            names.append(f"{finger_name}.{seg_index:02d}.{side}")
    return names
