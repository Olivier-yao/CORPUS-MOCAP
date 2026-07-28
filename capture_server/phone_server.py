"""CORPUS-MOCAP — pont téléphone (Phase 4, cahier des charges Module 5 ;
support multi-téléphone, Phase 5 — voir camera_config.py).

Sert la page web du compagnon mobile (capture_server/phone_client/) et
reçoit les landmarks de pose déjà détectés par MediaPipe.js DANS LE
NAVIGATEUR DU TÉLÉPHONE, via WebSocket — le téléphone n'envoie jamais de
flux vidéo brut au PC, seulement les landmarks (33 points x/y/z/
visibility, même convention que MediaPipe Pose côté Python), pour rester
léger sur le WiFi et éviter de dépendre de la puissance du PC pour la
détection. `server.py` lit ensuite ces landmarks exactement comme s'ils
venaient de `detect_for_video()` sur une webcam PC — aucun changement
nécessaire dans le reste du pipeline (filtres, protocole TCP vers
l'addon, bone_mapping.py).

**Plusieurs téléphones simultanés** : chaque connexion WebSocket est
identifiée par un paramètre `?cam=<nom>` dans l'URL (voir
phone_client/index.html), correspondant au "name" d'une caméra
"source": "phone" dans le fichier de configuration multi-caméra (voir
camera_config.py) — un téléphone par rôle (ex. "mains"), pas de
fusion. Sans ce paramètre (usage historique, un seul téléphone —
`server.py --source phone`), une connexion est rangée sous le nom
générique `DEFAULT_SLOT_NAME`. Chaque connexion (nommée ou par défaut) a
ses propres filtres One Euro (état temporel indépendant : un pour la
pose, un pour les blendshapes, un pour la rotation de tête), et son
propre indicateur "connecté".

Corps et/ou visage selon la configuration (le paramètre `?pose=1`/
`?face=1` dans l'URL indique au navigateur quel(s) détecteur(s)
MediaPipe.js charger, voir phone_client/index.html) — pas encore les
mains depuis le téléphone (voir la feuille de route du README pour
l'extension future ; camera_config.py rejette une configuration
"hands" sur une source "phone").

Servi en **HTTPS** (certificat auto-signé, voir tls_cert.py), pas en
HTTP simple : `getUserMedia` (accès caméra) exige un contexte sécurisé,
et contrairement à Chrome (qui propose un flag de contournement pour
une origine `http://` locale), **Safari iOS n'a aucun équivalent** —
confirmé en test réel. Le certificat auto-signé déclenche un
avertissement de sécurité au premier accès sur chaque navigateur/
appareil, à accepter manuellement une fois (voir README) — normal, pas
un bug. Respecte les exigences Apple publiées pour les certificats TLS
serveur (support.apple.com/en-us/HT210176) : validité ≤ 825 jours,
BasicConstraints/KeyUsage/ExtendedKeyUsage — voir tls_cert.py.

Nécessite les paquets "websockets" et "cryptography" (voir requirements.txt).
"""

from __future__ import annotations

import http.server
import json
import os
import socket
import threading
import time
from urllib.parse import parse_qs, urlsplit

import websockets.sync.server

import tls_cert
from one_euro_filter import BlendshapeFilter, HeadRotationFilter, LandmarkFilter
from protocol import NUM_LANDMARKS

PHONE_CLIENT_DIR = os.path.join(os.path.dirname(__file__), "phone_client")
MODELS_DIR = os.path.join(os.path.dirname(__file__), "models")

# Nom de créneau utilisé quand une connexion n'indique pas de "?cam=..."
# (mode historique un seul téléphone, `server.py --source phone`).
DEFAULT_SLOT_NAME = "_default"


def get_local_ip() -> str:
    """Astuce standard pour trouver l'IP locale utilisée sur le réseau :
    ouvre un socket UDP vers une adresse publique (aucune donnée n'est
    réellement envoyée, UDP est "connectionless") puis lit l'adresse
    source que le système a choisie pour cette route — fonctionne même
    sans accès Internet réel tant qu'une route par défaut existe (cas
    normal sur un réseau WiFi domestique)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


class _PhoneClientHTTPHandler(http.server.SimpleHTTPRequestHandler):
    """Sert capture_server/phone_client/ (page web) ET capture_server/
    models/ (fichiers .task, réutilisés tels quels par MediaPipe.js côté
    téléphone — pas besoin de les re-télécharger dans un format différent)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=PHONE_CLIENT_DIR, **kwargs)

    def translate_path(self, path: str) -> str:
        if path.startswith("/models/"):
            return os.path.join(MODELS_DIR, path[len("/models/"):].lstrip("/"))
        return super().translate_path(path)

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - signature imposée par http.server
        pass


class _PhoneSlot:
    """État d'une connexion téléphone nommée : filtres dédiés (état
    temporel indépendant des autres téléphones) pour la pose ET le
    visage — un téléphone peut alimenter l'un, l'autre, ou les deux
    (voir "pose"/"face" dans camera_config.py) — dernières données
    filtrées, indicateur de connexion."""

    def __init__(self, stability: float):
        self.pose_filter = LandmarkFilter(NUM_LANDMARKS)
        self.pose_filter.set_stability(stability)
        self.blendshape_filter = BlendshapeFilter()
        self.blendshape_filter.set_stability(stability)
        self.head_rotation_filter = HeadRotationFilter()
        self.head_rotation_filter.set_stability(stability)
        self.latest_landmarks: list[dict] | None = None
        self.updated_at: float = 0.0
        # (blendshapes, tracking_ok, head_rotation, horodatage monotonic)
        self.latest_face: tuple[dict, bool, list[float] | None, float] | None = None
        # Points bruts (x, y) du maillage visage — non filtrés, non
        # transmis à l'addon (voir get_latest_face_points ci-dessous) :
        # uniquement pour dessiner un vrai maillage dans la fenêtre
        # d'aperçu PC (run_multi_camera), à la place des coefficients
        # blend shapes qui ne portent pas de position.
        self.latest_face_points: list[tuple[float, float]] | None = None
        self.connected = False


class PhoneBridge:
    """Démarre le serveur HTTP (page web) et le serveur WebSocket
    (landmarks) dans des threads séparés. Chaque connexion WebSocket est
    rangée dans un créneau nommé (voir `?cam=` ci-dessus) — expose les
    derniers landmarks reçus PAR NOM (thread-safe) pour que la boucle
    principale de server.py les lise à chaque trame."""

    def __init__(self, http_port: int, ws_port: int):
        self.http_port = http_port
        self.ws_port = ws_port
        self._lock = threading.Lock()
        self._slots: dict[str, _PhoneSlot] = {}
        self._stability = 0.5
        self._http_server: http.server.ThreadingHTTPServer | None = None
        self._ws_server = None

    def _get_or_create_slot(self, name: str) -> _PhoneSlot:
        slot = self._slots.get(name)
        if slot is None:
            slot = _PhoneSlot(self._stability)
            self._slots[name] = slot
        return slot

    def connected_names(self) -> list[str]:
        with self._lock:
            return [name for name, slot in self._slots.items() if slot.connected]

    def is_connected(self, name: str = DEFAULT_SLOT_NAME) -> bool:
        with self._lock:
            slot = self._slots.get(name)
            return slot is not None and slot.connected

    # Alias conservé pour compatibilité (mode --source phone, un seul téléphone).
    @property
    def connected(self) -> bool:
        return self.is_connected(DEFAULT_SLOT_NAME)

    def get_latest_landmarks(self, name: str = DEFAULT_SLOT_NAME) -> list[dict] | None:
        with self._lock:
            slot = self._slots.get(name)
            return slot.latest_landmarks if slot is not None else None

    def get_latest_frame(self, name: str = DEFAULT_SLOT_NAME) -> tuple[list[dict], bool, float] | None:
        """Retourne (landmarks, tracking_ok, updated_at) — même forme que
        CameraWorker.get_latest_frame() dans server.py, pour une fusion
        uniforme entre webcams et téléphones dans run_multi_camera().
        tracking_ok toujours True ici : MediaPipe.js côté téléphone
        n'envoie un message que lorsqu'une pose est détectée (voir
        phone_client/index.html), donc la seule présence de landmarks
        signifie "détecté" — pas de distinction "gelé sur perte de
        tracking" comme côté webcam Python (Hand/Pose Landmarker
        Python exposent explicitement une confiance par trame, pas
        MediaPipe.js tel qu'utilisé ici)."""
        with self._lock:
            slot = self._slots.get(name)
            if slot is None or slot.latest_landmarks is None:
                return None
            return (slot.latest_landmarks, True, slot.updated_at)

    def get_latest_face(self, name: str = DEFAULT_SLOT_NAME) -> tuple[dict, bool, list[float] | None, float] | None:
        """Même forme que CameraWorker.get_latest_face() dans server.py
        (blendshapes, tracking_ok, head_rotation, updated_at), pour une
        fusion uniforme dans run_multi_camera()."""
        with self._lock:
            slot = self._slots.get(name)
            return slot.latest_face if slot is not None else None

    def get_latest_face_points(self, name: str = DEFAULT_SLOT_NAME) -> list[tuple[float, float]] | None:
        """Points bruts (x, y) du maillage visage, pour l'aperçu PC
        uniquement (voir _PhoneSlot.latest_face_points) — pas de forme
        partagée avec CameraWorker, cet accesseur n'est appelé que dans
        run_multi_camera pour dessiner la fenêtre dédiée à cette caméra."""
        with self._lock:
            slot = self._slots.get(name)
            return slot.latest_face_points if slot is not None else None

    def set_stability(self, value: float) -> None:
        with self._lock:
            self._stability = value
            for slot in self._slots.values():
                slot.pose_filter.set_stability(value)
                slot.blendshape_filter.set_stability(value)
                slot.head_rotation_filter.set_stability(value)

    def _handle_ws_connection(self, ws) -> None:
        query = parse_qs(urlsplit(ws.request.path).query)
        name = query.get("cam", [DEFAULT_SLOT_NAME])[0] or DEFAULT_SLOT_NAME

        with self._lock:
            slot = self._get_or_create_slot(name)
            slot.connected = True
        print(f"[phone_server] téléphone connecté (caméra '{name}')")
        try:
            for message in ws:
                try:
                    payload = json.loads(message)
                except (json.JSONDecodeError, TypeError):
                    continue
                msg_type = payload.get("type", "pose")

                if msg_type == "pose":
                    landmarks = payload.get("landmarks")
                    if isinstance(landmarks, list) and len(landmarks) == NUM_LANDMARKS:
                        with self._lock:
                            slot.latest_landmarks = slot.pose_filter.process(landmarks)
                            slot.updated_at = time.monotonic()

                elif msg_type == "face":
                    raw_blendshapes = payload.get("blendshapes")
                    raw_head_rotation = payload.get("head_rotation")
                    raw_points = payload.get("points")
                    if isinstance(raw_blendshapes, dict):
                        with self._lock:
                            blendshapes = slot.blendshape_filter.process(raw_blendshapes)
                            head_rotation = slot.head_rotation_filter.process(raw_head_rotation)
                            slot.latest_face = (blendshapes, True, head_rotation, time.monotonic())
                            if isinstance(raw_points, list):
                                slot.latest_face_points = [(p["x"], p["y"]) for p in raw_points]
        except Exception as exc:  # connexion coupée, réseau instable, etc.
            print(f"[phone_server] connexion téléphone '{name}' interrompue : {exc}")
        finally:
            with self._lock:
                slot.connected = False
                slot.latest_landmarks = None
                slot.latest_face = None
                slot.latest_face_points = None
            print(f"[phone_server] téléphone déconnecté (caméra '{name}')")

    def start(self, cameras: list[tuple[str, bool, bool]] | None = None) -> None:
        """`cameras` : liste de (name, pose, face) pour les caméras
        "phone" attendues (voir camera_config.py) — utilisée pour
        construire les URLs imprimées avec les bons paramètres
        ?cam=&pose=&face=, afin que chaque téléphone sache quel(s)
        détecteur(s) MediaPipe.js charger (voir phone_client/index.html).
        None (ou omis) pour le mode historique un seul téléphone (pose
        uniquement, aucun paramètre de rôle dans l'URL)."""
        local_ip = get_local_ip()

        # Un certificat frais par démarrage (voir tls_cert.py), partagé
        # entre les deux serveurs (HTTPS + WSS) — même identité de
        # certificat (create_ssl_context_pair génère le certificat une
        # seule fois, contrairement à deux appels séparés à
        # create_ssl_context qui produiraient chacun un certificat
        # différent).
        http_ssl_context, ws_ssl_context = tls_cert.create_ssl_context_pair(local_ip)

        self._http_server = http.server.ThreadingHTTPServer(("0.0.0.0", self.http_port), _PhoneClientHTTPHandler)
        self._http_server.socket = http_ssl_context.wrap_socket(self._http_server.socket, server_side=True)
        threading.Thread(target=self._http_server.serve_forever, daemon=True).start()

        def run_ws_server() -> None:
            with websockets.sync.server.serve(
                self._handle_ws_connection, "0.0.0.0", self.ws_port, ssl=ws_ssl_context
            ) as server:
                self._ws_server = server
                server.serve_forever()

        threading.Thread(target=run_ws_server, daemon=True).start()

        # Adresses SANS le paramètre ?ws=... : phone_client/index.html
        # déduit déjà l'adresse WebSocket depuis le nom d'hôte de la page
        # elle-même quand ce paramètre est absent (voir son commentaire
        # "wsAddress") — plus court à recopier sur le téléphone, donc
        # moins de risque de mélanger une ancienne adresse (déjà arrivé
        # en test réel : IP différente entre l'hôte de la page et le
        # paramètre ?ws=, copié-collé d'une session précédente).
        ws_url = f"https://{local_ip}:{self.ws_port}/"
        print("[phone_server] certificat auto-signé — le navigateur du téléphone va afficher")
        print("[phone_server] un avertissement de sécurité à accepter manuellement (normal, voir README).")
        print("[phone_server] sur CHAQUE téléphone (même réseau WiFi que ce PC), dans cet ordre —")
        print("[phone_server] ne réutilisez jamais une adresse notée lors d'une session précédente :")
        print(f"[phone_server]   1. Ouvrez {ws_url} et acceptez l'avertissement (page vide/erreur, normal)")

        if cameras:
            print("[phone_server]   2. Ouvrez, SUR LE TÉLÉPHONE CORRESPONDANT, l'adresse de sa caméra :")
            for name, pose, face in cameras:
                role_params = "&".join(
                    f"{r}=1" for r, on in (("pose", pose), ("face", face)) if on
                )
                page_url = f"https://{local_ip}:{self.http_port}/?cam={name}&{role_params}"
                roles = "+".join(r for r, on in (("pose", pose), ("face", face)) if on)
                print(f"[phone_server]        '{name}' ({roles}) : {page_url}")
        else:
            page_url = f"https://{local_ip}:{self.http_port}/"
            print(f"[phone_server]   2. Ouvrez {page_url} et acceptez l'avertissement, puis \"Démarrer la caméra\"")

    def stop(self) -> None:
        if self._http_server is not None:
            self._http_server.shutdown()
        if self._ws_server is not None:
            self._ws_server.shutdown()
