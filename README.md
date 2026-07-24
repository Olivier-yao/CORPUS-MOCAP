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

Si vous n'avez pas encore de personnage rigué, générez le tout, **dans
cet ordre** (les mains s'ajoutent au rig de corps déjà créé) :

1. Ouvrir Blender, onglet **Scripting**.
2. Ouvrir `tools/generate_test_rig.py`, cliquer **Run Script** → armature
   `CORPUS_MOCAP_TestRig` (T-pose).
3. Ouvrir `tools/generate_test_hands.py`, cliquer **Run Script** → ajoute
   les bones de doigts (`thumb.01.L`, `index.02.R`, etc.) à cette même
   armature.
4. Ouvrir `tools/generate_test_face.py`, cliquer **Run Script** → mesh
   `CORPUS_MOCAP_TestFace` (sphère à 10 shape keys nommées selon la
   convention ARKit : `jawOpen`, `eyeBlinkLeft`, `mouthSmileLeft`, etc.),
   attaché au bone "head" du rig.

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
- Rotation du poignet (`hand.L/R`) : réactivée après avoir trouvé un
  amortissement de profondeur manquant sur la référence de torsion
  (`right_raw` dans `_hand_orientation_matrix`) — à revalider en
  conditions réelles, notamment sur un rig externe (elle avait causé un
  chaos des doigts en cascade avant ce correctif).
- Mapping visage par correspondance de nom uniquement (pas de zone de
  mapping manuel dans l'UI pour l'instant) : fonctionne directement si le
  mesh a des shape keys nommées selon la convention ARKit, sinon les
  coefficients concernés sont simplement ignorés. Pour un rig facial à
  bones (pas à shape keys), voir la note dans `addon/face_mapping.py` sur
  le pattern recommandé (custom properties + drivers posés côté rig).
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
  torsion). Poignet (`hand.L/R`) en rotation complète 3-DOF (pronation/
  supination captée, `_hand_orientation_matrix`) — **validé fonctionnel**,
  mais avec un angle mort mono-caméra confirmé : si l'avant-bras pointe à
  peu près vers la caméra, la rotation du poignet autour de cet axe est
  quasi invisible en 2D (silhouette qui change à peine), donc peu/pas
  suivie. Fonctionne bien quand le bras est plus perpendiculaire à l'axe
  caméra (ex: bras tendu sur le côté). Limite géométrique du mono-caméra,
  pas un bug — la Phase 5 (multi-caméra) la résoudrait. Pas de gel sur
  confiance basse (MediaPipe Hand Landmarker ne donne pas de score de
  visibilité par point comme Pose) — une main est soit suivie entièrement,
  soit gelée entièrement si non détectée. Sensible à l'occlusion
  doigt-sur-doigt (même limite mono-caméra).
- Torsion buste/bassin (pivoter sans se pencher) : **tentée puis
  retirée** cette itération — le code existe (`_torso_orientation_matrix`,
  `_apply_full_rotation`, `TORSO_TWIST_DAMPING`, non utilisés actuellement)
  mais a causé plusieurs régressions (position anormale au neutre, rig
  désarticulé) malgré plusieurs correctifs, impossible à valider
  entièrement sans accès direct à Blender pour tester. `spine` utilise à
  nouveau le simple "aim" (2 degrés de liberté, sans torsion) comme les
  autres membres. À reprendre plus tard avec plus de recul, idéalement
  avec de meilleures données de profondeur (Phase 5, multi-caméra).
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
5. **Phase 3 — Stylisation cartoon** (post-traitement F-Curves : squash &
   stretch, amplification, timing) : pas commencé.
6. **Phase 4 — Compagnon mobile** (un téléphone comme source, via
   WebSocket local, même pipeline que la webcam PC) : pas commencé.
7. **Phase 5 — Multi-caméra** (plusieurs téléphones à angles différents,
   fusion des vues pour plus de précision — d'abord une moyenne pondérée
   par confiance, triangulation calibrée en raffinement ultérieur si
   besoin) : pas commencé, conception détaillée à faire le moment venu.
