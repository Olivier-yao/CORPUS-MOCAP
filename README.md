# CORPUS-MOCAP — Phases 1+2 (corps + visage, webcam PC)

Addon Blender de capture de mouvement. Couvre pour l'instant : capture du
squelette (33 points MediaPipe Pose) et du visage (MediaPipe Face
Landmarker, 52 coefficients blend shapes ARKit) via webcam PC, lissage
corps (One Euro Filter), application temps réel sur un rig + un mesh à
shape keys, enregistrement synchronisé en Actions Blender. Pas de
stylisation cartoon, pas de téléphone (voir
`CORPUS-MOCAP_cahier-des-charges.md`).

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

### 4. Rig et visage de test

Si vous n'avez pas encore de personnage rigué, générez les deux :

1. Ouvrir Blender, onglet **Scripting**.
2. Ouvrir `tools/generate_test_rig.py`, cliquer **Run Script** → armature
   `CORPUS_MOCAP_TestRig` (T-pose).
3. Ouvrir `tools/generate_test_face.py`, cliquer **Run Script** → mesh
   `CORPUS_MOCAP_TestFace` (sphère à 10 shape keys nommées selon la
   convention ARKit : `jawOpen`, `eyeBlinkLeft`, `mouthSmileLeft`, etc.).

### 5. Addon

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
   ARKit pour être reconnu automatiquement).
4. Ajuster **Stabilité** si besoin (léger = plus réactif, fort = plus lissé).
5. Cliquer **● Enregistrer la performance** — la webcam s'active côté
   `capture_server`, le rig et/ou le visage doivent suivre vos mouvements
   en temps réel.
6. Cliquer à nouveau pour arrêter : une Action `CORPUS_MOCAP_Take` (corps)
   et, si un mesh visage était sélectionné, `CORPUS_MOCAP_Face_Take` (sur
   le datablock Key du mesh) sont créées avec les keyframes de la prise,
   sur la même timeline.

## Limites connues

- Mapping d'os en dur pour la convention de nommage de
  `tools/generate_test_rig.py` (`addon/bone_mapping.py`). Un système de
  mapping configurable par personnage viendra dans une itération
  ultérieure (cahier des charges §7).
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
  qui en a 478). Le tracking mains/doigts n'est pas encore implémenté
  (voir feuille de route).
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
2. **Phase 2 — Visage** (blend shapes + rotation tête) : en cours de
   stabilisation (torsion buste/bassin, lissage, précision).
3. **Phase 3 — Stylisation cartoon** (post-traitement F-Curves : squash &
   stretch, amplification, timing) : pas commencé.
4. **Phase 4 — Compagnon mobile** (un téléphone comme source, via
   WebSocket local, même pipeline que la webcam PC) : pas commencé.
5. **Phase 5 — Multi-caméra** (plusieurs téléphones à angles différents,
   fusion des vues pour plus de précision — d'abord une moyenne pondérée
   par confiance, triangulation calibrée en raffinement ultérieur si
   besoin) : pas commencé, conception détaillée à faire le moment venu.
6. **Mains/doigts** (MediaPipe Hand Landmarker, 21 points par main) :
   pas commencé, en attente de la stabilisation Phase 2.
