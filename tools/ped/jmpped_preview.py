#!/usr/bin/env python3
"""Render a 360-degree turntable GIF of the baked JMPPED pedestrian.

This is a small, self-contained previewer: it reads the 12 MODEL blobs from
JMPPED.LEV, places the parts in a rough standing pose, and rasterises the PSX
polygons with Pillow. It deliberately does not use the game's textures -- the
requested preview colour is applied as a flat shaded tint.

Usage:
    python preview_jmpped.py --out tmp/jmpped_360.gif --color 200 200 50
"""

import argparse
import math
import struct
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

MAGIC = b"JMPPED\0\0"
PART_NAMES = [
    "JERI_TORSO", "JERI_HEAD",
    "JERI_U_ARM_LEFT", "JERI_L_ARM_LEFT",
    "JERI_U_ARM_RIGHT", "JERI_L_ARM_RIGHT",
    "THIGH_LEFT", "CALF_LEFT", "FOOT_LEFT",
    "THIGH_RIGHT", "CALF_RIGHT", "FOOT_RIGHT",
]
POLY_SIZES = [
    8, 12, 16, 24, 20, 20, 28, 32, 8, 12, 16, 16,
    0, 0, 0, 0, 12, 12, 12, 16, 20, 20, 24, 24,
    0, 0, 0, 0, 0, 0, 0, 0, 8, 12, 16, 24,
    20, 24, 28, 32, 0, 0, 0, 0, 0, 0, 0, 0,
    12, 12, 12, 16, 20, 20, 24, 24,
]
QUADS = {2, 3, 5, 7, 21, 23}


def parse_model(blob):
    instance = struct.unpack_from("<h", blob, 4)[0]
    nverts = struct.unpack_from("<H", blob, 12)[0]
    npolys = struct.unpack_from("<H", blob, 14)[0]
    voff = struct.unpack_from("<i", blob, 16)[0]
    poff = struct.unpack_from("<i", blob, 20)[0]

    verts = np.zeros((0, 3), dtype=np.float32)
    if 0 <= voff and voff + nverts * 8 <= len(blob):
        raw = np.frombuffer(blob, dtype="<i2", count=nverts * 4, offset=voff)
        verts = raw.reshape(-1, 4)[:, :3].astype(np.float32)

    polys = []
    p = poff
    for _ in range(npolys):
        if p < 0 or p >= len(blob):
            break
        kind = blob[p] & 0x1f
        step = POLY_SIZES[kind] if kind < len(POLY_SIZES) else 0
        if step <= 0 or p + step > len(blob):
            break
        count = 4 if kind in QUADS else 3
        idx = [blob[p + 4 + i] for i in range(count)]
        if max(idx) < nverts:
            polys.append(idx)
        p += step
    return {"instance": instance, "verts": verts, "polys": polys}


def load_models(path, character=None):
    data = Path(path).read_bytes()
    if data[:8] != MAGIC:
        raise SystemExit(f"{path}: not a JMPPED.LEV")
    version, count = struct.unpack_from("<II", data, 8)

    if version == 5:
        off = 16
        entries = []
        for _ in range(count):
            name_len = struct.unpack_from("<I", data, off)[0]
            off += 4
            name = data[off:off + name_len].decode("utf-8")
            off += name_len
            mcount = struct.unpack_from("<I", data, off)[0]
            off += 4
            models = []
            for _m in range(mcount):
                size = struct.unpack_from("<I", data, off)[0]
                off += 4
                models.append(parse_model(data[off:off + size]))
                off += size
            page_count = struct.unpack_from("<I", data, off)[0]
            off += 4
            for _p in range(page_count):
                _, _, clut_len, image_len, _ = struct.unpack_from("<IIIII", data, off)
                off += 20 + clut_len + image_len
            entries.append((name, models))
        if not entries:
            raise SystemExit(f"{path}: v5 archive has no entries")
        if character is None:
            index = len(entries) - 1
        elif isinstance(character, int):
            index = character
        else:
            index = next((i for i, (n, _m) in enumerate(entries)
                          if n == character), -1)
            if index < 0:
                raise SystemExit(f"{path}: no character named {character!r}")
        if index < 0 or index >= len(entries):
            raise SystemExit(f"{path}: character {index} is out of range")
        selected_name = entries[index][0]
        models = entries[index][1]
    elif version in (3, 4) and count in (14, 15):
        off = 16
        models = []
        for _ in range(count):
            size = struct.unpack_from("<I", data, off)[0]
            off += 4
            models.append(parse_model(data[off:off + size]))
            off += size
        selected_name = "Jericho"
    else:
        raise SystemExit(f"{path}: expected v3/v4/v5 JMPPED, got version {version}/{count}")

    for m in models:
        if m["instance"] >= 0:
            parent = models[m["instance"]]
            m["verts"] = parent["verts"]
    return models, selected_name

def part_offsets(models):
    """Rough humanoid layout. The bake stores separate parts, not a posed rig."""
    def size(i):
        v = models[i]["verts"]
        return (v.max(axis=0) - v.min(axis=0)) if len(v) else np.ones(3)

    torso = size(0)
    head = size(1)
    uw, uh, _ = size(2)
    lw, lh, _ = size(3)
    tw, th, _ = size(6)
    cw, ch, _ = size(7)
    fw, fh, _ = size(8)

    x_arm = torso[0] * 0.55 + uw * 0.5
    y_arm = torso[1] * 0.18
    x_leg = torso[0] * 0.22
    lower_y = y_arm + uh * 0.5 + lh * 0.5

    return {
        0: np.array([0.0, 0.0, 0.0]),
        1: np.array([0.0, -(torso[1] * 0.5 + head[1] * 0.5), 0.0]),
        2: np.array([-x_arm, y_arm, 0.0]),
        3: np.array([-x_arm, lower_y, 0.0]),
        4: np.array([x_arm, y_arm, 0.0]),
        5: np.array([x_arm, lower_y, 0.0]),
        6: np.array([-x_leg, torso[1] * 0.5 + th * 0.5, 0.0]),
        7: np.array([-x_leg, torso[1] * 0.5 + th + ch * 0.5, 0.0]),
        8: np.array([-x_leg, torso[1] * 0.5 + th + ch + fh * 0.5, 0.0]),
        9: np.array([x_leg, torso[1] * 0.5 + th * 0.5, 0.0]),
        10: np.array([x_leg, torso[1] * 0.5 + th + cw * 0.5, 0.0]),
        11: np.array([x_leg, torso[1] * 0.5 + th + cw + fh * 0.5, 0.0]),
    }


def make_world(models):
    offsets = part_offsets(models)
    parts = []
    for i, m in enumerate(models):
        v = m["verts"]
        if i not in offsets or len(v) == 0:
            continue
        v = v - (v.min(axis=0) + v.max(axis=0)) * 0.5
        parts.append((v + offsets[i], m["polys"]))
    return parts


def render_frame(parts, angle, colour, size=512):
    img = Image.new("RGB", (size, size), (18, 20, 24))
    draw = ImageDraw.Draw(img)

    ca, sa = math.cos(angle), math.sin(angle)
    rot = np.array([[ca, 0, sa], [0, 1, 0], [-sa, 0, ca]], dtype=np.float32)
    light = np.array([0.35, 0.75, 0.45], dtype=np.float32)
    light /= np.linalg.norm(light)

    all_pts = [v @ rot.T for v, _ in parts]
    mn = np.min([p.min(axis=0) for p in all_pts], axis=0)
    mx = np.max([p.max(axis=0) for p in all_pts], axis=0)
    span = max(float(mx[0] - mn[0]), float(mx[1] - mn[1]), 1.0)
    scale = (size * 0.78) / span
    cx = size * 0.5 - (mn[0] + mx[0]) * 0.5 * scale
    cy = size * 0.52 + (mn[1] + mx[1]) * 0.5 * scale

    faces = []
    for (verts, polys), pts in zip(parts, all_pts):
        for poly in polys:
            p = pts[poly]
            if len(p) == 4:
                n = np.cross(p[1] - p[0], p[2] - p[0])
            else:
                n = np.cross(p[1] - p[0], p[2] - p[0])
            ln = np.linalg.norm(n)
            if ln < 1e-5:
                continue
            shade = 0.40 + 0.60 * abs(float(np.dot(n / ln, light)))
            rgb = tuple(max(0, min(255, int(c * shade))) for c in colour)
            screen = [(cx + x * scale, cy - y * scale) for x, y, _ in p]
            faces.append((float(p[:, 2].mean()), screen, rgb))

    faces.sort(key=lambda f: f[0])
    for _, poly, rgb in faces:
        draw.polygon(poly, fill=rgb)

    draw.text((16, 16), "JMPPED 360", fill=(230, 230, 230))
    draw.text((16, size - 28), "tint %d %d %d" % colour, fill=(190, 190, 190))
    return img


def preview(lev, out, character=None, frames=36, size=512,
            color=(200, 200, 50)) -> int:
    models, name = load_models(lev, character)
    print(f"character : {name}")
    parts = make_world(models)
    images = []
    for i in range(frames):
        angle = 2.0 * math.pi * i / frames
        images.append(render_frame(parts, angle, tuple(color), size))
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    images[0].save(path, save_all=True, append_images=images[1:],
                   duration=80, loop=0, disposal=2, optimize=False)
    print(f"wrote {path} ({len(images)} frames, {size}x{size})")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lev", default="JERICHO/MODS/mp/tools/JMPPED.LEV")
    ap.add_argument("--out", default="tmp/jmpped_360.gif")
    ap.add_argument("--character", default=None,
                    help="character name in a v5 archive (default: last/custom)")
    ap.add_argument("--color", nargs=3, type=int, default=[200, 200, 50])
    ap.add_argument("--frames", type=int, default=36)
    ap.add_argument("--size", type=int, default=512)
    args = ap.parse_args()
    return preview(args.lev, args.out, args.character, args.frames, args.size,
                   tuple(args.color))


if __name__ == "__main__":
    raise SystemExit(main())
