"""Génère un personnage de base complet (armature + mesh humanoïde
proportionné + shape keys ARKit + bones faciaux jaw/eyebrow.L/R),
directement depuis le panneau CORPUS-MOCAP (bouton "Générer un
personnage de base").

Contrairement aux scripts autonomes de tools/ (générateurs de rig de
test séparés, à exécuter un par un dans l'onglet Scripting), ce module
est intégré à l'addon et produit en un clic un point de départ unique,
déjà relié (armature skinnée + shape keys + cibles pré-configurées) et
prêt à capturer — voir MOCAP_OT_generate_base_character dans
operators.py. L'utilisateur peut ensuite sculpter/redessiner ce
personnage à sa convenance (Edit Mode / Sculpt Mode, Weight Paint pour
affiner les poids d'os) SANS renommer les bones ni les shape keys, pour
garder la compatibilité avec le mapping de capture.

La géométrie générée est volontairement grossière (cylindres + une
sphère pour la tête) : le but n'est pas un rendu final mais une base
correctement proportionnée et déjà skinnée/nommée, sur laquelle
sculpter. Les doigts n'ont volontairement aucune géométrie propre (la
main est un simple galbe) : à sculpter/repeser à la main si des doigts
individualisés sont voulus. jaw/eyebrow.L/R n'ont pas non plus de
géométrie dédiée, ils héritent d'un poids automatique de la sphère de
tête (voir generate()) — à affiner en Weight Paint si besoin.

Convention de coordonnées : ce module construit tout (bones ET mesh) en
coordonnées "monde" absolues (rig à l'origine, sans transform propre),
exactement comme tools/generate_test_rig.py — voir addon/bone_mapping.py
pour la convention de nommage des bones du corps, addon/hand_mapping.py
pour les doigts, addon/face_mapping.py pour jaw/eyebrow.L/R."""

from __future__ import annotations

import bpy
from mathutils import Vector

CHARACTER_NAME = "CORPUS_MOCAP_Character"
MESH_NAME = "CORPUS_MOCAP_Character_Mesh"

HEAD_CENTER = Vector((0.0, 0.0, 1.64))
HEAD_RADIUS = 0.12

# (nom, tête, queue, parent, connecté à son parent) — identique à
# tools/generate_test_rig.py (dupliqué ici pour que ce module reste
# autonome : tools/ n'est pas embarqué dans l'addon installé).
BODY_BONES = [
    ("hips",        (0.0,  0.0, 1.00), (0.0,  0.0, 1.08), None,       False),
    ("spine",       (0.0,  0.0, 1.00), (0.0,  0.0, 1.25), "hips",     False),
    ("chest",       (0.0,  0.0, 1.25), (0.0,  0.0, 1.45), "spine",    True),
    ("neck",        (0.0,  0.0, 1.45), (0.0,  0.0, 1.55), "chest",    True),
    ("head",        (0.0,  0.0, 1.55), (0.0,  0.0, 1.72), "neck",     True),

    ("shoulder.L",  (0.05, 0.0, 1.45), (0.18, 0.0, 1.45), "chest",    False),
    ("upper_arm.L", (0.18, 0.0, 1.45), (0.50, 0.0, 1.45), "shoulder.L", True),
    ("forearm.L",   (0.50, 0.0, 1.45), (0.78, 0.0, 1.45), "upper_arm.L", True),
    ("hand.L",      (0.78, 0.0, 1.45), (0.92, 0.0, 1.45), "forearm.L", True),

    ("shoulder.R",  (-0.05, 0.0, 1.45), (-0.18, 0.0, 1.45), "chest",   False),
    ("upper_arm.R", (-0.18, 0.0, 1.45), (-0.50, 0.0, 1.45), "shoulder.R", True),
    ("forearm.R",   (-0.50, 0.0, 1.45), (-0.78, 0.0, 1.45), "upper_arm.R", True),
    ("hand.R",      (-0.78, 0.0, 1.45), (-0.92, 0.0, 1.45), "forearm.R", True),

    ("thigh.L",     (0.10, 0.0, 1.00), (0.10, 0.0, 0.55), "hips",     False),
    ("shin.L",      (0.10, 0.0, 0.55), (0.10, 0.0, 0.12), "thigh.L",  True),
    ("foot.L",      (0.10, 0.0, 0.12), (0.10, 0.14, 0.02), "shin.L",  True),

    ("thigh.R",     (-0.10, 0.0, 1.00), (-0.10, 0.0, 0.55), "hips",   False),
    ("shin.R",      (-0.10, 0.0, 0.55), (-0.10, 0.0, 0.12), "thigh.R", True),
    ("foot.R",      (-0.10, 0.0, 0.12), (-0.10, 0.14, 0.02), "shin.R", True),
]

# Doigts — même convention que tools/generate_test_hands.py (dupliqué
# pour la même raison que BODY_BONES ci-dessus). HAND_TIP_X/HAND_Z
# doivent rester cohérents avec hand.L/R dans BODY_BONES.
HAND_TIP_X = 0.92
HAND_Z = 1.45
FINGER_SPECS = [
    ("index", 0.025, (0.045, 0.030, 0.025)),
    ("middle", 0.008, (0.050, 0.035, 0.025)),
    ("ring", -0.008, (0.045, 0.030, 0.025)),
    ("pinky", -0.025, (0.035, 0.025, 0.020)),
]
THUMB_SEGMENT_LENGTHS = (0.035, 0.028, 0.022)

# Bones faciaux additionnels (nouveaux, absents de generate_test_rig.py) :
# jaw (mâchoire, pilotée en rotation par le coefficient "jawOpen") et
# eyebrow.L/R (sourcils, pilotés en translation par browInnerUp/
# browOuterUp*/browDown*) — voir addon/face_mapping.py.
FACE_BONES = [
    ("jaw",        (0.0, -0.04, 1.60), (0.0, -0.07, 1.545), "head", False),
    ("eyebrow.L",  (0.045, -0.095, 1.685), (0.045, -0.095, 1.705), "head", False),
    ("eyebrow.R",  (-0.045, -0.095, 1.685), (-0.045, -0.095, 1.705), "head", False),
]

# (nom du bone, rayon du cylindre) — géométrie du corps. Les bones
# absents de cette table (doigts, jaw, eyebrow.L/R) n'ont pas de
# géométrie propre : jaw/eyebrow.L/R héritent du poids automatique de la
# sphère de tête, les doigts n'ont aucune géométrie (voir docstring).
BODY_MESH_RADII = {
    "hips": 0.09, "spine": 0.09, "chest": 0.09, "neck": 0.045,
    "shoulder.L": 0.035, "shoulder.R": 0.035,
    "upper_arm.L": 0.035, "upper_arm.R": 0.035,
    "forearm.L": 0.028, "forearm.R": 0.028,
    "hand.L": 0.035, "hand.R": 0.035,
    "thigh.L": 0.06, "thigh.R": 0.06,
    "shin.L": 0.045, "shin.R": 0.045,
    "foot.L": 0.035, "foot.R": 0.035,
}

# (nom shape key ARKit, centre, rayon, offset) — sous-ensemble de
# tools/generate_test_face.py : jawOpen/browInnerUp/browDownLeft/
# browDownRight sont volontairement exclus (pilotés par les bones jaw/
# eyebrow.L/R à la place — pas de double animation de la même zone par
# deux mécanismes différents). Coordonnées en espace "monde" absolu
# (voir docstring du module), pas relatives à un centre de sphère.
FACE_SHAPE_KEYS = [
    ("eyeBlinkLeft",     (0.045, -0.09, 1.66), 0.045, (0.0, 0.01, -0.02)),
    ("eyeBlinkRight",    (-0.045, -0.09, 1.66), 0.045, (0.0, 0.01, -0.02)),
    ("mouthSmileLeft",   (0.035, -0.10, 1.58), 0.045, (0.01, 0.005, 0.015)),
    ("mouthSmileRight",  (-0.035, -0.10, 1.58), 0.045, (-0.01, 0.005, 0.015)),
    ("mouthPucker",      (0.0, -0.105, 1.58), 0.05, (0.0, -0.02, 0.0)),
    ("cheekPuff",        (0.0, -0.05, 1.62), 0.08, (0.0, -0.015, 0.0)),
]


def _smoothstep(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)


def _finger_bones(side_sign: float, side_suffix: str) -> list:
    hand_tip = Vector((HAND_TIP_X * side_sign, 0.0, HAND_Z))
    hand_bone_name = f"hand.{side_suffix}"
    bones = []

    for finger_name, z_offset, lengths in FINGER_SPECS:
        direction = Vector((side_sign, 0.0, 0.0))
        point = hand_tip + Vector((0.0, 0.0, z_offset))
        parent_name = hand_bone_name
        for i, length in enumerate(lengths, start=1):
            bone_name = f"{finger_name}.{i:02d}.{side_suffix}"
            next_point = point + direction * length
            bones.append((bone_name, tuple(point), tuple(next_point), parent_name, i > 1))
            point = next_point
            parent_name = bone_name

    thumb_dir = Vector((0.7 * side_sign, 0.0, 0.7)).normalized()
    point = hand_tip + Vector((-0.05 * side_sign, 0.0, -0.02))
    parent_name = hand_bone_name
    for i, length in enumerate(THUMB_SEGMENT_LENGTHS, start=1):
        bone_name = f"thumb.{i:02d}.{side_suffix}"
        next_point = point + thumb_dir * length
        bones.append((bone_name, tuple(point), tuple(next_point), parent_name, i > 1))
        point = next_point
        parent_name = bone_name

    return bones


def _all_bones() -> list:
    return BODY_BONES + _finger_bones(1.0, "L") + _finger_bones(-1.0, "R") + FACE_BONES


def _build_armature() -> bpy.types.Object:
    if CHARACTER_NAME in bpy.data.objects:
        bpy.data.objects.remove(bpy.data.objects[CHARACTER_NAME], do_unlink=True)

    armature_data = bpy.data.armatures.new(CHARACTER_NAME + "_Data")
    rig_obj = bpy.data.objects.new(CHARACTER_NAME, armature_data)
    bpy.context.collection.objects.link(rig_obj)
    bpy.context.view_layer.objects.active = rig_obj

    bpy.ops.object.mode_set(mode="EDIT")
    edit_bones = armature_data.edit_bones
    for name, head, tail, parent_name, connected in _all_bones():
        eb = edit_bones.new(name)
        eb.head = Vector(head)
        eb.tail = Vector(tail)
        if parent_name:
            eb.parent = edit_bones[parent_name]
            eb.use_connect = connected
    bpy.ops.object.mode_set(mode="OBJECT")

    return rig_obj


def _cylinder_between(name: str, head, tail, radius: float) -> bpy.types.Object | None:
    head_v = Vector(head)
    tail_v = Vector(tail)
    direction = tail_v - head_v
    length = direction.length
    if length < 1e-6:
        return None
    direction.normalize()
    midpoint = (head_v + tail_v) / 2.0

    bpy.ops.mesh.primitive_cylinder_add(
        radius=radius, depth=length, location=midpoint, align="WORLD"
    )
    obj = bpy.context.active_object
    obj.name = name
    obj.rotation_mode = "QUATERNION"
    # L'axe par défaut du cylindre est Z local : on l'aligne sur la
    # direction tête->queue du bone correspondant.
    obj.rotation_quaternion = Vector((0.0, 0.0, 1.0)).rotation_difference(direction)
    return obj


def _build_body_mesh() -> bpy.types.Object:
    if MESH_NAME in bpy.data.objects:
        bpy.data.objects.remove(bpy.data.objects[MESH_NAME], do_unlink=True)

    # Objet mesh vide créé à l'origine monde (sans transform propre) :
    # sert de cible de fusion (join) pour que les coordonnées locales du
    # mesh final correspondent directement aux coordonnées "monde" du rig
    # (même convention que les bones) — voir docstring du module.
    base_mesh = bpy.data.meshes.new(MESH_NAME + "_Data")
    mesh_obj = bpy.data.objects.new(MESH_NAME, base_mesh)
    bpy.context.collection.objects.link(mesh_obj)

    parts = []
    for name, head, tail, _parent, _connected in BODY_BONES:
        radius = BODY_MESH_RADII.get(name)
        if radius is None:
            continue
        part = _cylinder_between(f"{name}_mesh_part", head, tail, radius)
        if part is not None:
            parts.append(part)

    bpy.ops.mesh.primitive_uv_sphere_add(
        radius=HEAD_RADIUS, location=HEAD_CENTER, segments=32, ring_count=20, align="WORLD"
    )
    head_part = bpy.context.active_object
    head_part.name = "head_mesh_part"
    parts.append(head_part)

    bpy.ops.object.select_all(action="DESELECT")
    for part in parts:
        part.select_set(True)
    mesh_obj.select_set(True)
    bpy.context.view_layer.objects.active = mesh_obj
    bpy.ops.object.join()

    return mesh_obj


def _add_face_shape_keys(mesh_obj: bpy.types.Object) -> None:
    mesh_obj.shape_key_add(name="Basis")
    local_coords = [v.co.copy() for v in mesh_obj.data.vertices]

    for name, center, radius, offset in FACE_SHAPE_KEYS:
        center_v = Vector(center)
        offset_v = Vector(offset)
        # from_mix=False : voir tools/generate_test_face.py (évite une
        # explosion des coordonnées en cascade).
        key = mesh_obj.shape_key_add(name=name, from_mix=False)
        key.value = 0.0
        for i, co in enumerate(local_coords):
            dist = (co - center_v).length
            if dist < radius:
                falloff = _smoothstep(1.0 - dist / radius)
                key.data[i].co = co + offset_v * falloff


def generate() -> tuple[bpy.types.Object, bpy.types.Object]:
    """Génère (ou régénère — supprime tout personnage précédent du même
    nom, non fusionnable avec des modifications déjà faites dessus)
    l'armature + le mesh de base, skinne le mesh par poids automatiques
    (heat weighting), et ajoute les shape keys ARKit. Retourne
    (armature_obj, mesh_obj)."""
    armature_obj = _build_armature()
    mesh_obj = _build_body_mesh()
    _add_face_shape_keys(mesh_obj)

    bpy.ops.object.select_all(action="DESELECT")
    mesh_obj.select_set(True)
    armature_obj.select_set(True)
    bpy.context.view_layer.objects.active = armature_obj
    bpy.ops.object.parent_set(type="ARMATURE_AUTO")

    return armature_obj, mesh_obj
