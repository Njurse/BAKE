#!/usr/bin/env python3
"""preview_car.py - rotating in-window preview of a car bank's authoring art.

    python preview_car.py <bank.json | bank dir> [--car 1_BASE2] [--timeout S]
                          [--stop-file PATH] [--mute]

Reads what declare.cars.<car> names in bank.json (the clean OBJ + its MTL
textures, the wheel PNGs, and sounds/engine|idle wav) - NOT the baked LEV, so
it shows the art as authored, not the 16-colour pages.

Loop (repeats):  idle ~2s  ->  rev through 4 gears (clean wheel, then fast
wheel once speed is up)  ->  coast back to idle on the damaged wheel.
The car yaws slowly the whole time.

Exits on Esc / Q / window close, after --timeout seconds, or as soon as
--stop-file exists (rebake.bat touches it once you answer the 0-4 prompt).
Missing pygame / PyOpenGL: prints a note and exits 0, never blocks a bake.
"""
import argparse, json, math, os, sys, time
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
    texcache, lst = {}, glGenLists(1)
    glNewList(lst, GL_COMPILE)
    light = (0.4, 0.8, 0.45)
    for name, tris in groups.items():
        m = mats.get(name, {"kd": (0.7, 0.7, 0.7), "tex": None})
        tex = m["tex"]
        if tex:
            if tex not in texcache:
                texcache[tex] = gl_texture(tex)
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
    ap.add_argument("--bake-scale", type=float, default=265, help="rebake --scale (OBJ->engine units)")
    ap.add_argument("--ride", type=float, default=30, help="rest susCompression in engine units (handling.c seeds 14; bigger = wheels hang lower, body rides higher)")
    ap.add_argument("--yaw", type=float, help="fixed yaw in degrees (no spin) - for checking shots")
    ap.add_argument("--size", default="640x400")
    ap.add_argument("--shot", help="save PNG frames into this dir at 1s,4s,8s,12s")
    ap.add_argument("--flip-y", action="store_true", help="art is Y-down (engine space); default: OBJ is Y-up, as the bake expects with --flip-y")
    a = ap.parse_args()

    root, bank = load_bank(a.bank)
    cars = bank.get("declare", {}).get("cars", {})
    if not cars:
        print("preview_car: no declare.cars in bank.json"); return 0
    key = a.car or next(iter(cars))
    car = cars[key]
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
    glMatrixMode(GL_PROJECTION); gluPerspective(35, W / H, 0.1, 100)
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
    # the up axis is the smallest extent; long axis is the largest
    up = ext.index(min(ext))
    lng = ext.index(max(ext))
    side = 3 - up - lng
    flip_up = 1.0 if up == 1 else 1.0

    wheels = {}
    for k, w in (car.get("wheels") or {}).items():
        p = os.path.join(root, w.get("png", "")) if isinstance(w, dict) else ""
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

    # wheel layout: donor cosmetics wheelDisp/wheelSize (engine units, Y-down,
    # = OBJ * bake --scale), else a guess from the bounding box
    disp, wsize = None, None
    try:
        dd = os.path.join(root, bank.get("donor_dir") or ".")
        model = (bank.get("declare", {}).get("donor") or {}).get("model", 1)
        for fn in ("BETACARS.cosmetics.json", "cosmetics.json"):
            fp = os.path.join(dd, fn)
            if not os.path.exists(fp):
                continue
            for r in json.load(open(fp, encoding="utf-8")).get("records", []):
                f = r.get("fields", {})
                if r.get("model") == model and "wheelDisp" in f:
                    disp, wsize = f["wheelDisp"], f["wheelSize"]; break
            if disp:
                break
    except Exception as e:
        print("preview_car: no cosmetics wheels (%s)" % e)
    S = a.bake_scale
    if disp:
        rad = wsize / S
        # 1) hub in D2 ENGINE body space (-Y up, +Y down), as cars.c
        #    DrawCarWheels builds sWheelPos:
        #      x = +-17 - disp.x      (17 inboard, mirrored)
        #      y = -wheelSize - disp.y - susCompression + 14 ... negated with
        #          the rest of the vector -> hub sits below the origin by
        #          wheelSize + disp.y + susCompression - 14  (+ = down)
        #      z = -disp.z            (front/rear swap vs the raw table)
        #    susCompression is seeded at 14 (handling.c) and settles deeper
        #    under gravity -> --ride (30 fits the Firebird arches).
        def engine_hub(d):
            return (d[0] - math.copysign(17, d[0]),
                    wsize + d[1] + a.ride - 14,
                    -d[2])
        # 2) ONE conversion into preview/OBJ space (+Y up): the bake's
        #    --flip-y only negates Y, so (x, y, z) -> (x, -y, z). Ride and
        #    suspension therefore only ever move the hub vertically.
        def engine_to_preview(e):
            return (e[0] / S, -e[1] / S, e[2] / S)
        wpos = [engine_to_preview(engine_hub(d)) for d in disp]
    else:
        rad = ext[1] * 0.3
        wpos = [(sx * ext[0] * 0.45 + center[0], lo[1] + rad, sz * ext[2] * 0.32 + center[2])
                for sx in (1, -1) for sz in (1, -1)]
    rad *= scale
    wpos = [[(p[k] - center[k]) * scale for k in range(3)] for p in wpos]
    tyre_w = rad * 0.55

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
    t0 = time.time(); spin = 0.0; last_title = ""
    while True:
        for e in pygame.event.get():
            if e.type == pygame.QUIT or (e.type == pygame.KEYDOWN and e.key in (pygame.K_ESCAPE, pygame.K_q)):
                pygame.quit(); return 0
        now = time.time() - t0
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
            mix(0.0, pitch); wk = "fast" if speed > 0.4 else "clean"
        tex = wheels.get(wk) or wheels.get("clean")
        spin -= speed * 25.0
        title = "BAKE preview  %s  |  %s  |  wheel: %s  |  Esc closes" % (key, phase, wk)
        if title != last_title:
            pygame.display.set_caption(title); last_title = title

        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        glLoadIdentity()
        glTranslatef(0, -0.1, -4.2)
        glRotatef(18, 1, 0, 0)
        glRotatef(a.yaw if a.yaw is not None else now * 40.0, 0, 1, 0)          # slow yaw, ~9s a turn
        # map the model's up axis onto GL +Y
        if up == 2:
            glRotatef(-90, 1, 0, 0)
        elif up == 0:
            glRotatef(90, 0, 0, 1)
        if up == 1 and a.flip_y:
            glScalef(1, -1, 1)                  # engine-space art is Y-down
        glCallList(body)
        if tex:
            for p in wpos:
                draw_wheel(tex, p, spin)
        if a.shot:
            for st in (1, 4, 8, 12):
                if st not in shots_done and now >= st:
                    shots_done.add(st); os.makedirs(a.shot, exist_ok=True)
                    px = glReadPixels(0, 0, W, H, GL_RGB, GL_UNSIGNED_BYTE)
                    img = pygame.image.frombytes(px, (W, H), "RGB", True)
                    pygame.image.save(img, os.path.join(a.shot, "preview_%02ds_%s.png" % (st, phase.replace(" ", ""))))
        pygame.display.flip()
        clock.tick(60)
    pygame.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
