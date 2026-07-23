"""Operators : connexion à capture_server, application temps réel de la
capture sur le rig, et enregistrement sous forme d'Action Blender.

Cahier des charges §5/§3 : un bouton unique ("Enregistrer la performance")
démarre puis arrête l'enregistrement — pas d'étape de connexion séparée.
"""

from __future__ import annotations

import bpy

from . import bone_mapping, face_mapping, hand_mapping
from .socket_client import MocapSocketClient, SocketClientError


class _CaptureSession:
    """État de la session de capture en cours (une seule à la fois).

    Vit en dehors de l'instance d'Operator car Blender recrée une nouvelle
    instance à chaque invoke() : un second clic sur le bouton doit pouvoir
    retrouver puis arrêter la session démarrée par le premier clic.
    """

    instance: "_CaptureSession | None" = None

    def __init__(self, armature, client, face_mesh=None, bone_prefix="", bone_suffix=""):
        self.armature = armature
        self.client = client
        self.face_mesh = face_mesh
        self.bone_prefix = bone_prefix
        self.bone_suffix = bone_suffix
        self.timer = None
        self.initial_hip_center = None
        self.last_sent_stability = None


class MOCAP_OT_toggle_capture(bpy.types.Operator):
    """Démarre/arrête la capture temps réel et l'enregistrement."""

    bl_idname = "mocap.toggle_capture"
    bl_label = "Enregistrer la performance"

    def invoke(self, context, event):
        settings = context.scene.corpus_mocap

        if _CaptureSession.instance is not None:
            self._stop(context)
            return {'FINISHED'}

        if settings.target_armature is None:
            self.report({'ERROR'}, "Choisissez une armature cible")
            return {'CANCELLED'}

        try:
            client = MocapSocketClient(settings.host, settings.port)
        except OSError as exc:
            self.report({'ERROR'}, f"Connexion à capture_server impossible : {exc}")
            return {'CANCELLED'}

        armature = settings.target_armature
        armature.animation_data_create()
        action = bpy.data.actions.new("CORPUS_MOCAP_Take")
        armature.animation_data.action = action

        face_mesh = settings.target_face_mesh
        if face_mesh is not None and face_mesh.data.shape_keys is not None:
            face_mesh.data.shape_keys.animation_data_create()
            face_action = bpy.data.actions.new("CORPUS_MOCAP_Face_Take")
            face_mesh.data.shape_keys.animation_data.action = face_action
        else:
            face_mesh = None

        session = _CaptureSession(armature, client, face_mesh, settings.bone_prefix, settings.bone_suffix)
        session.timer = context.window_manager.event_timer_add(1.0 / 30.0, window=context.window)
        _CaptureSession.instance = session

        settings.is_connected = True
        settings.is_recording = True
        settings.status_message = "Enregistrement en cours..."

        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if _CaptureSession.instance is None:
            return {'FINISHED'}

        if event.type == 'ESC':
            self._stop(context)
            return {'FINISHED'}

        if event.type != 'TIMER':
            return {'PASS_THROUGH'}

        settings = context.scene.corpus_mocap
        session = _CaptureSession.instance

        if settings.stability != session.last_sent_stability:
            session.client.send_control({"type": "set_stability", "value": settings.stability})
            session.last_sent_stability = settings.stability

        try:
            messages = session.client.poll_latest()
        except SocketClientError as exc:
            self.report({'WARNING'}, f"Connexion perdue : {exc}")
            self._stop(context)
            return {'FINISHED'}

        frame_msg = messages.get("frame")
        face_msg = messages.get("face")
        hands_msg = messages.get("hands")

        if frame_msg is None and face_msg is None and hands_msg is None:
            return {'PASS_THROUGH'}

        frame = context.scene.frame_current

        prefix, suffix = session.bone_prefix, session.bone_suffix

        if frame_msg is not None:
            hip_center = bone_mapping.apply_pose(
                session.armature, frame_msg["landmarks"], session.initial_hip_center, prefix, suffix
            )
            if session.initial_hip_center is None:
                session.initial_hip_center = hip_center

            hips_bone_name = bone_mapping.resolve_bone_name("hips", prefix, suffix)
            hips_bone = session.armature.pose.bones.get(hips_bone_name)
            if hips_bone is not None:
                hips_bone.keyframe_insert(data_path="location", frame=frame)

            for bone_name in bone_mapping.get_animated_bone_names(prefix, suffix):
                if bone_name == hips_bone_name:
                    continue
                pose_bone = session.armature.pose.bones.get(bone_name)
                if pose_bone is None:
                    continue
                pose_bone.keyframe_insert(data_path="rotation_quaternion", frame=frame)

        if face_msg is not None:
            head_rotation = face_msg.get("head_rotation")
            if head_rotation is not None:
                face_mapping.apply_head_rotation(session.armature, head_rotation, prefix, suffix)
                head_bone_name = bone_mapping.resolve_bone_name(face_mapping.HEAD_BONE_NAME, prefix, suffix)
                head_bone = session.armature.pose.bones.get(head_bone_name)
                if head_bone is not None:
                    head_bone.keyframe_insert(data_path="rotation_quaternion", frame=frame)

            if session.face_mesh is not None:
                blendshapes = face_msg.get("blendshapes") or {}
                if blendshapes:
                    face_mapping.apply_blendshapes(session.face_mesh, blendshapes)
                    key_blocks = session.face_mesh.data.shape_keys.key_blocks
                    for name in blendshapes:
                        key_block = key_blocks.get(name)
                        if key_block is not None:
                            key_block.keyframe_insert(data_path="value", frame=frame)

        if hands_msg is not None:
            hands = hands_msg.get("hands") or {}
            for side, mp_side in (("L", "left"), ("R", "right")):
                landmarks = hands.get(mp_side)
                if landmarks is None:
                    continue
                hand_mapping.apply_hand(session.armature, landmarks, side, prefix, suffix)
                for bone_name in hand_mapping.get_animated_bone_names(side, prefix, suffix):
                    pose_bone = session.armature.pose.bones.get(bone_name)
                    if pose_bone is None:
                        continue
                    pose_bone.keyframe_insert(data_path="rotation_quaternion", frame=frame)

        context.scene.frame_current = frame + 1

        return {'PASS_THROUGH'}

    def _stop(self, context):
        settings = context.scene.corpus_mocap
        session = _CaptureSession.instance
        if session is not None:
            session.client.close()
            if session.timer is not None:
                context.window_manager.event_timer_remove(session.timer)
            _CaptureSession.instance = None
        settings.is_recording = False
        settings.is_connected = False
        settings.status_message = "Arrêté"


class MOCAP_OT_reset_rig(bpy.types.Operator):
    """Remet l'armature cible à sa pose de repos (utile si un enregistrement
    précédent l'a laissée dans une pose figée/désarticulée)."""

    bl_idname = "mocap.reset_rig"
    bl_label = "Réinitialiser le rig"

    @classmethod
    def poll(cls, context):
        settings = context.scene.corpus_mocap
        return settings.target_armature is not None and not settings.is_recording

    def execute(self, context):
        settings = context.scene.corpus_mocap
        for pose_bone in settings.target_armature.pose.bones:
            pose_bone.rotation_mode = "QUATERNION"
            pose_bone.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
            pose_bone.location = (0.0, 0.0, 0.0)
            pose_bone.scale = (1.0, 1.0, 1.0)
        self.report({'INFO'}, "Rig réinitialisé à la pose de repos")
        return {'FINISHED'}


class MOCAP_OT_apply_bone_affixes(bpy.types.Operator):
    """Ajoute le préfixe/suffixe des os configurés (panneau CORPUS-MOCAP)
    aux noms des os actuellement sélectionnés — à utiliser en Edit Mode
    sur l'armature. Utile pour faire correspondre les os d'un rig
    personnalisé à la convention CORPUS-MOCAP en les renommant en bloc,
    plutôt qu'un par un. (Les mêmes champs Préfixe/Suffixe servent aussi,
    au moment de la capture, à *chercher* des os déjà nommés ainsi — ex.
    un rig Rigify dont les os de déformation sont déjà préfixés "DEF-" :
    dans ce cas pas besoin de ce bouton, la recherche suffit.)"""

    bl_idname = "mocap.apply_bone_affixes"
    bl_label = "Appliquer aux os sélectionnés"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (
            context.mode == 'EDIT_ARMATURE'
            and context.active_object is not None
            and context.active_object.type == 'ARMATURE'
        )

    def execute(self, context):
        settings = context.scene.corpus_mocap
        prefix, suffix = settings.bone_prefix, settings.bone_suffix
        if not prefix and not suffix:
            self.report({'WARNING'}, "Préfixe et suffixe vides — rien à faire")
            return {'CANCELLED'}

        selected_bones = context.selected_editable_bones
        if not selected_bones:
            self.report({'WARNING'}, "Aucun os sélectionné")
            return {'CANCELLED'}

        count = 0
        for edit_bone in selected_bones:
            edit_bone.name = f"{prefix}{edit_bone.name}{suffix}"
            count += 1

        self.report({'INFO'}, f"{count} os renommés")
        return {'FINISHED'}


CLASSES = (MOCAP_OT_toggle_capture, MOCAP_OT_reset_rig, MOCAP_OT_apply_bone_affixes)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
