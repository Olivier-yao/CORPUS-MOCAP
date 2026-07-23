"""CORPUS-MOCAP — génère un rig de test humanoïde simple (T-pose).

À exécuter DANS Blender (onglet Scripting : ouvrir ce fichier, "Run Script"),
ou en headless : blender --background --python generate_test_rig.py

Crée une armature "CORPUS_MOCAP_TestRig" dont les noms d'os suivent la
convention attendue par addon/bone_mapping.py. Sert uniquement à valider
le pipeline de la Phase 1 en l'absence d'un rig de personnage définitif.
"""

import bpy
from mathutils import Vector

RIG_NAME = "CORPUS_MOCAP_TestRig"

# (nom, tête, queue, parent, connecté à son parent)
BONES = [
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


def generate() -> bpy.types.Object:
    if RIG_NAME in bpy.data.objects:
        bpy.data.objects.remove(bpy.data.objects[RIG_NAME], do_unlink=True)

    armature_data = bpy.data.armatures.new(RIG_NAME + "_Data")
    rig_obj = bpy.data.objects.new(RIG_NAME, armature_data)
    bpy.context.collection.objects.link(rig_obj)
    bpy.context.view_layer.objects.active = rig_obj

    bpy.ops.object.mode_set(mode="EDIT")
    edit_bones = armature_data.edit_bones
    for name, head, tail, parent_name, connected in BONES:
        eb = edit_bones.new(name)
        eb.head = Vector(head)
        eb.tail = Vector(tail)
        if parent_name:
            eb.parent = edit_bones[parent_name]
            eb.use_connect = connected

    bpy.ops.object.mode_set(mode="OBJECT")
    print(f"[generate_test_rig] Armature '{RIG_NAME}' créée avec {len(BONES)} os.")
    return rig_obj


if __name__ == "__main__":
    generate()
