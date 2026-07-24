"""CORPUS-MOCAP — pont téléphone (Phase 4, cahier des charges Module 5).

Sert la page web du compagnon mobile (capture_server/phone_client/) et
reçoit les landmarks de pose déjà détectés par MediaPipe.js DANS LE
NAVIGATEUR DU TÉLÉPHONE, via WebSocket — le téléphone n'envoie jamais de
flux vidéo brut au PC, seulement les landmarks (33 points x/y/z/
visibility, même convention que MediaPipe Pose côté Python), pour rester
léger sur le WiFi et éviter de dépendre de la puissance du PC pour la
détection. `server.py --source phone` lit ensuite ces landmarks exactement
comme s'ils venaient de `detect_for_video()` sur la webcam PC — aucun
changement nécessaire dans le reste du pipeline (filtres, protocole TCP
vers l'addon, bone_mapping.py).

Corps uniquement pour cette première version (pas de visage/mains depuis
le téléphone — voir la feuille de route du README pour l'extension
future). Non testé sur un vrai téléphone dans cette session (pas
d'appareil disponible côté développement) : à valider en conditions
réelles, itération probable comme pour les autres fonctionnalités du
projet.

Nécessite le paquet "websockets" (voir requirements.txt).
"""

from __future__ import annotations

import http.server
import json
import os
import socket
import threading

import websockets.sync.server

from protocol import NUM_LANDMARKS

PHONE_CLIENT_DIR = os.path.join(os.path.dirname(__file__), "phone_client")
MODELS_DIR = os.path.join(os.path.dirname(__file__), "models")


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


class PhoneBridge:
    """Démarre le serveur HTTP (page web) et le serveur WebSocket
    (landmarks) dans des threads séparés, et expose les derniers
    landmarks reçus (thread-safe) pour que la boucle principale de
    server.py les lise à chaque trame."""

    def __init__(self, http_port: int, ws_port: int):
        self.http_port = http_port
        self.ws_port = ws_port
        self._lock = threading.Lock()
        self._latest_landmarks: list[dict] | None = None
        self._connected = False
        self._http_server: http.server.ThreadingHTTPServer | None = None
        self._ws_server = None

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._connected

    def get_latest_landmarks(self) -> list[dict] | None:
        with self._lock:
            return self._latest_landmarks

    def _handle_ws_connection(self, ws) -> None:
        with self._lock:
            self._connected = True
        print("[phone_server] téléphone connecté")
        try:
            for message in ws:
                try:
                    payload = json.loads(message)
                except (json.JSONDecodeError, TypeError):
                    continue
                landmarks = payload.get("landmarks")
                if isinstance(landmarks, list) and len(landmarks) == NUM_LANDMARKS:
                    with self._lock:
                        self._latest_landmarks = landmarks
        except Exception as exc:  # connexion coupée, réseau instable, etc.
            print(f"[phone_server] connexion téléphone interrompue : {exc}")
        finally:
            with self._lock:
                self._connected = False
                self._latest_landmarks = None
            print("[phone_server] téléphone déconnecté")

    def start(self) -> None:
        local_ip = get_local_ip()

        self._http_server = http.server.ThreadingHTTPServer(("0.0.0.0", self.http_port), _PhoneClientHTTPHandler)
        threading.Thread(target=self._http_server.serve_forever, daemon=True).start()

        def run_ws_server() -> None:
            with websockets.sync.server.serve(self._handle_ws_connection, "0.0.0.0", self.ws_port) as server:
                self._ws_server = server
                server.serve_forever()

        threading.Thread(target=run_ws_server, daemon=True).start()

        url = f"http://{local_ip}:{self.http_port}/?ws={local_ip}:{self.ws_port}"
        print("[phone_server] sur le téléphone (même réseau WiFi que ce PC), ouvrez :")
        print(f"[phone_server]     {url}")

    def stop(self) -> None:
        if self._http_server is not None:
            self._http_server.shutdown()
        if self._ws_server is not None:
            self._ws_server.shutdown()
