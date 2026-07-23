"""Client TCP non-bloquant vers capture_server (voir protocol.py du serveur
pour le format des messages : une ligne JSON par trame)."""

from __future__ import annotations

import json
import socket


class SocketClientError(Exception):
    pass


class MocapSocketClient:
    def __init__(self, host: str, port: int, timeout: float = 2.0):
        self._sock = socket.create_connection((host, port), timeout=timeout)
        self._sock.setblocking(False)
        self._recv_buffer = b""

    def send_control(self, message: dict) -> None:
        payload = (json.dumps(message) + "\n").encode("utf-8")
        try:
            self._sock.sendall(payload)
        except (BlockingIOError, InterruptedError):
            pass
        except OSError as exc:
            raise SocketClientError(str(exc)) from exc

    def poll_latest(self) -> dict[str, dict]:
        """Lit toutes les données disponibles et ne garde que la dernière
        trame complète par type de message ("frame" pour le corps, "face"
        pour le visage, "hands" pour les mains) — on veut du temps réel,
        pas un historique."""
        try:
            data = self._sock.recv(65536)
            if not data:
                raise SocketClientError("connexion fermée par capture_server")
            self._recv_buffer += data
        except BlockingIOError:
            pass
        except OSError as exc:
            raise SocketClientError(str(exc)) from exc

        latest_by_type: dict[str, dict] = {}
        while b"\n" in self._recv_buffer:
            line, self._recv_buffer = self._recv_buffer.split(b"\n", 1)
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg_type = msg.get("type")
            if msg_type in ("frame", "face", "hands"):
                latest_by_type[msg_type] = msg
        return latest_by_type

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass
