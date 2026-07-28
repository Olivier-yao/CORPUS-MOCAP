"""CORPUS-MOCAP — capture_server (Phase 1+2+mains+téléphone).

Process externe, indépendant de Blender, avec deux sources possibles
(--source webcam, par défaut, ou --source phone — cahier des charges
Module 5) :

- **webcam** (défaut) : capture la webcam via OpenCV, détecte le
  squelette (MediaPipe Pose), le visage (MediaPipe Face Landmarker,
  coefficients blend shapes ARKit) et les mains (MediaPipe Hand
  Landmarker, 21 points par main) via la Tasks API — l'ancienne API
  `mp.solutions.*` a été retirée du paquet à partir de mediapipe 0.10.x
  récents.
- **phone** : le corps (pose) est détecté par MediaPipe.js directement
  DANS LE NAVIGATEUR du téléphone (voir phone_server.py et
  phone_client/) — ce process reçoit uniquement les landmarks déjà
  calculés via WebSocket, pas de flux vidéo. Visage/mains pas encore
  disponibles depuis le téléphone dans cette version (voir feuille de
  route du README) : ignorés en mode phone même si les modèles sont
  fournis.

Dans les deux cas, les landmarks sont lissés (One Euro Filter) puis
diffusés à l'addon Blender via un socket TCP local (une ligne JSON par
trame, voir protocol.py) — le reste du pipeline (filtres, protocole,
mapping côté addon) est identique quelle que soit la source.

Nécessite les modèles "pose_landmarker_lite.task", "face_landmarker.task"
et "hand_landmarker.task" dans ./models/ (voir README.md pour le
téléchargement). Visage et mains peuvent être désactivés avec --no-face /
--no-hands si non nécessaires (mode webcam).

Une fenêtre d'aperçu s'ouvre par défaut pour vérifier le cadrage avant/
pendant l'enregistrement dans Blender (cahier des charges, Module 1) —
flux caméra réel en mode webcam, squelette seul sur fond noir en mode
phone (pas de flux vidéo reçu du téléphone). Désactivable avec
--no-preview.

Usage :
    python server.py [--host 127.0.0.1] [--port 9001]
                      [--source webcam|phone]
                      # mode webcam :
                      [--camera 0]
                      [--model models/pose_landmarker_lite.task]
                      [--face-model models/face_landmarker.task] [--no-face]
                      [--hand-model models/hand_landmarker.task] [--no-hands]
                      # mode phone :
                      [--phone-http-port 8080] [--phone-ws-port 8766]
                      [--no-preview]
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks.python import vision
from mediapipe.tasks.python.core.base_options import BaseOptions

import camera_config
from one_euro_filter import BlendshapeFilter, HandFilter, HeadRotationFilter, LandmarkFilter
from phone_server import PhoneBridge
from protocol import (
    LANDMARK_INDEX,
    NUM_LANDMARKS,
    build_face_message,
    build_frame_message,
    build_hands_message,
)

del LANDMARK_INDEX  # référencé pour clarté ; le mapping vit côté addon

MODELS_DIR = os.path.join(os.path.dirname(__file__), "models")
DEFAULT_MODEL_PATH = os.path.join(MODELS_DIR, "pose_landmarker_lite.task")
DEFAULT_FACE_MODEL_PATH = os.path.join(MODELS_DIR, "face_landmarker.task")
DEFAULT_HAND_MODEL_PATH = os.path.join(MODELS_DIR, "hand_landmarker.task")

PREVIEW_WINDOW_NAME = "CORPUS-MOCAP - Apercu (Echap pour fermer)"

# Connexions squelette (paires d'indices de landmarks) pour le dessin de
# l'aperçu — mêmes paires que l'ancienne mp.solutions.pose.POSE_CONNECTIONS.
POSE_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 7), (0, 4), (4, 5), (5, 6), (6, 8), (9, 10),
    (11, 12), (11, 13), (13, 15), (15, 17), (15, 19), (15, 21), (17, 19),
    (12, 14), (14, 16), (16, 18), (16, 20), (16, 22), (18, 20),
    (11, 23), (12, 24), (23, 24),
    (23, 25), (25, 27), (27, 29), (27, 31), (29, 31),
    (24, 26), (26, 28), (28, 30), (28, 32), (30, 32),
]


def draw_preview(frame_bgr, landmarks: list[dict] | None, tracking_ok: bool) -> None:
    """Dessine le squelette détecté sur `frame_bgr` (modifié en place)."""
    h, w = frame_bgr.shape[:2]
    color = (0, 200, 0) if tracking_ok else (0, 0, 220)

    if landmarks:
        points = [(int(lm["x"] * w), int(lm["y"] * h)) for lm in landmarks]
        for a, b in POSE_CONNECTIONS:
            cv2.line(frame_bgr, points[a], points[b], color, 2)
        for x, y in points:
            cv2.circle(frame_bgr, (x, y), 3, color, -1)

    status = "Tracking OK" if tracking_ok else "Tracking perdu"
    cv2.putText(frame_bgr, status, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)


def draw_face_preview(frame_bgr, face_points: list[tuple[float, float]] | None) -> None:
    """Dessine le nuage des ~478 points du visage (Face Landmarker) sous
    forme de petits points cyan, pour donner un effet "maillage" qui
    englobe visuellement le visage et vérifier le cadrage."""
    if not face_points:
        return
    h, w = frame_bgr.shape[:2]
    for x, y in face_points:
        cv2.circle(frame_bgr, (int(x * w), int(y * h)), 1, (255, 220, 0), -1)


# Connexions squelette de main (21 points MediaPipe Hand Landmarker) pour
# le dessin de l'aperçu — même topologie que l'ancienne
# mp.solutions.hands.HAND_CONNECTIONS.
HAND_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),           # pouce
    (0, 5), (5, 6), (6, 7), (7, 8),           # index
    (5, 9), (9, 10), (10, 11), (11, 12),      # majeur
    (9, 13), (13, 14), (14, 15), (15, 16),    # annulaire
    (13, 17), (17, 18), (18, 19), (19, 20),   # auriculaire
    (0, 17),
]


def draw_hands_preview(frame_bgr, hands: dict[str, list[dict]] | None) -> None:
    """Dessine le squelette des mains détectées (magenta)."""
    if not hands:
        return
    h, w = frame_bgr.shape[:2]
    for points in hands.values():
        if not points:
            continue
        pixels = [(int(p["x"] * w), int(p["y"] * h)) for p in points]
        for a, b in HAND_CONNECTIONS:
            cv2.line(frame_bgr, pixels[a], pixels[b], (220, 0, 220), 2)
        for x, y in pixels:
            cv2.circle(frame_bgr, (x, y), 3, (220, 0, 220), -1)


class ClientConnection:
    """Gère l'unique client connecté (l'addon Blender) : envoi des trames,
    lecture des messages de contrôle (ex: changement de stabilité).

    `stability_targets` : tout objet exposant `.set_stability(value)` —
    en mode source unique, les 4 filtres (landmark/blendshape/head
    rotation/hand) ; en mode multi-caméra (voir run_multi_camera), un
    par CameraWorker/PhoneBridge, potentiellement plus de 4. Généralisé
    ainsi plutôt que 4 paramètres nommés fixes pour ne pas dépendre du
    nombre de caméras configurées."""

    def __init__(self, sock: socket.socket, stability_targets: list, pose_fusion: "PoseSourceFusion | None" = None):
        self.sock = sock
        self.sock.setblocking(False)
        self._recv_buffer = b""
        self._stability_targets = stability_targets
        self._pose_fusion = pose_fusion
        self._lock = threading.Lock()

    def send_frame(self, landmarks: list[dict], tracking_ok: bool) -> bool:
        return self._send(build_frame_message(landmarks, tracking_ok))

    def send_face(
        self, blendshapes: dict[str, float], tracking_ok: bool, head_rotation: list[float] | None = None
    ) -> bool:
        return self._send(build_face_message(blendshapes, tracking_ok, head_rotation))

    def send_hands(self, hands: dict[str, list[dict] | None], tracking_ok: bool) -> bool:
        return self._send(build_hands_message(hands, tracking_ok))

    def _send(self, message: dict) -> bool:
        payload = (json.dumps(message) + "\n").encode("utf-8")
        try:
            with self._lock:
                self.sock.sendall(payload)
            return True
        except (BlockingIOError, InterruptedError):
            return True
        except OSError:
            return False

    def poll_control_messages(self) -> None:
        try:
            data = self.sock.recv(4096)
            if not data:
                raise ConnectionResetError("client déconnecté")
            self._recv_buffer += data
        except BlockingIOError:
            pass

        while b"\n" in self._recv_buffer:
            line, self._recv_buffer = self._recv_buffer.split(b"\n", 1)
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("type") == "set_stability":
                value = float(msg.get("value", 0.5))
                for target in self._stability_targets:
                    target.set_stability(value)
            elif msg.get("type") == "set_primary_camera" and self._pose_fusion is not None:
                name = msg.get("name") or None  # chaîne vide -> None (comportement par défaut)
                self._pose_fusion.set_primary_override(name)


def extract_landmarks(result) -> list[dict] | None:
    if not result.pose_landmarks:
        return None
    return [
        {
            "x": lm.x,
            "y": lm.y,
            "z": lm.z,
            "visibility": lm.visibility if lm.visibility is not None else 1.0,
        }
        for lm in result.pose_landmarks[0]
    ]


def extract_face_points_2d(result) -> list[tuple[float, float]] | None:
    """Coordonnées x/y normalisées des ~478 points du visage, pour
    l'affichage dans l'aperçu (pas transmis à l'addon, trop volumineux
    pour un usage temps réel utile côté Blender à ce stade)."""
    if not result.face_landmarks:
        return None
    return [(lm.x, lm.y) for lm in result.face_landmarks[0]]


def extract_blendshapes(result) -> dict[str, float] | None:
    if not result.face_blendshapes:
        return None
    return {category.category_name: category.score for category in result.face_blendshapes[0]}


def extract_head_rotation(result) -> list[float] | None:
    """Sous-matrice de rotation 3x3 (9 floats, ligne par ligne) de la tête,
    dérivée de facial_transformation_matrixes (repère MediaPipe : X droite,
    Y haut, Z vers la caméra). La conversion vers l'espace du rig se fait
    côté addon (bone_mapping/face_mapping ont déjà mathutils)."""
    if not result.facial_transformation_matrixes:
        return None
    m = result.facial_transformation_matrixes[0]
    return [float(m[r][c]) for r in range(3) for c in range(3)]


def extract_hands(result) -> dict[str, list[dict]] | None:
    """Retourne {"left": [21 dicts {x,y,z}] | None, "right": [...] | None}
    selon la classification "handedness" de MediaPipe (main anatomique du
    sujet), ou None si aucune main détectée."""
    if not result.hand_landmarks:
        return None
    hands: dict[str, list[dict] | None] = {"left": None, "right": None}
    for landmarks, handedness in zip(result.hand_landmarks, result.handedness):
        if not handedness:
            continue
        label = handedness[0].category_name
        points = [{"x": lm.x, "y": lm.y, "z": lm.z} for lm in landmarks]
        if label == "Left":
            hands["left"] = points
        elif label == "Right":
            hands["right"] = points
    if hands["left"] is None and hands["right"] is None:
        return None
    return hands


def create_pose_landmarker(model_path: str) -> vision.PoseLandmarker:
    if not os.path.isfile(model_path):
        raise FileNotFoundError(
            f"Modèle introuvable : {model_path}\n"
            "Téléchargez pose_landmarker_lite.task (voir README.md) et placez-le dans capture_server/models/."
        )
    options = vision.PoseLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=model_path),
        running_mode=vision.RunningMode.VIDEO,
        num_poses=1,
        min_pose_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    return vision.PoseLandmarker.create_from_options(options)


def create_face_landmarker(model_path: str) -> vision.FaceLandmarker:
    if not os.path.isfile(model_path):
        raise FileNotFoundError(
            f"Modèle introuvable : {model_path}\n"
            "Téléchargez face_landmarker.task (voir README.md) et placez-le dans capture_server/models/."
        )
    options = vision.FaceLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=model_path),
        running_mode=vision.RunningMode.VIDEO,
        num_faces=1,
        output_face_blendshapes=True,
        output_facial_transformation_matrixes=True,
        min_face_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    return vision.FaceLandmarker.create_from_options(options)


def _open_webcam(camera_index: int) -> cv2.VideoCapture:
    """Ouvre la webcam via DirectShow (Windows) ou le backend par défaut
    (autres OS). Force le codec MJPG : sans ça, certaines webcams (surtout
    les webcams intégrées portables) négocient via DirectShow un format
    brut mal reconnu par OpenCV, ce qui produit une image de bruit coloré/
    bandes diagonales au lieu du flux réel (constaté en test réel) — MJPG
    est quasi-universellement supporté et OpenCV sait le décoder."""
    if sys.platform == "win32":
        cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
    else:
        cap = cv2.VideoCapture(camera_index)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    return cap


def create_hand_landmarker(model_path: str) -> vision.HandLandmarker:
    if not os.path.isfile(model_path):
        raise FileNotFoundError(
            f"Modèle introuvable : {model_path}\n"
            "Téléchargez hand_landmarker.task (voir README.md) et placez-le dans capture_server/models/."
        )
    options = vision.HandLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=model_path),
        running_mode=vision.RunningMode.VIDEO,
        num_hands=2,
        min_hand_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    return vision.HandLandmarker.create_from_options(options)


def run(
    host: str,
    port: int,
    camera_index: int,
    model_path: str,
    face_model_path: str | None,
    hand_model_path: str | None,
    show_preview: bool = True,
    source: str = "webcam",
    phone_http_port: int = 8080,
    phone_ws_port: int = 8766,
) -> None:
    landmark_filter = LandmarkFilter(NUM_LANDMARKS)
    blendshape_filter = BlendshapeFilter()
    head_rotation_filter = HeadRotationFilter()
    hand_filter = HandFilter()

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((host, port))
    server_sock.listen(1)
    server_sock.settimeout(0.01)
    print(f"[capture_server] en attente de l'addon Blender sur {host}:{port} ...")

    is_phone = source == "phone"
    if is_phone and (face_model_path or hand_model_path):
        print(
            "[capture_server] visage/mains pas encore disponibles en mode --source phone "
            "(le téléphone n'envoie que la pose du corps) — ignorés."
        )
        face_model_path = None
        hand_model_path = None

    cap = None
    phone_bridge: PhoneBridge | None = None
    landmarker = None

    if is_phone:
        phone_bridge = PhoneBridge(phone_http_port, phone_ws_port)
        phone_bridge.start()
    else:
        # Sous Windows, le backend par défaut d'OpenCV (MSMF) peut se
        # bloquer indéfiniment à l'ouverture sur certaines machines même
        # si la caméra fonctionne très bien ailleurs ; DirectShow est
        # nettement plus fiable.
        cap = _open_webcam(camera_index)
        if not cap.isOpened():
            raise RuntimeError(f"Impossible d'ouvrir la caméra index={camera_index}")
        landmarker = create_pose_landmarker(model_path)

    face_landmarker = create_face_landmarker(face_model_path) if face_model_path else None
    hand_landmarker = create_hand_landmarker(hand_model_path) if hand_model_path else None
    frame_timestamp_ms = 0

    client: ClientConnection | None = None

    if show_preview:
        cv2.namedWindow(PREVIEW_WINDOW_NAME, cv2.WINDOW_NORMAL)
        print(f"[capture_server] aperçu ouvert dans une fenêtre séparée ({PREVIEW_WINDOW_NAME})")

    try:
        while True:
            if client is None:
                try:
                    conn, addr = server_sock.accept()
                    print(f"[capture_server] addon connecté depuis {addr}")
                    client = ClientConnection(
                        conn, [landmark_filter, blendshape_filter, head_rotation_filter, hand_filter]
                    )
                except socket.timeout:
                    pass

            mp_image = None
            frame = None

            if is_phone:
                raw_landmarks = phone_bridge.get_latest_landmarks()
                time.sleep(1.0 / 30.0)  # pas de source bloquante (cap.read()) pour cadencer la boucle
                if show_preview:
                    frame = np.zeros((480, 640, 3), dtype=np.uint8)
            else:
                ok, frame = cap.read()
                if not ok:
                    time.sleep(0.01)
                    continue
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
                frame_timestamp_ms += 1
                result = landmarker.detect_for_video(mp_image, frame_timestamp_ms)
                raw_landmarks = extract_landmarks(result)

            tracking_ok = raw_landmarks is not None
            smoothed = landmark_filter.process(raw_landmarks)

            blendshapes = None
            head_rotation = None
            face_points_2d = None
            face_tracking_ok = False
            if face_landmarker is not None and mp_image is not None:
                face_result = face_landmarker.detect_for_video(mp_image, frame_timestamp_ms)
                raw_blendshapes = extract_blendshapes(face_result)
                raw_head_rotation = extract_head_rotation(face_result)
                face_tracking_ok = raw_blendshapes is not None
                blendshapes = blendshape_filter.process(raw_blendshapes)
                head_rotation = head_rotation_filter.process(raw_head_rotation)
                if show_preview:
                    face_points_2d = extract_face_points_2d(face_result)

            hands = None
            hands_tracking_ok = False
            if hand_landmarker is not None and mp_image is not None:
                hand_result = hand_landmarker.detect_for_video(mp_image, frame_timestamp_ms)
                raw_hands = extract_hands(hand_result)
                hands_tracking_ok = raw_hands is not None
                hands = hand_filter.process(raw_hands)

            if show_preview and frame is not None:
                draw_preview(frame, raw_landmarks, tracking_ok)
                if hand_landmarker is not None:
                    draw_hands_preview(frame, hands)
                if face_landmarker is not None:
                    draw_face_preview(frame, face_points_2d)
                    face_status = "Visage OK" if face_tracking_ok else "Visage non détecté"
                    face_color = (0, 200, 0) if face_tracking_ok else (0, 0, 220)
                    cv2.putText(frame, face_status, (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.7, face_color, 2)
                if hand_landmarker is not None:
                    hands_status = "Mains OK" if hands_tracking_ok else "Mains non détectées"
                    hands_color = (0, 200, 0) if hands_tracking_ok else (0, 0, 220)
                    cv2.putText(frame, hands_status, (10, 75), cv2.FONT_HERSHEY_SIMPLEX, 0.7, hands_color, 2)
                if is_phone:
                    phone_status = "Téléphone connecté" if phone_bridge.connected else "En attente du téléphone..."
                    phone_color = (0, 200, 0) if phone_bridge.connected else (0, 165, 255)
                    cv2.putText(frame, phone_status, (10, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.7, phone_color, 2)
                cv2.imshow(PREVIEW_WINDOW_NAME, frame)
                if cv2.waitKey(1) & 0xFF == 27:  # Echap : ferme juste l'aperçu, pas le serveur
                    cv2.destroyWindow(PREVIEW_WINDOW_NAME)
                    show_preview = False

            if client is not None:
                client.poll_control_messages()
                ok_body = client.send_frame(smoothed, tracking_ok)
                ok_face = True
                if blendshapes is not None:
                    ok_face = client.send_face(blendshapes, face_tracking_ok, head_rotation)
                ok_hands = True
                if hands is not None:
                    ok_hands = client.send_hands(hands, hands_tracking_ok)
                if not (ok_body and ok_face and ok_hands):
                    print("[capture_server] addon déconnecté, en attente d'une nouvelle connexion")
                    client.sock.close()
                    client = None

    except KeyboardInterrupt:
        print("[capture_server] arrêt demandé")
    finally:
        if cap is not None:
            cap.release()
        if landmarker is not None:
            landmarker.close()
        if face_landmarker is not None:
            face_landmarker.close()
        if hand_landmarker is not None:
            hand_landmarker.close()
        if phone_bridge is not None:
            phone_bridge.stop()
        server_sock.close()
        cv2.destroyAllWindows()


class CameraWorker(threading.Thread):
    """Thread dédié à UNE caméra webcam configurée (Phase 5, voir
    camera_config.py) : ouvre son propre flux OpenCV, ne charge que les
    landmarkers requis par sa configuration (pose/face/hands — jamais
    plus que nécessaire, pour ne pas gaspiller de calcul), et met à jour
    un état partagé thread-safe (`get_latest_*`) à chaque trame capturée.
    Lu par la boucle de fusion de run_multi_camera(), à SA PROPRE
    cadence — indépendante du rythme de capture de cette caméra (chaque
    caméra peut avoir son propre framerate/latence, la fusion prend
    simplement la donnée la plus récente disponible à chaque tick)."""

    def __init__(self, config: camera_config.CameraConfig, model_paths: dict, show_preview: bool):
        super().__init__(daemon=True, name=f"camera-{config.name}")
        self.config = config
        self.show_preview = show_preview and config.preview
        self._model_paths = model_paths
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        self._landmark_filter = LandmarkFilter(NUM_LANDMARKS) if config.pose else None
        self._blendshape_filter = BlendshapeFilter() if config.face else None
        self._head_rotation_filter = HeadRotationFilter() if config.face else None
        self._hand_filter = HandFilter() if config.hands else None

        # (données, tracking_ok[, head_rotation], horodatage monotonic)
        self._latest_frame: tuple | None = None
        self._latest_face: tuple | None = None
        self._latest_hands: tuple | None = None

    def set_stability(self, value: float) -> None:
        if self._landmark_filter is not None:
            self._landmark_filter.set_stability(value)
        if self._blendshape_filter is not None:
            self._blendshape_filter.set_stability(value)
        if self._head_rotation_filter is not None:
            self._head_rotation_filter.set_stability(value)
        if self._hand_filter is not None:
            self._hand_filter.set_stability(value)

    def stop(self) -> None:
        self._stop_event.set()

    def get_latest_frame(self) -> tuple[list[dict], bool, float] | None:
        with self._lock:
            return self._latest_frame

    def get_latest_face(self) -> tuple[dict, bool, list[float] | None, float] | None:
        with self._lock:
            return self._latest_face

    def get_latest_hands(self) -> tuple[dict, bool, float] | None:
        with self._lock:
            return self._latest_hands

    def run(self) -> None:
        window_name = f"CORPUS-MOCAP - {self.config.name}"
        if self.show_preview:
            cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)

        cap = _open_webcam(self.config.webcam_index)
        if not cap.isOpened():
            print(
                f"[capture_server] ERREUR : impossible d'ouvrir la caméra "
                f"'{self.config.name}' (index {self.config.webcam_index}) — cette caméra sera ignorée."
            )
            return

        landmarker = create_pose_landmarker(self._model_paths["pose"]) if self.config.pose else None
        face_landmarker = create_face_landmarker(self._model_paths["face"]) if self.config.face else None
        hand_landmarker = create_hand_landmarker(self._model_paths["hands"]) if self.config.hands else None
        frame_timestamp_ms = 0

        try:
            while not self._stop_event.is_set():
                ok, frame = cap.read()
                if not ok:
                    time.sleep(0.01)
                    continue

                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
                frame_timestamp_ms += 1
                now = time.monotonic()

                raw_landmarks = None
                tracking_ok = False
                if landmarker is not None:
                    result = landmarker.detect_for_video(mp_image, frame_timestamp_ms)
                    raw_landmarks = extract_landmarks(result)
                    tracking_ok = raw_landmarks is not None
                    smoothed = self._landmark_filter.process(raw_landmarks)
                    with self._lock:
                        self._latest_frame = (smoothed, tracking_ok, now)

                face_points_2d = None
                face_tracking_ok = False
                if face_landmarker is not None:
                    face_result = face_landmarker.detect_for_video(mp_image, frame_timestamp_ms)
                    raw_blendshapes = extract_blendshapes(face_result)
                    raw_head_rotation = extract_head_rotation(face_result)
                    face_tracking_ok = raw_blendshapes is not None
                    blendshapes = self._blendshape_filter.process(raw_blendshapes)
                    head_rotation = self._head_rotation_filter.process(raw_head_rotation)
                    with self._lock:
                        self._latest_face = (blendshapes, face_tracking_ok, head_rotation, now)
                    if self.show_preview:
                        face_points_2d = extract_face_points_2d(face_result)

                hands = None
                hands_tracking_ok = False
                if hand_landmarker is not None:
                    hand_result = hand_landmarker.detect_for_video(mp_image, frame_timestamp_ms)
                    raw_hands = extract_hands(hand_result)
                    hands_tracking_ok = raw_hands is not None
                    hands = self._hand_filter.process(raw_hands)
                    with self._lock:
                        self._latest_hands = (hands, hands_tracking_ok, now)

                if self.show_preview:
                    if landmarker is not None:
                        draw_preview(frame, raw_landmarks, tracking_ok)
                    if hand_landmarker is not None:
                        draw_hands_preview(frame, hands)
                    if face_landmarker is not None:
                        draw_face_preview(frame, face_points_2d)
                    cv2.putText(
                        frame, self.config.name, (10, frame.shape[0] - 15),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2,
                    )
                    cv2.imshow(window_name, frame)
                    cv2.waitKey(1)
        finally:
            cap.release()
            if landmarker is not None:
                landmarker.close()
            if face_landmarker is not None:
                face_landmarker.close()
            if hand_landmarker is not None:
                hand_landmarker.close()
            if self.show_preview:
                try:
                    cv2.destroyWindow(window_name)
                except cv2.error:
                    pass


def _pick_freshest(candidates: list[tuple | None]) -> tuple | None:
    """Politique de fusion "dernier arrivé gagne", utilisée pour
    visage/mains quand plusieurs caméras partagent ce rôle — PAS de
    triangulation/moyenne pondérée (voir docstring de camera_config.py et
    Limites connues du README) : retourne le tuple (…, horodatage
    monotonic) le plus récent parmi `candidates`, ou None si tous
    absents. Le dernier élément de chaque tuple est toujours
    l'horodatage, quelle que soit la forme du reste (get_latest_frame/
    _face/_hands ont des arités différentes).

    Le rôle "pose" (corps), lui, utilise PoseSourceFusion ci-dessous —
    voir sa docstring pour pourquoi "dernier arrivé gagne" y posait
    problème (tremblement/désync constatés en test réel avec webcam PC +
    téléphone toutes deux en pose)."""
    best = None
    for c in candidates:
        if c is None:
            continue
        if best is None or c[-1] > best[-1]:
            best = c
    return best


# Marge de confiance (0-1) qu'une caméra auxiliaire doit dépasser par
# rapport à la primaire pour qu'on lui emprunte un membre — évite les
# allers-retours dus à un simple écart de bruit (voir _LimbFusion).
POSE_SWITCH_CONFIDENCE_MARGIN = 0.15

# Nombre de trames sur lesquelles lisser (interpoler) un emprunt de membre
# effectif — à ~30 Hz, 6 trames ≈ 200 ms. Évite le saut brutal entre deux
# points de vue physiquement différents au moment précis du changement.
POSE_SWITCH_BLEND_FRAMES = 6

# En dessous de cette confiance (0-1, moyenne de "visibility" sur les 2
# points distaux du membre), la caméra primaire est considérée "en
# difficulté" sur CE membre précis — seul cas où on regarde les caméras
# auxiliaires (voir _LimbFusion). Au-dessus, la primaire garde toujours
# la main, même si une auxiliaire serait momentanément un peu meilleure —
# elle est l'autorité par défaut, pas de raison de la déloger sans besoin.
PRIMARY_LIMB_CONFIDENCE_THRESHOLD = 0.4

# Indices MediaPipe Pose des 2 points DISTAUX de chaque membre (coude+
# poignet, genou+cheville — même convention que LANDMARK_INDEX dans
# addon/bone_mapping.py) : jamais l'épaule/la hanche (point d'attache),
# qui reste toujours celle de la caméra primaire — voir PoseSourceFusion.
_ARM_L_DISTAL = (13, 15)
_ARM_R_DISTAL = (14, 16)
_LEG_L_DISTAL = (25, 27)
_LEG_R_DISTAL = (26, 28)

# Confiance minimale qu'une caméra auxiliaire doit atteindre pour qu'on
# lui emprunte un membre — DOIVENT rester synchronisés avec
# VISIBILITY_THRESHOLD/LEG_VISIBILITY_THRESHOLD dans addon/bone_mapping.py
# (processus/environnement Python séparé — bpy —, pas d'import possible
# entre les deux). Sans ce plancher, la fusion pourrait "emprunter" une
# caméra auxiliaire tout juste meilleure que la primaire mais toujours en
# dessous de ce que l'addon exige pour appliquer le membre — l'emprunt
# serait alors fait pour rien : l'addon gèlerait le membre de toute façon
# (voir _visible() côté addon).
ARM_MIN_BORROW_CONFIDENCE = 0.5   # = VISIBILITY_THRESHOLD (bone_mapping.py)
LEG_MIN_BORROW_CONFIDENCE = 0.3   # = LEG_VISIBILITY_THRESHOLD (bone_mapping.py)


def _limb_confidence(landmarks: list[dict] | None, indices: tuple[int, int]) -> float:
    """Confiance moyenne (0-1) d'un membre (2 points distaux), utilisée
    par _LimbFusion pour décider si la primaire est "en difficulté"
    dessus. 0.0 si pas de landmarks du tout."""
    if landmarks is None:
        return 0.0
    return sum(landmarks[i]["visibility"] for i in indices) / len(indices)


def _blend_one_landmark(a: dict, b: dict, t: float) -> dict:
    """Interpolation linéaire d'UN landmark (t=0 -> a, t=1 -> b), pour
    lisser un emprunt de membre effectif (voir _LimbFusion)."""
    return {
        "x": a["x"] + (b["x"] - a["x"]) * t,
        "y": a["y"] + (b["y"] - a["y"]) * t,
        "z": a["z"] + (b["z"] - a["z"]) * t,
        "visibility": a["visibility"] + (b["visibility"] - a["visibility"]) * t,
    }


class _LimbFusion:
    """Fusion pour UN membre (2 landmarks distaux : coude+poignet ou
    genou+cheville). La caméra primaire (webcam) reste privilégiée en
    permanence ; une caméra auxiliaire ne prend le relais QUE pour ce
    membre précis, et seulement quand la primaire y est clairement en
    difficulté (PRIMARY_LIMB_CONFIDENCE_THRESHOLD) et qu'une autre voit
    clairement mieux (POSE_SWITCH_CONFIDENCE_MARGIN) — jamais l'épaule/la
    hanche (point d'attache), qui reste toujours celle de la primaire :
    voir PoseSourceFusion pour le compromis assumé (léger décalage visuel
    possible au point d'attache, si l'auxiliaire empruntée n'est pas
    exactement dans le même repère que la primaire).

    `min_borrow_confidence` : plancher ABSOLU (pas seulement relatif à la
    primaire) qu'une auxiliaire doit atteindre pour être empruntée — voir
    ARM_MIN_BORROW_CONFIDENCE/LEG_MIN_BORROW_CONFIDENCE. Sans ce plancher,
    on pourrait emprunter une auxiliaire tout juste meilleure que la
    primaire mais encore en dessous de ce que l'addon exige pour
    appliquer le membre (VISIBILITY_THRESHOLD/LEG_VISIBILITY_THRESHOLD
    côté bone_mapping.py) — l'emprunt serait alors fait pour rien."""

    def __init__(self, indices: tuple[int, int], min_borrow_confidence: float) -> None:
        self._indices = indices
        self._min_borrow_confidence = min_borrow_confidence
        self._active_name: str | None = None
        self._blend_from: dict[int, dict] | None = None
        self._blend_frame = 0
        self._last_output: dict[int, dict] | None = None

    def pick(
        self,
        primary_name: str | None,
        primary_landmarks: list[dict] | None,
        auxiliaries: list[tuple[str, list[dict]]],
    ) -> dict[int, dict] | None:
        """`auxiliaries` : (name, landmarks) pour chaque caméra "pose"
        auxiliaire disposant de données valides cette trame. Retourne
        {index: landmark} pour ce membre (2 entrées), ou None si rien
        d'exploitable du tout (primaire absente ET aucune auxiliaire)."""
        primary_conf = _limb_confidence(primary_landmarks, self._indices)

        chosen_name, chosen_landmarks = primary_name, primary_landmarks
        if primary_conf < PRIMARY_LIMB_CONFIDENCE_THRESHOLD and auxiliaries:
            best_name, best_landmarks, best_conf = None, None, -1.0
            for name, landmarks in auxiliaries:
                conf = _limb_confidence(landmarks, self._indices)
                if conf > best_conf:
                    best_name, best_landmarks, best_conf = name, landmarks, conf
            if (
                best_landmarks is not None
                and best_conf >= self._min_borrow_confidence
                and best_conf > primary_conf + POSE_SWITCH_CONFIDENCE_MARGIN
            ):
                chosen_name, chosen_landmarks = best_name, best_landmarks

        if chosen_landmarks is None:
            self._active_name = None
            self._blend_from = None
            self._last_output = None
            return None

        current = {i: chosen_landmarks[i] for i in self._indices}

        if chosen_name != self._active_name:
            self._blend_from = self._last_output
            self._blend_frame = 0
            self._active_name = chosen_name

        if self._blend_from is not None and self._blend_frame < POSE_SWITCH_BLEND_FRAMES:
            t = (self._blend_frame + 1) / POSE_SWITCH_BLEND_FRAMES
            current = {i: _blend_one_landmark(self._blend_from[i], current[i], t) for i in self._indices}
            self._blend_frame += 1
            if self._blend_frame >= POSE_SWITCH_BLEND_FRAMES:
                self._blend_from = None

        self._last_output = current
        return current


def _select_pose_primary(
    pose_results: list[tuple[str, str, tuple[list[dict], bool, float] | None]], override: str | None
) -> tuple[str | None, tuple[list[dict], bool, float] | None]:
    """Décide quelle caméra "pose" est la primaire cette trame (voir
    PoseSourceFusion et le panneau Blender "Caméra prioritaire (corps)") :
    celle demandée par `override` si elle a des données cette trame,
    sinon la 1ère caméra "pose" de type webcam avec des données. Retourne
    (name, result), ou (None, None) si rien d'exploitable."""
    if override:
        for name, _stype, result in pose_results:
            if name == override and result is not None:
                return name, result
    for name, stype, result in pose_results:
        if stype == "webcam" and result is not None:
            return name, result
    return None, None


class PoseSourceFusion:
    """La caméra PRIMAIRE (la webcam parmi les caméras "pose" — voir son
    point d'appel dans run_multi_camera) pilote SEULE et EN CONTINU le
    buste/bassin (position + rotation, épaules/hanches : indices
    11/12/23/24) — jamais de bascule d'autorité globale.

    Une tentative précédente (bascule du squelette ENTIER selon la
    confiance globale, avec auto-calibration entre caméras pour
    compenser) a été retirée après avoir constaté en test réel qu'elle
    provoquait une inclinaison/accroupissement erroné au moment de la
    bascule : le "z"/profondeur MediaPipe change de sens selon l'angle de
    la caméra (ce qui est une largeur pour une caméra de face devient une
    profondeur pour une caméra de côté) — une simple rotation/translation
    ne suffit pas à corriger ça pour une rotation complète du buste (qui
    dépend fortement de cet axe, voir _torso_orientation_matrix côté
    addon). Voir Limites connues du README.

    Les AUTRES caméras "pose" (téléphones côté/dos) servent uniquement de
    **renfort PAR MEMBRE** (bras/jambe, voir _LimbFusion) : si la
    primaire voit mal un membre précis, on emprunte ses 2 points distaux
    à la caméra auxiliaire la plus confiante pour CE membre, si elle
    dépasse clairement la primaire — jamais le buste. Compromis assumé :
    peut laisser un léger décalage visuel au point d'attache (l'auxiliaire
    empruntée n'est pas exactement dans le même repère que la primaire),
    mais localisé et déjà amorti côté addon (LIMB_DEPTH_DAMPING) — sans
    commune mesure avec le problème de rotation complète du buste que
    cette approche remplace."""

    def __init__(self) -> None:
        self._limbs = {
            _ARM_L_DISTAL: _LimbFusion(_ARM_L_DISTAL, ARM_MIN_BORROW_CONFIDENCE),
            _ARM_R_DISTAL: _LimbFusion(_ARM_R_DISTAL, ARM_MIN_BORROW_CONFIDENCE),
            _LEG_L_DISTAL: _LimbFusion(_LEG_L_DISTAL, LEG_MIN_BORROW_CONFIDENCE),
            _LEG_R_DISTAL: _LimbFusion(_LEG_R_DISTAL, LEG_MIN_BORROW_CONFIDENCE),
        }
        # Nom de caméra imposé par l'utilisateur (panneau Blender —
        # "Caméra prioritaire (corps)") à la place du choix par défaut
        # (1ère caméra "pose" de type webcam) — voir set_primary_override
        # et son point de lecture dans run_multi_camera. None = défaut.
        self.primary_override: str | None = None

    def set_primary_override(self, name: str | None) -> None:
        self.primary_override = name

    def pick(
        self,
        primary_name: str | None,
        primary_result: tuple[list[dict], bool, float] | None,
        auxiliaries: list[tuple[str, list[dict], bool]],
    ) -> tuple[list[dict], bool, float] | None:
        """`auxiliaries` : (name, landmarks, tracking_ok) pour chaque
        caméra "pose" auxiliaire cette trame. Retourne (landmarks,
        tracking_ok, timestamp), même forme que _pick_freshest — None si
        la primaire n'a rien cette trame (pas d'autorité de secours pour
        le buste : voir docstring, compromis assumé)."""
        if primary_result is None:
            return None
        primary_landmarks, tracking_ok, ts = primary_result
        if primary_landmarks is None:
            return None

        aux_ok = [(name, lm) for name, lm, ok in auxiliaries if ok and lm is not None]

        landmarks = list(primary_landmarks)  # buste/épaules/hanches : toujours ceux de la primaire
        for indices, limb_fusion in self._limbs.items():
            borrowed = limb_fusion.pick(primary_name, primary_landmarks, aux_ok)
            if borrowed is not None:
                for i in indices:
                    landmarks[i] = borrowed[i]

        return (landmarks, tracking_ok, ts)


def run_multi_camera(
    host: str,
    port: int,
    config: camera_config.MultiCameraConfig,
    model_paths: dict,
    show_preview: bool,
    phone_http_port: int,
    phone_ws_port: int,
) -> None:
    """Phase 5 : une caméra par rôle (voir camera_config.py) — pas de
    triangulation 3D multi-angle (demanderait un calibrage caméra qui
    n'existe pas ici, voir Limites connues du README). Un CameraWorker
    (thread) par caméra webcam configurée, un PhoneBridge partagé (voir
    phone_server.py, créneaux nommés) pour toutes les caméras "phone". La
    boucle principale ici ne capture rien elle-même : elle "fusionne" à sa
    propre cadence (~30 Hz) les derniers résultats disponibles de chaque
    caméra concernée par chaque type de message et les envoie à l'addon
    Blender via le même protocole TCP qu'en mode source unique
    (protocol.py inchangé). Rôle "pose" : voir PoseSourceFusion (choix de
    la caméra la plus confiante, pas la plus récente, avec transition
    lissée). Rôles "face"/"hands" : voir _pick_freshest (dernier arrivé
    gagne, en général un seul téléphone/une seule webcam par rôle dans les
    configurations actuelles)."""
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((host, port))
    server_sock.listen(1)
    server_sock.settimeout(0.01)
    print(f"[capture_server] en attente de l'addon Blender sur {host}:{port} ...")

    def _describe(cam: camera_config.CameraConfig) -> str:
        roles = "+".join(r for r, on in (("pose", cam.pose), ("face", cam.face), ("hands", cam.hands)) if on)
        return f"{cam.name} ({cam.source_type}, {roles})"

    print(f"[capture_server] {len(config.cameras)} caméra(s) configurée(s) : " + ", ".join(_describe(c) for c in config.cameras))

    pose_cams_config = config.pose_cameras()
    if pose_cams_config and not any(c.source_type == "webcam" for c in pose_cams_config):
        print(
            "[capture_server] ATTENTION : aucune caméra webcam en rôle \"pose\" — "
            "sans \"Caméra prioritaire (corps)\" définie dans le panneau Blender, "
            "PoseSourceFusion n'a aucune autorité pour le buste/bassin et n'enverra "
            "AUCUN tracking du corps (voir README, PoseSourceFusion)."
        )

    workers: dict[str, CameraWorker] = {}
    for cam in config.webcam_cameras():
        worker = CameraWorker(cam, model_paths, show_preview)
        workers[cam.name] = worker
        worker.start()

    phone_cams = config.phone_cameras()
    phone_bridge: PhoneBridge | None = None
    if phone_cams:
        phone_bridge = PhoneBridge(phone_http_port, phone_ws_port)
        phone_bridge.start(cameras=[(c.name, c.pose, c.face) for c in phone_cams])

    # Une fenêtre d'aperçu par caméra téléphone (créée à la demande, au
    # premier passage) : contrairement aux webcams (flux vidéo réel via
    # CameraWorker), un téléphone n'a pas de flux vidéo côté PC — la
    # fenêtre affiche donc uniquement le squelette/maillage sur fond noir,
    # à partir des landmarks déjà reçus par PhoneBridge.
    phone_preview_windows: set[str] = set()

    pose_fusion = PoseSourceFusion()
    # Dernière valeur de pose_fusion.primary_override pour laquelle on a
    # déjà averti qu'elle est introuvable/sans données (voir plus bas) —
    # évite de spammer le terminal à ~30 Hz tant que le problème persiste,
    # tout en réavertissant si l'utilisateur change pour un autre nom
    # tout aussi invalide.
    last_warned_invalid_override: str | None = None

    stability_targets: list = list(workers.values())
    if phone_bridge is not None:
        stability_targets.append(phone_bridge)

    client: ClientConnection | None = None

    try:
        while True:
            if client is None:
                try:
                    conn, addr = server_sock.accept()
                    print(f"[capture_server] addon connecté depuis {addr}")
                    client = ClientConnection(conn, stability_targets, pose_fusion=pose_fusion)
                except socket.timeout:
                    pass

            time.sleep(1.0 / 30.0)

            # Résultat brut de chaque caméra "pose" cette trame, quel que
            # soit son rôle (primaire ou auxiliaire) — décidé ensuite.
            pose_results: list[tuple[str, str, tuple[list[dict], bool, float] | None]] = []
            for c in config.pose_cameras():
                if c.source_type == "webcam":
                    result = workers[c.name].get_latest_frame() if c.name in workers else None
                else:
                    result = phone_bridge.get_latest_frame(c.name) if phone_bridge is not None else None
                pose_results.append((c.name, c.source_type, result))

            primary_name, primary_result = _select_pose_primary(pose_results, pose_fusion.primary_override)

            override = pose_fusion.primary_override
            if override and primary_name != override:
                if last_warned_invalid_override != override:
                    print(
                        f"[capture_server] ATTENTION : caméra prioritaire '{override}' introuvable "
                        "ou sans données cette trame — vérifiez l'orthographe exacte dans "
                        "cameras.json. Repli sur le comportement par défaut (1ère webcam trouvée)."
                    )
                    last_warned_invalid_override = override
            else:
                last_warned_invalid_override = None

            aux_candidates = [
                (name, result[0], result[1])
                for name, _stype, result in pose_results
                if name != primary_name and result is not None
            ]
            frame_result = pose_fusion.pick(primary_name, primary_result, aux_candidates)
            face_result = _pick_freshest(
                [workers[c.name].get_latest_face() for c in config.face_cameras() if c.name in workers]
                + ([phone_bridge.get_latest_face(c.name) for c in phone_cams if c.face] if phone_bridge is not None else [])
            )
            hands_result = _pick_freshest(
                [workers[c.name].get_latest_hands() for c in config.hands_cameras() if c.name in workers]
            )

            if show_preview and phone_bridge is not None:
                for cam in phone_cams:
                    win_name = f"CORPUS-MOCAP - {cam.name}"
                    if win_name not in phone_preview_windows:
                        cv2.namedWindow(win_name, cv2.WINDOW_NORMAL)
                        phone_preview_windows.add(win_name)

                    cam_frame = np.zeros((480, 640, 3), dtype=np.uint8)
                    if cam.pose:
                        cam_frame_result = phone_bridge.get_latest_frame(cam.name)
                        if cam_frame_result is not None:
                            cam_landmarks, cam_tracking_ok, _ts = cam_frame_result
                            draw_preview(cam_frame, cam_landmarks, cam_tracking_ok)
                    if cam.face:
                        cam_face_result = phone_bridge.get_latest_face(cam.name)
                        cam_face_tracking_ok = cam_face_result is not None and cam_face_result[1]
                        draw_face_preview(cam_frame, phone_bridge.get_latest_face_points(cam.name))
                        face_status = "Visage OK" if cam_face_tracking_ok else "Visage non détecté"
                        face_color = (0, 200, 0) if cam_face_tracking_ok else (0, 0, 220)
                        cv2.putText(cam_frame, face_status, (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.7, face_color, 2)
                    if not phone_bridge.is_connected(cam.name):
                        cv2.putText(
                            cam_frame, "En attente du telephone...", (10, 75),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 165, 255), 2,
                        )
                    cv2.putText(
                        cam_frame, cam.name, (10, cam_frame.shape[0] - 15),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2,
                    )
                    cv2.imshow(win_name, cam_frame)
                cv2.waitKey(1)

            if client is not None:
                client.poll_control_messages()
                ok_body = True
                if frame_result is not None:
                    landmarks, tracking_ok, _ts = frame_result
                    ok_body = client.send_frame(landmarks, tracking_ok)
                ok_face = True
                if face_result is not None:
                    blendshapes, face_tracking_ok, head_rotation, _ts = face_result
                    ok_face = client.send_face(blendshapes, face_tracking_ok, head_rotation)
                ok_hands = True
                if hands_result is not None:
                    hands, hands_tracking_ok, _ts = hands_result
                    ok_hands = client.send_hands(hands, hands_tracking_ok)
                if not (ok_body and ok_face and ok_hands):
                    print("[capture_server] addon déconnecté, en attente d'une nouvelle connexion")
                    client.sock.close()
                    client = None

    except KeyboardInterrupt:
        print("[capture_server] arrêt demandé")
    finally:
        for worker in workers.values():
            worker.stop()
        for worker in workers.values():
            worker.join(timeout=2.0)
        if phone_bridge is not None:
            phone_bridge.stop()
        server_sock.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CORPUS-MOCAP capture_server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9001)
    parser.add_argument(
        "--source", choices=("webcam", "phone"), default="webcam",
        help="webcam (défaut, OpenCV + MediaPipe Python) ou phone (MediaPipe.js sur le téléphone, voir phone_server.py)",
    )
    parser.add_argument("--camera", type=int, default=0, help="Index de la webcam OpenCV (mode webcam)")
    parser.add_argument("--model", default=DEFAULT_MODEL_PATH, help="Chemin vers le fichier .task du modèle de pose")
    parser.add_argument(
        "--face-model", default=DEFAULT_FACE_MODEL_PATH, help="Chemin vers le fichier .task du modèle de visage"
    )
    parser.add_argument("--no-face", action="store_true", help="Désactive le tracking du visage")
    parser.add_argument(
        "--hand-model", default=DEFAULT_HAND_MODEL_PATH, help="Chemin vers le fichier .task du modèle de mains"
    )
    parser.add_argument("--no-hands", action="store_true", help="Désactive le tracking des mains")
    parser.add_argument("--no-preview", action="store_true", help="Désactive la fenêtre d'aperçu")
    parser.add_argument(
        "--phone-http-port", type=int, default=8080, help="Port HTTP de la page web du téléphone (mode phone)"
    )
    parser.add_argument(
        "--phone-ws-port", type=int, default=8766, help="Port WebSocket des landmarks du téléphone (mode phone)"
    )
    parser.add_argument(
        "--cameras", default=None,
        help=(
            "Chemin vers un fichier de configuration multi-caméra (Phase 5, voir "
            "camera_config.py et cameras.example.json) — active le mode multi-caméra "
            "à rôles (une caméra par rôle : corps/visage/mains, nombre illimité, "
            "webcams et téléphones combinables) et ignore --source/--camera/--no-face/"
            "--no-hands (chaque caméra du fichier définit son propre rôle)."
        ),
    )
    args = parser.parse_args()

    if args.cameras:
        config = camera_config.load(args.cameras)
        run_multi_camera(
            args.host,
            args.port,
            config,
            model_paths={"pose": args.model, "face": args.face_model, "hands": args.hand_model},
            show_preview=not args.no_preview,
            phone_http_port=args.phone_http_port,
            phone_ws_port=args.phone_ws_port,
        )
    else:
        run(
            args.host,
            args.port,
            args.camera,
            args.model,
            None if args.no_face else args.face_model,
            None if args.no_hands else args.hand_model,
            show_preview=not args.no_preview,
            source=args.source,
            phone_http_port=args.phone_http_port,
            phone_ws_port=args.phone_ws_port,
        )
