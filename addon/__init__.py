"""CORPUS-MOCAP — addon Blender de capture de mouvement (Phase 1 : corps,
Phase 2 : visage, source webcam PC). Voir CORPUS-MOCAP_cahier-des-charges.md."""

bl_info = {
    "name": "CORPUS-MOCAP",
    "author": "SNTRX (Éditions Prime)",
    "version": (0, 2, 0),
    "blender": (4, 0, 0),
    "location": "Vue 3D > N-panel > CORPUS-MOCAP",
    "description": "Capture de mouvement temps réel via webcam (MediaPipe Pose + Face)",
    "category": "Animation",
}

from . import properties, bone_mapping, face_mapping, operators, panel

del bone_mapping, face_mapping  # importés pour s'assurer qu'ils se chargent sans erreur


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
