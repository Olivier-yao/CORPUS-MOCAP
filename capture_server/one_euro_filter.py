"""Implémentation du One Euro Filter (Casiez et al., 2012).

Réduit le jitter de tracking tout en gardant une latence faible sur les
mouvements rapides. Un filtre scalaire par canal (x, y, z de chaque
landmark) ; `LandmarkFilter` regroupe 33 filtres 3D, un par point.
"""

from __future__ import annotations

import time


class _LowPassFilter:
    def __init__(self, alpha: float, initial_value: float = 0.0):
        self._alpha = alpha
        self._value = initial_value
        self._initialized = False

    def filter(self, value: float, alpha: float | None = None) -> float:
        if alpha is not None:
            self._alpha = alpha
        if not self._initialized:
            self._value = value
            self._initialized = True
        else:
            self._value = self._alpha * value + (1.0 - self._alpha) * self._value
        return self._value


class OneEuroFilter:
    """Filtre scalaire. min_cutoff et beta pilotent le compromis
    lissage/latence : min_cutoff bas = plus de lissage au repos,
    beta haut = moins de lag sur les mouvements rapides."""

    def __init__(self, min_cutoff: float = 1.0, beta: float = 0.0, d_cutoff: float = 1.0):
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self._x_filter = _LowPassFilter(1.0)
        self._dx_filter = _LowPassFilter(1.0)
        self._last_time: float | None = None

    @staticmethod
    def _alpha(cutoff: float, dt: float) -> float:
        tau = 1.0 / (2 * 3.141592653589793 * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def filter(self, value: float, timestamp: float | None = None) -> float:
        now = timestamp if timestamp is not None else time.monotonic()
        if self._last_time is None:
            dt = 1.0 / 30.0
        else:
            dt = max(now - self._last_time, 1e-6)
        self._last_time = now

        prev_x = self._x_filter._value if self._x_filter._initialized else value
        dx = (value - prev_x) / dt
        edx = self._dx_filter.filter(dx, self._alpha(self.d_cutoff, dt))

        cutoff = self.min_cutoff + self.beta * abs(edx)
        return self._x_filter.filter(value, self._alpha(cutoff, dt))


class LandmarkFilter:
    """Filtre un flux de 33 landmarks (x, y, z) avec, en cas de perte de
    tracking, gel de la dernière position connue au lieu de transmettre
    une donnée aberrante (Module 2 du cahier des charges)."""

    def __init__(self, num_landmarks: int):
        self._filters = [
            {axis: OneEuroFilter() for axis in ("x", "y", "z")}
            for _ in range(num_landmarks)
        ]
        self._last_landmarks: list[dict] | None = None
        self.set_stability(0.5)

    def set_stability(self, stability: float) -> None:
        """stability in [0, 1] : 0 = léger, 1 = fort."""
        stability = max(0.0, min(1.0, stability))
        min_cutoff = 1.0 - 0.9 * stability   # 1.0 (léger) -> 0.1 (fort)
        beta = 0.7 * (1.0 - stability)        # 0.7 (léger) -> 0.0 (fort)
        for landmark_filters in self._filters:
            for f in landmark_filters.values():
                f.min_cutoff = min_cutoff
                f.beta = beta

    def process(self, raw_landmarks: list[dict] | None) -> list[dict]:
        """raw_landmarks=None signifie perte de tracking : on renvoie la
        dernière trame filtrée connue (freeze)."""
        if raw_landmarks is None:
            if self._last_landmarks is None:
                return [{"x": 0.0, "y": 0.0, "z": 0.0, "visibility": 0.0}] * len(self._filters)
            return self._last_landmarks

        now = time.monotonic()
        result = []
        for lm, filters in zip(raw_landmarks, self._filters):
            result.append({
                "x": filters["x"].filter(lm["x"], now),
                "y": filters["y"].filter(lm["y"], now),
                "z": filters["z"].filter(lm["z"], now),
                "visibility": lm.get("visibility", 1.0),
            })
        self._last_landmarks = result
        return result


class BlendshapeFilter:
    """Filtre indépendant par nom de coefficient blendshape (52 noms
    ARKit possibles, créés à la volée au premier passage) — même principe
    que LandmarkFilter mais pour le visage (Module 2 du cahier des
    charges : lissage attendu sur corps ET visage)."""

    def __init__(self):
        self._filters: dict[str, OneEuroFilter] = {}
        self._min_cutoff = 1.0
        self._beta = 0.0
        self.set_stability(0.5)

    def set_stability(self, stability: float) -> None:
        stability = max(0.0, min(1.0, stability))
        self._min_cutoff = 1.0 - 0.9 * stability
        self._beta = 0.7 * (1.0 - stability)
        for f in self._filters.values():
            f.min_cutoff = self._min_cutoff
            f.beta = self._beta

    def process(self, raw: dict[str, float] | None) -> dict[str, float] | None:
        if raw is None:
            return None
        now = time.monotonic()
        result = {}
        for name, value in raw.items():
            if name not in self._filters:
                self._filters[name] = OneEuroFilter(self._min_cutoff, self._beta)
            result[name] = self._filters[name].filter(value, now)
        return result


def _orthonormalize_3x3(m: list[float]) -> list[float]:
    """Ré-orthonormalise (Gram-Schmidt) une matrice 3x3 (9 floats, ligne
    par ligne) : un filtrage indépendant composante par composante ne
    préserve pas l'orthonormalité requise pour une matrice de rotation."""

    def sub(a, b):
        return [a[i] - b[i] for i in range(3)]

    def dot(a, b):
        return sum(a[i] * b[i] for i in range(3))

    def scale(a, s):
        return [x * s for x in a]

    def norm(a):
        n = dot(a, a) ** 0.5
        return [x / n for x in a] if n > 1e-8 else a

    r0 = norm(m[0:3])
    r1 = norm(sub(m[3:6], scale(r0, dot(m[3:6], r0))))
    r2 = norm(sub(sub(m[6:9], scale(r0, dot(m[6:9], r0))), scale(r1, dot(m[6:9], r1))))
    return r0 + r1 + r2


class HeadRotationFilter:
    """Filtre les 9 éléments de la sous-matrice de rotation de tête, puis
    ré-orthonormalise le résultat pour rester une rotation valide."""

    def __init__(self):
        self._filters = [OneEuroFilter() for _ in range(9)]
        self.set_stability(0.5)

    def set_stability(self, stability: float) -> None:
        stability = max(0.0, min(1.0, stability))
        min_cutoff = 1.0 - 0.9 * stability
        beta = 0.7 * (1.0 - stability)
        for f in self._filters:
            f.min_cutoff = min_cutoff
            f.beta = beta

    def process(self, raw: list[float] | None) -> list[float] | None:
        if raw is None:
            return None
        now = time.monotonic()
        smoothed = [self._filters[i].filter(raw[i], now) for i in range(9)]
        return _orthonormalize_3x3(smoothed)
