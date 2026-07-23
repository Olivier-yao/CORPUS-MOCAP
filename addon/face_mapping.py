"""Applique les coefficients blend shapes (convention ARKit, calculés par
MediaPipe FaceLandmarker) sur les shape keys du mesh cible.

Mapping par correspondance de nom : si le mesh a une shape key nommée
exactement comme un coefficient MediaPipe (ex: "jawOpen"), sa valeur est
appliquée directement — aucune configuration nécessaire si le personnage
utilise déjà la convention ARKit (cas fréquent pour les rigs faciaux
compatibles Apple/Live Link). Un système de correspondance manuelle pour
les noms différents (cahier des charges §7) n'est pas encore implémenté
dans cette validation Phase 2.

Rig facial à bones (pas à shape keys) : pattern recommandé — exposer les
52 noms de coefficients ARKit comme des *custom properties* sur un bone
(ou l'armature), câbler des Drivers côté rig (fait par le riggeur) qui
traduisent chaque property en mouvement des bones contrôleurs. Cette
séparation garde le pipeline mocap indépendant des détails internes du
rig. `apply_blendshapes` ci-dessous n'écrit aujourd'hui que dans des
shape keys ; l'adapter pour écrire dans des custom properties serait un
changement mineur si ce pattern est retenu.
"""

from __future__ import annotations

import bpy
from mathutils import Matrix

from .bone_mapping import resolve_bone_name

HEAD_BONE_NAME = "head"

# Change de repère MediaPipe (X droite, Y haut, Z vers la caméra/viewer) ->
# repère du rig (X droite, Y devant soi, Z haut) : les deux étant des
# repères directs (main droite), c'est une matrice de changement de base
# pure rotation. Convention empirique (comme _landmark_to_vector dans
# bone_mapping.py) — à vérifier/ajuster si la tête tourne dans le mauvais
# sens lors des premiers tests (inverser le signe de la ligne concernée).
_MP_TO_RIG = Matrix((
    (1.0, 0.0, 0.0),
    (0.0, 0.0, -1.0),
    (0.0, 1.0, 0.0),
))


def apply_head_rotation(
    armature_obj: bpy.types.Object, rotation_9: list[float] | None, prefix: str = "", suffix: str = ""
) -> None:
    """rotation_9 : sous-matrice de rotation 3x3 (9 floats, ligne par
    ligne) issue de facial_transformation_matrixes, exprimée dans un
    repère "monde" (X droite, Y devant soi, Z haut) une fois passée par
    _MP_TO_RIG.

    `pose_bone.rotation_quaternion` s'applique dans le repère de REPOS du
    bone, pas dans le repère monde — pour le bone "head" (qui pointe vers
    le haut au repos, pas vers l'avant), appliquer r_rig tel quel donnait
    un axe de rotation erroné (yaw perçu comme un roll et vice-versa).
    On convertit donc r_rig (repère monde) vers le repère local du bone
    par conjugaison avec son orientation de repos (même principe que
    _aim_bone dans bone_mapping.py, mais pour une rotation complète et
    non une simple direction de visée).

    `prefix`/`suffix` : voir bone_mapping.resolve_bone_name."""
    if rotation_9 is None:
        return
    pose_bone = armature_obj.pose.bones.get(resolve_bone_name(HEAD_BONE_NAME, prefix, suffix))
    if pose_bone is None:
        return

    bone = pose_bone.bone
    try:
        if pose_bone.parent is not None:
            parent_world_rot = pose_bone.parent.matrix.to_3x3()
            rest_local_rot = (pose_bone.parent.bone.matrix_local.inverted() @ bone.matrix_local).to_3x3()
        else:
            parent_world_rot = armature_obj.matrix_world.to_3x3()
            rest_local_rot = bone.matrix_local.to_3x3()
        rest_world_rot = parent_world_rot @ rest_local_rot

        r_mp = Matrix((tuple(rotation_9[0:3]), tuple(rotation_9[3:6]), tuple(rotation_9[6:9])))
        r_rig = _MP_TO_RIG @ r_mp @ _MP_TO_RIG.transposed()

        local_rot = rest_world_rot.inverted() @ r_rig @ rest_world_rot
    except ValueError:
        print(f"[CORPUS-MOCAP] Matrice de repos non-inversible pour l'os '{bone.name}' — gelé cette trame.")
        return

    pose_bone.rotation_mode = "QUATERNION"
    pose_bone.rotation_quaternion = local_rot.to_quaternion()


def apply_blendshapes(mesh_obj: bpy.types.Object, blendshapes: dict[str, float]) -> None:
    key_blocks = getattr(mesh_obj.data.shape_keys, "key_blocks", None)
    if key_blocks is None:
        return
    for name, value in blendshapes.items():
        key_block = key_blocks.get(name)
        if key_block is not None:
            key_block.value = value


def get_mapped_shape_key_names(mesh_obj: bpy.types.Object, blendshape_names: list[str]) -> list[str]:
    """Noms de shape keys du mesh correspondant à un coefficient MediaPipe
    connu — utilisé pour savoir lesquelles garder au moment de keyframer."""
    key_blocks = getattr(mesh_obj.data.shape_keys, "key_blocks", None)
    if key_blocks is None:
        return []
    return [name for name in blendshape_names if name in key_blocks]
