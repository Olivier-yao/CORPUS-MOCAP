"""Format des messages échangés entre capture_server et l'addon Blender.

Les messages circulent sur un socket TCP, un objet JSON par ligne
(terminée par '\n'). Deux directions :

- server -> addon : trames corps (voir `build_frame_message`), trames
  visage (voir `build_face_message`) et trames mains (voir
  `build_hands_message`), envoyées indépendamment sur la même connexion
- addon -> server  : messages de contrôle (voir `build_control_message`)
"""

from __future__ import annotations

# Index des landmarks MediaPipe Pose utiles au mapping corps (33 points au total,
# seuls les indices utilisés par CORPUS-MOCAP Phase 1 sont listés ici).
LANDMARK_INDEX = {
    "nose": 0,
    "left_shoulder": 11,
    "right_shoulder": 12,
    "left_elbow": 13,
    "right_elbow": 14,
    "left_wrist": 15,
    "right_wrist": 16,
    "left_hip": 23,
    "right_hip": 24,
    "left_knee": 25,
    "right_knee": 26,
    "left_ankle": 27,
    "right_ankle": 28,
}

NUM_LANDMARKS = 33
NUM_HAND_LANDMARKS = 21


def build_frame_message(landmarks: list[dict], tracking_ok: bool) -> dict:
    """landmarks: liste de 33 dicts {x, y, z, visibility} en coordonnées
    normalisées MediaPipe (origine en haut à gauche, y vers le bas)."""
    return {
        "type": "frame",
        "tracking_ok": tracking_ok,
        "landmarks": landmarks,
    }


def build_face_message(
    blendshapes: dict[str, float], tracking_ok: bool, head_rotation: list[float] | None = None
) -> dict:
    """blendshapes: dict {nom_coefficient_ARKit: valeur 0.0-1.0}, ex.
    {"jawOpen": 0.42, "eyeBlinkLeft": 0.0, ...} — voir la liste complète
    des 52 noms standard dans la documentation MediaPipe FaceLandmarker.

    head_rotation: sous-matrice de rotation 3x3 (9 floats, ligne par ligne)
    issue de facial_transformation_matrixes, ou None si non détecté."""
    return {
        "type": "face",
        "tracking_ok": tracking_ok,
        "blendshapes": blendshapes,
        "head_rotation": head_rotation,
    }


def build_hands_message(hands: dict[str, list[dict] | None], tracking_ok: bool) -> dict:
    """hands: {"left": [21 dicts {x,y,z}] | None, "right": [...] | None} —
    "left"/"right" selon la classification "handedness" de MediaPipe
    (main anatomique du sujet, même convention que left_shoulder/
    right_shoulder pour le corps)."""
    return {
        "type": "hands",
        "tracking_ok": tracking_ok,
        "hands": hands,
    }


def build_control_message(stability: float) -> dict:
    """0.0 = lissage léger, 1.0 = lissage fort."""
    return {
        "type": "set_stability",
        "value": max(0.0, min(1.0, stability)),
    }
