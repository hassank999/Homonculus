"""Grid geometry: line of sight, bearings, and distance-dependent quantization.

Screen convention: +x east, +y south. Absolute bearing is atan2(dy, dx) in
degrees, so East=0, South=90, West=180, North=270.

The quantization here implements the resolution falloff from PLAN.md §4.1 —
fine near the body, coarse far away. That is both biologically motivated
(cortical magnification) and token economy: far-away entities shouldn't spend
precision the agent can't act on.
"""

from __future__ import annotations

import math

# (range_ceiling, range_step, bearing_step) — first matching band wins.
_BANDS: tuple[tuple[float, float, float], ...] = (
    (3.0, 0.5, 15.0),
    (8.0, 1.0, 30.0),
    (float("inf"), 2.0, 45.0),
)


def dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def abs_bearing(frm: tuple[float, float], to: tuple[float, float]) -> float:
    return math.degrees(math.atan2(to[1] - frm[1], to[0] - frm[0])) % 360.0


def rel_bearing(abs_deg: float, heading_deg: float) -> float:
    """Signed bearing relative to heading, in (-180, 180]."""
    d = (abs_deg - heading_deg) % 360.0
    return d - 360.0 if d > 180.0 else d


def _band(r: float) -> tuple[float, float]:
    for ceil, rstep, bstep in _BANDS:
        if r < ceil:
            return rstep, bstep
    return _BANDS[-1][1], _BANDS[-1][2]


def quantize(r: float, bearing: float) -> tuple[float, float]:
    """Round range and bearing to the precision the band allows."""
    rstep, bstep = _band(r)
    qr = round(round(r / rstep) * rstep, 3)
    qb = round(round(bearing / bstep) * bstep, 3)
    if qb <= -180.0:
        qb += 360.0
    return qr, qb


def quantization_var(r: float) -> float:
    """Positional variance contributed purely by coarse sensing at range r.

    Bearing is quantized in bands, so lateral uncertainty grows with distance
    (a 45-degree bin at range 12 is a wide arc); radial uncertainty is the range
    step. Uniform error over a bin of width w has variance w^2/12.
    """
    rstep, bstep = _band(r)
    lateral = r * math.radians(bstep)
    return (lateral ** 2 + rstep ** 2) / 12.0


def polar_to_offset(r: float, rel_deg: float, heading_deg: float) -> tuple[float, float]:
    """Inverse of the egocentric encoding: back to an (dx, dy) world offset."""
    a = math.radians((heading_deg + rel_deg) % 360.0)
    return r * math.cos(a), r * math.sin(a)


def line_cells(a: tuple[int, int], b: tuple[int, int]) -> list[tuple[int, int]]:
    """Bresenham cells from a to b inclusive."""
    x0, y0 = a
    x1, y1 = b
    cells = []
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    while True:
        cells.append((x0, y0))
        if x0 == x1 and y0 == y1:
            return cells
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


def visible(a: tuple[int, int], b: tuple[int, int], walls: set[tuple[int, int]]) -> bool:
    """True if no wall strictly between a and b blocks the ray."""
    for c in line_cells(a, b)[1:-1]:
        if c in walls:
            return False
    return True
