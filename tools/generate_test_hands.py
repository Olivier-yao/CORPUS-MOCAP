"""CORPUS-MOCAP — ajoute des bones de doigts au rig de test existant, pour
valider le mapping des mains/doigts (MediaPipe Hand Landmarker, 21 points
par main) en plus du corps et du visage.

À exécuter DANS Blender (onglet Scripting : ouvrir ce fichier, "Run Script"),
APRÈS avoir généré le rig de corps (tools/generate_test_rig.py) — ce script
ajoute des bones à l'armature existante, il n'en crée pas une nouvelle.

Convention de nommage : <doigt>.<01|02|03>.<L|R>, ex. "index.02.L",
"thumb.01.R" — 3 segments par doigt (y compris le pouce, pour rester
cohérent avec les 4 points MediaPipe par doigt : base, 2 articulations,
bout). Chaque chaîne de doigt est parentée à "hand.L"/"hand.R".
"""

import bpy
from mathutils import Vector

RIG_NAME = "CORPUS_MOCAP_TestRig"
HAND_TIP_X = 0.92  # doit correspondre à la position de hand.L/R dans generate_test_rig.py
HAND_Z = 1.45

# (nom du doigt, décalage Z à la base, longueurs des 3 segments)
FINGER_SPECS = [
    ("index", 0.025, (0.045, 0.030, 0.025)),
    ("middle", 0.008, (0.050, 0.035, 0.025)),
    ("ring", -0.008, (0.045, 0.030, 0.025)),
    ("pinky", -0.025, (0.035, 0.025, 0.020)),
]
THUMB_SEGMENT_LENGTHS = (0.035, 0.028, 0.022)


def _finger_bones(side_sign: float, side_suffix: str) -> list:
    """Retourne une liste (nom, tête, queue, nom_parent, connecté) pour
    les 5 doigts d'une main (côté +1=L / -1=R)."""
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

    # Pouce : part un peu avant le bout de la main (côté paume), direction
    # diagonale plutôt que droit dans l'axe de la main.
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


def generate() -> bpy.types.Object:
    rig = bpy.data.objects.get(RIG_NAME)
    if rig is None:
        raise RuntimeError(
            f"Rig '{RIG_NAME}' introuvable — lancez d'abord tools/generate_test_rig.py."
        )

    all_bones = _finger_bones(1.0, "L") + _finger_bones(-1.0, "R")

    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    edit_bones = rig.data.edit_bones

    # Nettoyage (ré-exécution idempotente) : supprime les bones de doigts
    # existants avant de les recréer.
    existing_names = {name for name, *_ in all_bones}
    for name in list(existing_names):
        if name in edit_bones:
            edit_bones.remove(edit_bones[name])

    for name, head, tail, parent_name, connected in all_bones:
        eb = edit_bones.new(name)
        eb.head = Vector(head)
        eb.tail = Vector(tail)
        if parent_name and parent_name in edit_bones:
            eb.parent = edit_bones[parent_name]
            eb.use_connect = connected

    bpy.ops.object.mode_set(mode="OBJECT")
    print(f"[generate_test_hands] {len(all_bones)} bones de doigts ajoutés à '{RIG_NAME}'.")
    return rig


if __name__ == "__main__":
    generate()
