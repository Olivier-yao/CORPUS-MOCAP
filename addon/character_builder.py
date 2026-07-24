"""Génère un rig CORPUS-MOCAP, de deux façons :

1. `generate()` : personnage de base complet (armature + mesh humanoïde
   proportionné + shape keys ARKit), pour tester sans modèle personnel —
   voir MOCAP_OT_generate_base_character dans operators.py, bouton
   "Générer un personnage de base".
2. `generate_rig_for_mesh(mesh_obj)` : **rig seul** (aucun mesh créé),
   mis à l'échelle et positionné pour correspondre approximativement au
   modèle 3D sélectionné (calé sur sa boîte englobante) — voir
   MOCAP_OT_generate_rig_for_mesh, bouton "Générer un rig pour le modèle
   sélectionné". Point de départ approximatif seulement : comme pour un
   meta-rig Rigify, l'utilisateur doit ensuite repositionner chaque bone
   à la main (Edit Mode) pour l'aligner précisément sur les articulations
   réelles de son modèle (yeux, coins de bouche, coudes, etc.) — ce
   module ne détecte aucun point du mesh automatiquement, il ne fait que
   fournir un squelette de la bonne taille/proportion globale comme
   base de travail. Le squelettage (Parent > Armature Deform) du mesh
   reste une étape manuelle séparée, comme pour n'importe quel rig.

Dans les deux cas, les bones sont nommés selon la convention attendue
par le mapping de capture — voir addon/bone_mapping.py (corps),
addon/hand_mapping.py (doigts), addon/face_mapping.py (visage : head,
jaw, brow.in/mid/out.L/R, et les autres bones faciaux ci-dessous qui ne
sont pas tous pilotés par la capture, voir CANONICAL_FACE_BONES).
L'utilisateur peut sculpter/redessiner un personnage généré par
generate() à sa convenance (Edit Mode / Sculpt Mode, Weight Paint pour
affiner les poids d'os) SANS renommer les bones ni les shape keys, pour
garder la compatibilité avec le mapping de capture.

Convention de coordonnées : les positions de CANONICAL_* ci-dessous sont
en coordonnées "monde" absolues pour un personnage de référence d'1m72
(rig à l'origine, sans transform propre), exactement comme
tools/generate_test_rig.py. generate_rig_for_mesh() applique une échelle
+ un décalage uniformes à ces coordonnées de référence pour approcher la
taille/position du mesh cible (voir compute_fit_transform)."""

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
CANONICAL_BODY_BONES = [
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
# pour la même raison que CANONICAL_BODY_BONES ci-dessus). HAND_TIP_X/
# HAND_Z doivent rester cohérents avec hand.L/R dans CANONICAL_BODY_BONES.
HAND_TIP_X = 0.92
HAND_Z = 1.45
FINGER_SPECS = [
    ("index", 0.025, (0.045, 0.030, 0.025)),
    ("middle", 0.008, (0.050, 0.035, 0.025)),
    ("ring", -0.008, (0.045, 0.030, 0.025)),
    ("pinky", -0.025, (0.035, 0.025, 0.020)),
]
THUMB_SEGMENT_LENGTHS = (0.035, 0.028, 0.022)

# Bones faciaux additionnels (nouveaux, absents de generate_test_rig.py),
# niveau de détail "intermédiaire" (comparable à un sous-ensemble du
# meta-rig Rigify, sans aller jusqu'aux paupières/lèvres en plusieurs
# segments ni langue/dents). Seule une partie est effectivement pilotée
# par la capture (voir addon/face_mapping.py : jaw, brow.in/out.L/R
# aujourd'hui) — les autres (eye.*, lid.*, brow.mid.*, nose*, cheek.*,
# chin, mouth.corner.*, lip.*, ear.*) sont des bones de contrôle pour
# affiner/animer à la main, ou pour brancher plus de coefficients ARKit
# plus tard sans avoir à régénérer le rig.
FACE_BONES = [
    ("jaw",             (0.0, -0.04, 1.60), (0.0, -0.07, 1.545), "head", False),
    ("chin",            (0.0, -0.07, 1.545), (0.0, -0.09, 1.525), "jaw", True),

    ("eye.L",            (0.045, -0.11, 1.665), (0.045, -0.13, 1.665), "head", False),
    ("eye.R",            (-0.045, -0.11, 1.665), (-0.045, -0.13, 1.665), "head", False),
    ("lid.T.L",          (0.045, -0.10, 1.675), (0.045, -0.105, 1.672), "head", False),
    ("lid.B.L",          (0.045, -0.10, 1.655), (0.045, -0.105, 1.658), "head", False),
    ("lid.T.R",          (-0.045, -0.10, 1.675), (-0.045, -0.105, 1.672), "head", False),
    ("lid.B.R",          (-0.045, -0.10, 1.655), (-0.045, -0.105, 1.658), "head", False),

    ("brow.in.L",        (0.02, -0.095, 1.695), (0.02, -0.095, 1.705), "head", False),
    ("brow.mid.L",       (0.045, -0.095, 1.70), (0.045, -0.095, 1.71), "head", False),
    ("brow.out.L",       (0.07, -0.09, 1.695), (0.07, -0.09, 1.705), "head", False),
    ("brow.in.R",        (-0.02, -0.095, 1.695), (-0.02, -0.095, 1.705), "head", False),
    ("brow.mid.R",       (-0.045, -0.095, 1.70), (-0.045, -0.095, 1.71), "head", False),
    ("brow.out.R",       (-0.07, -0.09, 1.695), (-0.07, -0.09, 1.705), "head", False),

    ("nose",             (0.0, -0.11, 1.63), (0.0, -0.125, 1.60), "head", False),
    ("nose.tip",         (0.0, -0.125, 1.60), (0.0, -0.135, 1.595), "nose", True),

    ("cheek.L",          (0.07, -0.08, 1.60), (0.09, -0.07, 1.60), "head", False),
    ("cheek.R",          (-0.07, -0.08, 1.60), (-0.09, -0.07, 1.60), "head", False),

    ("mouth.corner.L",   (0.035, -0.105, 1.575), (0.045, -0.10, 1.575), "head", False),
    ("mouth.corner.R",   (-0.035, -0.105, 1.575), (-0.045, -0.10, 1.575), "head", False),

    ("lip.T",            (0.0, -0.11, 1.585), (0.0, -0.115, 1.582), "head", False),
    ("lip.T.L",          (0.02, -0.108, 1.582), (0.03, -0.105, 1.58), "head", False),
    ("lip.T.R",          (-0.02, -0.108, 1.582), (-0.03, -0.105, 1.58), "head", False),
    ("lip.B",            (0.0, -0.108, 1.568), (0.0, -0.112, 1.565), "jaw", False),
    ("lip.B.L",          (0.02, -0.105, 1.568), (0.03, -0.10, 1.567), "jaw", False),
    ("lip.B.R",          (-0.02, -0.105, 1.568), (-0.03, -0.10, 1.567), "jaw", False),

    ("ear.L",            (0.11, -0.02, 1.645), (0.13, -0.02, 1.635), "head", False),
    ("ear.R",            (-0.11, -0.02, 1.645), (-0.13, -0.02, 1.635), "head", False),
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
    return CANONICAL_BODY_BONES + _finger_bones(1.0, "L") + _finger_bones(-1.0, "R") + FACE_BONES


def _reference_bounds() -> tuple[Vector, Vector]:
    """Boîte englobante (min, max) de tous les points tête/queue des bones
    canoniques — sert de référence pour compute_fit_transform."""
    points = []
    for _name, head, tail, *_rest in _all_bones():
        points.append(Vector(head))
        points.append(Vector(tail))
    min_v = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
    max_v = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
    return min_v, max_v


def compute_fit_transform(mesh_obj: bpy.types.Object) -> tuple[float, Vector]:
    """Calcule (échelle, décalage) pour que le rig canonique (référence
    ~1m72) approche la taille/position de `mesh_obj`, à partir de sa boîte
    englobante monde. Échelle uniforme basée sur la hauteur (axe Z) —
    l'approche la plus prévisible pour un point de départ à ajuster
    ensuite à la main, plutôt qu'une échelle non-uniforme par axe qui
    déformerait la direction des bones. Résultat : le rig généré a les
    pieds au niveau du point le plus bas du mesh et est centré
    horizontalement sur son centre — reste une approximation grossière,
    pas un alignement précis (voir docstring du module)."""
    corners_world = [mesh_obj.matrix_world @ Vector(c) for c in mesh_obj.bound_box]
    mesh_min = Vector((min(c.x for c in corners_world), min(c.y for c in corners_world), min(c.z for c in corners_world)))
    mesh_max = Vector((max(c.x for c in corners_world), max(c.y for c in corners_world), max(c.z for c in corners_world)))

    ref_min, ref_max = _reference_bounds()
    ref_height = ref_max.z - ref_min.z
    mesh_height = mesh_max.z - mesh_min.z
    scale = mesh_height / ref_height if ref_height > 1e-6 else 1.0

    ref_center_x = (ref_min.x + ref_max.x) / 2.0
    ref_center_y = (ref_min.y + ref_max.y) / 2.0
    mesh_center_x = (mesh_min.x + mesh_max.x) / 2.0
    mesh_center_y = (mesh_min.y + mesh_max.y) / 2.0

    offset = Vector((
        mesh_center_x - ref_center_x * scale,
        mesh_center_y - ref_center_y * scale,
        mesh_min.z - ref_min.z * scale,
    ))
    return scale, offset


def _build_armature(scale: float = 1.0, offset: Vector | None = None) -> bpy.types.Object:
    if offset is None:
        offset = Vector((0.0, 0.0, 0.0))

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
        eb.head = Vector(head) * scale + offset
        eb.tail = Vector(tail) * scale + offset
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
    for name, head, tail, _parent, _connected in CANONICAL_BODY_BONES:
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


def generate_rig_for_mesh(mesh_obj: bpy.types.Object) -> bpy.types.Object:
    """Génère UNIQUEMENT l'armature (aucun mesh créé), mise à l'échelle et
    positionnée pour approcher `mesh_obj` (voir compute_fit_transform) —
    reste une base approximative à ajuster à la main, pas un alignement
    précis. Ne skinne pas `mesh_obj` (pas de Parent > Armature Deform) :
    laissé à l'utilisateur, comme pour n'importe quel rig personnalisé.
    Retourne l'armature créée."""
    scale, offset = compute_fit_transform(mesh_obj)
    return _build_armature(scale=scale, offset=offset)
