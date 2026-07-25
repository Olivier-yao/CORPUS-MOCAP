# CORPUS-MOCAP — corps + visage + mains (webcam PC, téléphone(s), multi-caméra)

Addon Blender de capture de mouvement. Couvre : capture du squelette (33
points MediaPipe Pose, via webcam PC ou téléphone), du visage (MediaPipe
Face Landmarker, 52 coefficients blend shapes ARKit, webcam PC
uniquement), des mains (MediaPipe Hand Landmarker, 21 points articulés
par main, webcam PC uniquement), lissage (One Euro Filter), application
temps réel sur un rig + un mesh à shape keys, enregistrement synchronisé
en Actions Blender, un post-traitement optionnel "style cartoon"
(amplification, squash & stretch, timing — voir Utilisation), et un
mode **multi-caméra à rôles** (plusieurs webcams/téléphones simultanés,
chacun dédié à une partie du rig — voir Installation §4ter). Le cahier
des charges original (`CORPUS-MOCAP_cahier-des-charges.md`) est
entièrement couvert ; la multi-caméra est une extension au-delà, voir
la feuille de route ci-dessous.

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

### 4bis. Optionnel — compagnon mobile (téléphone comme source)

Alternative à la webcam PC pour le corps : `requirements.txt` installe
déjà les paquets `websockets` et `cryptography` nécessaires. Aucun
modèle supplémentaire à télécharger — MediaPipe.js (qui tourne dans le
navigateur du téléphone) réutilise directement les fichiers `.task`
déjà en place, servis par `capture_server` lui-même. Ce mode
(`--source phone`, un seul téléphone, hors fichier de configuration)
reste corps uniquement ; **un téléphone dédié au visage (voire au
visage + au corps ensemble) est possible via le mode multi-caméra à
rôles, voir §4ter** — les mains restent indisponibles depuis un
téléphone dans tous les cas.

```powershell
python server.py --source phone
```

Le terminal affiche deux adresses **en HTTPS** (certificat auto-signé —
voir `tls_cert.py` — généré à chaque démarrage, nécessaire pour l'accès
caméra sur Safari iOS qui, contrairement à Chrome, n'a aucun réglage de
contournement pour une page HTTP simple), du type :

```
1. https://192.168.1.64:8766/  (WebSocket — accepter puis fermer)
2. https://192.168.1.64:8080/  (page principale)
```

**Ni l'une ni l'autre n'a de `?ws=...` à la fin** (la page déduit
l'adresse WebSocket de sa propre adresse) — volontairement plus court à
recopier sur le téléphone, pour éviter de mélanger une ancienne adresse
avec la nouvelle (déjà arrivé en test réel : IP différente entre la page
et un `?ws=` recopié d'une session précédente, cause d'un échec de
connexion silencieux).

Sur le téléphone, **sur le même réseau WiFi que ce PC**, dans cet
ordre — **toujours les deux adresses fraîchement affichées, jamais une
notée lors d'une session précédente** :
1. Ouvrez d'abord l'adresse **1** (le port WebSocket) : le navigateur
   affiche un avertissement de sécurité ("Cette connexion n'est pas
   privée" / "Your connection is not private") — c'est normal, le
   certificat est auto-signé, pas délivré par une autorité reconnue.
   Acceptez-le ("Avancé > Continuer" / "Afficher les détails > Visiter
   ce site") ; la page qui s'affiche ensuite peut être vide ou une
   erreur, c'est sans importance, seul le fait d'avoir accepté compte.
2. Ouvrez ensuite l'adresse **2** (la page principale), acceptez le même
   avertissement de sécurité, puis cliquez "Démarrer la caméra"
   (autorisez l'accès caméra). Le champ "Serveur" affiché sur la page
   doit correspondre exactement à l'IP de l'adresse **1** — s'il diffère,
   fermez l'onglet et rouvrez les deux adresses depuis le terminal.

L'étape 1 est nécessaire séparément de l'étape 2 : le port WebSocket
(8766) est une origine différente du port de la page (8080) aux yeux du
navigateur, même avec le même certificat — sans l'avoir déjà acceptée,
la tentative de connexion WebSocket depuis la page échoue silencieusement.

Le squelette détecté sur le téléphone est envoyé au PC. **La fenêtre
d'aperçu du PC affiche le squelette sur fond noir, pas l'image de la
caméra du téléphone** — voulu : le téléphone n'envoie jamais son flux
vidéo, seulement les points déjà détectés (voir Limites connues). Voir
`capture_server/phone_client/` (page web) et
`capture_server/phone_server.py` (serveur HTTPS + WSS côté PC).
**Validé en conditions réelles sur Chrome Android** (le flux HTTP +
réglage Chrome utilisé lors de ce test a depuis été remplacé par le
passage en HTTPS ci-dessus, non re-testé sous cette forme) — Safari iOS
pas encore validé avec ce correctif HTTPS (motivé par le blocage
observé sur Safari, mais l'accès caméra effectif via HTTPS reste à
confirmer).

Si une adresse ne répond pas (page qui charge indéfiniment,
`ERR_CONNECTION_TIMED_OUT`) : l'IP de la machine change parfois entre
deux lancements (renouvellement DHCP du WiFi) — **redémarrez
`server.py --source phone`** pour réafficher les adresses actuelles
plutôt que de réutiliser une ancienne adresse notée précédemment.
`Test-NetConnection -ComputerName <ip> -Port 8080` en PowerShell (champ
`SourceAddress` du résultat = IP réelle actuelle de la machine) aide à
diagnostiquer un décalage d'adresse.

### 4ter. Optionnel — multi-caméra à rôles (plusieurs webcams et/ou téléphones)

Extension au-delà du cahier des charges original : au lieu d'une seule
source (webcam OU téléphone), **plusieurs caméras simultanées, chacune
dédiée à un rôle** — ex. une webcam cadrée sur le visage qui ne pilote
que le visage, un téléphone posé plus loin qui ne pilote que le corps.
**Pas de fusion/triangulation multi-angle du même point** (ce n'est pas
l'objectif ici, voir Limites connues) : chaque caméra alimente
uniquement le(s) type(s) de message pour lequel/lesquels elle est
configurée (corps/visage/mains), le protocole vers l'addon
(`protocol.py`) est inchangé. Nombre de caméras illimité, webcams et
téléphones combinables librement.

1. Copiez `capture_server/cameras.example.json` (ex. vers `cameras.json`)
   et adaptez-le à votre matériel :

   ```json
   {
     "cameras": [
       {"name": "corps_webcam",    "source": "webcam:0", "pose": true},
       {"name": "corps_telephone", "source": "phone",    "pose": true},
       {"name": "visage",          "source": "phone",    "face": true}
     ]
   }
   ```

   `"source"` : `"webcam:<index>"` (index OpenCV, comme `--camera`) ou
   `"phone"`. `"pose"`/`"face"`/`"hands"` (bool) : quels modèles
   MediaPipe tourner sur cette caméra — **une source `"phone"` peut
   avoir `"pose"` et/ou `"face"` à `true`** (détection dans le
   navigateur, voir §4bis), mais pas encore `"hands"` (non implémenté
   côté téléphone) ; une configuration invalide est rejetée au
   démarrage avec un message d'erreur explicite (nom dupliqué, index
   webcam réutilisé, rôle vide, `hands` sur un téléphone...).
   `"preview"` (bool, défaut `true`) : fenêtre d'aperçu OpenCV avec le
   flux vidéo réel (webcam uniquement). **Chaque caméra `"phone"` a en
   plus, automatiquement (non désactivable via `"preview"`), sa propre
   fenêtre PC dédiée** — squelette (corps) et/ou maillage (visage) sur
   fond noir, comme décrit en §4bis, en complément de l'aperçu sur
   l'écran du téléphone lui-même. Toutes les fenêtres d'aperçu (webcam
   et téléphone) sont redimensionnables.

2. Lancez :

   ```powershell
   python server.py --cameras cameras.json
   ```

   Ignore `--source`/`--camera`/`--no-face`/`--no-hands` (chaque caméra
   du fichier définit son propre rôle). Si le fichier contient une ou
   plusieurs caméras `"phone"`, le terminal affiche une adresse par
   caméra téléphone (voir §4bis) — chaque téléphone doit ouvrir **son
   adresse à lui** (`?cam=<name>` dans l'URL), pas celle d'un autre.

3. Une fenêtre d'aperçu OpenCV s'ouvre par webcam configurée (nommée
   d'après le `"name"` de la caméra), en plus de l'aperçu affiché sur
   l'écran de chaque téléphone.

Non testé avec du vrai matériel multi-caméra au moment de l'écriture
(validé par scripts autonomes : chargement/validation de configuration,
gestion de plusieurs téléphones simultanés avec créneaux/filtres
indépendants, boucle de fusion complète bout en bout via de vraies
connexions WebSocket + TCP, gestion propre d'une caméra introuvable) —
voir Limites connues.

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
    canonique approximative pour tout point non encore placé), puis
    **masque automatiquement** la collection `CORPUS_MOCAP_RigPoints`
    dans la vue 3D (leur rôle est terminé) — réaffichable via l'icône œil
    dans l'Outliner si vous voulez ajuster un point et reconstruire.
    Relancer "1. Points de repère" supprime et recrée tout le jeu de
    points (perd tout déplacement déjà fait).

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

1. Lancer le serveur : `python capture_server/server.py` (webcam PC,
   comportement par défaut) ou `python capture_server/server.py --source
   phone` (téléphone comme source — voir Installation §4bis). Le
   terminal affiche "en attente de l'addon Blender...".
2. Dans Blender, ouvrir le N-panel (touche `N` dans la Vue 3D) > onglet
   **CORPUS-MOCAP**. Le sélecteur **Source** (Webcam PC / Téléphone) en
   haut de l'encart connexion est purement indicatif — il rappelle
   comment `capture_server` a été lancé, la connexion TCP reçue par
   l'addon est identique quelle que soit la source réelle.
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
8. **Optionnel — Style cartoon** (cahier des charges Module 4) : réglez le
   curseur **Intensité** (0 = aucun effet) puis cliquez **"Appliquer le
   style cartoon"** — amplifie les mouvements, ajoute du squash & stretch
   sur les os principaux (colonne, bras, jambes — échelle dynamique selon
   la vitesse du mouvement) et accentue le timing (easing). **Jamais
   destructif** : crée une copie de l'Action (`..._Cartoon`) et l'assigne
   comme Action active, la capture brute reste intacte et retrouvable
   dans l'Action Editor. Cliquer plusieurs fois avec des intensités
   différentes crée une nouvelle copie à chaque fois (pas de composition
   d'effets) tant que la capture brute reste l'Action active au moment du
   clic — si vous repartez d'une version déjà stylisée, ré-assignez la
   capture brute avant de recliquer. Voir `addon/cartoon_style.py`.

## Limites connues

- **Régénérer un rig pendant un enregistrement en cours** : les boutons
  "Générer un personnage de base", "Générer un rig pour le modèle
  sélectionné" et "Construire le rig depuis les points" suppriment et
  recréent l'objet armature (et son mesh pour le premier) — si l'un
  d'eux est utilisé pendant qu'un enregistrement cible cette même
  armature, la référence Python détenue par la capture devient invalide
  (`ReferenceError: StructRNA of type Object has been removed`),
  provoquant un crash de la boucle modale. **Corrigé sur deux plans** :
  ces trois boutons sont désormais désactivés (grisés) tant qu'un
  enregistrement est en cours (`is_recording`) ; et la boucle de capture
  vérifie à chaque trame que l'armature/le mesh visage cible existent
  encore, et s'arrête proprement (message d'avertissement, pas de
  crash) si ce n'est plus le cas — filet de sécurité pour toute autre
  cause de suppression (Outliner, Undo...), pas seulement ces boutons.
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
  doigts (trop nombreux à positionner un par un) : leur forme (longueur/
  écartement des segments) reste toujours celle du personnage de
  référence, mais leur point d'ancrage **suit désormais la position
  résolue de "wrist.L/R"** (`_finger_bones` prend le joint "hand_tip.L/R"
  déjà calculé plutôt que des coordonnées fixes) — **corrigé** : avant ce
  correctif, déplacer le point du poignet loin de sa position canonique
  laissait les doigts "flotter" à l'ancien emplacement au lieu de suivre
  la main (repéré par un utilisateur, revalidé par script autonome avec
  un déplacement extrême du poignet).
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
  épaules, bras, jambes, tête, mâchoire, **doigts** — segments MCP/PIP/
  DIP par doigt, pouce traité à part car plus mobile/opposable) en un
  clic — filet de sécurité contre les déformations extrêmes (un membre
  ou un doigt qui part dans une direction impossible, mesh qui s'étire)
  causées par un glitch ponctuel
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
  l'écran) — la multi-caméra à rôles (§4ter) ne résout PAS ce cas
  précis : une seule caméra reste responsable de chaque rôle (corps/
  visage/mains), pas de fusion de plusieurs angles du même point pour
  compenser une occlusion sur l'un d'eux. Une vraie triangulation
  multi-angle reste un travail futur non commencé.
- **Multi-caméra à rôles** (`capture_server/camera_config.py`,
  `run_multi_camera` dans `server.py`) : non testé avec du vrai matériel
  au moment de l'écriture — validé uniquement par scripts autonomes
  (chargement/validation de configuration avec 7 cas d'erreur couverts,
  plusieurs téléphones simultanés avec créneaux/filtres indépendants,
  boucle de fusion complète bout en bout via de vraies connexions
  WebSocket + TCP avec un faux client Blender, gestion propre d'une
  caméra webcam introuvable). Si **plusieurs caméras portent le rôle
  "face" ou "hands"**, la politique de fusion reste volontairement
  simpliste — **"la plus récemment mise à jour gagne"** (`_pick_freshest`),
  pas de moyenne pondérée ni de triangulation.

  Le rôle **"pose"** (corps), lui, utilise `PoseSourceFusion` (pas
  `_pick_freshest`) depuis que webcam PC + téléphone toutes deux en
  `"pose": true` ont produit un tremblement/désync constaté en test réel
  (bascule quasi à chaque trame entre deux points de vue physiquement
  différents). `PoseSourceFusion` choisit la caméra la plus **confiante**
  (moyenne de `visibility` MediaPipe sur les 33 points), avec hystérésis
  (ne bascule que si l'écart de confiance dépasse
  `POSE_SWITCH_CONFIDENCE_MARGIN`) et lissage sur `POSE_SWITCH_BLEND_FRAMES`
  trames lors d'une bascule effective — reste une heuristique 2D par
  caméra, **pas une triangulation 3D** (demanderait un calibrage caméra —
  position/angle relatifs — qui n'existe pas dans le projet). Validé par
  script autonome (hystérésis, lissage progressif, disparition d'une
  caméra, aucune caméra visible) — **pas encore testé en conditions
  réelles avec deux caméras physiques**.

  Chaque caméra webcam tourne dans son propre
  thread avec sa propre fenêtre d'aperçu OpenCV (`cv2.imshow`/
  `cv2.waitKey` appelés depuis ce thread) — fonctionne sous Windows, mais
  certaines plateformes (notamment macOS) exigent que les fonctions
  d'interface graphique OpenCV tournent sur le thread principal ; non
  vérifié sur ces plateformes. La boucle de fusion tourne à ~30 Hz fixe,
  indépendamment du framerate réel de chaque caméra individuelle.
- **Visage sur téléphone** (`phone_client/index.html`, `FaceLandmarker`
  MediaPipe.js) : comme le reste de la multi-caméra, validé uniquement
  par scripts autonomes (message "face" reçu/filtré/routé par créneau
  nommé, intégration bout en bout avec un faux client Blender) —
  **jamais testé avec un vrai téléphone**. Point à vérifier en premier
  lors du premier test réel : l'orientation de `head_rotation` — la
  matrice 4x4 renvoyée par `facialTransformationMatrixes` en JS est
  supposée column-major (`extractHeadRotation` dans index.html), par
  analogie avec `extract_head_rotation` côté Python (webcam), mais cette
  hypothèse n'a pas été vérifiée empiriquement ; si la tête tourne dans
  le mauvais sens une fois pilotée dans Blender, c'est le premier
  suspect. Un téléphone peut cumuler `"pose"` et `"face"` dans la
  configuration (les deux détecteurs tournent en parallèle sur le même
  flux caméra) mais cela n'a pas non plus été testé en conditions
  réelles — vraisemblablement plus lourd pour un téléphone ancien (voir
  la limite iPhone SE ci-dessous).
- **Style cartoon** (`addon/cartoon_style.py`) : constantes empiriques
  (`AMPLIFICATION_MAX`, `SQUASH_STRETCH_VELOCITY_DEG_PER_FRAME`,
  `SQUASH_STRETCH_MAX`, `EASING_MIN/MAX_HANDLE_FRACTION`), non testées en
  conditions réelles (validées uniquement par script autonome sur la
  logique mathématique — amplification/clamp de quaternion, mapping
  vitesse→étirement avec préservation approximative du volume). Le
  squash & stretch ne s'applique qu'aux os "principaux" (colonne, bras,
  jambes — `SQUASH_STRETCH_BONE_PREFIXES`), pas aux mains/doigts ni au
  visage. Le easing (poignées bezier) peut nécessiter un réglage plus
  fin après un premier test visuel — à ajuster comme les autres
  constantes empiriques du projet si le rendu ne convient pas.
- **Compagnon mobile** (`capture_server/phone_client/`,
  `phone_server.py`) : **validé en conditions réelles sur Chrome
  Android (HTTPS)** — **Safari iOS toujours pas confirmé**, malgré deux
  correctifs successifs sur le certificat (`tls_cert.py`) après blocage
  répété en test réel (boucle sur l'avertissement de sécurité, jamais
  d'accès à la page même après avoir cliqué "visiter ce site web") :
  (1) durée de validité ramenée à 2 jours — iOS/Safari rejette
  silencieusement tout certificat serveur dépassant 825 jours, exigence
  Apple depuis iOS 13 ; (2) ajout des extensions `BasicConstraints`
  (CA=false), `KeyUsage` et `ExtendedKeyUsage` (serverAuth) — toutes
  deux listées dans les exigences Apple publiées pour les certificats
  TLS serveur (support.apple.com/en-us/HT210176), absentes du
  certificat initial. Aucune des deux incidence sur la régénération à
  chaque démarrage (toujours < 825 jours, extensions ajoutées
  systématiquement). **Reste à reconfirmer sur un vrai iPhone** — si le
  blocage persiste malgré ces deux correctifs, il faudra creuser plus
  précisément côté Safari (logs iOS, ou tester avec un certificat émis
  par une AC locale de confiance type mkcert plutôt qu'auto-signé).
  Un troisième piège déjà corrigé : les URLs affichées par
  `phone_server.py` **n'incluent plus le paramètre `?ws=...`** — la
  page déduit l'adresse WebSocket de son propre nom d'hôte, pour éviter
  de mélanger une IP différente entre la page et ce paramètre lors d'un
  copié-collé (déjà arrivé en test
  réel, cause d'un échec de connexion). Le mode `--source phone` (un
  seul téléphone, hors configuration multi-caméra) reste corps
  uniquement ; le visage est disponible depuis un téléphone via le
  fichier de configuration (§4ter, voir aussi la limite "Visage sur
  téléphone" ci-dessus) — les mains restent indisponibles depuis un
  téléphone dans tous les cas. Le navigateur n'envoie que les landmarks/
  coefficients déjà détectés, jamais de flux vidéo, pour rester léger
  sur le WiFi ; **la fenêtre d'aperçu du PC affiche donc le squelette
  sur fond noir, jamais l'image de la caméra du téléphone** —
  comportement voulu, pas une limitation à corriger. `getUserMedia`
  (accès caméra) exige un contexte sécurisé —
  servi en **HTTPS** (certificat auto-signé, `tls_cert.py`, régénéré à
  chaque démarrage) plutôt qu'en HTTP simple : une première version
  utilisait un flag de contournement Chrome
  (`chrome://flags/#unsafely-treat-insecure-origin-as-secure`), qui
  fonctionnait sur Android mais n'a **aucun équivalent sur Safari iOS**
  (confirmé en test réel — `getUserMedia` restait `undefined`) ; le
  passage en HTTPS élimine le besoin de ce réglage navigateur-spécifique
  au prix d'un avertissement de certificat auto-signé à accepter
  manuellement une fois par navigateur/appareil (voir Installation
  §4bis) — normal pour un serveur de développement local, pas une
  faille. Le port WebSocket (8766) et le port HTTP (8080) étant deux
  origines distinctes pour le navigateur, l'avertissement doit être
  accepté séparément sur chacun (visiter l'adresse WebSocket une fois
  avant la page principale). L'IP locale de la machine peut changer
  entre deux lancements de `server.py --source phone` (renouvellement
  DHCP du WiFi, déjà rencontré en test réel) : toujours utiliser les
  adresses fraîchement affichées au démarrage, pas une adresse notée
  lors d'une session précédente — sinon la connexion échoue
  silencieusement (`ERR_CONNECTION_TIMED_OUT`, diagnosticable via
  `Test-NetConnection`). Le sélecteur "Source" du
  panneau addon est purement indicatif (voir Utilisation) : il ne pilote
  aucune logique réelle côté addon, seulement `capture_server` (démarré
  séparément) détermine la source effective.

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
   stretch, amplification, timing) : ✅ fait (`addon/cartoon_style.py`,
   bouton "Appliquer le style cartoon") — voir Utilisation et Limites
   connues.
7. **Phase 4 — Compagnon mobile** (un téléphone comme source, via
   WebSocket local, même pipeline que la webcam PC) : ✅ fait,
   **validé en conditions réelles sur Chrome Android** (Safari iOS pas
   encore confirmé avec le passage en HTTPS, motivé par un blocage
   observé dessus), **corps seul** — page web
   (`capture_server/phone_client/`, MediaPipe.js dans le navigateur du
   téléphone, pas d'app native à installer) + pont HTTPS/WSS côté PC
   (`capture_server/phone_server.py`, `server.py --source phone`).
   Visage/mains pas encore disponibles depuis le téléphone — voir
   Limites connues (réglage navigateur
   nécessaire pour l'accès caméra, IP locale à reprendre au démarrage du
   serveur).
8. **Phase 5 — Multi-caméra** : reconçue en cours de route — pas une
   fusion/triangulation de plusieurs angles du même point (l'idée
   initiale ci-dessus, abandonnée), mais **une caméra dédiée par rôle**
   (corps/visage/mains), nombre illimité, webcams et téléphones
   combinables (voir Installation §4ter) : ✅ fait, non testé avec du
   vrai matériel multi-caméra (voir Limites connues). Une vraie fusion
   multi-angle de plusieurs caméras sur le MÊME rôle (pour combler une
   occlusion, ex., ce qui demanderait un calibrage caméra) reste un
   travail futur non commencé — la politique actuelle si deux caméras
   partagent le rôle "face" ou "hands" est simpliste ("la plus récente
   gagne") ; le rôle "pose" utilise depuis peu une sélection par
   confiance avec transition lissée, pas une vraie triangulation non
   plus (voir Limites connues).
