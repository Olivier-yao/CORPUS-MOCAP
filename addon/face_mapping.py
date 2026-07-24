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

import math

import bpy
from mathutils import Matrix, Quaternion, Vector

from .bone_mapping import bone_rest_world_rot, resolve_bone_name

HEAD_BONE_NAME = "head"

# Bones faciaux optionnels (voir addon/character_builder.py pour un
# générateur qui les crée déjà nommés/positionnés correctement) : s'ils
# sont absents du rig cible, apply_jaw/apply_eyebrows ne font rien
# silencieusement (comme le reste du mapping face aux bones manquants).
JAW_BONE_NAME = "jaw"
# brow.mid.L/R existent dans le rig généré (character_builder.py) mais ne
# sont pas pilotés ici : aucun coefficient ARKit ne correspond à "milieu
# du sourcil" isolément — bones de contrôle manuel uniquement.
BROW_IN_BONE_NAMES = {"L": "brow.in.L", "R": "brow.in.R"}
BROW_OUT_BONE_NAMES = {"L": "brow.out.L", "R": "brow.out.R"}

# Angle max d'ouverture de la mâchoire (rotation locale autour de l'axe X
# du bone jaw) pour un coefficient "jawOpen" ARKit à 1.0. Signe empirique
# (comme _MP_TO_RIG plus bas) — à inverser si la mâchoire s'ouvre vers le
# haut au lieu du bas sur votre rig.
JAW_OPEN_MAX_ANGLE = math.radians(25.0)

# Déplacement max (mètres, échelle du personnage généré par
# character_builder.py) d'un sourcil le long de l'axe Y local de son bone
# (axe de visée figé au repos, voir bone_rest_world_rot) pour un signal
# de hausse/baisse à pleine intensité.
BROW_MAX_OFFSET = 0.015

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
        rest_world_rot = bone_rest_world_rot(pose_bone, armature_obj)

        r_mp = Matrix((tuple(rotation_9[0:3]), tuple(rotation_9[3:6]), tuple(rotation_9[6:9])))
        r_rig = _MP_TO_RIG @ r_mp @ _MP_TO_RIG.transposed()

        local_rot = rest_world_rot.inverted() @ r_rig @ rest_world_rot
    except ValueError:
        print(f"[CORPUS-MOCAP] Matrice de repos non-inversible pour l'os '{bone.name}' — gelé cette trame.")
        return

    pose_bone.rotation_mode = "QUATERNION"
    pose_bone.rotation_quaternion = local_rot.to_quaternion()


def apply_jaw(armature_obj: bpy.types.Object, blendshapes: dict[str, float], prefix: str = "", suffix: str = "") -> None:
    """Ouvre/ferme la mâchoire (bone "jaw") par rotation locale autour de
    son axe X, à partir du coefficient ARKit "jawOpen" (0-1). Rotation
    locale directe (pas de conjugaison par bone_rest_world_rot) : le sens
    d'ouverture dépend uniquement de l'orientation de repos du bone jaw
    telle que construite par character_builder.py (tête vers l'arrière,
    queue vers l'avant-bas), pas de la pose courante de la tête."""
    pose_bone = armature_obj.pose.bones.get(resolve_bone_name(JAW_BONE_NAME, prefix, suffix))
    if pose_bone is None:
        return
    open_amount = max(0.0, min(1.0, blendshapes.get("jawOpen", 0.0)))
    angle = JAW_OPEN_MAX_ANGLE * open_amount
    pose_bone.rotation_mode = "QUATERNION"
    pose_bone.rotation_quaternion = Quaternion((1.0, 0.0, 0.0), -angle)


def apply_eyebrows(armature_obj: bpy.types.Object, blendshapes: dict[str, float], prefix: str = "", suffix: str = "") -> None:
    """Hausse/abaisse les sourcils par translation le long de l'axe Y
    local de chaque bone (figé à son orientation de repos, comme la
    torsion du poignet dans hand_mapping.py — évite de dépendre d'une
    conjugaison par la pose courante de la tête pour un mouvement aussi
    simple). Deux bones par côté, chacun sur son propre coefficient ARKit
    (pas de fusion) : "brow.in.L/R" <- browInnerUp (même valeur des deux
    côtés, ARKit ne le sépare pas par côté), "brow.out.L/R" <-
    browOuterUpLeft/Right, tous deux réduits par browDownLeft/Right pour
    la baisse. brow.mid.L/R n'est pas piloté (voir constantes ci-dessus)."""
    inner_up = blendshapes.get("browInnerUp", 0.0)
    values = {
        (BROW_IN_BONE_NAMES, "L"): inner_up - blendshapes.get("browDownLeft", 0.0),
        (BROW_IN_BONE_NAMES, "R"): inner_up - blendshapes.get("browDownRight", 0.0),
        (BROW_OUT_BONE_NAMES, "L"): blendshapes.get("browOuterUpLeft", 0.0) - blendshapes.get("browDownLeft", 0.0),
        (BROW_OUT_BONE_NAMES, "R"): blendshapes.get("browOuterUpRight", 0.0) - blendshapes.get("browDownRight", 0.0),
    }
    for (bone_names, side), raw_value in values.items():
        pose_bone = armature_obj.pose.bones.get(resolve_bone_name(bone_names[side], prefix, suffix))
        if pose_bone is None:
            continue
        clamped = max(-1.0, min(1.0, raw_value))
        pose_bone.location = Vector((0.0, clamped * BROW_MAX_OFFSET, 0.0))


def keyframeable_bone_names(prefix: str = "", suffix: str = "") -> list[tuple[str, str]]:
    """Liste (nom d'os résolu, data_path) pour tous les os faciaux animés
    par ce module — utilisé pour l'insertion de keyframes pendant
    l'enregistrement (voir MOCAP_OT_toggle_capture dans operators.py)."""
    result = [
        (resolve_bone_name(HEAD_BONE_NAME, prefix, suffix), "rotation_quaternion"),
        (resolve_bone_name(JAW_BONE_NAME, prefix, suffix), "rotation_quaternion"),
    ]
    for name in list(BROW_IN_BONE_NAMES.values()) + list(BROW_OUT_BONE_NAMES.values()):
        result.append((resolve_bone_name(name, prefix, suffix), "location"))
    return result


def get_animated_bone_names(prefix: str = "", suffix: str = "") -> list[str]:
    """Noms résolus des os faciaux (tête + mâchoire + sourcils) — utilisé
    par l'outil d'association interactive des os (operators.py)."""
    return [name for name, _ in keyframeable_bone_names(prefix, suffix)]


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
