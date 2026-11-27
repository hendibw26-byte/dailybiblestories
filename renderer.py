"""Generic cinematic-silhouette renderer.

Each scene is described in JSON (see samples/sample_script.json) using a small
vocabulary of skies, terrains, elements, characters and actions. The renderer
turns it into a 1080x1920 30 fps clip in the same visual style as the
"Daud & Goliat" pilot.
"""
import math, os, random, subprocess, zlib
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1080, 1920
CW, CH = 1240, 2100
M = 150
LW = CW + 2 * M
K = 2
FPS = 30
FONT_DIR = os.environ.get("FONT_DIR", "/usr/share/fonts/truetype/dejavu")
SERIF_B = f"{FONT_DIR}/DejaVuSerif-Bold.ttf"
SERIF = f"{FONT_DIR}/DejaVuSerif.ttf"
SANS_B = f"{FONT_DIR}/DejaVuSans-Bold.ttf"
GOLD = (244, 196, 98, 255)
CREAM = (255, 248, 236, 255)


def hexc(s, a=255):
    s = s.lstrip('#')
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4)) + (a,)


def ease(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


# ---------------------------------------------------------------- palettes
SKIES = {
    "dawn":   dict(stops=[(0, "#141a33"), (0.45, "#4a3157"), (0.72, "#d9774a"), (1, "#f2b56b")], glow="#ffd38a", gy=1420, gr=700, far="#5a3a55", mid="#33213a", near="#140d12", water="#e3a070"),
    "golden": dict(stops=[(0, "#2c4a7a"), (0.5, "#d98f55"), (1, "#f6cf8e")], glow="#fff0c0", gy=1250, gr=650, far="#a8735f", mid="#4c2e33", near="#1e1216", water="#f2c98a"),
    "day":    dict(stops=[(0, "#6f9cc8"), (0.55, "#f0d3a0"), (1, "#f7e6c0")], glow="#fff6d8", gy=1050, gr=600, far="#9aa37a", mid="#5f6b45", near="#2b2a1c", water="#8fb8c8"),
    "dusk":   dict(stops=[(0, "#1d1430"), (0.45, "#5c2c4a"), (0.75, "#d06a3c"), (1, "#f0a050")], glow="#ffd590", gy=1500, gr=520, far="#3a1f30", mid="#2a1624", near="#140d12", water="#d88a58"),
    "night":  dict(stops=[(0, "#070b1c"), (0.6, "#1b2a4a"), (1, "#2f3f63")], glow="#cfd8ff", gy=700, gr=260, far="#1a2440", mid="#121a30", near="#070a14", water="#4a6a9a"),
    "storm":  dict(stops=[(0, "#1a1d24"), (0.5, "#3a3f4a"), (1, "#6b6f78")], glow="#c8ccd8", gy=900, gr=500, far="#2b2f38", mid="#1e2128", near="#0c0d10", water="#5a6878"),
    "fire":   dict(stops=[(0, "#2a0f18"), (0.45, "#8a2e28"), (0.8, "#e0683a"), (1, "#f2a05a")], glow="#ffb070", gy=1000, gr=750, far="#4a1a1e", mid="#30121a", near="#140d12", water="#e07a48"),
    "heaven": dict(stops=[(0, "#3b5c8f"), (0.5, "#f0b46a"), (1, "#ffe2a8")], glow="#fff3cc", gy=1450, gr=900, far="#b4744e", mid="#7a4a36", near="#140d12", water="#ffd9a0"),
}
INTERIOR = dict(stops=[(0, "#1a0f0c"), (0.6, "#3a2216"), (1, "#5a3420")], glow="#ffc070", gy=900, gr=700, far="#2a1810", mid="#1e120c", near="#0e0806", water="#c08050")

GROUND = {"hills": 1780, "valley": 1780, "desert": 1790, "sea": 1830, "river": 1780, "mountain": 1780,
          "city": 1790, "garden": 1780, "interior": 1800}


# ---------------------------------------------------------------- drawing helpers
def new_layer():
    im = Image.new("RGBA", (LW * K, CH * K), (0, 0, 0, 0))
    return im, ImageDraw.Draw(im)


def done(im):
    return im.resize((LW, CH), Image.LANCZOS)


def sky_img(stops):
    ys = np.linspace(0, 1, CH)[:, None]
    out = np.zeros((CH, 1, 3))
    for i in range(len(stops) - 1):
        p0, c0 = stops[i]; p1, c1 = stops[i + 1]
        c0 = np.array(hexc(c0)[:3]); c1 = np.array(hexc(c1)[:3])
        m = (ys >= p0) & (ys <= p1)
        f = (ys - p0) / max(p1 - p0, 1e-6)
        out = np.where(m[..., None], c0 + (c1 - c0) * f[..., None], out)
    arr = np.repeat(out, LW, axis=1).astype(np.uint8)
    a = np.full((CH, LW, 1), 255, np.uint8)
    return Image.fromarray(np.concatenate([arr, a], 2), "RGBA")


def glow_img(cx, cy, r, col, strength=1.0):
    yy, xx = np.mgrid[0:CH, 0:LW]
    d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / r
    a = np.clip(1 - d, 0, 1) ** 2.2 * 255 * strength
    arr = np.zeros((CH, LW, 4), np.uint8)
    arr[..., :3] = np.array(hexc(col)[:3], np.uint8)
    arr[..., 3] = np.clip(a, 0, 255).astype(np.uint8)
    return Image.fromarray(arr, "RGBA")


def hill(d, y, amp, col, seed, freq=1.0):
    rnd = random.Random(seed)
    ph = [rnd.random() * 6.28 for _ in range(3)]
    pts = []
    for x in range(0, LW + 11, 10):
        u = x / LW
        v = (math.sin(u * 3.1 * freq + ph[0]) * 0.55 + math.sin(u * 7.3 * freq + ph[1]) * 0.3
             + math.sin(u * 15.7 * freq + ph[2]) * 0.15)
        pts.append((x * K, (y + amp * v) * K))
    pts += [(LW * K, CH * K), (0, CH * K)]
    d.polygon(pts, fill=col)


def hill_fn(d, fn, col):
    pts = [(x * K, fn(x) * K) for x in range(0, LW + 11, 8)]
    pts += [(LW * K, CH * K), (0, CH * K)]
    d.polygon(pts, fill=col)


def line(d, a, b, w, col):
    d.line([(a[0] * K, a[1] * K), (b[0] * K, b[1] * K)], fill=col, width=max(1, int(w * K)))
    r = w / 2
    for p in (a, b):
        d.ellipse([(p[0] - r) * K, (p[1] - r) * K, (p[0] + r) * K, (p[1] + r) * K], fill=col)


def poly(d, pts, col):
    d.polygon([(x * K, y * K) for x, y in pts], fill=col)


def circ(d, x, y, r, col):
    d.ellipse([(x - r) * K, (y - r) * K, (x + r) * K, (y + r) * K], fill=col)


def rect(d, x0, y0, x1, y1, col):
    d.rectangle([x0 * K, y0 * K, x1 * K, y1 * K], fill=col)


# ---------------------------------------------------------------- characters
def person(d, x, fy, h, col, robe=True, long_robe=False, helmet=False, crown=False, veil=False,
           spear=False, shield=False, staff=False, sword=False, scroll=False, lamp=False, wings=False,
           pose="stand", face=1, bulk=1.0, glow=None):
    if pose == "kneel":
        fy_draw = fy
        h_eff = h * 0.72
    else:
        fy_draw, h_eff = fy, h
    r = h * 0.065
    top = fy_draw - h_eff
    hy = top + r
    neck = top + 2 * r
    sh = h * 0.11 * bulk
    hip = fy_draw - h_eff * (0.47 if pose != "kneel" else 0.33)
    if wings:
        wc = col
        for s in (-1, 1):
            poly(d, [(x + s * sh * 0.4, neck + r), (x + s * sh * 4.2, neck - r * 3.5), (x + s * sh * 3.6, neck + r * 1.5),
                     (x + s * sh * 3.0, neck + r * 4), (x + s * sh * 0.6, hip - r)], wc)
    circ(d, x, hy, r, col)
    poly(d, [(x - r * 0.45, hy), (x + r * 0.45, hy), (x + r * 0.5, neck + r * 0.4), (x - r * 0.5, neck + r * 0.4)], col)
    poly(d, [(x - sh, neck + r * 0.3), (x + sh, neck + r * 0.3), (x + sh * 0.78, hip), (x - sh * 0.78, hip)], col)
    if pose == "kneel":
        line(d, (x - sh * 0.5, hip), (x - sh * 1.6 * face, fy_draw - h * 0.04), h * 0.07, col)
        line(d, (x + sh * 0.4, hip), (x + sh * 1.2 * face, hip + h * 0.02), h * 0.07, col)
        line(d, (x + sh * 1.2 * face, hip + h * 0.02), (x + sh * 1.3 * face, fy_draw), h * 0.06, col)
    elif pose == "walk":
        line(d, (x - sh * 0.4, hip), (x - sh * 1.4, fy_draw - h * 0.01), h * 0.065 * bulk, col)
        line(d, (x + sh * 0.4, hip), (x + sh * 1.3, fy_draw - h * 0.01), h * 0.065 * bulk, col)
    else:
        line(d, (x - sh * 0.4, hip), (x - sh * 0.6, fy_draw - h * 0.01), h * 0.065 * bulk, col)
        line(d, (x + sh * 0.4, hip), (x + sh * 0.6, fy_draw - h * 0.01), h * 0.065 * bulk, col)
    if robe or long_robe:
        bottom = fy_draw - (h * 0.02 if long_robe else h * 0.2)
        if pose == "kneel":
            bottom = fy_draw - h * 0.02
        poly(d, [(x - sh * 0.78, hip - 2), (x + sh * 0.78, hip - 2), (x + sh * (1.35 if long_robe else 1.15), bottom),
                 (x - sh * (1.35 if long_robe else 1.15), bottom)], col)
    sy = neck + r * 0.6
    # arms
    if pose == "raise_arm":
        line(d, (x - sh * 0.85 * face, sy), (x - sh * 1.35 * face, hip + h * 0.02), h * 0.05, col)
        line(d, (x + sh * 0.85 * face, sy), (x + sh * 1.1 * face, top - h * 0.12), h * 0.05, col)
    elif pose == "pray":
        line(d, (x - sh * 0.85, sy), (x - sh * 0.2, sy - h * 0.15), h * 0.05, col)
        line(d, (x + sh * 0.85, sy), (x + sh * 0.2, sy - h * 0.15), h * 0.05, col)
    elif pose == "arms_open":
        line(d, (x - sh * 0.85, sy), (x - sh * 2.6, sy - h * 0.12), h * 0.05, col)
        line(d, (x + sh * 0.85, sy), (x + sh * 2.6, sy - h * 0.12), h * 0.05, col)
    else:
        line(d, (x - sh * 0.85 * face, sy), (x - sh * 1.35 * face, hip + h * 0.02), h * 0.05 * bulk, col)
        line(d, (x + sh * 0.85 * face, sy), (x + sh * 1.3 * face, hip + h * 0.02), h * 0.05 * bulk, col)
    if veil:
        poly(d, [(x - r * 1.3, hy + r * 0.2), (x, hy - r * 1.25), (x + r * 1.3, hy + r * 0.2), (x + r * 1.6, neck + r * 2.2), (x - r * 1.6, neck + r * 2.2)], col)
    if helmet:
        poly(d, [(x - r * 1.15, hy + r * 0.2), (x - r * 0.9, hy - r * 1.05), (x + r * 0.9, hy - r * 1.05), (x + r * 1.15, hy + r * 0.2)], col)
        poly(d, [(x - r * 0.25, hy - r * 1.0), (x + r * 0.25, hy - r * 1.0), (x + r * 0.15, hy - r * 2.0), (x - r * 0.15, hy - r * 2.0)], col)
    if crown:
        cy0 = hy - r * 0.7
        poly(d, [(x - r, cy0), (x - r, cy0 - r * 0.9), (x - r * 0.5, cy0 - r * 0.4), (x, cy0 - r * 1.1),
                 (x + r * 0.5, cy0 - r * 0.4), (x + r, cy0 - r * 0.9), (x + r, cy0)], col)
    hand_x = x + sh * 1.3 * face
    if spear:
        sx = x + sh * 1.35 * face
        line(d, (sx, fy_draw + h * 0.01), (sx, fy_draw - h * 1.3), h * 0.028, col)
        poly(d, [(sx - h * 0.03, fy_draw - h * 1.3), (sx + h * 0.03, fy_draw - h * 1.3), (sx, fy_draw - h * 1.42)], col)
    if shield:
        circ(d, x - sh * 1.25 * face, hip - h * 0.1, h * 0.16, col)
    if staff:
        sx = x + sh * 1.45 * face
        line(d, (sx, fy_draw), (sx, fy_draw - h * 1.0), h * 0.022, col)
        d.arc([(sx - h * 0.18 * (1 if face > 0 else 0)) * K, (fy_draw - h * 1.13) * K,
               (sx + h * 0.18 * (0 if face > 0 else 1)) * K, (fy_draw - h * 0.95) * K], start=180, end=360, fill=col, width=int(h * 0.022 * K))
    if sword:
        line(d, (hand_x, hip), (hand_x + h * 0.05 * face, hip - h * 0.45), h * 0.025, col)
        line(d, (hand_x - h * 0.05, hip - h * 0.03), (hand_x + h * 0.05, hip - h * 0.03), h * 0.02, col)
    if scroll:
        rect(d, x - sh * 0.6, hip - h * 0.22, x + sh * 0.6, hip - h * 0.12, col)
    if lamp:
        circ(d, hand_x, hip + h * 0.02, h * 0.035, col)


def sheep(d, x, fy, s, body, headc):
    for dx, dy in [(-0.35, 0), (0, -0.12), (0.35, 0), (0, 0.08)]:
        circ(d, x + dx * s, fy - s * 0.55 + dy * s, s * 0.42, body)
    poly(d, [(x + s * 0.55, fy - s * 0.75), (x + s * 0.95, fy - s * 0.65), (x + s * 0.9, fy - s * 0.35), (x + s * 0.55, fy - s * 0.4)], headc)
    for lx in (-0.4, -0.15, 0.2, 0.45):
        line(d, (x + lx * s, fy - s * 0.25), (x + lx * s, fy), s * 0.09, headc)


def camel(d, x, fy, s, col):
    # body + hump
    d.ellipse([(x - s * 0.9) * K, (fy - s * 1.25) * K, (x + s * 0.5) * K, (fy - s * 0.7) * K], fill=col)
    d.ellipse([(x - s * 0.6) * K, (fy - s * 1.55) * K, (x + s * 0.1) * K, (fy - s * 0.95) * K], fill=col)
    # neck + head
    line(d, (x + s * 0.35, fy - s * 1.0), (x + s * 0.75, fy - s * 1.25), s * 0.2, col)
    line(d, (x + s * 0.75, fy - s * 1.25), (x + s * 0.85, fy - s * 1.7), s * 0.16, col)
    poly(d, [(x + s * 0.78, fy - s * 1.82), (x + s * 1.15, fy - s * 1.72), (x + s * 1.12, fy - s * 1.6), (x + s * 0.8, fy - s * 1.6)], col)
    for lx in (-0.75, -0.55, 0.15, 0.35):
        line(d, (x + lx * s, fy - s * 0.85), (x + lx * s + s * 0.03, fy), s * 0.08, col)


def palm(d, x, fy, h, col):
    tx, ty = x + h * 0.12, fy - h
    poly(d, [(x - h * 0.035, fy), (x + h * 0.035, fy), (tx + h * 0.018, ty), (tx - h * 0.018, ty)], col)
    for a in [-165, -135, -100, -70, -40, -12, 20, 160]:
        ar = math.radians(a)
        L = h * 0.42
        tip = (tx + math.cos(ar) * L, ty + math.sin(ar) * L * 0.6 + L * 0.35)
        mid = (tx + math.cos(ar) * L * 0.5, ty + math.sin(ar) * L * 0.5 - L * 0.05)
        nx, ny = -(mid[1] - ty), (mid[0] - tx)
        nn = math.hypot(nx, ny) + 1e-6
        wv = h * 0.05
        poly(d, [(tx, ty), (mid[0] + nx / nn * wv, mid[1] + ny / nn * wv), tip, (mid[0] - nx / nn * wv, mid[1] - ny / nn * wv)], col)


def tree(d, x, fy, h, col):
    line(d, (x, fy), (x, fy - h * 0.5), h * 0.07, col)
    for dx, dy, r in [(0, -0.75, 0.28), (-0.2, -0.6, 0.22), (0.2, -0.6, 0.22), (0, -0.55, 0.2)]:
        circ(d, x + dx * h, fy + dy * h, r * h, col)


def army(d, x0, x1, fy, h, col, seed, spears=True):
    rnd = random.Random(seed)
    x = x0
    while x < x1:
        hh = h * (0.9 + rnd.random() * 0.2)
        person(d, x, fy + rnd.random() * h * 0.15, hh, col, helmet=spears and rnd.random() < 0.6,
               spear=spears and rnd.random() < 0.7, veil=(not spears) and rnd.random() < 0.4)
        x += h * (0.32 + rnd.random() * 0.12)


# ---------------------------------------------------------------- caption
def wrap(text, font, maxw, d):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if d.textlength(t, font=font) <= maxw:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


def caption_img(text, ref=None, gold_words=(), size=54):
    font = ImageFont.truetype(SERIF_B, size)
    rf = ImageFont.truetype(SERIF, int(size * 0.62))
    tmp = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    lines = wrap(text, font, 880, tmp)
    while len(lines) > 5 and size > 40:
        size -= 4
        font = ImageFont.truetype(SERIF_B, size)
        lines = wrap(text, font, 880, tmp)
    lh = int(size * 1.32)
    th = lh * len(lines) + (int(size * 1.1) if ref else 0)
    im = Image.new("RGBA", (W, th + 120), (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle([60, 20, W - 60, th + 95], radius=36, fill=(10, 8, 6, 120))
    txt = Image.new("RGBA", im.size, (0, 0, 0, 0)); td = ImageDraw.Draw(txt)
    gw = {g.strip('.,“”"!?—:;').lower() for g in gold_words}
    y = 55
    for ln in lines:
        x = (W - td.textlength(ln, font=font)) / 2
        ws = ln.split()
        for i, w in enumerate(ws):
            wt = w + (" " if i < len(ws) - 1 else "")
            col = GOLD if w.strip('.,“”"!?—:;').lower() in gw else CREAM
            td.text((x, y), wt, font=font, fill=col)
            x += td.textlength(wt, font=font)
        y += lh
    if ref:
        td.text(((W - td.textlength(ref, font=rf)) / 2, y + 8), ref, font=rf, fill=GOLD)
    sh = txt.split()[3].filter(ImageFilter.GaussianBlur(6))
    shadow = Image.new("RGBA", im.size, (0, 0, 0, 0)); shadow.putalpha(sh.point(lambda v: int(v * 0.9)))
    im.alpha_composite(shadow); im.alpha_composite(txt)
    return im


def title_img(title, reference, kicker="K I S A H   A L K I T A B"):
    txt = Image.new("RGBA", (W, 900), (0, 0, 0, 0)); td = ImageDraw.Draw(txt)
    f1 = ImageFont.truetype(SANS_B, 34)
    size = 120
    parts = [p.strip() for p in title.upper().replace(" DAN ", " & ").split("&")]
    while True:
        f2 = ImageFont.truetype(SERIF_B, size)
        lines = []
        for p in parts:
            lines += wrap(p, f2, 940, td)
        if max(td.textlength(l, font=f2) for l in lines) <= 960 and len(lines) <= 4 or size <= 64:
            break
        size -= 8
    def ct(y, s, f, col):
        td.text(((W - td.textlength(s, font=f)) / 2, y), s, font=f, fill=col)
    y = 20
    ct(y, kicker, f1, GOLD); y += 80
    amp = ImageFont.truetype(SERIF, int(size * 0.58))
    pi = 0
    for i, p in enumerate(parts):
        for l in wrap(p, f2, 940, td):
            ct(y, l, f2, CREAM); y += int(size * 1.15)
        if i < len(parts) - 1:
            ct(y - int(size * 0.1), "&", amp, GOLD); y += int(size * 0.75)
    td.line([(W / 2 - 160, y + 10), (W / 2 + 160, y + 10)], fill=(244, 196, 98, 200), width=3)
    ct(y + 25, reference, ImageFont.truetype(SERIF, 52), (255, 236, 210, 230))
    sh = txt.split()[3].filter(ImageFilter.GaussianBlur(8))
    out = Image.new("RGBA", txt.size, (0, 0, 0, 0)); out.putalpha(sh.point(lambda v: int(v * 0.9)))
    out.alpha_composite(txt)
    return out


def vignette():
    yy, xx = np.mgrid[0:H, 0:W]
    d = np.sqrt(((xx - W / 2) / (W * 0.62)) ** 2 + ((yy - H / 2) / (H * 0.62)) ** 2)
    a = (np.clip(d - 0.55, 0, 1) / 0.45) ** 1.6 * 200
    arr = np.zeros((H, W, 4), np.uint8); arr[..., 3] = np.clip(a, 0, 210).astype(np.uint8)
    return Image.fromarray(arr, "RGBA")


# ---------------------------------------------------------------- scene building
POSX = {"far_left": LW / 2 - 430, "left": LW / 2 - 300, "center_left": LW / 2 - 150, "center": LW / 2,
        "center_right": LW / 2 + 150, "right": LW / 2 + 300, "far_right": LW / 2 + 430}
SIZES = {"small": 220, "medium": 330, "large": 520, "giant": 1000}


def build_scene(sc, idx, total, meta):
    P = dict(SKIES.get(sc.get("sky", "golden"), SKIES["golden"]))
    terrain = sc.get("terrain", "hills")
    if terrain == "interior":
        P = dict(INTERIOR)
    els = set(sc.get("elements", []))
    if idx == 0:
        els.discard("moon")  # keep the title card clear
    seed = zlib.crc32(f"{meta.get('title', '')}{idx}".encode()) & 0xffff
    gy = GROUND.get(terrain, 1780)
    layers = []
    layers.append((sky_img(P["stops"]), 0.0))
    if terrain != "interior":
        gx = LW / 2 + (200 if "moon" in els else 0)
        layers.append((glow_img(gx, P["gy"], P["gr"], P["glow"], 0.9), 0.05))
    far, mid, near = hexc(P["far"]), hexc(P["mid"]), hexc(P["near"])
    sil = near
    # rays (behind terrain)
    if "rays" in els or sc.get("sky") == "heaven":
        im, d = new_layer()
        cx, cy = LW / 2, P["gy"]
        for i in range(18):
            a = math.pi + i * math.pi / 17
            poly(d, [(cx, cy), (cx + 1800 * math.cos(a - 0.03), cy + 1800 * math.sin(a - 0.03)),
                     (cx + 1800 * math.cos(a + 0.03), cy + 1800 * math.sin(a + 0.03))], (255, 240, 200, 34))
        layers.append((done(im), 0.1))
    if "moon" in els:
        im, d = new_layer()
        circ(d, LW / 2 + 200, P["gy"], 70, (240, 240, 225, 255))
        circ(d, LW / 2 + 230, P["gy"] - 20, 66, hexc(P["stops"][0][1]))
        layers.append((done(im), 0.05))
    if "sun" in els and terrain != "interior":
        im, d = new_layer(); circ(d, LW / 2, P["gy"], 90, (255, 240, 200, 235)); layers.append((done(im), 0.05))
    # far layer
    im, d = new_layer()
    if terrain == "interior":
        rect(d, 0, 0, LW, CH, (0, 0, 0, 0))
        # back wall arches
        for i, x in enumerate(range(170, LW, 300)):
            poly(d, [(x - 90, 1700), (x - 90, 900), (x + 90, 900), (x + 90, 1700)], hexc("#2a1810", 120))
        layers.append((glow_img(LW / 2, 1100, 800, P["glow"], 0.55), 0.05))
    elif terrain == "mountain":
        hill_fn(d, lambda x: 750 + 900 * abs(x - LW / 2) / (LW / 2) + 30 * math.sin(x / 40), far)
    elif terrain == "sea":
        rect(d, 0, 1400, LW, CH, far)
    elif terrain == "desert":
        hill(d, 1450, 25, far, seed, 0.5)
    else:
        hill(d, 1350, 60, far, seed)
    if terrain != "interior":
        layers.append((done(im), 0.2))
    # mid layer
    im, d = new_layer()
    if terrain == "valley":
        hill_fn(d, lambda x: 1200 + 520 * min(1, x / LW) ** 1.4 + 25 * math.sin(x / 70), mid)
        hill_fn(d, lambda x: 1200 + 520 * max(0, 1 - x / LW) ** 1.4 + 25 * math.sin(x / 63), mid)
    elif terrain == "sea":
        wc = hexc(P["water"])
        for i in range(26):
            y = 1410 + i * 16 + i * i * 0.6
            line(d, (0, y), (LW, y + 4 * math.sin(i)), 3 + i * 0.5, (wc[0], wc[1], wc[2], 90 - i * 2))
    elif terrain == "desert":
        hill(d, 1600, 35, mid, seed + 1, 0.6)
    elif terrain == "interior":
        rect(d, 0, 1700, LW, CH, mid)
        for x in range(170, LW, 300):
            rect(d, x - 140, 700, x - 100, 1720, hexc("#140c08"))
            rect(d, x + 100, 700, x + 140, 1720, hexc("#140c08"))
        rect(d, 0, 640, LW, 700, hexc("#140c08"))
    else:
        hill(d, 1520, 70, mid, seed + 1)
    if "city_walls" in els or terrain == "city":
        rnd = random.Random(seed)
        base = 1560
        x = 0
        while x < LW:
            w = 60 + rnd.random() * 120; h = 120 + rnd.random() * 220
            rect(d, x, base - h, x + w, base + 200, mid)
            if rnd.random() < 0.3:
                circ(d, x + w / 2, base - h, w / 2, mid)
            x += w + 4
        for x in range(0, LW, 40):
            rect(d, x, base - 30, x + 22, base + 200, mid)
    if "temple" in els:
        bx = LW / 2 + 260
        poly(d, [(bx - 260, 1380), (bx, 1260), (bx + 260, 1380)], mid)
        rect(d, bx - 250, 1380, bx + 250, 1410, mid)
        for i in range(6):
            cx = bx - 220 + i * 88
            rect(d, cx - 16, 1410, cx + 16, 1620, mid)
        rect(d, bx - 270, 1620, bx + 270, 1660, mid)
    if "tents" in els:
        for i, x in enumerate([200, 420, 1150, 1330]):
            poly(d, [(x - 90, 1560), (x, 1440 + (i % 2) * 20), (x + 90, 1560)], mid)
    if "crowd" in els:
        army(d, 90, LW - 90, 1560, 80, mid, seed + 3, spears=False)
    if "army" in els:
        army(d, 90, 560, 1560, 80, mid, seed + 4)
        army(d, LW - 560, LW - 90, 1560, 80, mid, seed + 5)
    if "cross" in els:
        hill_fn(d, lambda x: 1450 - 160 * math.exp(-((x - LW / 2) / 300) ** 2), mid)
        for dx, s in [(-170, 0.7), (0, 1.0), (170, 0.7)]:
            cx = LW / 2 + dx
            rect(d, cx - 9 * s, 1400 - 360 * s, cx + 9 * s, 1420, mid)
            rect(d, cx - 90 * s, 1400 - 290 * s, cx + 90 * s, 1400 - 272 * s, mid)
    layers.append((done(im), 0.5))
    # near layer (ground + elements + characters)
    im, d = new_layer()
    if terrain == "sea":
        hill(d, gy, 18, sil, seed + 2, 0.4)
    elif terrain == "river":
        hill(d, gy - 60, 25, sil, seed + 2, 0.5)
        wc = hexc(P["water"])
        for i in range(14):
            y = gy + i * 22
            line(d, (0, y), (LW, y + 6 * math.sin(i)), 14, (wc[0], wc[1], wc[2], 160 - i * 6))
    elif terrain == "interior":
        rect(d, 0, gy, LW, CH, sil)
    else:
        hill(d, gy, 30, sil, seed + 2, 0.5)
    rnd = random.Random(seed + 9)
    if "palm_trees" in els:
        for x, h in [(270, 520), (LW - 300, 600)]:
            palm(d, x, gy + 20, h, sil)
    if "trees" in els or terrain == "garden":
        for x, h in [(130, 420), (LW - 150, 480), (LW / 2 + 420, 300)]:
            tree(d, x, gy + 20, h, sil)
    if "boat" in els:
        bx, by = LW / 2 + 250, gy - 10
        poly(d, [(bx - 200, by - 60), (bx + 200, by - 60), (bx + 150, by), (bx - 150, by)], sil)
        line(d, (bx, by - 60), (bx, by - 360), 8, sil)
        poly(d, [(bx + 6, by - 350), (bx + 170, by - 120), (bx + 6, by - 110)], sil)
    if "ark" in els:
        bx, by = LW / 2, gy - 30
        poly(d, [(bx - 480, by - 200), (bx + 480, by - 200), (bx + 400, by), (bx - 400, by)], sil)
        rect(d, bx - 300, by - 330, bx + 300, by - 200, sil)
        poly(d, [(bx - 330, by - 330), (bx, by - 420), (bx + 330, by - 330)], sil)
    if "stone_tablets" in els:
        for dx in (-50, 50):
            x0 = LW / 2 + dx
            rect(d, x0 - 42, gy - 300, x0 + 42, gy - 180, (230, 220, 190, 255))
            circ(d, x0, gy - 300, 42, (230, 220, 190, 255))
    if "tomb" in els:
        circ(d, LW / 2 + 300, gy - 60, 260, sil)
        circ(d, LW / 2 + 300, gy - 120, 110, hexc("#000000", 255))
        circ(d, LW / 2 + 120, gy - 120, 120, hexc(P["mid"]))
    if "manger" in els:
        poly(d, [(LW / 2 - 120, gy - 120), (LW / 2 + 120, gy - 120), (LW / 2 + 80, gy - 20), (LW / 2 - 80, gy - 20)], sil)
        line(d, (LW / 2 - 90, gy - 20), (LW / 2 - 120, gy), 10, sil)
        line(d, (LW / 2 + 90, gy - 20), (LW / 2 + 120, gy), 10, sil)
    if "well" in els:
        rect(d, LW / 2 + 260, gy - 150, LW / 2 + 460, gy, sil)
    if "sheep" in els:
        for x, yo, s in [(LW / 2 - 380, 20, 60), (LW / 2 + 220, 10, 70), (LW / 2 + 400, 30, 56)]:
            sheep(d, x, gy + yo, s, hexc("#e9e1cc") if sc.get("sky") in ("day", "golden") else sil, sil)
    if "camels" in els:
        camel(d, LW / 2 + 380, gy + 10, 110, sil)
    if "burning_bush" in els:
        tree(d, LW / 2 + 300, gy + 10, 260, sil)
    if "fire" in els and terrain == "interior":
        pass
    layers.append((done(im), 1.0))
    # characters
    chars = sc.get("characters", [])[:5]
    char_layers = []
    used = {}
    for c in chars:
        pos = c.get("position", "center")
        n = used.get(pos, 0); used[pos] = n + 1
        x = POSX.get(pos, LW / 2) + n * 90 * (1 if "left" not in pos else -1)
        size = SIZES.get(c.get("size", "medium"), 330)
        role = c.get("role", "hero")
        if role == "child":
            size *= 0.6
        props = set(c.get("props", []))
        face = 1 if x < LW / 2 else -1
        if pos == "center":
            face = 1
        im, d = new_layer()
        col = sil
        kw = dict(robe=True, long_robe=role in ("woman", "king", "elder", "jesus", "angel"), veil=role == "woman",
                  helmet="helmet" in props or role == "soldier", crown="crown" in props or role == "king",
                  spear="spear" in props, shield="shield" in props, staff="staff" in props or role == "elder",
                  sword="sword" in props, scroll="scroll" in props, lamp="lamp" in props,
                  wings=role == "angel", pose=c.get("pose", "stand"), face=face,
                  bulk=1.35 if c.get("size") == "giant" or role == "villain" and c.get("size") == "large" else 1.0)
        person(d, x, gy + 5, size, col, **kw)
        lay = done(im)
        bb = lay.getbbox() or (0, 0, 1, 1)
        char_layers.append(dict(img=lay, crop=lay.crop(bb), bb=bb, role=role, x=x, size=size, props=props, pose=c.get("pose", "stand"),
                                halo=role in ("jesus", "angel")))
    S = dict(layers=layers, chars=char_layers, sky=sc.get("sky"), terrain=terrain, els=els, gy=gy,
             action=sc.get("action", "none"), camera=sc.get("camera", "zoom_in"), P=P)
    return S


# ---------------------------------------------------------------- frame
class Ctx:
    vig = None


def camera(S, u):
    cam = S["camera"]
    if cam == "zoom_out":
        z = 1.10 - 0.10 * ease(u); pan = 0
    elif cam == "pan_left":
        z = 1.04; pan = 60 - 120 * ease(u)
    elif cam == "pan_right":
        z = 1.04; pan = -60 + 120 * ease(u)
    elif cam == "push_up":
        z = 1.0 + 0.12 * ease(u); pan = 0
    else:
        z = 1.0 + 0.08 * ease(u); pan = -25 + 50 * ease(u)
    return z, pan


def render_frame(S, t, dur, motes_, cap, title_ov, lesson, cta):
    u = t / dur
    z, pan = camera(S, u)
    canvas = Image.new("RGBA", (CW, CH), (0, 0, 0, 255))
    act = S["action"]
    shake = (0, 0)
    if act in ("stone_throw", "fall", "lightning_strike") and 1.2 < t < 2.2:
        k = (2.2 - t) / 1.0
        shake = (math.sin(t * 90) * 12 * k, math.cos(t * 77) * 9 * k)
    for lay, depth in S["layers"]:
        sx = max(0, min(2 * M, int(M - pan * depth + shake[0] * depth)))
        canvas.alpha_composite(lay, dest=(0, int(shake[1] * depth)), source=(sx, 0))
    cd = ImageDraw.Draw(canvas)
    off = M - pan + shake[0]       # layer x -> canvas x : x - off
    gy = S["gy"]
    # water shimmer
    if S["terrain"] in ("sea", "river"):
        wc = hexc(S["P"]["water"])
        for i in range(10):
            y = (1450 if S["terrain"] == "sea" else gy + 10) + i * 34 + 6 * math.sin(t * 2 + i)
            x0 = (i * 173 + t * 40) % CW
            cd.line([(x0, y), (x0 + 120, y)], fill=(255, 245, 220, 70), width=3)
    # waves parting
    if act == "waves_part":
        p = ease((t - 0.3) / (dur * 0.6))
        wc = hexc(S["P"]["water"])
        base = (int(wc[0] * 0.25 + 40), int(wc[1] * 0.35 + 80), int(wc[2] * 0.35 + 120))
        gap = 40 + 330 * p
        top = 950
        cd.polygon([(CW / 2 - gap + 10, gy + 40), (CW / 2 + gap - 10, gy + 40), (CW / 2 + 30, 1500), (CW / 2 - 30, 1500)], fill=(150, 120, 90, int(220 * p)))
        for side in (-1, 1):
            cx = CW / 2 + side * gap
            pts = [(cx, gy + 60)]
            for k_ in range(13):
                v = k_ / 12
                y = gy + 60 - (gy + 60 - top) * v
                x = cx + side * (60 * v * v) + side * 8 * math.sin(t * 3 + k_)
                pts.append((x, y))
            pts += [(CW / 2 + side * (CW), top - 60), (CW / 2 + side * CW, gy + 60)]
            cd.polygon(pts, fill=base + (255,))
            for k_ in range(14):
                yy = top + 30 + k_ * 62
                xx = cx + side * (20 + 40 * ((gy - yy) / (gy - top)) ** 2)
                cd.line([(xx, yy + 8 * math.sin(t * 3 + k_)), (xx + side * 500, yy - 20)], fill=(220, 240, 255, 45), width=3)
            cd.line([(cx, gy + 60), (cx + side * 60, top)], fill=(230, 245, 255, 120), width=6)
    # characters
    villain_fall = act in ("stone_throw", "fall")
    hero = next((c for c in S["chars"] if c["role"] in ("hero", "jesus", "king")), S["chars"][0] if S["chars"] else None)
    villain = next((c for c in S["chars"] if c["role"] == "villain"), None)
    for c in S["chars"]:
        dx = 0
        if act == "walk_in" and c is hero:
            dx = -(1 - ease(t / (dur * 0.6))) * 700
        if c.get("halo"):
            r = c["size"] * 0.5 * (1 + 0.05 * math.sin(t * 2))
            gl = Image.new("RGBA", (int(r * 2), int(r * 2)), (0, 0, 0, 0))
            ImageDraw.Draw(gl).ellipse([0, 0, r * 2, r * 2], fill=(255, 235, 180, 70))
            gl = gl.filter(ImageFilter.GaussianBlur(r * 0.3))
            canvas.alpha_composite(gl, dest=(int(c["x"] - off - r), int(gy - c["size"] * 0.85 - r)))
        if c is villain and villain_fall:
            impact = 1.15 if act == "stone_throw" else 0.6
            ang = 0 if t < impact else 78 * ease((t - impact) / 1.5) ** 1.4 * (-1 if c["x"] > LW / 2 else 1)
            bb = c["img"].getbbox()
            if bb:
                g = c["img"].crop(bb)
                big = Image.new("RGBA", (g.width * 3, g.height * 2), (0, 0, 0, 0))
                ox = g.width
                big.alpha_composite(g, (ox, 0))
                pv = (ox + c["x"] - bb[0], gy - bb[1])
                rot = big.rotate(ang, resample=Image.BICUBIC, center=pv)
                canvas.paste(rot, (int(bb[0] - ox - off), int(bb[1] + shake[1])), rot)
            continue
        g, bb = c["crop"], c["bb"]
        canvas.paste(g, (int(bb[0] - off + dx), int(bb[1] + shake[1])), g)
    # projectile
    if act == "stone_throw" and hero and villain and t < 1.15:
        p = ease(t / 1.15)
        sx0, sy0 = hero["x"] - off + 30, gy - hero["size"] * 0.9
        hx, hy = villain["x"] - off, gy - villain["size"] * 0.93
        for k_ in range(10):
            pp = max(0, p - k_ * 0.02)
            xx = sx0 + (hx - sx0) * pp; yy = sy0 + (hy - sy0) * pp - math.sin(pp * math.pi) * 120
            r_ = 9 - k_ * 0.7
            cd.ellipse([xx - r_, yy - r_, xx + r_, yy + r_], fill=(255, 240, 200, 230 - k_ * 20))
    # fire
    if "fire" in S["els"] or "burning_bush" in S["els"] or act == "fire_burst":
        k_ = ease(t / 1.2) if act == "fire_burst" else 1.0
        bases = [(CW / 2 + 150 + (0 if "burning_bush" in S["els"] else -150), gy - 60)]
        if act == "fire_burst":
            bases += [(CW / 2 - 350, gy - 20), (CW / 2 + 350, gy - 20)]
        for (bx, by) in bases:
            for i in range(14):
                ph = i * 1.7 + t * 6
                fh = (120 + 60 * math.sin(ph)) * k_
                fx = bx + (i - 7) * 14 + 8 * math.sin(ph * 1.3)
                col = (255, 200 - i * 6, 80, 170)
                cd.polygon([(fx - 22, by), (fx + 22, by), (fx + 6 * math.sin(ph), by - fh)], fill=col)
            for i in range(12):
                ey = by - ((t * 120 + i * 53) % 400)
                ex = bx + 60 * math.sin(i * 2.1 + t)
                cd.ellipse([ex - 3, ey - 3, ex + 3, ey + 3], fill=(255, 210, 120, 200))
    # rain
    if act == "rain_falls" or S["sky"] == "storm":
        rnd = random.Random(3)
        for i in range(160):
            x = (rnd.random() * CW + t * 90) % CW
            y = (rnd.random() * CH + t * 1600 * (0.8 + rnd.random() * 0.4)) % CH
            cd.line([(x, y), (x - 10, y + 50)], fill=(200, 210, 230, 90), width=2)
    # lightning
    if act == "lightning_strike":
        for tt0 in (1.0, 2.6):
            if tt0 < t < tt0 + 0.25:
                rnd = random.Random(int(tt0 * 10))
                x, y = CW / 2 + rnd.randint(-300, 300), 0
                pts = [(x, y)]
                while y < gy - 200:
                    x += rnd.randint(-60, 60); y += rnd.randint(60, 140); pts.append((x, y))
                cd.line(pts, fill=(240, 240, 255, 255), width=6)
                canvas.alpha_composite(Image.new("RGBA", (CW, CH), (230, 230, 255, int(120 * (1 - (t - tt0) / 0.25)))))
    # light shine
    if act == "light_shine":
        k_ = ease(t / (dur * 0.5))
        rays = Image.new("RGBA", (CW, CH), (0, 0, 0, 0)); rd = ImageDraw.Draw(rays)
        cx, cy = CW / 2, 300
        for i in range(14):
            a = math.pi / 2 + (i - 6.5) * 0.12 + 0.03 * math.sin(t + i)
            rd.polygon([(cx, cy), (cx + 2600 * math.cos(a - 0.025), cy + 2600 * math.sin(a - 0.025)),
                        (cx + 2600 * math.cos(a + 0.025), cy + 2600 * math.sin(a + 0.025))], fill=(255, 244, 210, int(40 * k_)))
        canvas.alpha_composite(rays)
        canvas.alpha_composite(glow_img(LW / 2, 300, 700, "#fff4d0", 0.6 * k_).crop((M, 0, M + CW, CH)))
    # dove
    if act == "dove_fly" or "dove" in S["els"]:
        p = (t / dur)
        x = -100 + (CW + 200) * p; y = 700 + 80 * math.sin(p * 6)
        f = math.sin(t * 12) * 30
        cd.polygon([(x - 40, y - f), (x, y), (x + 40, y - f), (x + 10, y + 8), (x - 10, y + 8)], fill=(255, 255, 250, 255))
        cd.ellipse([x - 14, y - 8, x + 22, y + 10], fill=(255, 255, 250, 255))
    # impact flash
    if act == "stone_throw" and 1.1 < t < 1.6:
        canvas.alpha_composite(Image.new("RGBA", (CW, CH), (255, 245, 220, int(170 * (1 - (t - 1.1) / 0.5)))))
    if villain_fall and villain and t > 2.0:
        dd = ease((t - 2.0) / 0.8) * (1 - ease((t - 3.4) / 2.0))
        hx = villain["x"] - off
        for i in range(36):
            rr = random.Random(i)
            x = hx + (1 if villain["x"] > LW / 2 else -1) * 250 + (rr.random() - 0.5) * 900 * (0.4 + dd)
            y = gy + 10 - rr.random() * 240 * dd
            r_ = 20 + rr.random() * 40
            cd.ellipse([x - r_, y - r_ * 0.6, x + r_, y + r_ * 0.6], fill=(120, 90, 80, int(38 * dd)))
    # camera crop
    cw, ch = W / z, H / z
    cy = min(max(CH * 0.5, ch / 2), CH - ch / 2)
    fr = canvas.resize((W, H), Image.BICUBIC, box=(CW / 2 - cw / 2, cy - ch / 2, CW / 2 + cw / 2, cy + ch / 2))
    fd = ImageDraw.Draw(fr)
    star = S["sky"] in ("night",)
    for (x, y, r, ph, sp) in motes_:
        if star:
            a = int(120 + 100 * math.sin(t * 2 + ph))
            fd.ellipse([x - r * 0.6, (y * 0.55) - r * 0.6, x + r * 0.6, (y * 0.55) + r * 0.6], fill=(255, 255, 240, a))
        else:
            yy = (y - t * sp) % H
            xx = x + math.sin(t * 0.8 + ph) * 12
            a = int(70 + 50 * math.sin(t * 1.5 + ph))
            fd.ellipse([xx - r, yy - r, xx + r, yy + r], fill=(255, 236, 200, a))
    fr.alpha_composite(Ctx.vig)
    if title_ov is not None:
        a = ease((t - 0.3) / 0.9)
        ov = title_ov.copy(); ov.putalpha(ov.split()[3].point(lambda v: int(v * a)))
        fr.alpha_composite(ov, dest=(0, int(500 + (1 - a) * 30)))
    if cap is not None:
        a = ease((t - (0.5 if not lesson else 0.3)) / 0.5) * (1 - ease((t - (dur - 0.35)) / 0.3))
        if a > 0:
            c = cap.copy(); c.putalpha(c.split()[3].point(lambda v: int(v * a)))
            fr.alpha_composite(c, dest=(0, int((560 if lesson else 300) + (1 - a) * 25)))
    if cta is not None:
        a = ease((t - 1.5) / 0.6)
        c = cta.copy(); c.putalpha(c.split()[3].point(lambda v: int(v * a)))
        fr.alpha_composite(c, dest=(0, 1020))
    edge = min(1.0, t / 0.2, (dur - t) / 0.2)
    out = fr.convert("RGB")
    if edge < 1:
        out = Image.blend(Image.new("RGB", (W, H)), out, max(0.0, edge))
    return out


def cta_img(text):
    f = ImageFont.truetype(SANS_B, 40)
    im = Image.new("RGBA", (W, 120), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    w_ = d.textlength(text, font=f)
    d.rounded_rectangle([(W - w_) / 2 - 36, 20, (W + w_) / 2 + 36, 100], radius=40, fill=(244, 196, 98, 235))
    d.text(((W - w_) / 2, 36), text, font=f, fill=(40, 24, 14, 255))
    return im


def motes(seed, n=36):
    rnd = random.Random(seed)
    return [(rnd.random() * W, rnd.random() * H, 1.5 + rnd.random() * 3.5, rnd.random() * 6.28, 8 + rnd.random() * 20) for _ in range(n)]


def render_scene(args):
    """args: (script, idx, dur, out_path). Writes an mp4 clip of `dur` seconds."""
    script, idx, dur, out_path = args
    Ctx.vig = vignette()
    scenes = script["scenes"]
    sc = scenes[idx]
    S = build_scene(sc, idx, len(scenes), script)
    last = idx == len(scenes) - 1
    title_ov = title_img(script["title"], script.get("reference", "")) if idx == 0 else None
    cap = None
    txt = sc.get("on_screen_text")
    if idx > 0 and txt:
        cap = caption_img(txt, ref=None if last else sc.get("verse_ref"), gold_words=sc.get("highlight_words", []),
                          size=60 if last else 54)
    cta = cta_img(script.get("cta", "Ikuti untuk kisah Alkitab setiap hari")) if last else None
    mt = motes(idx + 7)
    n = int(round(dur * FPS))
    tmp = out_path.replace(".mp4", ".tmp.mp4")
    p = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                          "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-crf", "20", "-preset", "veryfast",
                          "-pix_fmt", "yuv420p", tmp], stdin=subprocess.PIPE)
    for f in range(n):
        p.stdin.write(render_frame(S, f / FPS, dur, mt, cap, title_ov, last, cta).tobytes())
    p.stdin.close()
    if p.wait() != 0:
        raise RuntimeError(f"ffmpeg failed for scene {idx}")
    os.replace(tmp, out_path)
    return out_path


def preview(script, idx, t, dur=7.0):
    Ctx.vig = vignette()
    sc = script["scenes"][idx]
    S = build_scene(sc, idx, len(script["scenes"]), script)
    last = idx == len(script["scenes"]) - 1
    title_ov = title_img(script["title"], script.get("reference", "")) if idx == 0 else None
    cap = caption_img(sc["on_screen_text"], None if last else sc.get("verse_ref"), sc.get("highlight_words", []), 60 if last else 54) if idx > 0 and sc.get("on_screen_text") else None
    cta = cta_img(script.get("cta", "Ikuti untuk kisah Alkitab setiap hari")) if last else None
    return render_frame(S, t, dur, motes(idx + 7), cap, title_ov, last, cta)
