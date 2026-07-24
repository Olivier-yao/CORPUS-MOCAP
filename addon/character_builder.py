"""Génère un rig CORPUS-MOCAP, de trois façons :

1. `generate()` : personnage de base complet (armature + mesh humanoïde
   proportionné + shape keys ARKit), pour tester sans modèle personnel —
   voir MOCAP_OT_generate_base_character dans operators.py, bouton
   "Générer un personnage de base".
2. `generate_rig_for_mesh(mesh_obj)` : **rig seul** (aucun mesh créé),
   mis à l'échelle et positionné pour correspondre approximativement au
   modèle 3D sélectionné (calé sur sa boîte englobante) — voir
   MOCAP_OT_generate_rig_for_mesh, bouton "Générer un rig pour le modèle
   sélectionné". Point de départ approximatif : comme pour un meta-rig
   Rigify, l'utilisateur doit ensuite repositionner chaque bone à la main
   (Edit Mode) pour l'aligner précisément sur les articulations réelles
   de son modèle.
3. `create_reference_point(...)` + `build_rig_from_points()` : variante
   en 2 étapes de l'option 2, pour un positionnement plus précis sans
   manipuler des bones directement. Étape 1 (pilotée par le modal
   MOCAP_OT_generate_reference_points dans operators.py, PAS par ce
   module directement) crée les points UN PAR UN — pas tous en même
   temps, sinon ~78 petits cercles superposés sur le visage sont
   illisibles (retour utilisateur direct sur une première version qui
   les créait tous d'un coup). Seuls les joints "primaires" sont proposés
   (voir primary_joint_names) : les joints "secondaires" (bout d'un bone
   de contrôle sans signification anatomique propre) sont dérivés
   automatiquement, jamais positionnés à la main. L'utilisateur déplace
   chaque point (activer le Snap to Vertex de Blender pour le coller
   exactement sur la surface du modèle — yeux, coudes, coins de bouche,
   etc., beaucoup plus simple à manipuler en Object Mode qu'un bone en
   Edit Mode) puis valide pour passer au suivant. Étape 2
   (`build_rig_from_points`) lit la position ACTUELLE de chaque point et
   construit l'armature à partir de ces positions. Ce module ne détecte
   lui-même aucun point sur le mesh : le positionnement précis reste
   entièrement manuel dans les trois cas.

Dans tous les cas, les bones sont nommés selon la convention attendue
par le mapping de capture — voir addon/bone_mapping.py (corps),
addon/hand_mapping.py (doigts), addon/face_mapping.py (visage : head,
jaw, brow.in/out.L/R, et les autres bones faciaux définis ici qui ne
sont pas tous pilotés par la capture, voir FACE_BONE_JOINTS). Le
squelettage (Parent > Armature Deform) d'un mesh existant reste toujours
une étape manuelle séparée, jamais faite automatiquement par ce module
(sauf dans generate(), qui skinne le mesh QU'IL a lui-même créé).
L'utilisateur peut sculpter/redessiner un personnage généré par
generate() à sa convenance (Edit Mode / Sculpt Mode, Weight Paint pour
affiner les poids d'os) SANS renommer les bones ni les shape keys, pour
garder la compatibilité avec le mapping de capture.

Convention de coordonnées : les positions de JOINTS sont en coordonnées
"monde" absolues pour un personnage de référence d'1m72 (rig à
l'origine, sans transform propre), exactement comme
tools/generate_test_rig.py. generate_rig_for_mesh() et
generate_reference_points() appliquent une échelle + un décalage
uniformes à ces coordonnées de référence pour approcher la taille/
position du mesh cible (voir compute_fit_transform)."""

from __future__ import annotations

import bpy
from mathutils import Vector

CHARACTER_NAME = "CORPUS_MOCAP_Character"
MESH_NAME = "CORPUS_MOCAP_Character_Mesh"

HEAD_CENTER = Vector((0.0, 0.0, 1.64))
HEAD_RADIUS = 0.12

# Position de référence (coordonnées "monde", personnage d'1m72) de
# chaque articulation nommée — source unique utilisée à la fois pour
# construire le rig canonique (generate/generate_rig_for_mesh) ET pour
# placer les points de repère déplaçables (generate_reference_points).
# Chaque bone de BODY_BONE_JOINTS/FACE_BONE_JOINTS référence deux de ces
# noms (tête, queue) plutôt que des coordonnées en dur — voir
# _resolve_bone_coords. Les bones CONNECTÉS (connected=True) partagent le
# même nom de joint entre la queue du parent et la tête de l'enfant :
# Blender force de toute façon la tête d'un bone connecté à coïncider
# avec la queue de son parent, donc leur donner le même point est à la
# fois correct et permet de les déplacer ensemble comme une seule
# articulation dans le flux "points de repère".
JOINTS: dict[str, tuple[float, float, float]] = {
    # --- Corps ---
    "root":          (0.0, 0.0, 1.00),   # hips.head, spine.head
    "hips_top":      (0.0, 0.0, 1.08),   # hips.tail (bone court, pas une vraie articulation)
    "chest_base":    (0.0, 0.0, 1.25),   # spine.tail / chest.head
    "neck_base":     (0.0, 0.0, 1.45),   # chest.tail / neck.head
    "head_base":     (0.0, 0.0, 1.55),   # neck.tail / head.head
    "head_top":      (0.0, 0.0, 1.72),   # head.tail

    "clavicle_in.L": (0.05, 0.0, 1.45),
    "shoulder.L":    (0.18, 0.0, 1.45),  # shoulder.L.tail / upper_arm.L.head
    "elbow.L":       (0.50, 0.0, 1.45),  # upper_arm.L.tail / forearm.L.head
    "wrist.L":       (0.78, 0.0, 1.45),  # forearm.L.tail / hand.L.head
    "hand_tip.L":    (0.92, 0.0, 1.45),

    "clavicle_in.R": (-0.05, 0.0, 1.45),
    "shoulder.R":    (-0.18, 0.0, 1.45),
    "elbow.R":       (-0.50, 0.0, 1.45),
    "wrist.R":       (-0.78, 0.0, 1.45),
    "hand_tip.R":    (-0.92, 0.0, 1.45),

    "hip.L":         (0.10, 0.0, 1.00),
    "knee.L":        (0.10, 0.0, 0.55),  # thigh.L.tail / shin.L.head
    "ankle.L":       (0.10, 0.0, 0.12),  # shin.L.tail / foot.L.head
    "foot_tip.L":    (0.10, 0.14, 0.02),

    "hip.R":         (-0.10, 0.0, 1.00),
    "knee.R":        (-0.10, 0.0, 0.55),
    "ankle.R":       (-0.10, 0.0, 0.12),
    "foot_tip.R":    (-0.10, 0.14, 0.02),

    # --- Visage (niveau "intermédiaire", voir FACE_BONE_JOINTS) ---
    # Y positif = "devant soi" (voir bone_mapping._landmark_to_vector et
    # foot_tip.L/R ci-dessus, dont le Y positif fait pointer le pied vers
    # l'avant) : le visage doit donc être du côté +Y de la tête, pas -Y.
    "jaw_hinge":      (0.0, 0.04, 1.60),
    "chin_top":       (0.0, 0.07, 1.545),   # jaw.tail / chin.head
    "chin_tip":       (0.0, 0.09, 1.525),

    "eye.L":          (0.045, 0.11, 1.665),
    "eye_socket.L":   (0.045, 0.13, 1.665),
    "eye.R":          (-0.045, 0.11, 1.665),
    "eye_socket.R":   (-0.045, 0.13, 1.665),

    "lid_T.L":        (0.045, 0.10, 1.675),
    "lid_T_end.L":    (0.045, 0.105, 1.672),
    "lid_B.L":        (0.045, 0.10, 1.655),
    "lid_B_end.L":    (0.045, 0.105, 1.658),
    "lid_T.R":        (-0.045, 0.10, 1.675),
    "lid_T_end.R":    (-0.045, 0.105, 1.672),
    "lid_B.R":        (-0.045, 0.10, 1.655),
    "lid_B_end.R":    (-0.045, 0.105, 1.658),

    "brow_in.L":      (0.02, 0.095, 1.695),
    "brow_in_end.L":  (0.02, 0.095, 1.705),
    "brow_mid.L":     (0.045, 0.095, 1.70),
    "brow_mid_end.L": (0.045, 0.095, 1.71),
    "brow_out.L":     (0.07, 0.09, 1.695),
    "brow_out_end.L": (0.07, 0.09, 1.705),
    "brow_in.R":      (-0.02, 0.095, 1.695),
    "brow_in_end.R":  (-0.02, 0.095, 1.705),
    "brow_mid.R":     (-0.045, 0.095, 1.70),
    "brow_mid_end.R": (-0.045, 0.095, 1.71),
    "brow_out.R":     (-0.07, 0.09, 1.695),
    "brow_out_end.R": (-0.07, 0.09, 1.705),

    "nose_bridge":    (0.0, 0.11, 1.63),
    "nose_tip":       (0.0, 0.125, 1.60),   # nose.tail / nose.tip.head
    "nose_tip_end":   (0.0, 0.135, 1.595),

    "cheek.L":        (0.07, 0.08, 1.60),
    "cheek_end.L":    (0.09, 0.07, 1.60),
    "cheek.R":        (-0.07, 0.08, 1.60),
    "cheek_end.R":    (-0.09, 0.07, 1.60),

    "mouth_corner.L":     (0.035, 0.105, 1.575),
    "mouth_corner_end.L": (0.045, 0.10, 1.575),
    "mouth_corner.R":     (-0.035, 0.105, 1.575),
    "mouth_corner_end.R": (-0.045, 0.10, 1.575),

    "lip_T_center":       (0.0, 0.11, 1.585),
    "lip_T_center_end":   (0.0, 0.115, 1.582),
    "lip_T.L":            (0.02, 0.108, 1.582),
    "lip_T_end.L":        (0.03, 0.105, 1.58),
    "lip_T.R":            (-0.02, 0.108, 1.582),
    "lip_T_end.R":        (-0.03, 0.105, 1.58),

    "lip_B_center":       (0.0, 0.108, 1.568),
    "lip_B_center_end":   (0.0, 0.112, 1.565),
    "lip_B.L":            (0.02, 0.105, 1.568),
    "lip_B_end.L":        (0.03, 0.10, 1.567),
    "lip_B.R":            (-0.02, 0.105, 1.568),
    "lip_B_end.R":        (-0.03, 0.10, 1.567),

    "ear.L":          (0.11, 0.02, 1.645),
    "ear_end.L":      (0.13, 0.02, 1.635),
    "ear.R":          (-0.11, 0.02, 1.645),
    "ear_end.R":      (-0.13, 0.02, 1.635),
}

# (nom du bone, joint tête, joint queue, parent, connecté à son parent) —
# mêmes proportions que tools/generate_test_rig.py (dupliqué ici pour que
# ce module reste autonome : tools/ n'est pas embarqué dans l'addon
# installé), réécrit en référence à JOINTS ci-dessus.
BODY_BONE_JOINTS = [
    ("hips",        "root",          "hips_top",   None,          False),
    ("spine",       "root",          "chest_base", "hips",        False),
    ("chest",       "chest_base",    "neck_base",  "spine",       True),
    ("neck",        "neck_base",     "head_base",  "chest",       True),
    ("head",        "head_base",     "head_top",   "neck",        True),

    ("shoulder.L",  "clavicle_in.L", "shoulder.L", "chest",       False),
    ("upper_arm.L", "shoulder.L",    "elbow.L",    "shoulder.L",  True),
    ("forearm.L",   "elbow.L",       "wrist.L",    "upper_arm.L", True),
    ("hand.L",      "wrist.L",       "hand_tip.L", "forearm.L",   True),

    ("shoulder.R",  "clavicle_in.R", "shoulder.R", "chest",       False),
    ("upper_arm.R", "shoulder.R",    "elbow.R",    "shoulder.R",  True),
    ("forearm.R",   "elbow.R",       "wrist.R",    "upper_arm.R", True),
    ("hand.R",      "wrist.R",       "hand_tip.R", "forearm.R",   True),

    ("thigh.L",     "hip.L",         "knee.L",     "hips",        False),
    ("shin.L",      "knee.L",        "ankle.L",    "thigh.L",     True),
    ("foot.L",      "ankle.L",       "foot_tip.L", "shin.L",      True),

    ("thigh.R",     "hip.R",         "knee.R",     "hips",        False),
    ("shin.R",      "knee.R",        "ankle.R",    "thigh.R",     True),
    ("foot.R",      "ankle.R",       "foot_tip.R", "shin.R",      True),
]

# Doigts — même convention que tools/generate_test_hands.py (dupliqué
# pour la même raison que BODY_BONE_JOINTS ci-dessus). Exclus du système
# de points de repère (trop nombreux à positionner un par un) : leur
# forme (longueur/écartement des segments) reste toujours celle du
# personnage de référence, mais leur point d'ancrage suit la position
# RÉSOLUE du joint "hand_tip.L/R" (voir _all_bones) — donc la valeur
# canonique par défaut si non touché, ou la position dérivée du point
# "wrist.L/R" si déplacé via le flux de points de repère. Sans ce
# rattachement dynamique, déplacer "wrist.L/R" loin de sa position
# canonique laissait les doigts "flotter" à l'ancien emplacement au lieu
# de suivre la main (bug repéré par un utilisateur).
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
# par la capture (voir addon/face_mapping.py : jaw, brow_in/out.L/R
# aujourd'hui) — les autres (eye.*, lid.*, brow_mid.*, nose*, cheek.*,
# chin, mouth_corner.*, lip.*, ear.*) sont des bones de contrôle pour
# affiner/animer à la main, ou pour brancher plus de coefficients ARKit
# plus tard sans avoir à régénérer le rig.
FACE_BONE_JOINTS = [
    ("jaw",             "jaw_hinge",      "chin_top",       "head", False),
    ("chin",            "chin_top",       "chin_tip",       "jaw",  True),

    ("eye.L",            "eye.L",          "eye_socket.L",         "head", False),
    ("eye.R",            "eye.R",          "eye_socket.R",         "head", False),
    ("lid.T.L",          "lid_T.L",        "lid_T_end.L",          "head", False),
    ("lid.B.L",          "lid_B.L",        "lid_B_end.L",          "head", False),
    ("lid.T.R",          "lid_T.R",        "lid_T_end.R",          "head", False),
    ("lid.B.R",          "lid_B.R",        "lid_B_end.R",          "head", False),

    ("brow.in.L",        "brow_in.L",      "brow_in_end.L",        "head", False),
    ("brow.mid.L",       "brow_mid.L",     "brow_mid_end.L",       "head", False),
    ("brow.out.L",       "brow_out.L",     "brow_out_end.L",       "head", False),
    ("brow.in.R",        "brow_in.R",      "brow_in_end.R",        "head", False),
    ("brow.mid.R",       "brow_mid.R",     "brow_mid_end.R",       "head", False),
    ("brow.out.R",       "brow_out.R",     "brow_out_end.R",       "head", False),

    ("nose",             "nose_bridge",    "nose_tip",             "head", False),
    ("nose.tip",         "nose_tip",       "nose_tip_end",         "nose", True),

    ("cheek.L",          "cheek.L",        "cheek_end.L",          "head", False),
    ("cheek.R",          "cheek.R",        "cheek_end.R",          "head", False),

    ("mouth.corner.L",   "mouth_corner.L", "mouth_corner_end.L",   "head", False),
    ("mouth.corner.R",   "mouth_corner.R", "mouth_corner_end.R",   "head", False),

    ("lip.T",            "lip_T_center",   "lip_T_center_end",     "head", False),
    ("lip.T.L",          "lip_T.L",        "lip_T_end.L",          "head", False),
    ("lip.T.R",          "lip_T.R",        "lip_T_end.R",          "head", False),
    ("lip.B",            "lip_B_center",   "lip_B_center_end",     "jaw",  False),
    ("lip.B.L",          "lip_B.L",        "lip_B_end.L",          "jaw",  False),
    ("lip.B.R",          "lip_B.R",        "lip_B_end.R",          "jaw",  False),

    ("ear.L",            "ear.L",          "ear_end.L",            "head", False),
    ("ear.R",            "ear.R",          "ear_end.R",            "head", False),
]

# Bones faciaux effectivement pilotés par la capture (voir
# addon/face_mapping.py) — tous les autres bones de FACE_BONE_JOINTS
# (chin, eye.*, lid.*, brow.mid.*, nose*, cheek.*, mouth.corner.*, lip.*,
# ear.*) sont de purs bones de contrôle, pas encore animés par MediaPipe.
DRIVEN_FACE_BONES = {"head", "jaw", "brow.in.L", "brow.in.R", "brow.out.L", "brow.out.R"}

# Bones de contrôle sans géométrie de mesh clairement associée : marqués
# use_deform=False à la création (voir _build_armature) pour que Blender
# les ignore lors du "Armature Deform with Automatic Weights" — sinon le
# solveur de heat weighting échoue systématiquement dessus ("Bone Heat
# Weighting: failed to find solution for one or more bones") puisqu'ils
# n'ont aucune zone de mesh qui leur soit propre. L'utilisateur peut
# réactiver use_deform à la main (Bone Properties > Deform) sur l'un
# d'eux s'il veut l'utiliser pour déformer son mesh.
FACE_CONTROL_ONLY_BONES = {name for name, *_ in FACE_BONE_JOINTS if name not in DRIVEN_FACE_BONES}

# (nom du bone, rayon du cylindre) — géométrie du corps (bouton "Générer
# un personnage de base" uniquement). Les bones absents de cette table
# (doigts, tous les bones faciaux) n'ont pas de géométrie propre : les
# bones faciaux héritent du poids automatique de la sphère de tête, les
# doigts n'ont aucune géométrie (voir docstring).
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
# brow.in/out.L/R à la place — pas de double animation de la même zone
# par deux mécanismes différents). Coordonnées en espace "monde" absolu
# (voir docstring du module), pas relatives à un centre de sphère.
FACE_SHAPE_KEYS = [
    ("eyeBlinkLeft",     (0.045, 0.09, 1.66), 0.045, (0.0, 0.01, -0.02)),
    ("eyeBlinkRight",    (-0.045, 0.09, 1.66), 0.045, (0.0, 0.01, -0.02)),
    ("mouthSmileLeft",   (0.035, 0.10, 1.58), 0.045, (0.01, 0.005, 0.015)),
    ("mouthSmileRight",  (-0.035, 0.10, 1.58), 0.045, (-0.01, 0.005, 0.015)),
    ("mouthPucker",      (0.0, 0.105, 1.58), 0.05, (0.0, -0.02, 0.0)),
    ("cheekPuff",        (0.0, 0.05, 1.62), 0.08, (0.0, -0.015, 0.0)),
]

# Points de repère déplaçables (create_reference_point/
# build_rig_from_points) : préfixe de nom d'objet, taille d'affichage, et
# nom de la Collection Blender qui les regroupe (pour les sélectionner/
# masquer/supprimer en bloc facilement depuis l'Outliner).
POINT_NAME_PREFIX = "CMP_pt."
POINT_DISPLAY_SIZE = 0.012
POINTS_COLLECTION_NAME = "CORPUS_MOCAP_RigPoints"

# Traduction française indicative de chaque nom de joint (voir
# translate_joint_name) — affichée dans la barre de statut du flux
# interactif pour clarifier quelle articulation positionner.
JOINT_TRANSLATIONS: dict[str, str] = {
    "root": "Racine (bassin)",
    "hips_top": "Sommet du bassin",
    "chest_base": "Base du torse",
    "neck_base": "Base du cou",
    "head_base": "Base de la tête",
    "head_top": "Sommet de la tête",

    "clavicle_in.L": "Clavicule interne gauche",
    "shoulder.L": "Épaule gauche",
    "elbow.L": "Coude gauche",
    "wrist.L": "Poignet gauche",
    "hand_tip.L": "Bout de la main gauche",
    "clavicle_in.R": "Clavicule interne droite",
    "shoulder.R": "Épaule droite",
    "elbow.R": "Coude droit",
    "wrist.R": "Poignet droit",
    "hand_tip.R": "Bout de la main droite",

    "hip.L": "Hanche gauche",
    "knee.L": "Genou gauche",
    "ankle.L": "Cheville gauche",
    "foot_tip.L": "Bout du pied gauche",
    "hip.R": "Hanche droite",
    "knee.R": "Genou droit",
    "ankle.R": "Cheville droite",
    "foot_tip.R": "Bout du pied droit",

    "jaw_hinge": "Articulation de la mâchoire",
    "chin_top": "Haut du menton",
    "chin_tip": "Bout du menton",

    "eye.L": "Œil gauche",
    "eye_socket.L": "Fond de l'orbite gauche",
    "eye.R": "Œil droit",
    "eye_socket.R": "Fond de l'orbite droite",

    "lid_T.L": "Paupière haute gauche",
    "lid_T_end.L": "Paupière haute gauche (extrémité)",
    "lid_B.L": "Paupière basse gauche",
    "lid_B_end.L": "Paupière basse gauche (extrémité)",
    "lid_T.R": "Paupière haute droite",
    "lid_T_end.R": "Paupière haute droite (extrémité)",
    "lid_B.R": "Paupière basse droite",
    "lid_B_end.R": "Paupière basse droite (extrémité)",

    "brow_in.L": "Sourcil interne gauche",
    "brow_in_end.L": "Sourcil interne gauche (extrémité)",
    "brow_mid.L": "Sourcil milieu gauche",
    "brow_mid_end.L": "Sourcil milieu gauche (extrémité)",
    "brow_out.L": "Sourcil externe gauche",
    "brow_out_end.L": "Sourcil externe gauche (extrémité)",
    "brow_in.R": "Sourcil interne droit",
    "brow_in_end.R": "Sourcil interne droit (extrémité)",
    "brow_mid.R": "Sourcil milieu droit",
    "brow_mid_end.R": "Sourcil milieu droit (extrémité)",
    "brow_out.R": "Sourcil externe droit",
    "brow_out_end.R": "Sourcil externe droit (extrémité)",

    "nose_bridge": "Arête du nez",
    "nose_tip": "Bout du nez",
    "nose_tip_end": "Bout du nez (extrémité)",

    "cheek.L": "Joue gauche",
    "cheek_end.L": "Joue gauche (extrémité)",
    "cheek.R": "Joue droite",
    "cheek_end.R": "Joue droite (extrémité)",

    "mouth_corner.L": "Coin de bouche gauche",
    "mouth_corner_end.L": "Coin de bouche gauche (extrémité)",
    "mouth_corner.R": "Coin de bouche droit",
    "mouth_corner_end.R": "Coin de bouche droit (extrémité)",

    "lip_T_center": "Lèvre supérieure (centre)",
    "lip_T_center_end": "Lèvre supérieure (centre, extrémité)",
    "lip_T.L": "Lèvre supérieure gauche",
    "lip_T_end.L": "Lèvre supérieure gauche (extrémité)",
    "lip_T.R": "Lèvre supérieure droite",
    "lip_T_end.R": "Lèvre supérieure droite (extrémité)",

    "lip_B_center": "Lèvre inférieure (centre)",
    "lip_B_center_end": "Lèvre inférieure (centre, extrémité)",
    "lip_B.L": "Lèvre inférieure gauche",
    "lip_B_end.L": "Lèvre inférieure gauche (extrémité)",
    "lip_B.R": "Lèvre inférieure droite",
    "lip_B_end.R": "Lèvre inférieure droite (extrémité)",

    "ear.L": "Oreille gauche",
    "ear_end.L": "Oreille gauche (extrémité)",
    "ear.R": "Oreille droite",
    "ear_end.R": "Oreille droite (extrémité)",
}


def _smoothstep(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)


def _finger_bones(side_sign: float, side_suffix: str, hand_tip) -> list:
    hand_tip = Vector(hand_tip)
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


def _resolve_bone_coords(bone_joint_defs: list, joint_positions: dict) -> list:
    """Convertit une liste (nom, joint_tête, joint_queue, parent,
    connecté) en (nom, coord_tête, coord_queue, parent, connecté) en
    résolvant chaque nom de joint via `joint_positions` (soit JOINTS —
    valeurs canoniques —, soit les positions actuelles des Empties de
    generate_reference_points, voir build_rig_from_points)."""
    resolved = []
    for name, head_joint, tail_joint, parent_name, connected in bone_joint_defs:
        resolved.append((name, tuple(joint_positions[head_joint]), tuple(joint_positions[tail_joint]), parent_name, connected))
    return resolved


def _all_bones(joint_positions: dict | None = None) -> list:
    if joint_positions is None:
        joint_positions = JOINTS
    body = _resolve_bone_coords(BODY_BONE_JOINTS, joint_positions)
    face = _resolve_bone_coords(FACE_BONE_JOINTS, joint_positions)
    fingers = (
        _finger_bones(1.0, "L", joint_positions["hand_tip.L"])
        + _finger_bones(-1.0, "R", joint_positions["hand_tip.R"])
    )
    return body + fingers + face


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


def _build_armature(joint_positions: dict | None = None, scale: float = 1.0, offset: Vector | None = None) -> bpy.types.Object:
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
    for name, head, tail, parent_name, connected in _all_bones(joint_positions):
        eb = edit_bones.new(name)
        eb.head = Vector(head) * scale + offset
        eb.tail = Vector(tail) * scale + offset
        if parent_name:
            eb.parent = edit_bones[parent_name]
            eb.use_connect = connected
        if name in FACE_CONTROL_ONLY_BONES:
            eb.use_deform = False
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

    body_bones = _resolve_bone_coords(BODY_BONE_JOINTS, JOINTS)
    parts = []
    for name, head, tail, _parent, _connected in body_bones:
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


def _joint_head_tail_sets() -> tuple[set, set]:
    heads, tails = set(), set()
    for bone_defs in (BODY_BONE_JOINTS, FACE_BONE_JOINTS):
        for _name, head_j, tail_j, _parent, _connected in bone_defs:
            heads.add(head_j)
            tails.add(tail_j)
    return heads, tails


def _secondary_joint_offsets() -> dict[str, tuple[str, tuple[float, float, float]]]:
    """Joints qui ne sont QUE la queue d'un bone, jamais la tête d'un
    autre (bout d'un bone de contrôle court sans réelle signification
    anatomique propre, ex. "hand_tip.L" ou "eye_socket.L") : leur position
    est dérivée automatiquement (tête du bone + décalage canonique
    tête->queue) plutôt que proposée individuellement dans le flux
    interactif de points de repère — sinon, positionner ~78 points un par
    un pour un simple visage est ingérable ; voir primary_joint_names."""
    heads, tails = _joint_head_tail_sets()
    secondary = tails - heads
    offsets: dict[str, tuple[str, tuple[float, float, float]]] = {}
    for bone_defs in (BODY_BONE_JOINTS, FACE_BONE_JOINTS):
        for _name, head_j, tail_j, _parent, _connected in bone_defs:
            if tail_j in secondary and tail_j not in offsets:
                offset = Vector(JOINTS[tail_j]) - Vector(JOINTS[head_j])
                offsets[tail_j] = (head_j, tuple(offset))
    return offsets


def primary_joint_names() -> list[str]:
    """Articulations proposées une par une (dans l'ordre de JOINTS —
    corps puis visage) par le flux interactif "Générer les points de
    repère" — toutes sauf les queues de bones "orphelines" sans
    signification anatomique propre (voir _secondary_joint_offsets)."""
    secondary = set(_secondary_joint_offsets())
    return [name for name in JOINTS if name not in secondary]


def translate_joint_name(joint_name: str) -> str:
    """Traduction française indicative d'un nom de joint (affichage dans
    la barre de statut du flux interactif), ex. "wrist.L" -> "Poignet
    gauche". Purement informatif, ne remplace pas le nom utilisé en
    interne. Retourne le nom tel quel si absent de JOINT_TRANSLATIONS."""
    return JOINT_TRANSLATIONS.get(joint_name, joint_name)


def mirror_joint_name(joint_name: str) -> str | None:
    """Nom du joint symétrique (".L" <-> ".R"), ou None si `joint_name`
    n'a pas de côté (ex. "root", "nose_bridge") — utilisé par le mode
    symétrie du flux interactif de points de repère (voir
    MOCAP_OT_generate_reference_points dans operators.py)."""
    if joint_name.endswith(".L"):
        return joint_name[:-2] + ".R"
    if joint_name.endswith(".R"):
        return joint_name[:-2] + ".L"
    return None


def mirror_position(location, axis_x: float) -> Vector:
    """Réflexion d'une position autour du plan sagittal X = axis_x (axe
    gauche/droite du personnage, voir compute_fit_transform) — Y/Z
    inchangés. Accepte tout objet indexable [0]/[1]/[2] (Vector ou
    tuple)."""
    return Vector((2.0 * axis_x - location[0], location[1], location[2]))


def clear_reference_points() -> None:
    coll = bpy.data.collections.get(POINTS_COLLECTION_NAME)
    if coll is None:
        return
    for obj in list(coll.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.data.collections.remove(coll)


def create_reference_point(joint_name: str, scale: float, offset: Vector) -> bpy.types.Object:
    """Crée (ou recrée si déjà présent) le point de repère d'une seule
    articulation, positionné à sa coordonnée canonique mise à l'échelle —
    utilisé un par un par MOCAP_OT_generate_reference_points (flux
    interactif, voir operators.py) plutôt que tous en même temps, pour
    rester lisible dans la vue 3D (voir docstring du module)."""
    existing = bpy.data.objects.get(f"{POINT_NAME_PREFIX}{joint_name}")
    if existing is not None:
        bpy.data.objects.remove(existing, do_unlink=True)

    coll = bpy.data.collections.get(POINTS_COLLECTION_NAME)
    if coll is None:
        coll = bpy.data.collections.new(POINTS_COLLECTION_NAME)
        bpy.context.scene.collection.children.link(coll)

    point_obj = bpy.data.objects.new(f"{POINT_NAME_PREFIX}{joint_name}", None)
    point_obj.empty_display_type = "SPHERE"
    point_obj.empty_display_size = POINT_DISPLAY_SIZE
    point_obj.location = Vector(JOINTS[joint_name]) * scale + offset
    coll.objects.link(point_obj)
    return point_obj


def build_rig_from_points() -> bpy.types.Object:
    """Étape 2 du flux en 2 temps : construit l'armature en lisant la
    position ACTUELLE (après ajustement manuel par l'utilisateur) de
    chaque point "primaire" créé par le flux interactif (voir
    primary_joint_names), puis dérive les joints "secondaires" à partir
    de ces positions (voir _secondary_joint_offsets) — donc à appeler
    après avoir repositionné les points, pas juste après les avoir
    générés. Un point primaire manquant (jamais placé — flux arrêté tôt
    via Echap — ou supprimé) retombe silencieusement sur sa position
    canonique (JOINTS), avec un print d'avertissement listant les joints
    concernés plutôt qu'un crash. Lève RuntimeError si aucun point
    n'existe. Ne touche à aucun mesh. Retourne l'armature créée."""
    coll = bpy.data.collections.get(POINTS_COLLECTION_NAME)
    if coll is None:
        raise RuntimeError(
            "Aucun point de repère trouvé — utilisez d'abord "
            "\"Générer les points de repère\"."
        )

    joint_positions = dict(JOINTS)
    missing = []
    for joint_name in primary_joint_names():
        point_obj = bpy.data.objects.get(f"{POINT_NAME_PREFIX}{joint_name}")
        if point_obj is None:
            missing.append(joint_name)
            continue
        joint_positions[joint_name] = tuple(point_obj.location)

    for secondary_name, (head_joint, rel_offset) in _secondary_joint_offsets().items():
        head_pos = Vector(joint_positions[head_joint])
        joint_positions[secondary_name] = tuple(head_pos + Vector(rel_offset))

    if missing:
        print(
            f"[CORPUS-MOCAP] {len(missing)} point(s) de repère manquant(s), "
            f"position canonique utilisée : {', '.join(missing)}"
        )

    return _build_armature(joint_positions=joint_positions)
