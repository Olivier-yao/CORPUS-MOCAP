"""N-panel CORPUS-MOCAP (Phase 1 : source webcam PC uniquement)."""

import bpy


class VIEW3D_PT_corpus_mocap(bpy.types.Panel):
    bl_label = "CORPUS-MOCAP"
    bl_idname = "VIEW3D_PT_corpus_mocap"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "CORPUS-MOCAP"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.corpus_mocap

        layout.prop(settings, "target_armature")
        layout.prop(settings, "target_face_mesh")

        mapping_box = layout.box()
        mapping_box.label(text="Mapping des os (optionnel)", icon="OUTLINER_DATA_ARMATURE")
        mapping_box.prop(settings, "bone_prefix")
        mapping_box.prop(settings, "bone_suffix")
        mapping_box.operator(
            "mocap.apply_bone_affixes", text="Appliquer aux os sélectionnés", icon="SORTALPHA"
        )
        if context.mode == "EDIT_ARMATURE":
            mapping_box.label(text="Sélectionnez les os à renommer en Edit Mode", icon="INFO")
        else:
            mapping_box.label(text="(bouton actif en Edit Mode sur l'armature)", icon="INFO")

        box = layout.box()
        box.label(text="Source : Webcam PC")
        row = box.row(align=True)
        row.prop(settings, "host")
        row.prop(settings, "port")

        layout.prop(settings, "stability")

        layout.separator()
        icon = "PAUSE" if settings.is_recording else "REC"
        label = "Arrêter l'enregistrement" if settings.is_recording else "Enregistrer la performance"
        layout.operator("mocap.toggle_capture", text=label, icon=icon, depress=settings.is_recording)
        layout.operator("mocap.reset_rig", text="Réinitialiser le rig", icon="LOOP_BACK")

        layout.label(text=settings.status_message)


CLASSES = (VIEW3D_PT_corpus_mocap,)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
