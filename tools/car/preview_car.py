#!/usr/bin/env python3
"""preview_car.py - rotating in-window preview of a car bank's authoring art.

    python preview_car.py <bank.json | bank dir> [--car 1_BASE2] [--timeout S]
                          [--stop-file PATH] [--mute] [--variant clean|damaged|low]
                          [--rebuilt] [--cycle S] [--turntable | --no-scene]

Reads what declare.cars.<car> names in bank.json (the clean / damaged / low
OBJs + their MTL textures, the wheel PNGs, and sounds/engine|idle wav) - NOT
the baked LEV, so it shows the art as authored, not the 16-colour pages.

Orientation: the bank's OBJs are in engine space, where -Y is up (what the bake's
plain mode expects). --y-up is for a bank baked with flipy / full / flipfull.

Keys:   1 / 2 / 3   clean / damaged / low model      Tab   next model
        G           road scene <-> turntable          Esc/Q close
        drag (left button) or arrow keys   orbit the car; wheel or PgUp/PgDn  zoom;
        R           back to the automatic camera
        The road's back faces are culled, so a wall between the camera and the car
        is see-through from behind and the car stays in view (--no-cull to disable).
A damaged or low OBJ whose vertex count differs from the clean one is flagged
in the title: the bake pairs the damaged vertices with the CLEAN polygon table,
so a count mismatch garbles the car when it is dented (see rebuild_damaged.py;
--rebuilt shows its *_damaged.rebuilt.obj instead).

Road scene: test_scenes/vegas.glb is drawn as an endless road. The car holds its
lane while the road scrolls past at the speed of the rev cycle, and the camera
sits close and low beside it. --scene PATH uses another .glb; --turntable (or
--no-scene) gives the old rotating car on an empty background.

Loop (repeats):  idle ~2s  ->  rev through 4 gears (clean wheel, then fast
wheel once speed is up)  ->  coast back to idle on the damaged wheel.

Exits on Esc / Q / window close, after --timeout seconds, or as soon as
--stop-file exists (rebake.bat touches it once you answer the 0-4 prompt).
Missing pygame / PyOpenGL: prints a note and exits 0, never blocks a bake.
"""
import argparse, io, json, math, os, struct, sys, time
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

try:
    import pygame
    from OpenGL.GL import *  # noqa
    from OpenGL.GLU import gluPerspective
except Exception as e:  # pragma: no cover
    print("preview_car: skipped (%s) - pip install pygame-ce PyOpenGL numpy" % e)
    sys.exit(0)
try:
    import numpy as np
except Exception:
    np = None


# The engine's suspension (wheelforces.c): each wheel pushes with 230 * compression
# - 100 * last compression, i.e. a spring of 130 per wheel plus damping, against a
# gravity of 7456. The body therefore settles at 7456 / (4 * 130) = 14.3 units of
# compression, which is also what handling.c seeds a spawning car with.
SUS_SPRING, SUS_GRAVITY = 130.0, 7456.0
REST_COMP = SUS_GRAVITY / (4 * SUS_SPRING)


def perspective(fovy, aspect, near, far):
    """gluPerspective, or the same matrix from glFrustum where GLU is missing."""
    if bool(gluPerspective):
        gluPerspective(fovy, aspect, near, far)
        return
    top = near * math.tan(math.radians(fovy) / 2)
    glFrustum(-top * aspect, top * aspect, -top, top, near, far)


SKY = (0.45, 0.55, 0.70)      # road-scene sky / distance fog


def wheels_of(car):
    """The car's wheel entries: textures.wheels, or the older top-level wheels."""
    return (car.get("textures") or {}).get("wheels") or car.get("wheels") or {}


def load_bank(path):
    if os.path.isdir(path):
        path = os.path.join(path, "bank.json")
    with open(path, encoding="utf-8") as f:
        bank = json.load(f)
    return os.path.dirname(os.path.abspath(path)), bank


def load_mtl(path):
    mats, cur = {}, None
    if not os.path.exists(path):
        return mats
    base = os.path.dirname(path)
    for line in open(path, encoding="utf-8", errors="replace"):
        p = line.strip().split(None, 1)
        if not p:
            continue
        if p[0] == "newmtl":
            cur = mats.setdefault(p[1].strip(), {"kd": (0.7, 0.7, 0.7), "tex": None})
        elif cur is not None and p[0] == "Kd":
            cur["kd"] = tuple(float(x) for x in p[1].split()[:3])
        elif cur is not None and p[0] == "map_Kd":
            t = os.path.join(base, p[1].strip())
            cur["tex"] = t if os.path.exists(t) else None
    return mats


def load_obj(path):
    vs, vts, groups = [], [], {}
    mats, cur = {}, None
    base = os.path.dirname(path)
    for line in open(path, encoding="utf-8", errors="replace"):
        p = line.split()
        if not p:
            continue
        if p[0] == "v":
            vs.append(tuple(float(x) for x in p[1:4]))
        elif p[0] == "vt":
            vts.append((float(p[1]), float(p[2]) if len(p) > 2 else 0.0))
        elif p[0] == "mtllib":
            mats.update(load_mtl(os.path.join(base, line.split(None, 1)[1].strip())))
        elif p[0] == "usemtl":
            cur = line.split(None, 1)[1].strip()
        elif p[0] == "f":
            idx = []
            for t in p[1:]:
                a = t.split("/")
                vi = int(a[0]); vi = vi - 1 if vi > 0 else len(vs) + vi
                ti = None
                if len(a) > 1 and a[1]:
                    ti = int(a[1]); ti = ti - 1 if ti > 0 else len(vts) + ti
                idx.append((vi, ti))
            for i in range(1, len(idx) - 1):
                groups.setdefault(cur, []).append((idx[0], idx[i], idx[i + 1]))
    return vs, vts, groups, mats


def gl_texture(path):
    surf = pygame.image.load(path).convert_alpha()
    w, h = surf.get_size()
    if np is not None:
        rgb = pygame.surfarray.pixels3d(surf); al = pygame.surfarray.pixels_alpha(surf)
        al[(rgb[:, :, 0] == 0) & (rgb[:, :, 1] == 0) & (rgb[:, :, 2] == 0)] = 0
        del rgb, al
    else:
        for x in range(w):
            for y in range(h):
                c = surf.get_at((x, y))
                if c.r == 0 and c.g == 0 and c.b == 0:
                    surf.set_at((x, y), (0, 0, 0, 0))
    data = pygame.image.tobytes(surf, "RGBA", True)
    tid = glGenTextures(1)
    glBindTexture(GL_TEXTURE_2D, tid)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST)
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, w, h, 0, GL_RGBA, GL_UNSIGNED_BYTE, data)
    return tid


def build_list(vs, vts, groups, mats, center, scale):
    texcache = {}                       # uploaded BEFORE the list: inside glNewList an upload is only recorded
    for name in groups:
        t_ = mats.get(name, {"tex": None})["tex"]
        if t_ and t_ not in texcache:
            texcache[t_] = gl_texture(t_)
    lst = glGenLists(1)
    glNewList(lst, GL_COMPILE)
    light = (0.4, 0.8, 0.45)
    for name, tris in groups.items():
        m = mats.get(name, {"kd": (0.7, 0.7, 0.7), "tex": None})
        tex = m["tex"]
        if tex:
            glEnable(GL_TEXTURE_2D); glBindTexture(GL_TEXTURE_2D, texcache[tex])
            base = (1.0, 1.0, 1.0)
        else:
            glDisable(GL_TEXTURE_2D); base = m["kd"]
        glBegin(GL_TRIANGLES)
        for tri in tris:
            P = [[(vs[v][k] - center[k]) * scale for k in range(3)] for v, _ in tri]
            ux, uy, uz = [P[1][k] - P[0][k] for k in range(3)]
            vx, vy, vz = [P[2][k] - P[0][k] for k in range(3)]
            n = (uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx)
            ln = math.sqrt(sum(c * c for c in n)) or 1.0
            s = 0.55 + 0.45 * abs(sum(n[k] * light[k] for k in range(3)) / ln)
            glColor3f(base[0] * s, base[1] * s, base[2] * s)
            for (v, t), p in zip(tri, P):
                if t is not None and t < len(vts):
                    glTexCoord2f(*vts[t])
                glVertex3f(*p)
        glEnd()
    glDisable(GL_TEXTURE_2D)
    glEndList()
    return lst


# ---- test scene: a .glb road ------------------------------------------------

_GLTF_DT = {5120: "i1", 5121: "u1", 5122: "<i2", 5123: "<u2", 5125: "<u4", 5126: "<f4"}
_GLTF_N = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}

# per-scene tuning, keyed by file name; anything else is measured from the mesh.
# scale: scene units -> preview units (the car is 2 long, so ~4.6 makes the
# 0.71-wide road a believable two lanes); period: the repeat length along the
# road; road_y: the road surface height; lane: the car's lateral offset
SCENE_DEFAULTS = {
    "vegas.glb": {"scale": 4.6, "period": 22.9, "road_y": -0.068, "lane": 0.125},
}


def _quat_matrix(q):
    x, y, z, w = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def load_glb(path):
    """Minimal glTF 2.0 binary reader (no animation or skins).

    -> list of {pos, nrm, uv, idx, image, image_key, color}: every node's
    transform is already applied to pos/nrm, image is the PNG/JPEG bytes."""
    data = open(path, "rb").read()
    magic, ver, _ = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF" or ver != 2:
        raise ValueError("%s is not a glTF 2.0 binary" % path)
    js, blob, off = None, b"", 12
    while off + 8 <= len(data):
        ln, typ = struct.unpack_from("<I4s", data, off)
        body = data[off + 8:off + 8 + ln]
        if typ == b"JSON":
            js = json.loads(body.decode("utf-8"))
        elif typ == b"BIN\x00":
            blob = body
        off += 8 + ln

    def accessor(i):
        ac = js["accessors"][i]
        bv = js["bufferViews"][ac["bufferView"]]
        dt = np.dtype(_GLTF_DT[ac["componentType"]])
        n = _GLTF_N[ac["type"]]
        width = dt.itemsize * n
        stride = bv.get("byteStride") or width
        start = bv.get("byteOffset", 0) + ac.get("byteOffset", 0)
        raw = np.frombuffer(blob, dtype=np.uint8, count=stride * (ac["count"] - 1) + width, offset=start)
        rows = np.lib.stride_tricks.as_strided(raw, shape=(ac["count"], width), strides=(stride, 1))
        return np.ascontiguousarray(rows).view(dt).reshape(ac["count"], n)

    def material(mi):
        m = js["materials"][mi] if mi is not None else {}
        pbr = m.get("pbrMetallicRoughness", {})
        t = pbr.get("baseColorTexture")
        key, img = None, None
        if t is not None:
            key = js["textures"][t["index"]].get("source")
            im = js["images"][key] if key is not None else {}
            if "bufferView" in im:
                bv = js["bufferViews"][im["bufferView"]]
                o = bv.get("byteOffset", 0)
                img = blob[o:o + bv["byteLength"]]
        return key, img, pbr.get("baseColorFactor", [0.7, 0.7, 0.7, 1.0])

    prims = []

    def walk(ni, parent):
        nd = js["nodes"][ni]
        if "matrix" in nd:
            M = np.array(nd["matrix"], dtype=float).reshape(4, 4).T
        else:
            T = np.eye(4); T[:3, 3] = nd.get("translation", (0, 0, 0))
            R = np.eye(4); R[:3, :3] = _quat_matrix(nd.get("rotation", (0, 0, 0, 1)))
            Sc = np.diag(list(nd.get("scale", (1, 1, 1))) + [1.0])
            M = T @ R @ Sc
        W = parent @ M
        if "mesh" in nd:
            for pr in js["meshes"][nd["mesh"]]["primitives"]:
                if pr.get("mode", 4) != 4:
                    continue
                at = pr["attributes"]
                pos = accessor(at["POSITION"]).astype(np.float64) @ W[:3, :3].T + W[:3, 3]
                nrm = None
                if "NORMAL" in at:
                    nrm = accessor(at["NORMAL"]).astype(np.float64) @ W[:3, :3].T
                    ln = np.linalg.norm(nrm, axis=1, keepdims=True)
                    nrm = nrm / np.where(ln == 0, 1, ln)
                uv = accessor(at["TEXCOORD_0"]).astype(np.float64) if "TEXCOORD_0" in at else None
                idx = (accessor(pr["indices"]).reshape(-1).astype(np.int64)
                       if "indices" in pr else np.arange(len(pos)))
                key, img, col = material(pr.get("material"))
                prims.append({"pos": pos, "nrm": nrm, "uv": uv, "idx": idx,
                              "image": img, "image_key": key, "color": col})
        for c in nd.get("children", []):
            walk(c, W)

    for n in js["scenes"][js.get("scene", 0)]["nodes"]:
        walk(n, np.eye(4))
    return prims


def gl_texture_bytes(png):
    """A scene texture: the file's own alpha, magenta keyed out, mip-mapped."""
    surf = pygame.image.load(io.BytesIO(png)).convert_alpha()
    w, h = surf.get_size()
    if np is not None:
        rgb = pygame.surfarray.pixels3d(surf); al = pygame.surfarray.pixels_alpha(surf)
        al[(rgb[:, :, 0] == 255) & (rgb[:, :, 1] == 0) & (rgb[:, :, 2] == 255)] = 0
        del rgb, al
    data = pygame.image.tobytes(surf, "RGBA", False)      # glTF v runs top-down
    tid = glGenTextures(1)
    glBindTexture(GL_TEXTURE_2D, tid)
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, w, h, 0, GL_RGBA, GL_UNSIGNED_BYTE, data)
    try:
        glGenerateMipmap(GL_TEXTURE_2D)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST_MIPMAP_LINEAR)
    except Exception:
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST)
    return tid


def build_scene(prims):
    """One display list for the whole scene (glTF world space)."""
    light = np.array([0.4, 0.8, 0.45]); light /= np.linalg.norm(light)
    # textures are made BEFORE the list is compiled: inside glNewList an upload is
    # only recorded, and would then run again on every frame the list is called
    texcache = {}
    for p in prims:
        if p["image"] is not None and p["image_key"] not in texcache:
            texcache[p["image_key"]] = gl_texture_bytes(p["image"])
    lst = glGenLists(1)
    glNewList(lst, GL_COMPILE)
    for p in prims:
        if p["image"] is not None:
            glEnable(GL_TEXTURE_2D); glBindTexture(GL_TEXTURE_2D, texcache[p["image_key"]])
            base = (1.0, 1.0, 1.0)
        else:
            glDisable(GL_TEXTURE_2D); base = p["color"][:3]
        sh = (0.6 + 0.4 * np.abs(p["nrm"] @ light)) if p["nrm"] is not None else np.ones(len(p["pos"]))
        glBegin(GL_TRIANGLES)
        for i in p["idx"]:
            s_ = sh[i]
            glColor3f(base[0] * s_, base[1] * s_, base[2] * s_)
            if p["uv"] is not None:
                glTexCoord2f(*p["uv"][i])
            glVertex3f(*p["pos"][i])
        glEnd()
    glDisable(GL_TEXTURE_2D)
    glEndList()
    return lst


def scene_params(prims, name, scale=None, period=None, lane=None, road_y=None):
    """Tuning for one scene: the SCENE_DEFAULTS entry, else measured from the mesh
    (repeat length = the whole x extent, road surface = the lowest flat band)."""
    d = dict(SCENE_DEFAULTS.get(os.path.basename(name).lower(), {}))
    allp = np.vstack([p["pos"] for p in prims])
    if "road_y" not in d:
        lo = allp[:, 1].min()
        d["road_y"] = float(np.median(allp[allp[:, 1] < lo + 0.02 * (allp[:, 1].max() - lo + 1e-9), 1]))
    d.setdefault("scale", 4.6)
    d.setdefault("period", float(allp[:, 0].max() - allp[:, 0].min()))
    d.setdefault("lane", float((allp[:, 2].max() + allp[:, 2].min()) / 2))
    for k, v in (("scale", scale), ("period", period), ("lane", lane), ("road_y", road_y)):
        if v is not None:
            d[k] = v
    return d


def make_sound(path, pitches):
    """{pitch: Sound} by resampling the wav (numpy) - pitch 1.0 if no numpy."""
    out = {}
    if not path or not os.path.exists(path):
        return out
    try:
        snd = pygame.mixer.Sound(path)
    except Exception as e:
        print("preview_car: can't load %s (%s)" % (path, e)); return out
    if np is None:
        return {1.0: snd}
    a = pygame.sndarray.array(snd)
    for p in pitches:
        n = max(2, int(len(a) / p))
        x = np.linspace(0, len(a) - 1, n)
        if a.ndim == 1:
            r = np.interp(x, np.arange(len(a)), a)
        else:
            r = np.stack([np.interp(x, np.arange(len(a)), a[:, c]) for c in range(a.shape[1])], 1)
        out[p] = pygame.sndarray.make_sound(np.ascontiguousarray(r.astype(a.dtype)))
    return out


# timeline: idle, then each gear sweeps its own rpm band (shorter, wider
# low gears; longer, narrower high gears), then a coast back to idle.
# (seconds, pitch_from, pitch_to) per gear
GEARS_T = [(1.4, 0.70, 1.55), (1.9, 0.95, 1.60), (2.4, 1.08, 1.65), (3.1, 1.18, 1.70)]
IDLE, COAST = 2.0, 4.0
GEARS = sum(g[0] for g in GEARS_T)
CYCLE = IDLE + GEARS + COAST
PITCH_IDLE = 0.70


def state(t):
    """-> phase, speed 0..1, engine pitch, gear"""
    t %= CYCLE
    if t < IDLE:
        return "idle", 0.0, PITCH_IDLE, 0
    g = t - IDLE
    if g < GEARS:
        for i, (dur, p0, p1) in enumerate(GEARS_T):
            if g < dur:
                x = g / dur
                x = 1 - (1 - x) ** 1.6          # pulls hard, flattens near the shift
                return "gear %d" % (i + 1), (t - IDLE) / GEARS, p0 + (p1 - p0) * x, i + 1
            g -= dur
    c = (t - IDLE - GEARS) / COAST
    return "coast", 1.0 - c, PITCH_IDLE + (GEARS_T[-1][2] - PITCH_IDLE) * (1 - c) ** 1.5, 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bank")
    ap.add_argument("--car")
    ap.add_argument("--timeout", type=float, default=0)
    ap.add_argument("--stop-file")
    ap.add_argument("--mute", action="store_true")
    ap.add_argument("--bake-scale", type=float, help="rebake --scale (OBJ->engine units); default: 1 if the OBJ is already engine-sized, else 265")
    ap.add_argument("--ride", type=float, help="rest susCompression in engine units. Default: where the engine settles, gravity / (4 wheels x spring), 7456 / (4 x 130) = 14.3 (handling.c seeds 14)")
    ap.add_argument("--no-suspension", action="store_true", help="hold the body at its rest height (no squat, dive or bounce)")
    ap.add_argument("--susp-gain", type=float, default=1.0, help="exaggerate the suspension motion (1 = the engine's own spring rates)")
    ap.add_argument("--yaw", type=float, help="fixed yaw in degrees (no spin) - for checking shots")
    ap.add_argument("--size", default="640x400")
    ap.add_argument("--shot", help="save PNG frames into this dir at 1s,4s,8s,12s")
    ap.add_argument("--flip-y", action="store_true", help="the OBJ is in engine space, -Y up (the default; kept so older preview.bat / rebake.bat still work)")
    ap.add_argument("--y-up", action="store_true", help="the OBJ is Y-up instead (a bank baked with rebake flipy / full / flipfull)")
    ap.add_argument("--variant", choices=("clean", "damaged", "low"), default="clean", help="model to start on (1/2/3 or Tab switch it live)")
    ap.add_argument("--rebuilt", action="store_true", help="show <damaged>.rebuilt.obj (rebuild_damaged.py) instead of the declared damaged OBJ when it exists")
    ap.add_argument("--cycle", type=float, default=0, help="step through clean/damaged/low every S seconds (unattended shots)")
    ap.add_argument("--scene", help="road scene .glb (default: test_scenes/vegas.glb beside this script)")
    ap.add_argument("--no-scene", "--turntable", dest="no_scene", action="store_true", help="no road: the car turns on an empty background")
    ap.add_argument("--scene-scale", type=float, help="scene units -> preview units (default 4.6 for vegas.glb)")
    ap.add_argument("--scene-period", type=float, help="road repeat length, in scene units")
    ap.add_argument("--scene-lane", type=float, help="car's lateral position, in scene units")
    ap.add_argument("--road-speed", type=float, default=22.0, help="road speed at full revs, preview units/s")
    ap.add_argument("--no-cull", action="store_true", help="draw the road scene double-sided instead of culling its back faces")
    ap.add_argument("--cam-dist", type=float, default=3.3, help="camera distance (was 4.2)")
    ap.add_argument("--cam-pitch", type=float, default=7.0, help="camera pitch down, degrees (was 18)")
    ap.add_argument("--shot-at", default="1,4,8,12", help="--shot times in seconds, comma separated")
    a = ap.parse_args()
    a.flip_y = not a.y_up       # Driver 2 models face -Y up: the bank's OBJs are engine space unless the bake flipped them

    root, bank = load_bank(a.bank)
    cars = bank.get("declare", {}).get("cars", {})
    if not cars:
        print("preview_car: no declare.cars in bank.json"); return 0
    key = a.car or next(iter(cars))
    bank_root = root

    def resolve(entry):
        # a car may be a path to its own file (cars/<car>/handling.json),
        # whose paths are relative to that file - as bankjson reads it
        if isinstance(entry, str):
            f = os.path.normpath(os.path.join(bank_root, entry))
            with open(f, encoding="utf-8") as fh:
                return os.path.dirname(f), json.load(fh)
        return bank_root, entry or {}

    root, car = resolve(cars[key])
    wroot, wcar = root, car
    if not wheels_of(car):      # this car inherits the bank's first declared set
        for other in cars.values():
            r, c = resolve(other)
            if wheels_of(c):
                wroot, wcar = r, c
                break
    objp = os.path.join(root, car.get("clean", ""))
    if not os.path.exists(objp):
        print("preview_car: no clean model %s" % objp); return 0

    pygame.init()
    try:
        pygame.mixer.init(frequency=22050, size=-16, channels=1)
    except Exception:
        a.mute = True
    W, H = (int(x) for x in a.size.split("x"))
    os.environ.setdefault("SDL_VIDEO_WINDOW_POS", "40,40")
    pygame.display.gl_set_attribute(pygame.GL_DEPTH_SIZE, 24)
    pygame.display.set_mode((W, H), pygame.OPENGL | pygame.DOUBLEBUF)
    glViewport(0, 0, W, H)
    glMatrixMode(GL_PROJECTION); perspective(35, W / H, 0.05, 400)
    glMatrixMode(GL_MODELVIEW)
    glEnable(GL_DEPTH_TEST)
    glEnable(GL_BLEND); glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
    glEnable(GL_ALPHA_TEST); glAlphaFunc(GL_GREATER, 0.5)
    glClearColor(0.18, 0.2, 0.24, 1)

    vs, vts, groups, mats = load_obj(objp)
    lo = [min(v[k] for v in vs) for k in range(3)]
    hi = [max(v[k] for v in vs) for k in range(3)]
    center = [(lo[k] + hi[k]) / 2 for k in range(3)]
    ext = [hi[k] - lo[k] for k in range(3)]
    scale = 2.0 / (max(ext) or 1)
    body = build_list(vs, vts, groups, mats, center, scale)

    # the three models share the clean one's centre and scale, so switching
    # shows exactly how the dent / the low-poly body sits on the clean car
    variants, vcount = {"clean": body}, {"clean": len(vs)}
    for vname in ("damaged", "low"):
        rel = car.get(vname)
        vp = os.path.join(root, rel) if rel else None
        if vp and vname == "damaged" and a.rebuilt:
            rp = os.path.splitext(vp)[0] + ".rebuilt.obj"
            vp = rp if os.path.exists(rp) else vp
        if not vp or not os.path.exists(vp) or os.path.normpath(vp) == os.path.normpath(objp):
            variants[vname], vcount[vname] = body, len(vs)       # declared as the clean one
            continue
        v2, vt2, g2, m2 = load_obj(vp)
        for k, m in mats.items():
            if k not in m2 or not m2[k]["tex"]:
                m2[k] = m
        variants[vname] = build_list(v2, vt2, g2, m2, center, scale)
        vcount[vname] = len(v2)
    variant = a.variant
    # the up axis is the smallest extent; long axis is the largest
    up = ext.index(min(ext))
    lng = ext.index(max(ext))
    side = 3 - up - lng
    flip_up = 1.0 if up == 1 else 1.0

    wheels = {}
    for k, w in wheels_of(wcar).items():
        if k in ("transparent", "transparent_tolerance", "transparent_chroma"):
            continue
        p = os.path.join(wroot, (w.get("png", "") if isinstance(w, dict) else w) or "") if w else ""
        if p and os.path.exists(p):
            wheels[k] = gl_texture(p)

    snd = car.get("sounds") or {}
    def wav(n):
        s = snd.get(n)
        return os.path.join(root, s["wav"]) if isinstance(s, dict) and s.get("wav") else None
    idl = {} if a.mute else make_sound(wav("idle"), [1.0])
    eng_src = None
    if not a.mute and np is not None and wav("engine") and os.path.exists(wav("engine")):
        try:
            eng_src = pygame.sndarray.array(pygame.mixer.Sound(wav("engine"))).astype(np.float32)
        except Exception as e:
            print("preview_car: engine wav (%s)" % e)
    eng_fixed = {} if (a.mute or eng_src is not None) else make_sound(wav("engine"), [1.0])
    if not a.mute:
        pygame.mixer.set_num_channels(4)
    ich = pygame.mixer.Channel(0) if (idl and not a.mute) else None
    ech = pygame.mixer.Channel(1) if ((eng_src is not None or eng_fixed) and not a.mute) else None
    if ich:
        ich.play(idl[1.0], loops=-1)          # idle loops the whole run
    if ech and eng_fixed:
        ech.play(eng_fixed[1.0], loops=-1)
    mix_rate = (pygame.mixer.get_init() or (22050,))[0]
    CHUNK = int(mix_rate * 0.04)
    pos = 0.0
    last_p = PITCH_IDLE

    def engine_chunk(p):
        """CHUNK frames read from the loop at a pitch sliding last_p -> p."""
        nonlocal pos, last_p
        n = len(eng_src)
        step = np.linspace(last_p, p, CHUNK, endpoint=False)
        idx = (pos + np.cumsum(step)) % n
        pos = float(idx[-1]); last_p = p
        i0 = idx.astype(np.int64); i1 = (i0 + 1) % n; f = idx - i0
        if eng_src.ndim == 1:
            out = eng_src[i0] * (1 - f) + eng_src[i1] * f
        else:
            out = eng_src[i0] * (1 - f)[:, None] + eng_src[i1] * f[:, None]
        return pygame.sndarray.make_sound(np.ascontiguousarray(np.clip(out, -32768, 32767).astype(np.int16)))

    def mix(idle_vol, pitch):
        if ich:
            ich.set_volume(idle_vol)
        if ech:
            ech.set_volume(1.0 - idle_vol if ich else 1.0)
            if eng_src is not None:
                if not ech.get_busy():
                    ech.play(engine_chunk(pitch))
                while ech.get_queue() is None:
                    ech.queue(engine_chunk(pitch))
                    break

    def axis_vec(i, val):
        v = [0.0, 0.0, 0.0]; v[i] = val; return v

    # wheel layout: the car's own handling.json wheelDisp/wheelSize (engine
    # units, Y-down, = OBJ * bake --scale), else a guess from the bounding box
    disp, wsize, cog = None, None, None
    hd = car.get("handling") or {}
    if hd.get("wheelDisp") and hd.get("wheelSize"):
        disp, wsize, cog = hd["wheelDisp"], hd["wheelSize"], hd.get("cog")
    # handling colBox is engine half-extents. A Driver 1 OBJ (REDBUICK and the
    # rest of the pool) is ~3000/2048 times that box; treating every mesh longer
    # than 50 as already engine-sized left the wheels on a short wheelbase and
    # the body reading huge next to them. The baked LEV is already scaled, which
    # is why the same car is normal in game.
    import preview
    ride0 = a.ride if a.ride is not None else REST_COMP
    hubs_for = None
    col = (hd.get("colBox") if hd else None)
    S = a.bake_scale if a.bake_scale else preview.infer_bake_scale(ext, col)
    if disp:
        rad = wsize / S
        # 1) hub relative to the BODY MODEL ORIGIN, D2 engine space (Y-down),
        #    exactly as cars.c draws them in one frame (same matrix, same pos):
        #      body  (DrawCarObject): model origin = pos + cog
        #      wheel (DrawCarWheels): hub = pos + (17 - disp.x  [wheels 0,1]
        #                                          -17 - disp.x  [wheels 2,3],
        #                                          -wheelSize - disp.y - susCompression + 14,
        #                                          -disp.z)
        #    so hub - body = that vector - cog. (FixCarCos moves disp.z and
        #    cog.z by the same delta, so it cancels here.) susCompression is
        #    the settled rest value -> --ride.
        cg = cog or (0, 0, 0)
        # engine space is Y-DOWN (-Y is up): more compression subtracts from the hub's
        # y, which lifts the wheel toward the body; engine_to_preview then negates Y
        # for a Y-up OBJ, so a compressed wheel rises in the preview as in the game
        def engine_hub(i, d, comp=None):
            return ((17 if (i & 2) == 0 else -17) - d[0] - cg[0],
                    -wsize - d[1] - (ride0 if comp is None else comp) + 14 - cg[1],
                    -d[2] - cg[2])
        # 2) ONE conversion into preview/OBJ space (+Y up): the bake's
        #    --flip-y only negates Y, so (x, y, z) -> (x, -y, z). Ride and
        #    suspension therefore only ever move the hub vertically.
        def engine_to_preview(e):
            # --flip-y art is already Y-down engine space and the draw
            # flips it with the body, so don't negate twice
            return (e[0] / S, (e[1] if a.flip_y else -e[1]) / S, e[2] / S)
        wpos = [engine_to_preview(engine_hub(i, d)) for i, d in enumerate(disp)]

        def hubs_for(comps):
            """Hub positions (preview space, centred) for per-wheel compressions."""
            ws = [engine_to_preview(engine_hub(i, d, c)) for i, (d, c) in enumerate(zip(disp, comps))]
            return [[(q[k] - center[k]) * scale for k in range(3)] for q in ws]
    else:
        rad = ext[1] * 0.3
        wpos = [(sx * ext[0] * 0.45 + center[0], lo[1] + rad, sz * ext[2] * 0.32 + center[2])
                for sx in (1, -1) for sz in (1, -1)]
    rad *= scale
    wpos = [[(p[k] - center[k]) * scale for k in range(3)] for p in wpos]
    tyre_w = rad * 0.55

    # GL height of the tyres' underside (the same up-axis mapping the draw uses):
    # the road is laid so its surface meets it
    def gl_up(p):
        if up == 2:
            return p[2]
        if up == 0:
            return p[0]
        return -p[1] if a.flip_y else p[1]
    ground_gl = min(gl_up(p) for p in wpos) - rad

    scene = scene_on = None
    sc = None
    if not a.no_scene:
        sp = a.scene or os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_scenes", "vegas.glb")
        if os.path.exists(sp):
            try:
                prims = load_glb(sp)
                sc = scene_params(prims, sp, a.scene_scale, a.scene_period, a.scene_lane)
                scene = build_scene(prims)
                scene_on = True
                glFogi(GL_FOG_MODE, GL_LINEAR)
                glFogfv(GL_FOG_COLOR, SKY + (1.0,)); glFogf(GL_FOG_START, 16.0); glFogf(GL_FOG_END, 60.0)
            except Exception as e:
                print("preview_car: road scene skipped (%s)" % e)
        else:
            print("preview_car: no road scene at %s - turntable only" % sp)
    scene_on = bool(scene_on)
    travel = 0.0

    # Body heave (h, engine units, + up) and pitch (p, radians, + nose up) on the
    # engine's spring. Springs and rest height are the engine's; the masses and
    # damping ratio are chosen for a ~1.9 Hz bounce and ~1.6 Hz pitch at ~0.3 damped.
    susp = None
    if hubs_for is not None and up == 1 and not a.no_suspension:
        zi = [-d[2] for d in disp]                       # hub z, engine units, front negative
        k_h = 4 * SUS_SPRING
        k_p = SUS_SPRING * sum(z * z for z in zi)
        m_h = k_h / (2 * math.pi * 1.9) ** 2
        i_p = k_p / (2 * math.pi * 1.6) ** 2
        susp = {"zi": zi, "e2p": scale / S, "k_h": k_h, "k_p": k_p, "m_h": m_h, "i_p": i_p,
                "c_h": 0.3 * 2 * math.sqrt(k_h * m_h), "c_p": 0.3 * 2 * math.sqrt(k_p * i_p),
                "tq": 0.035 * k_p}                       # ~2 degrees of squat at full pull
    sh = shv = sp = spv = 0.0
    thr = 0.0
    last_eng = PITCH_IDLE

    tyre = glGenLists(1); glNewList(tyre, GL_COMPILE)
    N = 10
    glBegin(GL_QUADS)
    for i in range(N):
        a0_, a1_ = 2 * math.pi * i / N, 2 * math.pi * (i + 1) / N
        sh = 0.10 + 0.08 * abs(math.cos((a0_ + a1_) / 2))
        glColor3f(sh, sh, sh)
        for aa, xx in ((a0_, -1), (a1_, -1), (a1_, 0), (a0_, 0)):
            glVertex3f(xx * tyre_w, math.sin(aa) * rad * 0.97, math.cos(aa) * rad * 0.97)
    glEnd()
    for xx in (-1, 0):                       # side walls
        glColor3f(0.05, 0.05, 0.05); glBegin(GL_POLYGON)
        for i in range(N):
            aa = 2 * math.pi * i / N
            glVertex3f(xx * tyre_w - 0.002 * (xx + 0.5), math.sin(aa) * rad * 0.97, math.cos(aa) * rad * 0.97)
        glEnd()
    glEndList()

    def draw_wheel(tex, pos, spin):
        out = 1.0 if pos[0] >= 0 else -1.0
        glPushMatrix(); glTranslatef(*pos)
        glScalef(out, 1, 1)                  # local +X points away from the car
        glRotatef(spin, 1, 0, 0)
        glCallList(tyre)                     # tyre sits inboard of the rim
        glEnable(GL_TEXTURE_2D); glBindTexture(GL_TEXTURE_2D, tex)
        glColor3f(1, 1, 1)
        glBegin(GL_QUADS)
        for u, v in ((0, 0), (1, 0), (1, 1), (0, 1)):
            glTexCoord2f(u, v); glVertex3f(0.004, (v - .5) * 2 * rad, (u - .5) * 2 * rad * out)
        glEnd(); glDisable(GL_TEXTURE_2D); glPopMatrix()

    clock = pygame.time.Clock()
    shots_done = set()
    t0 = time.time(); spin = 0.0; last_title = ""; dt = 1 / 60.0
    order = ("clean", "damaged", "low")
    shot_times = [float(x) for x in a.shot_at.split(",") if x.strip()]
    cam_dist, cam_pitch, cam_yaw, manual, last_yaw = a.cam_dist, a.cam_pitch, 0.0, False, 0.0

    def take_camera():
        nonlocal manual, cam_yaw
        if not manual:
            manual, cam_yaw = True, last_yaw        # start from where the automatic camera is
    while True:
        for e in pygame.event.get():
            if e.type == pygame.QUIT or (e.type == pygame.KEYDOWN and e.key in (pygame.K_ESCAPE, pygame.K_q)):
                pygame.quit(); return 0
            if e.type == pygame.MOUSEMOTION and e.buttons[0]:
                take_camera()
                cam_yaw += e.rel[0] * 0.4
                cam_pitch = max(-3.0, min(85.0, cam_pitch + e.rel[1] * 0.3))
            elif e.type == pygame.MOUSEWHEEL:
                cam_dist = max(1.2, min(12.0, cam_dist * (0.9 ** e.y)))
            elif e.type == pygame.KEYDOWN and e.key in (pygame.K_LEFT, pygame.K_RIGHT, pygame.K_UP, pygame.K_DOWN,
                                                       pygame.K_PAGEUP, pygame.K_PAGEDOWN):
                take_camera()
                if e.key == pygame.K_LEFT:
                    cam_yaw -= 10.0
                elif e.key == pygame.K_RIGHT:
                    cam_yaw += 10.0
                elif e.key == pygame.K_UP:
                    cam_pitch = min(85.0, cam_pitch + 5.0)
                elif e.key == pygame.K_DOWN:
                    cam_pitch = max(-3.0, cam_pitch - 5.0)
                else:
                    cam_dist = max(1.2, min(12.0, cam_dist * (0.9 if e.key == pygame.K_PAGEUP else 1.1)))
            if e.type == pygame.KEYDOWN:
                if e.key == pygame.K_r:
                    manual, cam_dist, cam_pitch = False, a.cam_dist, a.cam_pitch
                elif e.key in (pygame.K_1, pygame.K_KP1):
                    variant = "clean"
                elif e.key in (pygame.K_2, pygame.K_KP2):
                    variant = "damaged"
                elif e.key in (pygame.K_3, pygame.K_KP3):
                    variant = "low"
                elif e.key == pygame.K_TAB:
                    variant = order[(order.index(variant) + 1) % 3]
                elif e.key == pygame.K_g and scene is not None:
                    scene_on = not scene_on
        now = time.time() - t0
        if a.cycle:
            variant = order[int(now // a.cycle) % 3]
        if a.timeout and now > a.timeout:
            break
        if a.stop_file and os.path.exists(a.stop_file):
            break
        phase, speed, pitch, gear = state(now)
        if phase == "idle":
            mix(1.0, PITCH_IDLE); wk = "clean"
        elif phase == "coast":
            mix(max(0.0, min(1.0, 1.0 - (pitch - PITCH_IDLE) / 0.3)), pitch); wk = "damaged"
        else:
            mix(0.0, pitch); wk = "fast" if speed > 0.15 else "clean"
        if variant == "damaged":
            wk = "damaged"                  # the dented body rides on the buckled wheel
        tex = wheels.get(wk) or wheels.get("clean")
        spin -= speed * 60.0
        note = ""
        if vcount[variant] != vcount["clean"]:
            note = " (%d verts vs %d clean: MISALIGNED)" % (vcount[variant], vcount["clean"])
        title = "BAKE preview  %s  |  %s%s  |  %s  |  wheel: %s  |  1/2/3 Tab model, G road, Esc" % (
            key, variant.upper(), note, phase, wk)
        if title != last_title:
            pygame.display.set_caption(title); last_title = title

        pose_h = pose_p = 0.0
        wp, comps = wpos, None
        if susp:
            # throttle ~ how hard the engine note is climbing; lifting at a shift or on the coast dives
            thr += (max(-0.5, min(1.0, (pitch - last_eng) / max(dt, 1e-3) / 0.9)) - thr) * min(1.0, dt * 6.0)
            last_eng = pitch
            bump = speed * 600.0 * (math.sin(now * 7.3) + 0.6 * math.sin(now * 13.1 + 1.0)
                                    + 0.4 * math.sin(now * 23.7 + 2.0)) / 2.0
            g_ = a.susp_gain
            nsub = max(1, int(dt * 240.0 + 0.999)); hh = dt / nsub
            for _ in range(nsub):
                ah = (-susp["k_h"] * sh - susp["c_h"] * shv + bump * g_) / susp["m_h"]
                ap_ = (-susp["k_p"] * sp - susp["c_p"] * spv + thr * susp["tq"] * g_) / susp["i_p"]
                shv += ah * hh; sh += shv * hh
                spv += ap_ * hh; sp += spv * hh
            pose_h, pose_p = sh * susp["e2p"], sp
            # each wheel stays on the road: the body rises, the spring decompresses
            comps = [max(0.0, min(42.0, ride0 - sh + sp * z)) for z in susp["zi"]]
            wp = hubs_for(comps)
        if scene_on:
            glClearColor(SKY[0], SKY[1], SKY[2], 1); glEnable(GL_FOG)
            travel += speed * a.road_speed * dt
        else:
            glClearColor(0.18, 0.2, 0.24, 1); glDisable(GL_FOG)
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        glLoadIdentity()
        glTranslatef(0, -0.05, -cam_dist)
        glRotatef(cam_pitch, 1, 0, 0)
        if manual:
            yaw = cam_yaw
        elif scene_on:
            # the car holds its lane, the camera drifts a little around it
            yaw = a.yaw if a.yaw is not None else 30.0 + 8.0 * math.sin(now * 0.4)
        else:
            yaw = a.yaw if a.yaw is not None else now * 40.0                  # slow yaw, ~9s a turn
        last_yaw = yaw
        glRotatef(yaw, 0, 1, 0)
        if scene_on:
            sS, sL = sc["scale"], sc["period"]
            glPushMatrix()
            glTranslatef(-sc["lane"] * sS, ground_gl - sc["road_y"] * sS, 0)   # lane, and road under the tyres
            glRotatef(90, 0, 1, 0)                                              # the road runs along -Z
            glScalef(sS, sS, sS)
            off = (travel / sS) % sL
            if not a.no_cull:
                glEnable(GL_CULL_FACE); glCullFace(GL_BACK); glFrontFace(GL_CCW)    # see through a wall from behind
            for k in (-1, 0, 1):
                glPushMatrix(); glTranslatef(k * sL - off, 0, 0); glCallList(scene); glPopMatrix()
            glDisable(GL_CULL_FACE)
            glPopMatrix()
        if susp:
            glPushMatrix()
            glTranslatef(0, pose_h, 0)
            glRotatef(math.degrees(pose_p), 1, 0, 0)       # nose up = front (-Z) rises
        # map the model's up axis onto GL +Y
        if up == 2:
            glRotatef(-90, 1, 0, 0)
        elif up == 0:
            glRotatef(90, 0, 0, 1)
        if up == 1 and a.flip_y:
            glScalef(1, -1, 1)                  # engine-space art is Y-down
        glCallList(variants[variant])
        if tex:
            for p in wp:
                draw_wheel(tex, p, spin)
        if susp:
            glPopMatrix()
        if a.shot:
            for st in shot_times:
                if st not in shots_done and now >= st:
                    shots_done.add(st); os.makedirs(a.shot, exist_ok=True)
                    px = glReadPixels(0, 0, W, H, GL_RGB, GL_UNSIGNED_BYTE)
                    img = pygame.image.frombytes(px, (W, H), "RGB", True)
                    pygame.image.save(img, os.path.join(a.shot, "preview_%02ds_%s_%s.png" % (st, variant, phase.replace(" ", ""))))
                    if comps:
                        print("shot %gs %s %s: compression front %.1f/%.1f  rear %.1f/%.1f (rest %.1f)  heave %+.2f  pitch %+.2f deg" % (
                            st, variant, phase, comps[0], comps[2], comps[1], comps[3], ride0, sh, math.degrees(sp)))
        pygame.display.flip()
        dt = min(clock.tick(60) / 1000.0, 0.1)
    pygame.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
