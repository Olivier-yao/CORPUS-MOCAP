"""CORPUS-MOCAP — capture_server (Phase 1+2, source webcam PC).

Process externe, indépendant de Blender : capture la webcam via OpenCV,
détecte le squelette (MediaPipe Pose) et le visage (MediaPipe Face
Landmarker, coefficients blend shapes ARKit) via la Tasks API — l'ancienne
API `mp.solutions.*` a été retirée du paquet à partir de mediapipe 0.10.x
récents —, lisse le signal corps (One Euro Filter) et diffuse le tout à
l'addon Blender via un socket TCP local (une ligne JSON par trame, voir
protocol.py).

Nécessite les modèles "pose_landmarker_lite.task" et "face_landmarker.task"
dans ./models/ (voir README.md pour le téléchargement). Le visage peut être
désactivé avec --no-face si seul le corps est nécessaire.

Une fenêtre d'aperçu (flux caméra + squelette détecté superposé) s'ouvre
par défaut pour vérifier le cadrage avant/pendant l'enregistrement dans
Blender (cahier des charges, Module 1). Désactivable avec --no-preview.

Usage :
    python server.py [--host 127.0.0.1] [--port 9001] [--camera 0]
                      [--model models/pose_landmarker_lite.task]
                      [--face-model models/face_landmarker.task] [--no-face]
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
from mediapipe.tasks.python import vision
from mediapipe.tasks.python.core.base_options import BaseOptions

from one_euro_filter import BlendshapeFilter, HeadRotationFilter, LandmarkFilter
from protocol import LANDMARK_INDEX, NUM_LANDMARKS, build_face_message, build_frame_message

del LANDMARK_INDEX  # référencé pour clarté ; le mapping vit côté addon

MODELS_DIR = os.path.join(os.path.dirname(__file__), "models")
DEFAULT_MODEL_PATH = os.path.join(MODELS_DIR, "pose_landmarker_lite.task")
DEFAULT_FACE_MODEL_PATH = os.path.join(MODELS_DIR, "face_landmarker.task")

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


class ClientConnection:
    """Gère l'unique client connecté (l'addon Blender) : envoi des trames,
    lecture des messages de contrôle (ex: changement de stabilité)."""

    def __init__(
        self,
        sock: socket.socket,
        landmark_filter: LandmarkFilter,
        blendshape_filter: BlendshapeFilter,
        head_rotation_filter: HeadRotationFilter,
    ):
        self.sock = sock
        self.sock.setblocking(False)
        self._recv_buffer = b""
        self._landmark_filter = landmark_filter
        self._blendshape_filter = blendshape_filter
        self._head_rotation_filter = head_rotation_filter
        self._lock = threading.Lock()

    def send_frame(self, landmarks: list[dict], tracking_ok: bool) -> bool:
        return self._send(build_frame_message(landmarks, tracking_ok))

    def send_face(
        self, blendshapes: dict[str, float], tracking_ok: bool, head_rotation: list[float] | None = None
    ) -> bool:
        return self._send(build_face_message(blendshapes, tracking_ok, head_rotation))

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
                self._landmark_filter.set_stability(value)
                self._blendshape_filter.set_stability(value)
                self._head_rotation_filter.set_stability(value)


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


def run(
    host: str,
    port: int,
    camera_index: int,
    model_path: str,
    face_model_path: str | None,
    show_preview: bool = True,
) -> None:
    landmark_filter = LandmarkFilter(NUM_LANDMARKS)
    blendshape_filter = BlendshapeFilter()
    head_rotation_filter = HeadRotationFilter()

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((host, port))
    server_sock.listen(1)
    server_sock.settimeout(0.01)
    print(f"[capture_server] en attente de l'addon Blender sur {host}:{port} ...")

    # Sous Windows, le backend par défaut d'OpenCV (MSMF) peut se bloquer
    # indéfiniment à l'ouverture sur certaines machines même si la caméra
    # fonctionne très bien ailleurs ; DirectShow est nettement plus fiable.
    if sys.platform == "win32":
        cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
    else:
        cap = cv2.VideoCapture(camera_index)
    if not cap.isOpened():
        raise RuntimeError(f"Impossible d'ouvrir la caméra index={camera_index}")

    landmarker = create_pose_landmarker(model_path)
    face_landmarker = create_face_landmarker(face_model_path) if face_model_path else None
    frame_timestamp_ms = 0

    client: ClientConnection | None = None

    if show_preview:
        print(f"[capture_server] aperçu ouvert dans une fenêtre séparée ({PREVIEW_WINDOW_NAME})")

    try:
        while True:
            if client is None:
                try:
                    conn, addr = server_sock.accept()
                    print(f"[capture_server] addon connecté depuis {addr}")
                    client = ClientConnection(conn, landmark_filter, blendshape_filter, head_rotation_filter)
                except socket.timeout:
                    pass

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
            if face_landmarker is not None:
                face_result = face_landmarker.detect_for_video(mp_image, frame_timestamp_ms)
                raw_blendshapes = extract_blendshapes(face_result)
                raw_head_rotation = extract_head_rotation(face_result)
                face_tracking_ok = raw_blendshapes is not None
                blendshapes = blendshape_filter.process(raw_blendshapes)
                head_rotation = head_rotation_filter.process(raw_head_rotation)
                if show_preview:
                    face_points_2d = extract_face_points_2d(face_result)

            if show_preview:
                draw_preview(frame, raw_landmarks, tracking_ok)
                if face_landmarker is not None:
                    draw_face_preview(frame, face_points_2d)
                    face_status = "Visage OK" if face_tracking_ok else "Visage non détecté"
                    face_color = (0, 200, 0) if face_tracking_ok else (0, 0, 220)
                    cv2.putText(frame, face_status, (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.7, face_color, 2)
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
                if not (ok_body and ok_face):
                    print("[capture_server] addon déconnecté, en attente d'une nouvelle connexion")
                    client.sock.close()
                    client = None

    except KeyboardInterrupt:
        print("[capture_server] arrêt demandé")
    finally:
        cap.release()
        landmarker.close()
        if face_landmarker is not None:
            face_landmarker.close()
        server_sock.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CORPUS-MOCAP capture_server (Phase 1)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9001)
    parser.add_argument("--camera", type=int, default=0, help="Index de la webcam OpenCV")
    parser.add_argument("--model", default=DEFAULT_MODEL_PATH, help="Chemin vers le fichier .task du modèle de pose")
    parser.add_argument(
        "--face-model", default=DEFAULT_FACE_MODEL_PATH, help="Chemin vers le fichier .task du modèle de visage"
    )
    parser.add_argument("--no-face", action="store_true", help="Désactive le tracking du visage")
    parser.add_argument("--no-preview", action="store_true", help="Désactive la fenêtre d'aperçu caméra")
    args = parser.parse_args()
    run(
        args.host,
        args.port,
        args.camera,
        args.model,
        None if args.no_face else args.face_model,
        show_preview=not args.no_preview,
    )
