"""État et réglages exposés dans le N-panel CORPUS-MOCAP."""

import bpy


class MOCAP_Settings(bpy.types.PropertyGroup):
    target_armature: bpy.props.PointerProperty(
        name="Cible (corps)",
        description="Armature sur laquelle appliquer la capture du corps",
        type=bpy.types.Object,
        poll=lambda self, obj: obj.type == "ARMATURE",
    )

    target_face_mesh: bpy.props.PointerProperty(
        name="Cible (visage)",
        description="Mesh à shape keys sur lequel appliquer les blend shapes du visage (optionnel)",
        type=bpy.types.Object,
        poll=lambda self, obj: obj.type == "MESH" and obj.data.shape_keys is not None,
    )

    stability: bpy.props.FloatProperty(
        name="Stabilité",
        description="Léger = plus réactif mais plus de tremblement. Fort = plus lisse mais plus de latence.",
        default=0.5,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )

    bone_prefix: bpy.props.StringProperty(
        name="Préfixe des os",
        description=(
            "Préfixé à chaque nom d'os attendu (ex: 'hips' -> 'DEF-hips'). "
            "Utile pour un rig auto-généré (ex: Rigify utilise le préfixe 'DEF-' "
            "sur ses os de déformation). Laisser vide si vos os sont nommés "
            "exactement comme la convention CORPUS-MOCAP (voir tools/generate_test_rig.py)"
        ),
        default="",
    )

    bone_suffix: bpy.props.StringProperty(
        name="Suffixe des os",
        description="Suffixé à chaque nom d'os attendu (ex: 'hips' -> 'hips_deform')",
        default="",
    )

    host: bpy.props.StringProperty(
        name="Hôte",
        default="127.0.0.1",
    )

    port: bpy.props.IntProperty(
        name="Port",
        default=9001,
        min=1,
        max=65535,
    )

    is_connected: bpy.props.BoolProperty(default=False)
    is_recording: bpy.props.BoolProperty(default=False)
    status_message: bpy.props.StringProperty(default="Non connecté")


CLASSES = (MOCAP_Settings,)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.corpus_mocap = bpy.props.PointerProperty(type=MOCAP_Settings)


def unregister():
    del bpy.types.Scene.corpus_mocap
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
