"""Operators : connexion à capture_server, application temps réel de la
capture sur le rig, et enregistrement sous forme d'Action Blender.

Cahier des charges §5/§3 : un bouton unique ("Enregistrer la performance")
démarre puis arrête l'enregistrement — pas d'étape de connexion séparée.
"""

from __future__ import annotations

import math

import bpy

from . import bone_mapping, character_builder, face_mapping, hand_mapping
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
            blendshapes = face_msg.get("blendshapes") or {}

            if head_rotation is not None:
                face_mapping.apply_head_rotation(session.armature, head_rotation, prefix, suffix)
            if blendshapes:
                face_mapping.apply_jaw(session.armature, blendshapes, prefix, suffix)
                face_mapping.apply_eyebrows(session.armature, blendshapes, prefix, suffix)

            if head_rotation is not None or blendshapes:
                for bone_name, data_path in face_mapping.keyframeable_bone_names(prefix, suffix):
                    pose_bone = session.armature.pose.bones.get(bone_name)
                    if pose_bone is not None:
                        pose_bone.keyframe_insert(data_path=data_path, frame=frame)

            if session.face_mesh is not None and blendshapes:
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


def _all_expected_roles() -> list[str]:
    """Liste ordonnée de tous les noms d'os canoniques (sans préfixe/
    suffixe) que CORPUS-MOCAP sait animer — corps, tête, main gauche,
    main droite."""
    roles = list(bone_mapping.get_animated_bone_names())
    roles.extend(face_mapping.get_animated_bone_names())
    roles.extend(hand_mapping.get_animated_bone_names("L"))
    roles.extend(hand_mapping.get_animated_bone_names("R"))
    return roles


class MOCAP_OT_interactive_bone_mapping(bpy.types.Operator):
    """Associe interactivement les os de votre rig aux noms attendus par
    CORPUS-MOCAP : pour chaque rôle affiché en bas de la fenêtre, cliquez
    l'os correspondant (vue 3D ou Outliner) puis validez avec Entrée.
    Passez les rôles sans équivalent dans votre rig avec S. Renomme
    directement l'os actif vers le nom canonique attendu (Edit Mode)."""

    bl_idname = "mocap.interactive_bone_mapping"
    bl_label = "Associer les os par clic"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (
            context.mode == 'EDIT_ARMATURE'
            and context.active_object is not None
            and context.active_object.type == 'ARMATURE'
        )

    def invoke(self, context, event):
        self._roles = _all_expected_roles()
        self._index = 0
        self._mapped = 0
        self._skipped = 0
        self._update_status(context)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _update_status(self, context):
        if self._index < len(self._roles):
            role = self._roles[self._index]
            translation = bone_mapping.translate_role_name(role)
            context.workspace.status_text_set(
                f"CORPUS-MOCAP [{self._index + 1}/{len(self._roles)}] cliquez l'os pour "
                f"\"{role}\" ({translation}) puis Entrée pour valider  |  S : passer  |  Echap : arrêter"
            )
        else:
            context.workspace.status_text_set(None)

    def _finish(self, context, message):
        context.workspace.status_text_set(None)
        self.report({'INFO'}, message)

    def modal(self, context, event):
        if self._index >= len(self._roles):
            self._finish(context, f"Terminé : {self._mapped} os associés, {self._skipped} passés")
            return {'FINISHED'}

        if event.type == 'ESC':
            self._finish(context, f"Arrêté : {self._mapped} os associés, {self._skipped} passés")
            return {'CANCELLED'}

        if event.type == 'S' and event.value == 'PRESS':
            self._skipped += 1
            self._index += 1
            self._update_status(context)
            return {'RUNNING_MODAL'}

        if event.type in {'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS':
            active = context.active_bone
            if active is None:
                self.report({'WARNING'}, "Aucun os actif — cliquez un os d'abord")
            else:
                active.name = self._roles[self._index]
                self._mapped += 1
                self._index += 1
                self._update_status(context)
            return {'RUNNING_MODAL'}

        # Laisse passer clics, sélection, orbite/zoom normalement.
        return {'PASS_THROUGH'}


class MOCAP_OT_add_wrist_rotation_limit(bpy.types.Operator):
    """Ajoute une contrainte "Limit Rotation" avec des valeurs de départ
    raisonnables pour un poignet (flexion/extension modérée, torsion très
    limitée) sur le bone actif — s'applique par-dessus ce que la capture
    calcule, sans modifier le code de mapping. Utile en attendant une
    calibration parfaite de la rotation du poignet, ou pour n'importe
    quel bone dont la rotation doit rester dans une plage anatomique
    plausible. Valeurs ajustables ensuite dans Bone Constraint
    Properties."""

    bl_idname = "mocap.add_wrist_rotation_limit"
    bl_label = "Limiter la rotation (poignet)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'POSE' and context.active_pose_bone is not None

    def execute(self, context):
        pose_bone = context.active_pose_bone
        constraint = pose_bone.constraints.new(type='LIMIT_ROTATION')
        constraint.owner_space = 'LOCAL'
        constraint.use_limit_x = True
        constraint.min_x = math.radians(-70)
        constraint.max_x = math.radians(70)
        constraint.use_limit_y = True
        constraint.min_y = math.radians(-20)
        constraint.max_y = math.radians(20)
        constraint.use_limit_z = True
        constraint.min_z = math.radians(-30)
        constraint.max_z = math.radians(30)
        self.report(
            {'INFO'},
            f"Contrainte de rotation limitée ajoutée sur '{pose_bone.name}' "
            "(ajustable dans Bone Constraint Properties)",
        )
        return {'FINISHED'}


class MOCAP_OT_generate_base_character(bpy.types.Operator):
    """Génère un personnage de base (armature + mesh humanoïde skinné +
    shape keys ARKit + bones faciaux jaw/eyebrow.L/R), déjà nommé selon
    la convention CORPUS-MOCAP et prêt à capturer — voir
    addon/character_builder.py. Point de départ à sculpter/personnaliser
    ensuite (Edit Mode, Sculpt Mode, Weight Paint pour affiner les
    poids). Ré-exécuter ce bouton supprime et recrée entièrement l'objet
    "CORPUS_MOCAP_Character" (et son mesh) : ne pas l'utiliser pour
    régénérer un personnage déjà personnalisé, sous peine de perdre les
    modifications."""

    bl_idname = "mocap.generate_base_character"
    bl_label = "Générer un personnage de base"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        armature_obj, mesh_obj = character_builder.generate()

        settings = context.scene.corpus_mocap
        settings.target_armature = armature_obj
        settings.target_face_mesh = mesh_obj

        self.report(
            {'INFO'},
            f"Personnage '{armature_obj.name}' généré et assigné comme cibles "
            "(corps + visage) — sculptez-le à votre convenance sans renommer "
            "les os ni les shape keys.",
        )
        return {'FINISHED'}


CLASSES = (
    MOCAP_OT_toggle_capture,
    MOCAP_OT_reset_rig,
    MOCAP_OT_apply_bone_affixes,
    MOCAP_OT_interactive_bone_mapping,
    MOCAP_OT_add_wrist_rotation_limit,
    MOCAP_OT_generate_base_character,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
