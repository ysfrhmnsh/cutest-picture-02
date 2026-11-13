#!/usr/bin/env python3
"""Milo plays with yarn -- a deterministic procedural cute-picture generator.

Entry for make.fun bounty "Make the cutest picture" (cutest-picture-01),
second entry (v2). Different scene from v1 ("Momo the kitten", front-facing
portrait): here a fluffy cream kitten sits on a pink rug in a lavender
garden, one paw raised batting at a big coral ball of yarn, eyes looking up
at it, tail curled around its side.

The program draws one 1024x1024 picture and writes it to
/output/entry.png (or $MAKEFUN_OUTPUT_DIR/entry.png).

Pure Python 3.13 standard library: no PIL, no network, no model calls, no
clock, no unseeded randomness. Every shape is a fixed constant, so two runs
write byte-identical output.

Rendering notes
---------------
* The canvas is three separate byte planes (R, G, B) at 2x supersampling
  (2048x2048).  Translucent spans are blended with bytes.translate() and a
  per-colour lookup table, which runs in C and keeps the whole render fast.
* Shapes are drawn with opaque span fills (slice assignment) and soft
  radial-gradient shapes are approximated by horizontal constant-alpha bands.
* The 2x canvas is downsampled 2x2 to 1024x1024, then encoded as a PNG with
  zlib (filter 0 per row) written by hand with struct + crc32.
"""

import math
import os
import struct
import zlib

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

OUT_NAME = "entry.png"
SIZE = 1024            # final image is SIZE x SIZE
SS = 2                 # supersampling factor
W2 = SIZE * SS         # internal canvas width/height

# --------------------------------------------------------------------------
# Palette (soft pastel, warm and cohesive)
# --------------------------------------------------------------------------

SKY_TOP = (159, 212, 242)
SKY_MID = (216, 240, 251)
SKY_LOW = (255, 241, 207)
SUN_GLOW = (255, 247, 192)
SUN_DISC = (255, 251, 232)
CLOUD = (255, 255, 255)
MEADOW_TOP = (183, 230, 164)
MEADOW_BOT = (124, 196, 127)
HILL = (140, 203, 132)
SHADOW = (62, 122, 58)

FUR = (255, 246, 232)
FUR_SHADE = (243, 211, 167)
FUR_STRIPE = (240, 176, 112)
OUTLINE = (107, 66, 38)
INNER_EAR = (255, 179, 193)
NOSE = (255, 143, 168)
NOSE_DARK = (217, 108, 132)
BLUSH = (255, 159, 178)
IRIS = (138, 90, 43)
PUPIL = (36, 20, 14)
WHITE = (255, 255, 255)
BOW = (255, 143, 171)
BOW_DARK = (232, 90, 133)
WHISKER = (216, 207, 196)
FLOWER_WHITE = (255, 255, 255)
FLOWER_PINK = (255, 194, 212)
FLOWER_YELLOW = (255, 214, 107)
GRASS = (95, 168, 94)
BUTTERFLY_WING = (191, 227, 255)
BUTTERFLY_EDGE = (127, 184, 232)


# --------------------------------------------------------------------------
# Canvas: three byte planes, all coordinates in logical 1024-space
# --------------------------------------------------------------------------

# Blend lookup tables are a pure function of (source colour, alpha); cache
# them because soft shapes issue tens of thousands of constant-alpha spans.
_TABLE_CACHE = {}


def _blend_tables(rgb, alpha):
    key = (rgb[0], rgb[1], rgb[2], round(alpha, 2))
    tables = _TABLE_CACHE.get(key)
    if tables is None:
        inv = 1.0 - alpha
        tables = tuple(
            bytes(min(255, int(src * alpha + d * inv + 0.5))
                  for d in range(256))
            for src in rgb)
        _TABLE_CACHE[key] = tables
    return tables


class Canvas:
    def __init__(self, size, ss):
        self.size = size
        self.ss = ss
        self.w = size * ss
        self.h = size * ss
        n = self.w * self.h
        self.planes = [bytearray(n), bytearray(n), bytearray(n)]

    # -- coordinate helpers -------------------------------------------------
    def _sx(self, x):
        return int(round(x * self.ss))

    # -- opaque span fill ---------------------------------------------------
    def span(self, y, x0, x1, rgb):
        """Fill [x0, x1) on row y (logical coords, floats allowed)."""
        yi = self._sx(y)
        if yi < 0 or yi >= self.h:
            return
        a = max(0, self._sx(x0))
        b = min(self.w, self._sx(x1))
        if b <= a:
            return
        base = yi * self.w
        for ch in range(3):
            self.planes[ch][base + a:base + b] = bytes([rgb[ch]]) * (b - a)

    # -- translucent span fill (constant alpha, C-speed via translate) ------
    def blend_span(self, y, x0, x1, rgb, alpha):
        yi = self._sx(y)
        if yi < 0 or yi >= self.h or alpha <= 0.0:
            return
        a = max(0, self._sx(x0))
        b = min(self.w, self._sx(x1))
        if b <= a:
            return
        if alpha >= 1.0:
            self.span(y, x0, x1, rgb)
            return
        base = yi * self.w
        tables = _blend_tables(rgb, alpha)
        for ch in range(3):
            pl = self.planes[ch]
            pl[base + a:base + b] = pl[base + a:base + b].translate(tables[ch])

    # -- filled ellipse (opaque or translucent) ------------------------------
    def ellipse(self, cx, cy, rx, ry, rgb, alpha=1.0):
        if rx <= 0 or ry <= 0:
            return
        cx2, cy2, rx2, ry2 = cx * self.ss, cy * self.ss, rx * self.ss, ry * self.ss
        y0 = max(0, int(math.floor(cy2 - ry2)))
        y1 = min(self.h - 1, int(math.ceil(cy2 + ry2)))
        for yi in range(y0, y1 + 1):
            dy = (yi + 0.5 - cy2) / ry2
            if abs(dy) >= 1.0:
                continue
            dx = rx2 * math.sqrt(1.0 - dy * dy)
            x0 = (cx2 - dx) / self.ss
            x1 = (cx2 + dx) / self.ss
            if alpha >= 1.0:
                self.span(yi / self.ss, x0, x1, rgb)
            else:
                self.blend_span(yi / self.ss, x0, x1, rgb, alpha)

    def circle(self, cx, cy, r, rgb, alpha=1.0):
        self.ellipse(cx, cy, r, r, rgb, alpha)

    # -- soft radial-gradient ellipse (alpha falls to 0 at the edge) ---------
    def soft_ellipse(self, cx, cy, rx, ry, rgb, a0, bands=20):
        """Radial falloff: alpha = a0 * (1 - t), t = normalised radius^2.

        Approximated with horizontal constant-alpha bands so the fast
        translate-based blend can be used.
        """
        if rx <= 0 or ry <= 0 or a0 <= 0.0:
            return
        cx2, cy2, rx2, ry2 = cx * self.ss, cy * self.ss, rx * self.ss, ry * self.ss
        y0 = max(0, int(math.floor(cy2 - ry2)))
        y1 = min(self.h - 1, int(math.ceil(cy2 + ry2)))
        for yi in range(y0, y1 + 1):
            dy = (yi + 0.5 - cy2) / ry2
            if abs(dy) >= 1.0:
                continue
            dx = rx2 * math.sqrt(1.0 - dy * dy)
            # Split the row into bands; alpha at band centre.
            span_w = 2.0 * dx / self.ss
            if span_w <= 0:
                continue
            step = span_w / bands
            for i in range(bands):
                bx0 = (cx2 - dx) / self.ss + i * step
                bx1 = bx0 + step
                mid = (bx0 + bx1) / 2.0
                tx = ((mid - cx) / rx) ** 2 + dy * dy
                a = a0 * max(0.0, 1.0 - tx)
                if a > 0.01:
                    self.blend_span(yi / self.ss, bx0, bx1, rgb, a)

    # -- filled polygon (scanline) -------------------------------------------
    def polygon(self, pts, rgb, alpha=1.0):
        if len(pts) < 3:
            return
        pts2 = [(x * self.ss, y * self.ss) for (x, y) in pts]
        ys = [p[1] for p in pts2]
        y0 = max(0, int(math.floor(min(ys))))
        y1 = min(self.h - 1, int(math.ceil(max(ys))))
        n = len(pts2)
        for yi in range(y0, y1 + 1):
            yc = yi + 0.5
            xs = []
            for i in range(n):
                x1, yy1 = pts2[i]
                x2, yy2 = pts2[(i + 1) % n]
                if yy1 == yy2:
                    continue
                lo, hi = (yy1, yy2) if yy1 < yy2 else (yy2, yy1)
                if lo <= yc < hi:
                    t = (yc - yy1) / (yy2 - yy1)
                    xs.append(x1 + t * (x2 - x1))
            if len(xs) < 2:
                continue
            xs.sort()
            for k in range(0, len(xs) - 1, 2):
                x0 = xs[k] / self.ss
                x1 = xs[k + 1] / self.ss
                if alpha >= 1.0:
                    self.span(yi / self.ss, x0, x1, rgb)
                else:
                    self.blend_span(yi / self.ss, x0, x1, rgb, alpha)

    def polygon_outline(self, pts, rgb, scale=1.05, alpha=1.0):
        """Draw a polygon slightly enlarged about its centroid (outline)."""
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        big = [(cx + (x - cx) * scale, cy + (y - cy) * scale) for (x, y) in pts]
        self.polygon(big, rgb, alpha)

    # -- thick polyline / curve (round caps via circles) ----------------------
    def thick_line(self, x1, y1, x2, y2, r, rgb, alpha=1.0):
        dx, dy = x2 - x1, y2 - y1
        dist = math.hypot(dx, dy)
        steps = max(1, int(dist / max(1.0, r * 0.7)) + 1)
        for i in range(steps + 1):
            t = i / steps
            self.circle(x1 + dx * t, y1 + dy * t, r, rgb, alpha)

    def bezier(self, p0, p1, p2, r, rgb, alpha=1.0, steps=24):
        prev = p0
        for i in range(1, steps + 1):
            t = i / steps
            mt = 1.0 - t
            x = mt * mt * p0[0] + 2 * mt * t * p1[0] + t * t * p2[0]
            y = mt * mt * p0[1] + 2 * mt * t * p1[1] + t * t * p2[1]
            self.thick_line(prev[0], prev[1], x, y, r, rgb, alpha)
            prev = (x, y)

    # -- vertical gradient over a row range -----------------------------------
    def vgrad(self, y0, y1, top, mid, bot):
        """Two-segment vertical gradient, opaque, full width."""
        for yi in range(self._sx(y0), self._sx(y1)):
            y = yi / self.ss
            if y <= (y0 + y1) / 2.0:
                t = (y - y0) / max(1.0, (y1 - y0) / 2.0)
                c = tuple(int(top[i] + (mid[i] - top[i]) * t) for i in range(3))
            else:
                t = (y - (y0 + y1) / 2.0) / max(1.0, (y1 - y0) / 2.0)
                c = tuple(int(mid[i] + (bot[i] - mid[i]) * t) for i in range(3))
            self.span(y, 0, self.size, c)


# --------------------------------------------------------------------------
# Downsample (2x2 box average) and PNG encoding
# --------------------------------------------------------------------------

def _avg2(a, b, c, d):
    """Average four bytes of one channel across a 2x2 block, C-free loop."""
    return bytes((x + y + z + w) // 4 for x, y, z, w in zip(a, b, c, d))


def downsample(canvas):
    """Return the final 1024x1024 RGB bytes from the 2x canvas."""
    w2, h2 = canvas.w, canvas.h
    w, h = w2 // SS, h2 // SS
    out = bytearray(w * h * 3)
    for y in range(h):
        r0 = [canvas.planes[ch][(2 * y) * w2:(2 * y + 1) * w2] for ch in range(3)]
        r1 = [canvas.planes[ch][(2 * y + 1) * w2:(2 * y + 2) * w2] for ch in range(3)]
        rows = []
        for ch in range(3):
            a, b = r0[ch][0::2], r0[ch][1::2]
            c, d = r1[ch][0::2], r1[ch][1::2]
            rows.append(_avg2(a, b, c, d))
        # Interleave R, G, B rows into one RGB row (C-speed slice assign).
        row = bytearray(w * 3)
        row[0::3] = rows[0]
        row[1::3] = rows[1]
        row[2::3] = rows[2]
        out[y * w * 3:(y + 1) * w * 3] = row
    return bytes(out)


def write_png(path, w, h, rgb):
    """Write RGB bytes as a whole, valid PNG (filter 0, zlib level 6)."""
    raw = bytearray()
    stride = w * 3
    for y in range(h):
        raw.append(0)
        raw += rgb[y * stride:(y + 1) * stride]
    data = zlib.compress(bytes(raw), 6)

    def chunk(tag, payload):
        c = struct.pack(">I", len(payload)) + tag + payload
        return c + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", ihdr)
           + chunk(b"IDAT", data)
           + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)
    return len(png)



# --------------------------------------------------------------------------
# v2 palette (overrides the v1 defaults above) — lavender garden, warm cream
# kitten, coral yarn. The scene functions below use these names.
# --------------------------------------------------------------------------

SKY_TOP = (196, 178, 238)
SKY_MID = (244, 212, 233)
SKY_LOW = (255, 236, 205)
SUN_GLOW = (255, 246, 190)
SUN_DISC = (255, 251, 232)
CLOUD = (255, 255, 255)
MEADOW_TOP = (188, 226, 168)
MEADOW_BOT = (128, 194, 130)
RUG = (255, 213, 222)
RUG_INNER = (255, 228, 233)
SHADOW = (90, 110, 70)

FUR = (255, 243, 220)
FUR_SHADE = (243, 205, 160)
FUR_STRIPE = (235, 165, 100)
OUTLINE = (107, 66, 38)
INNER_EAR = (255, 175, 190)
NOSE = (255, 143, 168)
NOSE_DARK = (217, 108, 132)
BLUSH = (255, 158, 178)
IRIS = (96, 138, 84)
IRIS_DARK = (60, 100, 55)
PUPIL = (30, 26, 20)
WHITE = (255, 255, 255)
BOW = (196, 164, 240)
BOW_DARK = (158, 122, 220)
WHISKER = (220, 208, 196)
FLOWER_WHITE = (255, 255, 255)
FLOWER_PINK = (255, 194, 212)
FLOWER_YELLOW = (255, 214, 107)
GRASS = (95, 168, 94)
YARN = (255, 143, 150)
YARN_DARK = (232, 90, 110)
BUTTERFLY_WING = (255, 183, 213)
BUTTERFLY_EDGE = (240, 140, 180)


# --------------------------------------------------------------------------
# Scene: lavender garden with a pink rug
# --------------------------------------------------------------------------

def draw_background(cv):
    # Sky: lavender at the top warming to cream at the horizon.
    cv.vgrad(0, 700, SKY_TOP, SKY_MID, SKY_LOW)
    # Sun glow, upper left, and a soft sun disc.
    cv.soft_ellipse(180, 140, 240, 200, SUN_GLOW, 0.55)
    cv.soft_ellipse(180, 140, 55, 55, SUN_DISC, 0.95)
    # Dreamy bokeh dots, kept away from the kitten and the yarn.
    for (x, y, r, col, a) in [
            (120, 480, 26, WHITE, 0.18), (920, 520, 34, (255, 214, 232), 0.20),
            (880, 420, 18, WHITE, 0.22), (150, 620, 22, (255, 233, 168), 0.20),
            (950, 660, 16, WHITE, 0.15), (60, 280, 20, WHITE, 0.16)]:
        cv.soft_ellipse(x, y, r, r, col, a)
    # Soft clouds: clusters of overlapping ellipses.
    for (cx, cy, rx, ry) in [(660, 140, 80, 36), (740, 120, 100, 48),
                             (820, 145, 75, 34)]:
        cv.soft_ellipse(cx, cy, rx, ry, CLOUD, 0.90)
    for (cx, cy, rx, ry) in [(420, 85, 60, 26), (500, 70, 75, 32)]:
        cv.soft_ellipse(cx, cy, rx, ry, CLOUD, 0.80)
    # Meadow.
    cv.vgrad(690, SIZE, MEADOW_TOP, (168, 215, 148), MEADOW_BOT)
    # Round pink rug the kitten sits on.
    cv.ellipse(512, 965, 400, 82, RUG)
    cv.ellipse(512, 965, 330, 62, RUG_INNER)
    # Soft shadow under the kitten, grounding the subject.
    cv.soft_ellipse(470, 905, 270, 42, SHADOW, 0.18)


def draw_flower(cv, x, y, petal, center, scale=1.0):
    """A simple five-petal flower with a yellow heart."""
    for k in range(5):
        ang = -math.pi / 2 + k * 2 * math.pi / 5
        px = x + math.cos(ang) * 15 * scale
        py = y + math.sin(ang) * 15 * scale
        cv.circle(px, py, 13 * scale, petal)
    cv.circle(x, y, 10 * scale, center)
    cv.circle(x - 3, y - 3, 3 * scale, (255, 240, 180))


def draw_grass(cv):
    for (x, y, h, lean) in [(140, 1010, 26, -8), (170, 1016, 20, 6),
                            (860, 1005, 24, 8), (890, 1012, 18, -6),
                            (60, 1000, 22, -6), (965, 998, 22, 6)]:
        cv.polygon([(x, y), (x + lean, y - h), (x + 7, y)], GRASS)
    draw_flower(cv, 110, 945, FLOWER_WHITE, FLOWER_YELLOW, 1.0)
    draw_flower(cv, 915, 950, FLOWER_PINK, FLOWER_YELLOW, 1.0)
    draw_flower(cv, 205, 995, FLOWER_WHITE, FLOWER_YELLOW, 0.7)
    draw_flower(cv, 830, 1000, FLOWER_PINK, FLOWER_YELLOW, 0.7)
    cv.thick_line(110, 958, 110, 990, 3, GRASS)
    cv.thick_line(915, 963, 915, 992, 3, GRASS)


def draw_butterfly(cv, x, y):
    """A tiny pink butterfly drifting on the left, balancing the yarn."""
    cv.ellipse(x - 16, y - 10, 15, 22, BUTTERFLY_WING)
    cv.ellipse(x + 16, y - 10, 15, 22, BUTTERFLY_WING)
    cv.ellipse(x - 12, y + 12, 10, 14, BUTTERFLY_WING)
    cv.ellipse(x + 12, y + 12, 10, 14, BUTTERFLY_WING)
    cv.ellipse(x - 16, y - 10, 15, 22, BUTTERFLY_EDGE, 0.25)
    cv.ellipse(x + 16, y - 10, 15, 22, BUTTERFLY_EDGE, 0.25)
    cv.thick_line(x, y - 18, x, y + 20, 3, (90, 70, 60))
    cv.circle(x - 4, y - 24, 2, (90, 70, 60))
    cv.circle(x + 4, y - 24, 2, (90, 70, 60))

# --------------------------------------------------------------------------
# Scene: Milo the kitten, playing with yarn
# --------------------------------------------------------------------------

HEAD = (470, 470)      # head centre
HEAD_R = 240           # head radius
BALL = (830, 280)      # yarn ball centre
BALL_R = 85


def draw_string(cv):
    """Loose yarn string dangling from the ball down the right side to the
    floor, where it pools beside the hind paw.

    Routed clear of the arm and body (both drawn later) so the whole
    string stays visible.
    """
    cv.bezier((862, 352), (925, 470), (905, 600), 5, YARN_DARK)
    cv.bezier((905, 600), (880, 740), (810, 845), 5, YARN_DARK)
    cv.bezier((810, 845), (760, 905), (700, 915), 5, YARN_DARK)
    cv.circle(700, 915, 7, YARN_DARK)


def draw_tail(cv):
    """Tail curled on the ground at the kitten's left side, tip shaded."""
    cv.bezier((300, 880), (205, 945), (150, 875), 32, OUTLINE)
    cv.bezier((300, 880), (205, 945), (150, 875), 26, FUR)
    cv.ellipse(150, 875, 16, 12, FUR_SHADE, 0.5)


def draw_body(cv):
    cv.ellipse(470, 800, 206, 171, OUTLINE)
    cv.ellipse(470, 800, 200, 165, FUR)
    # Cream belly patch.
    cv.ellipse(470, 835, 125, 105, (255, 252, 246))
    # Soft shading low on the body.
    cv.soft_ellipse(470, 900, 160, 70, FUR_SHADE, 0.30)


def draw_arm(cv):
    """Raised right arm reaching up toward the yarn ball."""
    cv.thick_line(655, 720, 790, 430, 34, OUTLINE)
    cv.thick_line(655, 720, 790, 430, 28, FUR)


def draw_ears(cv):
    left = [(290, 370), (385, 140), (495, 345)]
    right = [(650, 370), (555, 140), (445, 345)]
    left_in = [(325, 330), (392, 185), (460, 325)]
    right_in = [(615, 330), (548, 185), (480, 325)]
    for ear, inner in ((left, left_in), (right, right_in)):
        cv.polygon_outline(ear, OUTLINE, scale=1.06)
        cv.polygon(ear, FUR)
        cv.polygon(inner, INNER_EAR)
        cx = sum(p[0] for p in inner) / 3
        cy = sum(p[1] for p in inner) / 3
        cv.ellipse(cx, cy + 8, 24, 28, (255, 150, 170), 0.55)


def draw_head(cv):
    # Fluffy scalloped edge: a ring of fur circles around the head, drawn
    # once in outline colour (slightly bigger) and once in fur colour.
    cx, cy = HEAD
    for (rr, col) in ((HEAD_R + 14, OUTLINE), (HEAD_R + 8, FUR)):
        for k in range(36):
            ang = math.radians(k * 10)
            px = cx + math.cos(ang) * (HEAD_R + 2)
            py = cy + math.sin(ang) * (HEAD_R + 2)
            cv.circle(px, py, 28 if col is FUR else 34, col)
    cv.circle(cx, cy, HEAD_R + 6, OUTLINE)
    cv.circle(cx, cy, HEAD_R, FUR)
    # Soft shading low on the face, soft light high on the forehead.
    cv.soft_ellipse(470, 655, 205, 95, FUR_SHADE, 0.35)
    cv.soft_ellipse(470, 295, 165, 70, WHITE, 0.18)
    # Three little tabby stripes on the forehead.
    cv.thick_line(470, 262, 470, 312, 9, FUR_STRIPE, 0.9)
    cv.thick_line(420, 272, 405, 318, 9, FUR_STRIPE, 0.9)
    cv.thick_line(520, 272, 535, 318, 9, FUR_STRIPE, 0.9)


def draw_eye(cv, cx, cy):
    # Big glossy green eye, looking up-right toward the yarn ball:
    # dark outline, white, green iris, pupil, sparkles.
    cv.ellipse(cx, cy, 58, 74, OUTLINE)
    cv.ellipse(cx, cy, 53, 69, WHITE)
    cv.circle(cx + 8, cy - 6, 42, IRIS)
    cv.circle(cx + 8, cy - 6, 42, IRIS_DARK, 0.35)
    cv.circle(cx + 8, cy - 6, 22, PUPIL)
    cv.circle(cx - 8, cy - 26, 13, WHITE, 0.95)
    cv.circle(cx + 22, cy + 16, 6, WHITE, 0.85)
    # Upper lash line following the top of the eye, plus two tiny lashes.
    for k in range(9):
        ang = math.radians(195 + k * 20)
        x = cx + math.cos(ang) * 53
        y = cy + math.sin(ang) * 69
        cv.circle(x, y, 5, OUTLINE)
    cv.thick_line(cx - 46, cy - 52, cx - 58, cy - 66, 3, OUTLINE)
    cv.thick_line(cx - 52, cy - 38, cx - 66, cy - 46, 3, OUTLINE)


def draw_face(cv):
    draw_eye(cv, 385, 470)
    draw_eye(cv, 555, 470)
    # Little pink nose with a highlight, then the classic "w" cat mouth.
    nose = [(452, 552), (488, 552), (470, 574)]
    cv.polygon_outline(nose, NOSE_DARK, scale=1.12)
    cv.polygon(nose, NOSE)
    cv.circle(461, 560, 4, WHITE, 0.9)
    cv.thick_line(470, 576, 470, 590, 3, OUTLINE)
    cv.bezier((470, 590), (458, 604), (446, 602), 3, OUTLINE)
    cv.bezier((470, 590), (482, 604), (494, 602), 3, OUTLINE)
    # Rosy cheeks.
    cv.soft_ellipse(320, 575, 48, 28, BLUSH, 0.5)
    cv.soft_ellipse(620, 575, 48, 28, BLUSH, 0.5)
    # Whiskers, three a side, fanning out over the cheeks.
    for (x0, y0, x1, y1, x2, y2) in [
            (315, 550, 235, 533, 152, 525),
            (313, 575, 233, 575, 150, 577),
            (317, 600, 237, 617, 156, 633)]:
        cv.bezier((x0, y0), (x1, y1), (x2, y2), 2.5, WHISKER, 0.95)
    for (x0, y0, x1, y1, x2, y2) in [
            (625, 550, 705, 533, 788, 525),
            (627, 575, 707, 575, 790, 577),
            (623, 600, 703, 617, 784, 633)]:
        cv.bezier((x0, y0), (x1, y1), (x2, y2), 2.5, WHISKER, 0.95)


def draw_bow(cv):
    # A tiny lavender bow on the left ear: the "one small touch of
    # personality". Sized to sit inside the ear triangle.
    cx, cy = 392, 250
    left = [(cx, cy), (355, 222), (358, 278)]
    right = [(cx, cy), (429, 222), (426, 278)]
    for loop in (left, right):
        cv.polygon_outline(loop, BOW_DARK, scale=1.08)
        cv.polygon(loop, BOW)
    cv.circle(cx, cy, 12, BOW_DARK)
    cv.circle(cx, cy, 8, (220, 195, 250))


def draw_paw_ground(cv, cx, cy, rx, ry):
    """A paw resting on the ground, with toe beans on top and toe lines."""
    cv.ellipse(cx, cy, rx + 5, ry + 5, OUTLINE)
    cv.ellipse(cx, cy, rx, ry, FUR)
    for dx in (-28, 0, 28):
        cv.ellipse(cx + dx, cy - ry + 30, 10, 13, (255, 170, 190), 0.95)
    cv.thick_line(cx - 17, cy + ry - 15, cx - 17, cy + ry - 2, 2.5, OUTLINE)
    cv.thick_line(cx + 17, cy + ry - 15, cx + 17, cy + ry - 2, 2.5, OUTLINE)


def draw_paw_raised(cv, cx, cy):
    """The batting paw, toe beans facing up toward the yarn ball."""
    cv.ellipse(cx, cy, 63, 55, OUTLINE)
    cv.ellipse(cx, cy, 58, 50, FUR)
    for dx in (-20, 0, 20):
        cv.ellipse(cx + dx, cy - 42, 10, 12, (255, 170, 190), 0.95)
    cv.ellipse(cx, cy + 16, 22, 14, (255, 170, 190), 0.95)


def draw_yarn_ball(cv):
    """Big coral ball of yarn with wound-string arcs and a soft highlight."""
    cx, cy = BALL
    cv.circle(cx, cy, BALL_R + 4, OUTLINE)
    cv.circle(cx, cy, BALL_R, YARN)
    # Horizontal wound-string chords; sag alternates for a woven look.
    for (dy, half, sag) in [(-55, 64.8, 16), (-28, 80.2, -14), (0, 85, 18),
                            (28, 80.2, -14), (55, 64.8, 16)]:
        cv.bezier((cx - half, cy + dy), (cx, cy + dy + sag),
                  (cx + half, cy + dy), 4.5, YARN_DARK)
    # Vertical wound-string chords.
    cv.bezier((775, 215), (759, 280), (775, 345), 4.5, YARN_DARK)
    cv.bezier((858, 200), (874, 280), (858, 360), 4.5, YARN_DARK)
    # Soft highlight, upper left of the ball.
    cv.soft_ellipse(cx - 30, cy - 30, 38, 26, WHITE, 0.40)


def draw_kitten(cv):
    draw_string(cv)
    draw_tail(cv)
    draw_body(cv)
    draw_arm(cv)
    draw_ears(cv)
    draw_head(cv)
    draw_face(cv)
    draw_bow(cv)
    draw_paw_ground(cv, 360, 880, 75, 48)
    draw_paw_ground(cv, 620, 930, 65, 40)
    draw_paw_raised(cv, 790, 430)
    draw_yarn_ball(cv)


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def render():
    cv = Canvas(SIZE, SS)
    draw_background(cv)
    draw_kitten(cv)
    draw_butterfly(cv, 140, 320)
    draw_grass(cv)
    return downsample(cv)


def main():
    outdir = os.environ.get("MAKEFUN_OUTPUT_DIR", "/output")
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, OUT_NAME)
    rgb = render()
    size = write_png(path, SIZE, SIZE, rgb)
    print(f"wrote {path} ({size} bytes)")


if __name__ == "__main__":
    main()
