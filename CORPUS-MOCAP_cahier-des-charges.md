# CORPUS-MOCAP
## Cahier des charges technique — Addon Blender de capture de mouvement (webcam / téléphone)

**Auteur du projet :** SNTRX (Éditions Prime)
**Destiné à :** implémentation par Claude Code
**Version du document :** 1.0

---

## 1. Objectif du projet

Créer un addon Blender unique qui permet de capturer les mouvements corporels ET les expressions faciales de l'utilisateur en temps réel (via webcam PC ou caméra de téléphone), de nettoyer automatiquement le signal, de l'appliquer sur un rig de personnage 3D, puis d'exagérer le résultat pour matcher un style cartoon/semi-réaliste. Le tout en une seule session d'enregistrement, sans étapes manuelles intermédiaires.

**Cas d'usage principal :** animer rapidement le personnage "Le Stratège" (REF-001, cartoon semi-réaliste, adolescent, hoodie orange, bandeau DBBSO) et d'autres personnages similaires pour des contenus TikTok/vidéos courtes, sans matériel de mocap professionnel.

---

## 2. Architecture générale

```
[Source vidéo]                [Traitement]                [Sortie]
                                                            
Webcam PC        ──┐                                       
                    ├──► MediaPipe (Pose + Face Mesh) ──► Filtrage/lissage ──► Mapping sur rig Blender ──► Stylisation cartoon (optionnelle) ──► Action Blender exportable
Téléphone (réseau) ─┘         (33 pts corps                (One Euro Filter)
                                + 468 pts visage)
```

Deux modes de source, un seul pipeline de traitement derrière.

---

## 3. Modules fonctionnels

### Module 1 — Capture
- Intégrer **MediaPipe Pose** (squelette, 33 points) et **MediaPipe Face Mesh** (visage, 468 points) en parallèle, dans la même session.
- Sélecteur de source dans l'UI de l'addon :
  - **Webcam PC** (capture directe via OpenCV)
  - **Téléphone** (réception via serveur WebSocket local — voir Module 5)
- Prévisualisation en direct dans une fenêtre annexe (flux vidéo + squelette détecté superposé), pour vérifier le cadrage avant l'enregistrement.

### Module 2 — Nettoyage du signal (temps réel)
- Appliquer un **One Euro Filter** sur chaque point de tracking (corps + visage) avant transmission au rig, pour réduire le tremblement ("jitter") inhérent à la capture webcam.
- Détection de perte de tracking (ex: main sortie du cadre) : geler la dernière position connue plutôt que de transmettre une donnée aberrante.
- Un seul contrôle utilisateur exposé : curseur **"Stabilité"** (léger → fort), qui ajuste les paramètres du filtre en interne (pas de configuration technique exposée à l'utilisateur final).

### Module 3 — Mapping et application sur le rig
- Mapping automatique des points de pose détectés vers les os du rig (rotation/position des bones correspondants : épaules, coudes, poignets, hanches, genoux, chevilles, colonne).
- Mapping des points du visage vers les **blend shapes** du rig facial (si le rig cible en possède — prévoir un système de correspondance configurable par l'utilisateur si les noms de shape keys diffèrent d'un personnage à l'autre).
- Capture corps + visage **synchronisée sur la même timeline**, en un seul enregistrement.
- Bouton unique dans l'UI : **"Enregistrer la performance"** → démarre/arrête l'enregistrement et génère directement une Action Blender (keyframes sur les os et les shape keys).

### Module 4 — Stylisation cartoon (post-traitement optionnel)
- Case à cocher **"Style cartoon"** dans l'UI.
- Si activée, applique un post-traitement sur les courbes d'animation (F-Curves) après capture :
  - Amplification de l'amplitude des mouvements (facteur réglable)
  - Ajout de squash & stretch basique sur les os principaux (échelle dynamique selon la vitesse du mouvement)
  - Ajustement du timing (easing plus marqué)
- Si désactivée, l'animation reste fidèle à la capture brute (réalisme 1:1).

### Module 5 — Compagnon mobile (optionnel, phase 2)
- Application mobile légère (Android prioritaire) qui :
  - Active la caméra du téléphone
  - Fait tourner MediaPipe directement sur le téléphone (ou transmet le flux vidéo brut au PC selon les capacités de calcul retenues — à trancher en phase de dev)
  - Envoie les données de tracking au serveur local via WebSocket (réseau WiFi local, pas de cloud)
- Le PC (Blender + addon) fait tourner un petit serveur WebSocket qui reçoit ces données et les injecte dans le même pipeline que la webcam.

---

## 4. Stack technique recommandée

| Composant | Technologie |
|---|---|
| Addon Blender | Python (bpy), Blender 4.x |
| Détection de pose/visage | MediaPipe (Pose + Face Mesh) |
| Capture webcam | OpenCV |
| Lissage | One Euro Filter (implémentation Python légère) |
| Communication téléphone → PC | WebSocket (réseau local, sans dépendance cloud) |
| App mobile (phase 2) | À déterminer (Kotlin/Android natif ou solution cross-platform) |

---

## 5. Interface utilisateur (panneau addon Blender)

Un seul panneau latéral (N-panel) avec :
1. Sélecteur de source : `Webcam` / `Téléphone`
2. Bouton `Aperçu caméra`
3. Curseur `Stabilité` (lissage)
4. Case à cocher `Style cartoon` + curseur d'intensité (si activée)
5. Bouton principal `● Enregistrer la performance`
6. Zone de mapping manuel (associer les shape keys du personnage si détection automatique échoue)

---

## 6. Priorités de développement (ordre suggéré)

1. **Phase 1 — Corps only, webcam PC** : capture pose + mapping sur rig + lissage basique. Valider que le concept fonctionne de bout en bout avant d'ajouter la complexité.
2. **Phase 2 — Ajout du visage** (Face Mesh + blend shapes).
3. **Phase 3 — Stylisation cartoon** (post-traitement des F-Curves).
4. **Phase 4 — Compagnon mobile** (source téléphone via WebSocket).

---

## 7. Points d'attention techniques

- Vérifier la compatibilité de MediaPipe avec la version de Python embarquée dans Blender (Blender a son propre interpréteur Python interne — peut nécessiter d'installer les dépendances dans le Python de Blender spécifiquement, ou de faire tourner un processus externe qui communique avec l'addon via socket/pipe).
- Le rig cible doit avoir une hiérarchie d'os standard reconnaissable (prévoir un système de mapping configurable plutôt qu'un mapping figé, pour que l'addon fonctionne sur différents personnages, pas uniquement "Le Stratège").
- Prévoir une gestion propre des erreurs de tracking (perte de caméra, mauvais éclairage, personnage partiellement hors cadre).
