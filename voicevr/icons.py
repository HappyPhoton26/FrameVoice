"""Draws the small status icons (64x64 RGBA PNG) shown in the headset; pure Python, no imaging library."""
import math
import os
import struct
import zlib

S = 64


def _png(path, px):
    raw = b"".join(b"\0" + bytes(row) for row in px)
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", S, S, 8, 6, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def _seg(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def _glyph(kind, x, y):
    """Distance (px) from the white glyph's edge; negative inside."""
    c = S / 2
    if kind in ("listening", "thinking", "button", "button-off"):
        body = _seg(x, y, c, c - 10, c, c - 1) - 6                     # mic capsule
        arc = abs(math.hypot(x - c, y - (c - 3)) - 11) - 1.6           # holder arc (lower half)
        if y < c - 3:
            arc = 99
        stem = _seg(x, y, c, c + 8, c, c + 13) - 1.6
        base = _seg(x, y, c - 6, c + 13, c + 6, c + 13) - 1.6
        return min(body, arc, stem, base)
    if kind == "ok":
        return min(_seg(x, y, c - 11, c + 1, c - 3, c + 9), _seg(x, y, c - 3, c + 9, c + 12, c - 8)) - 3
    return min(_seg(x, y, c - 9, c - 9, c + 9, c + 9), _seg(x, y, c - 9, c + 9, c + 9, c - 9)) - 3   # fail


COLORS = {"button": (220, 40, 50), "button-off": (90, 90, 90), "listening": (220, 40, 50), "thinking": (235, 160, 20), "ok": (40, 170, 80), "fail": (200, 40, 40)}


def make_icons(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    for kind, (r, g, b) in COLORS.items():
        path = os.path.join(out_dir, kind + ".png")
        if os.path.exists(path):
            continue
        px = []
        for yy in range(S):
            row = []
            for xx in range(S):
                x, y = xx + 0.5, yy + 0.5
                disc = max(0.0, min(1.0, S / 2 - 1 - math.hypot(x - S / 2, y - S / 2)))   # antialiased disc
                w = max(0.0, min(1.0, 0.5 - _glyph(kind, x, y)))                         # white glyph
                a = max(disc, w)
                rr, gg, bb = (r * (1 - w) + 255 * w, g * (1 - w) + 255 * w, b * (1 - w) + 255 * w)
                row += [int(rr), int(gg), int(bb), int(255 * a)]
            px.append(row)
        _png(path, px)
