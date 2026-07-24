"""CORPUS-MOCAP — addon Blender de capture de mouvement (corps, visage,
mains, source webcam PC). Voir CORPUS-MOCAP_cahier-des-charges.md."""

bl_info = {
    "name": "CORPUS-MOCAP",
    "author": "SNTRX (Éditions Prime)",
    "version": (0, 4, 0),
    "blender": (4, 0, 0),
    "location": "Vue 3D > N-panel > CORPUS-MOCAP",
    "description": "Capture de mouvement temps réel via webcam (MediaPipe Pose + Face + Hands)",
    "category": "Animation",
}

from . import properties, bone_mapping, face_mapping, hand_mapping, character_builder, operators, panel

del bone_mapping, face_mapping, hand_mapping, character_builder  # importés pour s'assurer qu'ils se chargent sans erreur


def register():
    properties.register()
    operators.register()
    panel.register()


def unregister():
    panel.unregister()
    operators.unregister()
    properties.unregister()


if __name__ == "__main__":
    register()
