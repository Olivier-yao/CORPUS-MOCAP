# CORPUS-MOCAP — corps + visage + mains (webcam PC)

Addon Blender de capture de mouvement. Couvre pour l'instant : capture du
squelette (33 points MediaPipe Pose), du visage (MediaPipe Face
Landmarker, 52 coefficients blend shapes ARKit) et des mains (MediaPipe
Hand Landmarker, 21 points articulés par main) via webcam PC, lissage
(One Euro Filter), application temps réel sur un rig + un mesh à shape
keys, enregistrement synchronisé en Actions Blender. Pas de stylisation
cartoon, pas de téléphone (voir `CORPUS-MOCAP_cahier-des-charges.md` et
la feuille de route ci-dessous).

## Architecture

Deux processus séparés qui communiquent par socket TCP local :

- **`capture_server/`** : process Python externe (venv classique). Capture
  la webcam (OpenCV), détecte la pose (MediaPipe), lisse le signal, diffuse
  les landmarks à l'addon.
- **`addon/`** : addon Blender (bpy uniquement, pas de dépendance lourde).
  Se connecte au `capture_server`, applique les landmarks sur le rig,
  enregistre les keyframes.

## Installation

### 1. capture_server

MediaPipe ne supporte pas encore les toutes dernières versions de Python
(ex: 3.13/3.14 au moment de l'écriture) : utilisez une version 3.10 ou
3.11 pour le venv, même si votre Python système est plus récent
(`py -3.11 -m venv venv` si plusieurs versions sont installées).

```powershell
cd capture_server
py -3.11 -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Modèle MediaPipe Pose

Depuis mediapipe 0.10.x récent, l'ancienne API `mp.solutions.pose` a été
retirée du paquet pip au profit de la nouvelle "Tasks API", qui nécessite
un fichier modèle téléchargé séparément (non inclus dans le paquet).

Téléchargez `pose_landmarker_lite.task` et placez-le dans
`capture_server/models/` :

```powershell
mkdir models
curl.exe -L -o models\pose_landmarker_lite.task https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/latest/pose_landmarker_lite.task
```

(Variantes plus précises mais plus lentes : `pose_landmarker_full.task` /
`pose_landmarker_heavy.task`, même URL en remplaçant `lite` par `full`/
`heavy` — passer le chemin via `python server.py --model ...` si vous
n'utilisez pas le nom par défaut.)

### 3. Modèle MediaPipe Face Landmarker

Téléchargez `face_landmarker.task` (inclut le calcul des blend shapes) et
placez-le aussi dans `capture_server/models/` :

```powershell
curl.exe -L -o models\face_landmarker.task https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/latest/face_landmarker.task
```

Le tracking visage peut être désactivé avec `python server.py --no-face`
si seul le corps est nécessaire.

### 4. Modèle MediaPipe Hand Landmarker

Téléchargez `hand_landmarker.task` et placez-le aussi dans
`capture_server/models/` :

```powershell
curl.exe -L -o models\hand_landmarker.task https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task
```

Le tracking mains peut être désactivé avec `python server.py --no-hands`.

### 5. Rig, visage et mains de test

Trois options, selon votre cas :

- **Vous avez déjà un modèle 3D personnel** (recommandé dans ce cas) :
  une fois l'addon installé (étape 6 ci-dessous), sélectionnez votre mesh
  dans la scène, puis deux façons de générer un rig calé sur sa taille
  (boîte englobante) — **aucune des deux ne crée de mesh ni ne skinne
  automatiquement votre modèle**, ça reste une étape manuelle (Ctrl+P >
  **Armature Deform**, poids automatiques ou à la main), comme pour
  n'importe quel rig :
  - **Direct** : bouton **"Générer un rig pour le modèle sélectionné"**
    — génère l'armature immédiatement. Comme pour un meta-rig Rigify,
    c'est une base approximative : repositionnez ensuite chaque os à la
    main (Edit Mode) sur les articulations réelles de votre modèle (yeux,
    coins de bouche, coudes, etc.).
  - **En 2 étapes, plus précis** : bouton **"1. Points de repère"** —
    outil interactif qui place les points **un par un** (pas tous en même
    temps : trop de cercles superposés, surtout sur le visage, rendent
    impossible de savoir lequel est lequel). Le nom de l'articulation à
    positionner (ex. "Poignet gauche") s'affiche dans la barre de statut
    en bas de la fenêtre à chaque étape. Pour chaque point : activez le
    **Snap to Vertex** de Blender (aimant en haut de la Vue 3D, mode
    Vertex), `G` pour le déplacer et le coller exactement sur la surface
    de votre modèle, puis **Entrée** pour valider et passer au point
    suivant (**S** pour passer un point sans le déplacer, **Echap** pour
    arrêter — les points déjà placés sont conservés). **Mode symétrie**
    activé par défaut (touche **X** pour basculer) : à la validation d'un
    point `.L`/`.R`, son symétrique est automatiquement repositionné en
    miroir (réflexion autour de l'axe gauche/droite du personnage) —
    évite de repositionner deux fois chaque articulation sur un modèle
    symétrique ; désactivez-le si le vôtre ne l'est pas, ou repositionnez
    le symétrique ensuite s'il ne l'est que localement. 46 points
    proposés (sur les 78 articulations du rig — le reste est dérivé
    automatiquement, voir Limites connues). Une fois terminé (ou arrêté
    en cours de route), bouton **"2. Construire le rig"** — génère
    l'armature à partir de la position actuelle de chaque point (position
    canonique approximative pour tout point non encore placé). Les points
    restent dans la collection `CORPUS_MOCAP_RigPoints` (Outliner) après
    coup, à supprimer une fois le rig construit si vous n'en avez plus
    besoin. Relancer "1. Points de repère" supprime et recrée tout le jeu
    de points (perd tout déplacement déjà fait).

  Les deux inclus un set de bones faciaux "intermédiaire" (~28 os : yeux,
  paupières, sourcils en 3 points par côté, nez, joues, mâchoire, menton,
  coins de bouche, lèvres, oreilles — voir Limites connues), et
  n'incluent PAS les doigts dans le placement manuel (trop nombreux à
  positionner un par un) : les bones de doigts restent mis à l'échelle
  automatiquement, à ajuster ensuite en Edit Mode si besoin. Voir
  `addon/character_builder.py` (`generate_rig_for_mesh`,
  `create_reference_point`, `build_rig_from_points`).
- **Vous n'avez pas encore de modèle, testez le pipeline** : bouton
  **"Générer un personnage de base"** — génère en un clic une armature
  humanoïde skinnée (poids automatiques) + un mesh (corps + tête) + les
  shape keys ARKit + les mêmes bones faciaux, le tout nommé selon la
  convention attendue et déjà assigné comme cibles. Géométrie
  volontairement grossière (cylindres + sphère) : un point de départ à
  sculpter/redessiner ensuite (Edit Mode / Sculpt Mode / Weight Paint)
  **sans renommer les os ni les shape keys** pour rester compatible avec
  la capture. Voir `addon/character_builder.py:generate`.
  Ré-exécuter n'importe lequel de ces boutons de génération supprime et
  recrée entièrement l'armature (et le mesh pour "personnage de base")
  du même nom — ne pas les utiliser pour régénérer un rig déjà
  ajusté/personnalisé.
- **Scripts séparés** (utile pour valider le pipeline sans mesh, ou avant
  que l'addon ne soit installé) : dans Blender, onglet **Scripting**,
  **dans cet ordre** (les mains s'ajoutent au rig de corps déjà créé) :
  1. `tools/generate_test_rig.py`, **Run Script** → armature
     `CORPUS_MOCAP_TestRig` (T-pose, sans mesh).
  2. `tools/generate_test_hands.py`, **Run Script** → ajoute les bones de
     doigts (`thumb.01.L`, `index.02.R`, etc.) à cette même armature.
  3. `tools/generate_test_face.py`, **Run Script** → mesh
     `CORPUS_MOCAP_TestFace` (sphère à 10 shape keys ARKit : `jawOpen`,
     `eyeBlinkLeft`, `mouthSmileLeft`, etc.), attaché au bone "head" du rig
     — ne crée pas les bones `jaw`/`eyebrow.L/R` (propres au générateur
     intégré ci-dessus).

### 6. Addon

1. Compresser le dossier `addon/` en `addon.zip` (le zip doit contenir
   directement `__init__.py` etc., pas un sous-dossier supplémentaire).
2. Blender > Edit > Preferences > Add-ons > Install..., sélectionner le zip.
3. Activer "CORPUS-MOCAP".

## Utilisation

1. Lancer le serveur : `python capture_server/server.py` (le terminal
   affiche "en attente de l'addon Blender...").
2. Dans Blender, ouvrir le N-panel (touche `N` dans la Vue 3D) > onglet
   **CORPUS-MOCAP**.
3. Choisir l'armature cible (`CORPUS_MOCAP_TestRig` ou votre personnage) et,
   optionnellement, le mesh visage cible (`CORPUS_MOCAP_TestFace` ou votre
   personnage — doit avoir des shape keys nommées selon la convention
   ARKit pour être reconnu automatiquement). Les mains n'ont pas de
   sélecteur séparé : si l'armature cible a des bones de doigts nommés
   selon la convention (`thumb.01.L`, etc.), ils sont animés automatiquement.
4. Si vos os ne sont pas nommés exactement `hips`, `spine`, `upper_arm.L`,
   etc. (ex: un rig auto-généré type Rigify qui préfixe ses os de
   déformation en `DEF-`), renseignez **Préfixe des os** / **Suffixe des
   os** dans le panneau — ex. préfixe `DEF-` pour un rig où l'attente
   `hips` correspond en réalité à `DEF-hips` (à condition que cet os soit
   directement animable, pas piloté par une contrainte — voir Limites
   connues). Trois façons de faire correspondre les noms sinon :
   - **Préfixe/suffixe cohérent** : le renseigner directement (ci-dessus).
   - **Renommage en bloc** : sélectionner les os concernés en **Edit
     Mode**, cliquer **"Appliquer aux os sélectionnés"** pour leur
     ajouter le préfixe/suffixe renseigné.
   - **Convention totalement différente** (ex: `lowerarm_r` au lieu de
     `forearm.R`) : cliquer **"Associer les os par clic"** (Edit Mode) —
     l'addon annonce un nom attendu à la fois (barre de statut en bas de
     la fenêtre), vous cliquez l'os correspondant dans la vue 3D ou
     l'Outliner puis `Entrée` pour valider (renomme l'os cliqué vers le
     nom canonique), `S` pour passer un rôle que votre rig n'a pas,
     `Echap` pour arrêter.
5. Ajuster **Stabilité** si besoin (léger = plus réactif, fort = plus lissé).
6. Cliquer **● Enregistrer la performance** — la webcam s'active côté
   `capture_server`, le rig, le visage et/ou les mains doivent suivre vos
   mouvements en temps réel.
7. Cliquer à nouveau pour arrêter : une Action `CORPUS_MOCAP_Take` (corps +
   mains) et, si un mesh visage était sélectionné, `CORPUS_MOCAP_Face_Take`
   (sur le datablock Key du mesh) sont créées avec les keyframes de la
   prise, sur la même timeline.

## Limites connues

- Mapping configurable (`bone_prefix`/`bone_suffix` dans le panneau) :
  un seul préfixe/suffixe **global** appliqué à tous les noms d'os
  attendus — couvre le cas d'un rig auto-généré avec une convention
  cohérente (ex. Rigify `DEF-`), mais pas un remapping par bone
  individuel. Si vos noms ne suivent aucun préfixe/suffixe cohérent, il
  faut renommer les os (à la main, ou via le bouton "Appliquer aux os
  sélectionnés" en Edit Mode) pour correspondre à la convention par
  défaut de `tools/generate_test_rig.py`.
- **Rigs à contraintes (Rigify)** : les os de déformation (`DEF-...`)
  d'un rig Rigify généré suivent généralement des os de contrôle via des
  contraintes (Copy Rotation/Transforms) plutôt que d'être directement
  animables. Notre addon écrit une rotation directement sur l'os ciblé :
  si cet os est contraint, la contrainte l'emporte et la capture n'a
  visuellement aucun effet. Vérifiez l'onglet Bone Constraint Properties
  de l'os visé avant de vous fier au mapping — ciblez l'os de contrôle
  (généralement sans le préfixe `DEF-`) si l'os de déformation est
  contraint.
- Retargeting simplifié ("aim" sans gestion du twist/roll) : suffisant
  pour valider le concept, pas encore un rendu final.
- **Le cadrage caméra doit couvrir tout le corps** (jusqu'aux pieds) pour
  que hanches/jambes soient suivies : si un landmark a une confiance
  MediaPipe trop basse (`VISIBILITY_THRESHOLD` dans `bone_mapping.py`,
  souvent le cas hors cadre), le membre concerné est gelé plutôt que de
  suivre une position devinée.
- Échelles de translation du bassin (`ROOT_TRANSLATION_SCALE_LATERAL` /
  `ROOT_TRANSLATION_SCALE_DEPTH` dans `bone_mapping.py`) empiriques, à
  ajuster selon votre recul webcam. L'axe de profondeur est volontairement
  très amorti car le "z" MediaPipe (déduit d'une seule caméra RGB) est
  bruité — c'était la cause probable d'un effet de "glissement" du rig.
  Le même amortissement (`LIMB_DEPTH_DAMPING`) s'applique maintenant aussi
  à la direction de chaque membre (bras/jambe) — son absence causait un
  membre pointant parfois dans une direction très éloignée du mouvement
  réel, repéré tardivement car ça passait inaperçu sur des mouvements
  simples.
- Épaule/clavicule (`shoulder.L/R`, `CLAVICLE_SEGMENTS`) : nouveau,
  visé depuis le centre des épaules vers chaque épaule (amortissement de
  profondeur fort, `CLAVICLE_DEPTH_DAMPING`, signal subtil) — à valider
  en conditions réelles.
- Rotation du poignet (`hand.L/R`) : refonte architecturale — l'ancienne
  approche calculait une orientation complète à 3 degrés de liberté à
  partir des seuls landmarks de la main (`_hand_orientation_matrix`,
  supprimée), indépendamment de la direction de l'avant-bras (issue d'un
  autre modèle MediaPipe, Pose Landmarker) ; sans garantie de cohérence
  entre les deux, la main pouvait visuellement se "décrocher" du
  prolongement de l'avant-bras, faisant tourner la rotation sur le
  mauvais axe. Nouvelle approche (`_wrist_twist_quaternion` dans
  `hand_mapping.py`) : l'axe de visée du poignet est désormais **toujours**
  celui de l'orientation de repos actuelle du bone (donc exactement dans
  le prolongement de l'avant-bras, via `bone_mapping.bone_rest_world_rot`,
  jamais recalculé depuis les landmarks main) ; seule une **torsion pure**
  autour de cet axe fixe (pronation/supination) est dérivée de la
  direction index→auriculaire. Preuve mathématique de pureté de la
  torsion vérifiée par un test autonome. **Validé en conditions réelles**
  (la main reste alignée avec l'avant-bras, torsion toujours suivie).
  Reste soumis au même angle mort mono-caméra (voir plus bas).
- Mapping visage par correspondance de nom uniquement (pas de zone de
  mapping manuel dans l'UI pour l'instant) : fonctionne directement si le
  mesh a des shape keys nommées selon la convention ARKit, sinon les
  coefficients concernés sont simplement ignorés. Pour un rig facial à
  bones encore plus complet que celui décrit ci-dessous (paupières/lèvres
  en plusieurs segments, langue, dents...), voir la note dans
  `addon/face_mapping.py` sur le pattern recommandé (custom properties +
  drivers posés côté rig).
- **Set de bones faciaux "intermédiaire"** (~28 os, généré par
  `addon/character_builder.py` — boutons "Générer un personnage de base"
  et "Générer un rig pour le modèle sélectionné") : `jaw`/`chin`,
  `eye.L/R`, `lid.T/B.L/R`, `brow.in/mid/out.L/R`, `nose`/`nose.tip`,
  `cheek.L/R`, `mouth.corner.L/R`, `lip.T`/`lip.T.L/R`,
  `lip.B`/`lip.B.L/R`, `ear.L/R`. **Corrigé** : les coordonnées du visage
  (`JOINTS`, `FACE_SHAPE_KEYS`) utilisaient un signe Y inversé, plaçant
  tout le visage derrière la tête au lieu de devant (Y positif = "devant
  soi" dans la convention du projet, voir
  `bone_mapping._landmark_to_vector` et `foot_tip.L/R`) — repéré par un
  utilisateur ("la tête est dans le sens contraire par rapport au
  corps"), corrigé et revalidé (script autonome : plus aucun joint du
  visage en Y négatif, paires symétriques et dérivation des joints
  secondaires toujours cohérentes). **Seule une partie est réellement
  pilotée par la capture** (voir `face_mapping.py`) : `jaw` (rotation,
  `jawOpen`) et `brow.in/out.L/R` (translation,
  `browInnerUp`/`browOuterUpLeft/Right`/`browDownLeft/Right` —
  `brow.mid.L/R` n'a pas d'équivalent ARKit isolé, non piloté). Les
  autres (`eye.*`, `lid.*`, `nose*`, `cheek.*`, `chin`, `mouth.corner.*`,
  `lip.*`, `ear.*`) sont des bones de contrôle présents pour l'animation
  manuelle et une extension future du mapping, **pas encore pilotés par
  MediaPipe**. Rotation/translation locale directe, sans conjugaison par
  la pose de tête courante (voir `face_mapping.apply_jaw`/
  `apply_eyebrows`). Sur un rig personnalisé, ajoutez ces bones vous-même
  (ou associez vos propres bones à ces noms via "Associer les os par
  clic") pour bénéficier du sous-ensemble piloté. `jaw`/`brow.in/out.L/R`
  volontairement exclus des shape keys générées par "Générer un
  personnage de base" (pas de double animation de la même zone par deux
  mécanismes) — à revalider en conditions réelles. Les 23 bones de
  contrôle non pilotés sont créés avec **`use_deform` désactivé**
  (`character_builder.FACE_CONTROL_ONLY_BONES`) : sans zone de mesh qui
  leur soit propre, Blender échoue systématiquement à leur trouver une
  solution lors du "Armature Deform with Automatic Weights" (message
  "Bone Heat Weighting: failed to find solution for one or more bones")
  — les désactiver les fait ignorer par ce calcul. Réactivable à la main
  (Bone Properties > Deform) sur un bone si vous voulez l'utiliser pour
  déformer votre mesh.
- **Rig calé sur un modèle** (direct ou en 2 étapes via points de repère)
  : la mise à l'échelle (`character_builder.compute_fit_transform`) est
  une approximation grossière basée uniquement sur la hauteur (boîte
  englobante monde du mesh, axe Z) et un centrage horizontal — **aucune
  détection automatique** des articulations réelles du modèle (yeux,
  coudes, etc.), le positionnement précis reste entièrement manuel, que
  ce soit sur des bones (Edit Mode, variante directe) ou sur des points
  (Object Mode + Snap to Vertex, variante en 2 étapes) — cette dernière
  n'est qu'une manipulation différente, pas une précision automatique en
  plus. Aucune des deux variantes ne skinne jamais le mesh cible
  automatiquement.
- **Points de repère** (`character_builder.py` : `primary_joint_names`,
  `_secondary_joint_offsets`) : sur les 78 articulations du rig, 46 sont
  proposées individuellement dans le flux interactif ("primaires") et 32
  ("secondaires" — bout d'un bone de contrôle court sans signification
  anatomique propre, ex. `eye_socket.L`) sont dérivées automatiquement de
  leur point primaire associé (même décalage relatif que la position
  canonique) plutôt que positionnées à la main — sinon, ~78 points un par
  un pour un simple visage est ingérable. Cette dérivation reste une
  simple translation figée : si votre modèle a des proportions très
  différentes du personnage de référence à cet endroit précis (ex. des
  oreilles bien plus grandes), le point dérivé peut nécessiter un ajustage
  manuel après coup (Edit Mode sur l'armature générée) — pas de recalcul
  automatique tenant compte de la géométrie réelle. N'incluent pas les
  doigts (trop nombreux à positionner un par un) : les bones de doigts
  restent mis à l'échelle automatiquement, comme pour la variante directe.
- Rotation de tête (`facial_transformation_matrixes` → bone "head") :
  mapping d'axes empirique (`addon/face_mapping.py`, `_MP_TO_RIG`), pas
  formellement documenté par MediaPipe — à vérifier/ajuster si un axe
  tourne dans le mauvais sens sur votre configuration caméra.
- `tools/generate_test_face.py` attache le mesh de test au bone "head"
  via une contrainte Child Of (matrice inverse calculée au moment du
  setup — nécessite que le bone "head" soit à sa pose de repos, via
  "Réinitialiser le rig", avant de relancer le script).
- Aperçu caméra : le corps n'affiche que les 33 points MediaPipe Pose
  (pas de maillage dense disponible côté corps, contrairement au visage
  qui en a 478).
- Mains/doigts (`addon/hand_mapping.py`) : doigts en simple "aim" (pas de
  torsion). Poignet (`hand.L/R`) en **torsion pure autour de l'axe fixe de
  l'avant-bras** (pronation/supination captée, `_wrist_twist_quaternion` —
  voir la note ci-dessus sur la refonte) — garantit que la main reste
  toujours dans le prolongement visuel de l'avant-bras, quel que soit le
  bruit sur les landmarks de la main. Angle mort mono-caméra confirmé : si
  l'avant-bras pointe à peu près vers la caméra, la rotation du poignet
  autour de cet axe est quasi invisible en 2D (silhouette qui change à
  peine), donc peu/pas suivie. Fonctionne bien quand le bras est plus
  perpendiculaire à l'axe caméra (ex: bras tendu sur le côté). Limite
  géométrique du mono-caméra, pas un bug — la Phase 5 (multi-caméra) la
  résoudrait. Une limite de rotation anatomique optionnelle est
  disponible via le bouton "Limiter la rotation (poignet)" du panneau
  (ajoute une contrainte `LIMIT_ROTATION` sur l'os actif). Pas de gel sur
  confiance basse (MediaPipe Hand Landmarker ne donne pas de score de
  visibilité par point comme Pose) — une main est soit suivie entièrement,
  soit gelée entièrement si non détectée. Sensible à l'occlusion
  doigt-sur-doigt (même limite mono-caméra).
- **Limites anatomiques globales** (bouton "Ajouter des limites
  anatomiques (tout le corps)" du panneau) : ajoute une contrainte
  `LIMIT_ROTATION` avec des plages par défaut (`operators.
  ANATOMICAL_LIMITS_DEG`, empiriques) sur tous les os reconnus de
  l'armature cible (colonne vertébrale — tous les segments détectés,
  épaules, bras, jambes, tête, mâchoire) en un clic — filet de sécurité
  contre les déformations extrêmes (un membre qui part dans une
  direction impossible, mesh qui s'étire) causées par un glitch ponctuel
  de tracking (landmark bruité ou mal détecté, ex. main hors cadre
  brièvement). Complémentaire au bouton "Limiter la rotation (poignet)"
  (réglage plus fin, un seul os à la fois). Idempotent (ré-exécuter met à
  jour les mêmes contraintes plutôt que d'en empiler) ; valeurs
  ajustables ensuite dans Bone Constraint Properties si trop
  restrictives/permissives pour votre rig.
  **Insuffisant à lui seul** : confirmé en conditions réelles qu'un
  membre peut rester déformé de façon extrême même avec cette contrainte
  active, car un saut brutal d'une trame à l'autre peut très bien rester
  DANS la plage anatomique tout en étant physiquement impossible aussi
  vite (`LIMIT_ROTATION` borne la plage atteignable, pas la vitesse à
  laquelle elle est atteinte). D'où le filtre de continuité ci-dessous,
  qui s'attaque au problème à la source plutôt qu'en aval.
- **Filtre de continuité anti-saut brutal** (`bone_mapping.
  MAX_DIRECTION_CHANGE_DEG`, 90° par défaut) : `_aim_bone` compare
  désormais la nouvelle direction cible à la direction *actuelle* du
  bone (lue sur `rotation_quaternion` avant écrasement) ; si l'écart
  dépasse ce seuil en une seule trame (~1/30s), le bone est gelé cette
  trame plutôt que de suivre le saut — même logique que le gel sur
  confiance basse ou matrice non-inversible, mais déclenché par la
  *vitesse* du changement plutôt que par la confiance MediaPipe (un
  landmark peut être confiant tout en étant ponctuellement faux).
  S'applique à tous les appels de `_aim_bone` (colonne vertébrale,
  épaules/clavicules, bras, jambes). Complémentaire aux limites
  anatomiques ci-dessus (l'un borne la vitesse, l'autre la plage) — à
  valider en conditions réelles.
- Torsion buste/bassin (pivoter sans se pencher) : **tentée puis
  retirée** cette itération — le code existe (`_torso_orientation_matrix`,
  `_apply_full_rotation`, `TORSO_TWIST_DAMPING`, non utilisés actuellement)
  mais a causé plusieurs régressions (position anormale au neutre, rig
  désarticulé) malgré plusieurs correctifs, impossible à valider
  entièrement sans accès direct à Blender pour tester. `spine` utilise
  toujours le simple "aim" (2 degrés de liberté, sans torsion) comme les
  autres membres. À reprendre plus tard avec plus de recul, idéalement
  avec de meilleures données de profondeur (Phase 5, multi-caméra).
- **Colonne vertébrale à plusieurs segments** (`spine`, `spine.001`,
  `spine.002`, ... — convention Rigify/Mixamo, cas le plus courant sur un
  rig personnalisé) : détectée dynamiquement sur le rig cible
  (`bone_mapping._spine_chain_bone_names`, aucune configuration
  nécessaire) — un rig à 1 seul bone `spine` (comportement historique) ou
  à N segments est piloté automatiquement. La MÊME direction cible
  (bassin->épaules) est appliquée à chaque segment de la chaîne : la
  colonne s'incline comme un bloc rigide (tous les segments parallèles),
  pas une courbe en S répartie — volontairement simple pour éviter de
  reproduire les régressions de la torsion buste/bassin ci-dessus. L'outil
  "Associer les os par clic" propose désormais 3 rôles `spine`/
  `spine.001`/`spine.002` par défaut (à passer avec `S` si votre rig en a
  moins). Les générateurs intégrés (`character_builder.py`) créent
  toujours un rig à 1 seul bone `spine` pour l'instant — non étendu à
  plusieurs segments dans cette itération.
- Occlusion (ex: bras croisés) : limite du tracking mono-caméra elle-même
  (MediaPipe perd la capacité à distinguer les membres superposés à
  l'écran), pas quelque chose de corrigible par le mapping — voir la
  Phase 5 (multi-caméra) sur la feuille de route ci-dessous.

## Feuille de route

Ordre prévu (cahier des charges + extensions discutées en cours de route) :

1. **Phase 1 — Corps** (webcam PC) : ✅ fait.
2. **Phase 2 — Visage** (blend shapes + rotation tête) : ✅ fait
   (torsion buste/bassin abandonnée, voir limites connues).
3. **Mains/doigts** (MediaPipe Hand Landmarker, 21 points par main) :
   ✅ fait, y compris rotation du poignet (angle mort mono-caméra connu).
4. **Système de mapping configurable** (préfixe/suffixe de noms de bones,
   ex. pour un rig Rigify `DEF-upper_arm.L` — cahier des charges §7) :
   ✅ fait (préfixe/suffixe global uniquement, pas de remapping par bone
   individuel — voir limites connues).
5. **Générateurs de rig/personnage intégrés** (boutons du panneau) :
   ✅ fait (`addon/character_builder.py`) — "Générer un personnage de
   base" (armature+mesh skinné+shape keys ARKit) pour tester sans modèle,
   "Générer un rig pour le modèle sélectionné" (rig seul, calé sur la
   taille d'un modèle importé), et sa variante en 2 étapes "Points de
   repère" + "Construire le rig" (positionnement précis via des Empties
   déplaçables plutôt que des bones en Edit Mode). Set de bones faciaux
   "intermédiaire" (~28 os), voir limites connues pour le sous-ensemble
   réellement piloté par la capture. Point de départ à ajuster
   manuellement, pas un rig fini.
6. **Phase 3 — Stylisation cartoon** (post-traitement F-Curves : squash &
   stretch, amplification, timing) : pas commencé.
7. **Phase 4 — Compagnon mobile** (un téléphone comme source, via
   WebSocket local, même pipeline que la webcam PC) : pas commencé.
8. **Phase 5 — Multi-caméra** (plusieurs téléphones à angles différents,
   fusion des vues pour plus de précision — d'abord une moyenne pondérée
   par confiance, triangulation calibrée en raffinement ultérieur si
   besoin) : pas commencé, conception détaillée à faire le moment venu.
