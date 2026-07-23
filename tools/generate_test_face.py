"""CORPUS-MOCAP — génère un mesh de test avec des shape keys nommées selon
la convention ARKit (utilisée par MediaPipe FaceLandmarker), pour valider
le mapping du visage en Phase 2.

À exécuter DANS Blender (onglet Scripting : ouvrir ce fichier, "Run Script").

Simple sphère positionnée près de la tête du rig de test, attachée au
bone "head" via une contrainte Child Of (si ce rig existe dans la scène,
et de préférence avec le bone "head" à sa pose de repos — utilisez
"Réinitialiser le rig" avant de lancer ce script si besoin) pour suivre
la rotation de tête capturée. Chaque shape key ne couvre qu'une région
approximative de la sphère (aucune ressemblance avec un vrai visage
n'est recherchée) : le but est uniquement de vérifier que chaque
coefficient MediaPipe déplace la bonne zone.
"""

import bpy
from mathutils import Vector

FACE_NAME = "CORPUS_MOCAP_TestFace"
RIG_NAME = "CORPUS_MOCAP_TestRig"
HEAD_BONE_NAME = "head"
HEAD_WORLD_POS = (0.0, 0.0, 1.78)  # juste au-dessus du bone "head" du rig de test
SPHERE_RADIUS = 0.12

# (nom shape key = nom du coefficient blendshape ARKit/MediaPipe,
#  centre de la région influencée en espace local, rayon d'influence, offset)
# Rayons volontairement généreux (>= ~0.05) pour être sûr de couvrir au
# moins quelques sommets vu la résolution modeste du maillage (24x16).
FACE_SHAPE_KEYS = [
    ("eyeBlinkLeft",     (0.045, -0.10, 0.03), 0.05, (0.0, 0.01, -0.02)),
    ("eyeBlinkRight",    (-0.045, -0.10, 0.03), 0.05, (0.0, 0.01, -0.02)),
    ("browInnerUp",      (0.0, -0.09, 0.07), 0.055, (0.0, -0.01, 0.02)),
    ("browDownLeft",     (0.045, -0.10, 0.06), 0.05, (0.0, 0.0, -0.015)),
    ("browDownRight",    (-0.045, -0.10, 0.06), 0.05, (0.0, 0.0, -0.015)),
    ("jawOpen",          (0.0, -0.10, -0.08), 0.06, (0.0, -0.02, -0.03)),
    ("mouthSmileLeft",   (0.035, -0.11, -0.03), 0.05, (0.01, 0.005, 0.015)),
    ("mouthSmileRight",  (-0.035, -0.11, -0.03), 0.05, (-0.01, 0.005, 0.015)),
    ("mouthPucker",      (0.0, -0.115, -0.03), 0.055, (0.0, -0.02, 0.0)),
    ("cheekPuff",        (0.0, -0.06, -0.01), 0.09, (0.0, -0.015, 0.0)),
]


def _smoothstep(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)


def generate() -> bpy.types.Object:
    if FACE_NAME in bpy.data.objects:
        bpy.data.objects.remove(bpy.data.objects[FACE_NAME], do_unlink=True)

    bpy.ops.mesh.primitive_uv_sphere_add(
        radius=SPHERE_RADIUS, location=HEAD_WORLD_POS, segments=32, ring_count=20
    )
    obj = bpy.context.active_object
    obj.name = FACE_NAME
    obj.data.name = FACE_NAME + "_Mesh"

    mesh = obj.data
    obj.shape_key_add(name="Basis")
    local_coords = [v.co.copy() for v in mesh.vertices]

    for name, center, radius, offset in FACE_SHAPE_KEYS:
        center_v = Vector(center)
        offset_v = Vector(offset)
        # from_mix=False : chaque shape key part d'une copie propre de la
        # Basis, pas d'un mélange cumulatif des shape keys précédentes
        # (le mélange cumulatif + value=1.0 par défaut sur les nouvelles
        # shape keys causait une explosion des coordonnées en cascade).
        key = obj.shape_key_add(name=name, from_mix=False)
        key.value = 0.0
        affected = 0
        for i, co in enumerate(local_coords):
            dist = (co - center_v).length
            if dist < radius:
                falloff = _smoothstep(1.0 - dist / radius)
                key.data[i].co = co + offset_v * falloff
                affected += 1
        if affected == 0:
            print(f"[generate_test_face] ATTENTION : '{name}' n'a touché aucun sommet (rayon trop petit ?).")

    rig = bpy.data.objects.get(RIG_NAME)
    if rig is not None and HEAD_BONE_NAME in rig.pose.bones:
        child_of = obj.constraints.new(type="CHILD_OF")
        child_of.target = rig
        child_of.subtarget = HEAD_BONE_NAME
        # Neutralise le décalage entre l'orientation de repos du bone
        # (qui pointe vers le haut, pas vers l'avant) et celle du mesh au
        # moment du setup : seule la rotation RELATIVE capturée par la
        # suite sera appliquée au mesh, pas l'inclinaison propre du bone.
        # Nécessite que le bone soit à sa pose de repos maintenant.
        head_bone_matrix_world = rig.matrix_world @ rig.pose.bones[HEAD_BONE_NAME].matrix
        child_of.inverse_matrix = head_bone_matrix_world.inverted()

        print(f"[generate_test_face] Attaché au bone '{HEAD_BONE_NAME}' de '{RIG_NAME}' "
              "via contrainte Child Of.")
    else:
        print(f"[generate_test_face] Rig '{RIG_NAME}' (bone '{HEAD_BONE_NAME}') introuvable "
              "— mesh laissé libre, non attaché.")

    print(f"[generate_test_face] Mesh '{FACE_NAME}' créé avec {len(FACE_SHAPE_KEYS)} shape keys.")
    return obj


if __name__ == "__main__":
    generate()
