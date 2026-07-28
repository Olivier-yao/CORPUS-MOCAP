"""Post-traitement "style cartoon" (cahier des charges, Module 4) :
amplifie les rotations, ajoute du squash & stretch basé sur la vitesse
angulaire, et accentue le timing (easing) des courbes d'animation
(F-Curves) d'une capture CORPUS-MOCAP déjà enregistrée.

Volontairement un bouton SÉPARÉ, appliqué APRÈS la capture (pas
automatique à l'arrêt de l'enregistrement, cahier des charges Module 4 :
"Si activée, applique un post-traitement... après capture") — une prise
webcam prend du temps à refaire, un bouton réappliquable permet
d'essayer plusieurs intensités sans recapturer. Voir
MOCAP_OT_apply_cartoon_style dans operators.py.

Non-destructif : `apply_cartoon_style` ne modifie JAMAIS l'Action
actuellement assignée à l'armature/au mesh visage — elle la DUPLIQUE
(`Action.copy()`, nommage Blender standard "..._Cartoon", "..._Cartoon.001"...)
et post-traite la copie, qui devient la nouvelle Action active. La
capture brute reste donc toujours intacte et sélectionnable (Action
Editor), et chaque appel repart de l'Action encore active au moment du
clic — pour ne jamais composer les effets d'un appel précédent, gardez
l'Action brute active avant de recliquer avec une autre intensité (ou
dupliquez-la vous-même si vous êtes reparti d'une version déjà stylisée).
"""

from __future__ import annotations

import math

import bpy
from mathutils import Quaternion

from .bone_mapping import resolve_bone_name

# --- Amplification ---
# Angle de chaque keyframe de rotation, mesuré par rapport au repos
# (quaternion identité — apply_pose/_aim_bone n'expriment jamais une
# rotation "absolue", toujours relative au repos du bone), multiplié par
# ce facteur. intensity=0 -> 1.0 (aucun effet), intensity=1 -> ce max.
AMPLIFICATION_MAX = 1.6

# --- Squash & stretch ---
# Vitesse angulaire (deg/trame) au-delà de laquelle l'étirement max est
# atteint — empirique, à ajuster selon le rythme de vos prises.
SQUASH_STRETCH_VELOCITY_DEG_PER_FRAME = 25.0
# Étirement max (fraction, ex. 0.3 = os 30% plus long au pic de vitesse)
# à intensity=1 ; volume approximativement préservé (compression
# perpendiculaire à l'axe d'étirement, racine carrée inverse).
SQUASH_STRETCH_MAX = 0.3
# Os "principaux" concernés par le squash & stretch (cahier des charges
# Module 4) : la colonne et les membres, pas les bones de contrôle fins
# (visage, doigts) ni les bones structurels non directement animés
# (chest/neck). "spine" couvre aussi une éventuelle chaîne spine.001/002
# (préfixe, voir bone_mapping._spine_chain_bone_names).
SQUASH_STRETCH_BONE_PREFIXES = ("spine", "upper_arm.", "forearm.", "thigh.", "shin.")

# --- Timing / easing ---
# Fraction de l'intervalle entre deux keyframes que les poignées bezier
# occupent, plate (même valeur Y que le keyframe) — un intervalle plus
# long crée un "maintien" plus marqué avant la transition, donc un easing
# plus prononcé. intensity=0 -> EASING_MIN_HANDLE_FRACTION, intensity=1
# -> EASING_MAX_HANDLE_FRACTION. Plafonné nettement sous 0.5 : au-delà,
# les poignées de deux keyframes voisines se chevauchent et produisent du
# survirage/rebond indésirable (empirique).
EASING_MIN_HANDLE_FRACTION = 0.15
EASING_MAX_HANDLE_FRACTION = 0.35
# Durée plancher (en trames) d'une poignée, même sur un intervalle très
# court entre deux keyframes rapprochées.
EASING_MIN_HANDLE_FRAMES = 0.1

CARTOON_SUFFIX = "_Cartoon"


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _wrap_angle(angle: float) -> float:
    """Ramène un angle dans [-pi, pi] — évite d'amplifier "le grand côté"
    d'une rotation dont l'angle brut sortirait de cet intervalle."""
    return (angle + math.pi) % (2.0 * math.pi) - math.pi


def _duplicate_action(action: bpy.types.Action) -> bpy.types.Action:
    styled = action.copy()
    styled.name = f"{action.name}{CARTOON_SUFFIX}"
    return styled


def _find_rotation_fcurves(action: bpy.types.Action, data_path: str) -> list | None:
    """Retourne les 4 FCurves (index 0=w,1=x,2=y,3=z) d'un data_path de
    rotation_quaternion, ou None si l'une des 4 manque."""
    curves: list = [None, None, None, None]
    for fc in action.fcurves:
        if fc.data_path == data_path and 0 <= fc.array_index <= 3:
            curves[fc.array_index] = fc
    return curves if all(c is not None for c in curves) else None


def _get_or_create_fcurves(action: bpy.types.Action, data_path: str, count: int, group: str) -> list:
    curves: list = [None] * count
    for fc in action.fcurves:
        if fc.data_path == data_path and 0 <= fc.array_index < count:
            curves[fc.array_index] = fc
    for i in range(count):
        if curves[i] is None:
            curves[i] = action.fcurves.new(data_path=data_path, index=i, action_group=group)
    return curves


def _bone_matches_prefix(bone_name: str, prefixes: tuple[str, ...], bone_prefix: str = "", bone_suffix: str = "") -> bool:
    """Compare `bone_name` (nom résolu réel, ex. "DEF-spine-suffix") aux
    rôles canoniques de `prefixes` (ex. "spine") après avoir retiré
    `bone_prefix`/`bone_suffix` — sans ça, un rig utilisant le préfixe/
    suffixe configurable du panneau (voir bone_mapping.resolve_bone_name)
    ne matcherait jamais aucun rôle ici."""
    name = bone_name
    if bone_prefix and name.startswith(bone_prefix):
        name = name[len(bone_prefix):]
    if bone_suffix and name.endswith(bone_suffix):
        name = name[: len(name) - len(bone_suffix)]
    return any(name == p or name.startswith(p) for p in prefixes)


def _apply_easing(fcurve: bpy.types.FCurve, intensity: float) -> None:
    handle_fraction = _lerp(EASING_MIN_HANDLE_FRACTION, EASING_MAX_HANDLE_FRACTION, intensity)
    points = fcurve.keyframe_points
    n = len(points)
    if n == 0:
        return
    frames = [points[i].co.x for i in range(n)]
    for i in range(n):
        kp = points[i]
        prev_gap = frames[i] - frames[i - 1] if i > 0 else (frames[i + 1] - frames[i] if n > 1 else 1.0)
        next_gap = frames[i + 1] - frames[i] if i < n - 1 else prev_gap
        left_offset = max(prev_gap * handle_fraction, EASING_MIN_HANDLE_FRAMES)
        right_offset = max(next_gap * handle_fraction, EASING_MIN_HANDLE_FRAMES)
        kp.interpolation = 'BEZIER'
        kp.easing = 'EASE_IN_OUT'
        kp.handle_left_type = 'FREE'
        kp.handle_right_type = 'FREE'
        kp.handle_left = (kp.co.x - left_offset, kp.co.y)
        kp.handle_right = (kp.co.x + right_offset, kp.co.y)
    fcurve.update()


def _process_bone_rotation(
    action: bpy.types.Action, bone_name: str, intensity: float, apply_stretch: bool
) -> None:
    """Amplifie l'angle de chaque keyframe de rotation_quaternion du bone
    (par rapport au repos), puis, si `apply_stretch`, ajoute des
    keyframes de squash & stretch (échelle) dérivées de la vitesse
    angulaire entre keyframes consécutives — voir docstring du module."""
    data_path = f'pose.bones["{bone_name}"].rotation_quaternion'
    curves = _find_rotation_fcurves(action, data_path)
    if curves is None:
        return

    n = len(curves[0].keyframe_points)
    if n == 0:
        return

    amplification = _lerp(1.0, AMPLIFICATION_MAX, intensity)
    frames = [curves[0].keyframe_points[i].co.x for i in range(n)]
    quats = [
        Quaternion((curves[c].keyframe_points[i].co.y for c in range(4))).normalized()
        for i in range(n)
    ]

    amplified: list[Quaternion] = []
    for quat in quats:
        axis, angle = quat.to_axis_angle()
        if angle < 1e-6:
            amplified.append(Quaternion())
        else:
            # Plafonné à pi (180°) : au-delà, la rotation "dépasse" et
            # repart dans l'autre sens (survirage visuel) plutôt que de
            # simplement s'amplifier — n'arrive qu'aux trames déjà très
            # proches d'une rotation extrême (rare pour un membre en
            # fonctionnement normal).
            amplified.append(Quaternion(axis, min(angle * amplification, math.pi)))

    for i, quat in enumerate(amplified):
        for c in range(4):
            curves[c].keyframe_points[i].co.y = quat[c]
    for c in curves:
        c.update()

    if apply_stretch and n >= 1:
        scale_data_path = f'pose.bones["{bone_name}"].scale'
        scale_curves = _get_or_create_fcurves(action, scale_data_path, 3, bone_name)
        for c in scale_curves:
            c.keyframe_points.clear()

        stretch_max = SQUASH_STRETCH_MAX * intensity
        for i in range(n):
            if i == 0 or stretch_max <= 0.0:
                velocity_deg = 0.0
            else:
                dt = max(frames[i] - frames[i - 1], 1e-6)
                delta = amplified[i - 1].inverted() @ amplified[i]
                _, delta_angle = delta.to_axis_angle()
                velocity_deg = math.degrees(abs(_wrap_angle(delta_angle))) / dt

            stretch_t = min(velocity_deg / SQUASH_STRETCH_VELOCITY_DEG_PER_FRAME, 1.0)
            stretch = stretch_max * stretch_t
            scale_y = 1.0 + stretch
            scale_xz = 1.0 / math.sqrt(scale_y)
            for curve, value in zip(scale_curves, (scale_xz, scale_y, scale_xz)):
                curve.keyframe_points.insert(frames[i], value, keyframe_type='KEYFRAME')

        for c in scale_curves:
            _apply_easing(c, intensity)
            c.update()

    for c in curves:
        _apply_easing(c, intensity)
        c.update()


def _process_location(action: bpy.types.Action, bone_name: str, intensity: float) -> None:
    """Amplifie une courbe de translation (bone "hips" uniquement — voir
    bone_mapping.apply_pose, déjà exprimée en delta depuis la position
    initiale, donc amplifiable directement autour de zéro)."""
    data_path = f'pose.bones["{bone_name}"].location'
    amplification = _lerp(1.0, AMPLIFICATION_MAX, intensity)
    for fc in action.fcurves:
        if fc.data_path != data_path:
            continue
        for kp in fc.keyframe_points:
            kp.co.y *= amplification
        _apply_easing(fc, intensity)
        fc.update()


def _process_shape_key_value(fcurve: bpy.types.FCurve, intensity: float) -> None:
    """Amplifie une courbe de shape key ("value", 0-1) autour de zéro,
    bornée à [0, 1] (plage standard d'une shape key) pour éviter une
    valeur hors plage visuellement absurde."""
    amplification = _lerp(1.0, AMPLIFICATION_MAX, intensity)
    for kp in fcurve.keyframe_points:
        kp.co.y = max(0.0, min(1.0, kp.co.y * amplification))
    _apply_easing(fcurve, intensity)
    fcurve.update()


def apply_cartoon_style(
    armature_obj: bpy.types.Object,
    face_mesh_obj: bpy.types.Object | None,
    intensity: float,
    bone_prefix: str = "",
    bone_suffix: str = "",
) -> tuple[bpy.types.Action | None, bpy.types.Action | None]:
    """Duplique l'Action actuellement assignée à `armature_obj` (et, si
    `face_mesh_obj` a des shape keys animées, sa propre Action) et
    applique le post-traitement "style cartoon" sur les copies, jamais
    sur les Actions d'origine (voir docstring du module). Assigne les
    copies comme Actions actives. Retourne (action_corps_stylisée ou
    None, action_visage_stylisée ou None).

    `bone_prefix`/`bone_suffix` : mêmes réglages que pour la capture (voir
    bone_mapping.resolve_bone_name) — nécessaires pour reconnaître "hips"
    et les os à squash & stretch sur un rig dont les os ne sont pas
    nommés exactement selon la convention par défaut."""
    hips_name = resolve_bone_name("hips", bone_prefix, bone_suffix)

    body_action = None
    if armature_obj.animation_data is not None and armature_obj.animation_data.action is not None:
        source = armature_obj.animation_data.action
        body_action = _duplicate_action(source)
        armature_obj.animation_data.action = body_action

        for pose_bone in armature_obj.pose.bones:
            name = pose_bone.name
            if name == hips_name:
                _process_location(body_action, name, intensity)
                # "hips" a aussi une rotation (voir bone_mapping.apply_pose) —
                # jamais de squash & stretch dessus (pas dans
                # SQUASH_STRETCH_BONE_PREFIXES, c'est la racine, pas un
                # membre qui s'étire).
                _process_bone_rotation(body_action, name, intensity, apply_stretch=False)
                continue
            apply_stretch = _bone_matches_prefix(name, SQUASH_STRETCH_BONE_PREFIXES, bone_prefix, bone_suffix)
            _process_bone_rotation(body_action, name, intensity, apply_stretch)

    face_action = None
    if face_mesh_obj is not None:
        shape_keys = face_mesh_obj.data.shape_keys if face_mesh_obj.data else None
        if shape_keys is not None and shape_keys.animation_data is not None:
            source = shape_keys.animation_data.action
            if source is not None:
                face_action = _duplicate_action(source)
                shape_keys.animation_data.action = face_action
                for fc in list(face_action.fcurves):
                    if fc.data_path.endswith(".value"):
                        _process_shape_key_value(fc, intensity)

    return body_action, face_action
