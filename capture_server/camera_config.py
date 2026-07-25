"""Configuration multi-caméra (Phase 5, extension au-delà du cahier des
charges original — voir README, feuille de route).

Contrairement à une fusion/triangulation de plusieurs caméras filmant le
même point (approche initialement envisagée pour la "Phase 5"), le
besoin exprimé est plus simple : chaque caméra a un **rôle dédié** — par
exemple une caméra cadrée sur le visage pilote uniquement le visage, une
caméra cadrée sur le corps entier pilote uniquement le corps. Aucune
fusion nécessaire : chaque caméra alimente son propre sous-ensemble du
protocole existant (messages "frame"/"face"/"hands", voir protocol.py),
inchangé. Un fichier de configuration JSON (pas d'arguments en ligne de
commande — le nombre de caméras n'est volontairement pas limité, une
liste dans un fichier passe à l'échelle, pas des flags `--camera-1`,
`--camera-2`...) liste les caméras et ce que chacune capture.

Exemple (voir aussi cameras.example.json) :

{
  "cameras": [
    {"name": "corps",  "source": "webcam:0", "pose": true},
    {"name": "visage", "source": "webcam:1", "face": true},
    {"name": "mains",  "source": "phone",    "pose": true}
  ]
}

- "source": "webcam:N" (index OpenCV) ou "phone" (voir phone_server.py —
  le téléphone se connecte à une adresse taguée ?cam=<name> pour indiquer
  quelle entrée de la configuration il représente).
- "pose"/"face"/"hands" (bool, défaut False) : quels modèles MediaPipe
  tourner sur cette caméra — donc quel(s) type(s) de message elle
  alimente. **Une source "phone" peut avoir "pose" et/ou "face" à
  true** (détection dans le navigateur via MediaPipe.js — voir
  phone_client/), mais pas encore "hands" (non implémenté côté
  téléphone) — un rôle hands sur une source phone est une erreur de
  configuration, rejetée au chargement.
- "preview" (bool, défaut True) : ouvrir une fenêtre d'aperçu OpenCV
  pour cette caméra (webcam uniquement — un téléphone a son propre
  aperçu affiché sur son propre écran).

Si plusieurs caméras portent le même rôle (ex. deux caméras avec
"pose": true), **la plus récemment mise à jour "gagne"** au moment de
la fusion (voir server.py) — pas de triangulation/moyenne pondérée.
Documenté comme limite connue (voir README) : une vraie fusion
multi-angle reste un travail futur.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field


class CameraConfigError(Exception):
    """Configuration de caméra invalide (voir validate())."""


@dataclass
class CameraConfig:
    name: str
    source_type: str  # "webcam" ou "phone"
    webcam_index: int | None = None  # défini si source_type == "webcam"
    pose: bool = False
    face: bool = False
    hands: bool = False
    preview: bool = True


@dataclass
class MultiCameraConfig:
    cameras: list[CameraConfig] = field(default_factory=list)

    def webcam_cameras(self) -> list[CameraConfig]:
        return [c for c in self.cameras if c.source_type == "webcam"]

    def phone_cameras(self) -> list[CameraConfig]:
        return [c for c in self.cameras if c.source_type == "phone"]

    def pose_cameras(self) -> list[CameraConfig]:
        return [c for c in self.cameras if c.pose]

    def face_cameras(self) -> list[CameraConfig]:
        return [c for c in self.cameras if c.face]

    def hands_cameras(self) -> list[CameraConfig]:
        return [c for c in self.cameras if c.hands]


def _parse_source(name: str, raw_source: str) -> tuple[str, int | None]:
    if raw_source == "phone":
        return "phone", None
    if raw_source.startswith("webcam:"):
        index_str = raw_source[len("webcam:"):]
        try:
            return "webcam", int(index_str)
        except ValueError:
            raise CameraConfigError(
                f"caméra '{name}' : index de webcam invalide dans \"{raw_source}\" "
                "(attendu \"webcam:<entier>\", ex. \"webcam:0\")"
            )
    raise CameraConfigError(
        f"caméra '{name}' : source \"{raw_source}\" non reconnue "
        "(attendu \"webcam:<index>\" ou \"phone\")"
    )


def _parse_camera(raw: dict) -> CameraConfig:
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise CameraConfigError("chaque caméra doit avoir un \"name\" (chaîne non vide)")

    raw_source = raw.get("source")
    if not isinstance(raw_source, str):
        raise CameraConfigError(f"caméra '{name}' : \"source\" manquant ou invalide")
    source_type, webcam_index = _parse_source(name, raw_source)

    pose = bool(raw.get("pose", False))
    face = bool(raw.get("face", False))
    hands = bool(raw.get("hands", False))
    preview = bool(raw.get("preview", True))

    if not (pose or face or hands):
        raise CameraConfigError(
            f"caméra '{name}' : au moins un de \"pose\"/\"face\"/\"hands\" doit être true "
            "(une caméra qui ne capture rien n'a pas d'utilité)"
        )

    if source_type == "phone" and hands:
        raise CameraConfigError(
            f"caméra '{name}' : \"hands\" non supporté sur une source \"phone\" "
            "pour l'instant (MediaPipe.js ne détecte pas encore les mains sur le "
            "téléphone, voir phone_client/) — seuls \"pose\"/\"face\" sont valides ici"
        )

    return CameraConfig(
        name=name, source_type=source_type, webcam_index=webcam_index,
        pose=pose, face=face, hands=hands, preview=preview,
    )


def load(path: str) -> MultiCameraConfig:
    """Charge et valide un fichier de configuration multi-caméra. Lève
    CameraConfigError (noms dupliqués, index webcam dupliqué, source
    invalide...) ou les erreurs standard (FileNotFoundError,
    json.JSONDecodeError) si le fichier est absent/mal formé."""
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    raw_cameras = raw.get("cameras")
    if not isinstance(raw_cameras, list) or not raw_cameras:
        raise CameraConfigError('le fichier de configuration doit contenir une liste "cameras" non vide')

    cameras = [_parse_camera(c) for c in raw_cameras]

    names_seen = set()
    for cam in cameras:
        if cam.name in names_seen:
            raise CameraConfigError(f"nom de caméra dupliqué : '{cam.name}'")
        names_seen.add(cam.name)

    webcam_indices_seen = set()
    for cam in cameras:
        if cam.source_type != "webcam":
            continue
        if cam.webcam_index in webcam_indices_seen:
            raise CameraConfigError(
                f"caméra '{cam.name}' : index webcam {cam.webcam_index} déjà utilisé "
                "par une autre caméra de la configuration"
            )
        webcam_indices_seen.add(cam.webcam_index)

    return MultiCameraConfig(cameras=cameras)
