#!/usr/bin/env python3
"""driveby.py - watch one D1 car and listen to the beta engine run to the redline.

A live preview of what `add_car_sounds.py` is about to bake: the window shows a
pool car on a turntable view with its wheels turning, three seconds pass at the
beta's idle, then six gears of the Euro-43 engine loop with the wheel speed
following the tacho.

The two percentages are exactly the ones the bake writes into each imported
model's 3-sample bank (`BETA_ENGINE_PITCH` / `BETA_IDLE_PITCH`), so what you
hear is what the bake produces - but with `--engine-pitch` you can hear a
candidate before touching the constant or re-baking:

    python driveby.py                       # MIAMI_0, the bake's current pitch
    python driveby.py --engine-pitch 90     # hear 10% below the raw sample rate
    python driveby.py --car FRISCO_5 --gears 8 --gear-seconds 1.0

The wheels are drawn as discs with spokes rather than as the D1 wheel mesh.
That mesh is a flat billboard sprite (two side quads and four edge quads), so
spinning it flat-shaded reads as a square turning, not as a tyre; the game
shows rotation through the sprite texture, which a Canvas cannot rotate.

Audio is a temp WAV played through winsound (Windows). Without winsound the
window still runs, silently. Space replays, Esc quits.
"""

import argparse
import array
import json
import math
import os
import sys
import tempfile
import time
import wave

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import add_car_sounds          # noqa: E402
import sbk_samples             # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
SOUND_SBK = os.path.join(ROOT, "src_rebuild", "bin", "Release_dev",
                         "EURO43", "SOUND", "SOUND.SBK")

# The static previews' camera: the game's y is DOWN-positive, so screen y
# follows +y and must not be negated.
YAW = math.radians(34.0)
ELEV = math.radians(18.0)
LIGHT = (0.36, -0.86, -0.36)

IDLE_SPIN = 2.0        # rad/s while the car stands still
TOP_SPIN = 12.0        # rad/s at the top of the run


def _project(v, cy, sy, cp, sp):
    x, y, z = v
    cx = x * cy + z * sy
    cz = -x * sy + z * cy
    return cx, y * cp - cz * sp, cz


def _face_normal(verts, face):
    """Outward normal, same operand order as preview.py (cross(v, u))."""
    a, b, c = verts[face[0]], verts[face[1]], verts[face[2]]
    u = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    v = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
    n = (v[1] * u[2] - v[2] * u[1],
         v[2] * u[0] - v[0] * u[2],
         v[0] * u[1] - v[1] * u[0])
    L = math.sqrt(n[0] * n[0] + n[1] * n[1] + n[2] * n[2]) or 1.0
    return (n[0] / L, n[1] / L, n[2] / L)


def _shade(n):
    d = max(0.0, n[0] * LIGHT[0] + n[1] * LIGHT[1] + n[2] * LIGHT[2])
    s = int(38 + 200 * d)
    return "#%02x%02x%02x" % (s, s, min(255, s + 20))


def load_car(pool_path, want):
    """(name, body verts, body faces, per-face normal, [(centre, radius)]).

    The body comes from the same `pool.json` / `*_geom.json` pair preview.py
    reads. The wheel centres and radius come from the placement record
    `wheels1.py --place` wrote, using `render_centres` when it exists.
    """
    d = json.load(open(pool_path))
    items = d["items"]
    if want.isdigit():
        idx = int(want)
        if not 0 <= idx < len(items):
            raise SystemExit("pool has %d items; --car %s is out of range"
                             % (len(items), want))
        item = items[idx]
    else:
        hits = [it for it in items if want.lower() in it["name"].lower()]
        if not hits:
            raise SystemExit("no pool item matches %r (try --list)" % want)
        item = hits[0]

    geom_path = os.path.join(os.path.dirname(pool_path), item["geom"])
    geom = json.load(open(geom_path))
    k = geom["scale"]
    verts = [(x * k, y * k, z * k) for x, y, z in geom["vertices"]]
    faces = [tuple(p["v"]) for p in geom["polys"] if len(p["v"]) in (3, 4)]
    normals = [_face_normal(verts, f) for f in faces]

    wheels = []
    pl_path = os.path.join(os.path.dirname(pool_path), "wheels", "placement.json")
    if os.path.exists(pl_path):
        recs = {r["pool_index"]: r for r in json.load(open(pl_path))["items"]}
        rec = recs.get(item["pool_index"])
        if rec:
            centres = rec.get("render_centres") or rec["centres"]
            radius = rec["radius"] * rec["scale"]
            wheels = [(tuple(c), radius) for c in centres]
    return item["name"], verts, faces, normals, wheels


def make_schedule(idle_seconds, gear_seconds, gears, engine_rate, idle_rate):
    """The drive as time segments. Pitch inside a gear rises exponentially -
    linear sounds like a siren, not an engine - and each shift drops back to a
    slightly higher start than the last, so 0.72 -> 1.35, 0.81 -> 1.35, ..."""
    sched = [{
        "kind": "idle", "gear": 0, "src": "idle",
        "t0": 0.0, "t1": idle_seconds,
        "rate0": idle_rate, "rate1": idle_rate,
    }]
    t = idle_seconds
    for g in range(gears):
        f0 = 0.72 + 0.09 * g
        f1 = 1.42 if g == gears - 1 else 1.35
        sched.append({
            "kind": "gear", "gear": g + 1, "src": "engine",
            "t0": t, "t1": t + gear_seconds,
            "rate0": engine_rate * f0, "rate1": engine_rate * f1,
        })
        t += gear_seconds
    return sched, t


def _segment_at(sched, t):
    if not sched or t < 0:
        return None
    for seg in sched:
        if t < seg["t1"]:
            return seg
    return sched[-1]


def synthesize(sched, sources, out_rate, volume=0.80):
    """Loop the engine/idle VAG at the schedule's rates, into one mono PCM list.

    The source samples stay at their recorded rate; the pitch comes from the
    rate field the bake would store per sample, exactly as the sound engine
    reads it. Rate changes are interpolated per output sample, and each segment
    gets a 12 ms fade so the gear changes click a little rather than crack.
    """
    out = array.array("h")
    phase = {name: 0.0 for name in sources}
    fade_n = max(1, int(0.012 * out_rate))
    for seg in sched:
        src = sources[seg["src"]]
        n = max(1, int(round((seg["t1"] - seg["t0"]) * out_rate)))
        r0, r1 = seg["rate0"], seg["rate1"]
        ratio = (r1 / r0) ** (1.0 / n) if r1 != r0 else 1.0
        rate = r0
        pos = phase[seg["src"]]
        L = len(src)
        for i in range(n):
            j = int(pos)
            frac = pos - j
            a = src[j % L]
            b = src[(j + 1) % L]
            env = 1.0
            if i < fade_n:
                env = i / float(fade_n)
            elif i > n - fade_n:
                env = (n - i) / float(fade_n)
            out.append(int((a + (b - a) * frac) * volume * env))
            pos += rate / out_rate
            while pos >= L:
                pos -= L
            rate *= ratio
        phase[seg["src"]] = pos
    return out


def write_wav(path, pcm, rate):
    if sys.byteorder != "little":
        pcm = array.array("h", pcm)
        pcm.byteswap()
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return path


def load_voice_samples(voices, engine_index, idle_index):
    if max(engine_index, idle_index) >= len(voices["entries"]):
        raise SystemExit("bank has %d samples; no sample %d"
                         % (len(voices["entries"]),
                            max(engine_index, idle_index)))
    samples = {}
    for name, idx in (("engine", engine_index), ("idle", idle_index)):
        a, length, _loop, _rate = voices["entries"][idx]
        samples[name] = sbk_samples.decode_vag(voices["vag"][a:a + length])
    return samples


class View:
    """The window: one fixed 3/4 camera, a painter-sorted body, wheel discs."""

    HUD = 78

    def __init__(self, root, name, verts, faces, normals, wheels,
                 sched, total, wav_path, args, base_rate):
        import tkinter as tk

        self.root = root
        self.name = name
        self.verts = verts
        self.faces = faces
        self.normals = normals
        self.wheels = wheels
        self.sched = sched
        self.total = total
        self.wav_path = wav_path
        self.args = args
        self.base_rate = base_rate

        self.w = args.width
        self.h = args.height
        self.cy, self.sy = math.cos(math.radians(args.yaw)), math.sin(math.radians(args.yaw))
        self.cp, self.sp = math.cos(math.radians(args.elev)), math.sin(math.radians(args.elev))
        self.ox = 0.0
        self.oy = 0.0
        self.zoom = 1.0
        self.angle = 0.0
        self.last_el = 0.0
        self.t0 = time.perf_counter()

        self.canvas = tk.Canvas(root, width=self.w, height=self.h,
                                bg="#0d1117", highlightthickness=0)
        self.canvas.pack()
        self._frame_camera()

        root.bind("<space>", self.replay)
        root.bind("<Escape>", self.quit)
        root.bind("q", self.quit)
        root.protocol("WM_DELETE_WINDOW", self.quit)

        self._play()
        self.root.after(0, self.tick)

    # -- camera ---------------------------------------------------------
    def _frame_camera(self):
        """Fit the car (and its wheel discs) once; the pose never changes."""
        pts = [_project(v, self.cy, self.sy, self.cp, self.sp) for v in self.verts]
        for centre, radius in self.wheels:
            for i in range(24):
                a = 2.0 * math.pi * i / 24.0
                p = (centre[0], centre[1] + radius * math.cos(a),
                     centre[2] + radius * math.sin(a))
                pts.append(_project(p, self.cy, self.sy, self.cp, self.sp))
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        span_x = max(1e-6, max(xs) - min(xs))
        span_y = max(1e-6, max(ys) - min(ys))
        pad = 26.0
        self.zoom = min((self.w - 2 * pad) / span_x,
                        (self.h - self.HUD - 2 * pad) / span_y)
        self.ox = self.w / 2.0 - (min(xs) + max(xs)) / 2.0 * self.zoom
        self.oy = (self.h - self.HUD) / 2.0 - (min(ys) + max(ys)) / 2.0 * self.zoom

    def _screen(self, v):
        px, py, pz = _project(v, self.cy, self.sy, self.cp, self.sp)
        return self.ox + px * self.zoom, self.oy + py * self.zoom, pz

    # -- audio ----------------------------------------------------------
    def _play(self):
        try:
            import winsound
        except ImportError:
            return
        winsound.PlaySound(self.wav_path,
                           winsound.SND_FILENAME | winsound.SND_ASYNC
                           | winsound.SND_NODEFAULT)

    def _stop(self):
        try:
            import winsound
        except ImportError:
            return
        winsound.PlaySound(None, winsound.SND_PURGE)

    def replay(self, _event=None):
        self._stop()
        self.angle = 0.0
        self.last_el = 0.0
        self.t0 = time.perf_counter()
        self._play()

    def quit(self, _event=None):
        self._stop()
        self.root.destroy()

    # -- drawing --------------------------------------------------------
    def _wheel(self, centre, radius, spin):
        """Tyre, rim, hub and five spokes, all on the wheel's own 3D plane."""
        n = 20

        def ring(r):
            out = []
            for i in range(n):
                a = 2.0 * math.pi * i / n
                x, y, z = self._screen((centre[0],
                                        centre[1] + r * math.cos(a),
                                        centre[2] + r * math.sin(a)))
                out.extend((x, y))
            return out

        self.canvas.create_polygon(ring(radius), fill="#14171b",
                                   outline="#14171b")
        self.canvas.create_polygon(ring(radius * 0.62), fill="#6a7078",
                                   outline="#4a5058")
        self.canvas.create_polygon(ring(radius * 0.16), fill="#22262b",
                                   outline="#22262b")
        width = max(2, int(radius * self.zoom * 0.15))
        for i in range(5):
            a = spin + 2.0 * math.pi * i / 5.0
            p0 = self._screen((centre[0],
                               centre[1] + radius * 0.16 * math.cos(a),
                               centre[2] + radius * 0.16 * math.sin(a)))
            p1 = self._screen((centre[0],
                               centre[1] + radius * 0.60 * math.cos(a),
                               centre[2] + radius * 0.60 * math.sin(a)))
            self.canvas.create_line(p0[0], p0[1], p1[0], p1[1],
                                    fill="#b9bec6", width=width)

    def _hud(self, t, seg):
        c = self.canvas
        if t < 0:
            state = "READY"
        elif seg["kind"] == "idle":
            state = "IDLE"
        elif t >= self.total:
            state = "DONE  -  SPACE replays, ESC quits"
        else:
            state = "GEAR %d / %d" % (seg["gear"], self.args.gears)
        c.create_text(16, 14, anchor="nw", fill="#c9d1d9",
                      font=("Consolas", 12, "bold"), text=state)
        detail = "%s   engine %.0f%%   idle %.0f%%" % (
            self.name, self.args.engine_pitch, self.args.idle_pitch)
        c.create_text(self.w - 16, 14, anchor="ne", fill="#6e7681",
                      font=("Consolas", 10), text=detail)

        # tacho: the engine's rate relative to the sample's own rate
        base = self.base_rate
        rate = 0.0
        if seg and seg["kind"] == "gear" and t < self.total:
            u = max(0.0, min(1.0, (t - seg["t0"]) / (seg["t1"] - seg["t0"])))
            rate = seg["rate0"] * (seg["rate1"] / seg["rate0"]) ** u
        frac = max(0.0, min(1.0, (rate / base - 0.5) / 1.0)) if base else 0.0
        y = self.h - 44
        c.create_rectangle(16, y, self.w - 16, y + 14, outline="#30363d",
                           fill="#161b22")
        if frac > 0:
            c.create_rectangle(17, y + 1, 17 + (self.w - 34) * frac, y + 13,
                               outline="", fill="#e3b341")
        c.create_text(self.w // 2, self.h - 18, fill="#8b949e",
                      font=("Consolas", 10),
                      text="t = %.1f / %.1f s" % (min(t, self.total), self.total))

    def tick(self):
        now = time.perf_counter()
        t = now - self.t0
        dt = min(0.10, max(0.0, t - self.last_el))
        self.last_el = t
        if t < self.total:
            speed = IDLE_SPIN if t < self.args.idle_seconds else (
                IDLE_SPIN + (TOP_SPIN - IDLE_SPIN)
                * min(1.0, (t - self.args.idle_seconds)
                      / max(1e-6, self.total - self.args.idle_seconds)))
            self.angle = (self.angle + speed * dt) % (2.0 * math.pi)

        seg = _segment_at(self.sched, t)
        self.canvas.delete("all")

        draw = []
        for face, normal in zip(self.faces, self.normals):
            pts = [self._screen(self.verts[i]) for i in face]
            depth = sum(p[2] for p in pts) / float(len(pts))
            coords = []
            for x, y, _z in pts:
                coords.extend((x, y))
            draw.append((depth, "poly", coords, _shade(normal)))
        for centre, radius in self.wheels:
            _x, _y, depth = self._screen(centre)
            draw.append((depth, "wheel", centre, radius))
        draw.sort(key=lambda d: -d[0])          # far to near

        for _depth, kind, a, b in draw:
            if kind == "poly":
                self.canvas.create_polygon(a, fill=b, outline=b)
            else:
                self._wheel(a, b, self.angle)

        self._hud(t, seg)
        self.root.after(30, self.tick)


def selftest(args, pcm, total):
    peak = max((abs(v) for v in pcm), default=0)
    print("driveby: car %s, %.2f s, %d samples @ %d Hz, peak %d"
          % (args.car, total, len(pcm), args.rate, peak))
    print("         engine %d%% of raw, idle %d%% of raw, %d gears x %.2f s"
          % (args.engine_pitch, args.idle_pitch, args.gears, args.gear_seconds))
    return 0 if len(pcm) > 0 and peak > 0 else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pool", default=os.path.join(HERE, "out", "pool.json"))
    ap.add_argument("--car", default="MIAMI_0",
                    help="pool index or a substring of the item name")
    ap.add_argument("--list", action="store_true", help="list the pool items and exit")
    ap.add_argument("--sound", default=SOUND_SBK,
                    help="EURO43/SOUND/SOUND.SBK (or a retail VOICES.BLK)")
    ap.add_argument("--engine-pitch", type=int,
                    default=add_car_sounds.BETA_ENGINE_PITCH,
                    help="engine loop rate, as a percent of the sample's own "
                         "rate (default: BETA_ENGINE_PITCH = %d)"
                         % add_car_sounds.BETA_ENGINE_PITCH)
    ap.add_argument("--idle-pitch", type=int,
                    default=add_car_sounds.BETA_IDLE_PITCH,
                    help="idle loop rate, as a percent of the sample's own "
                         "rate (default: BETA_IDLE_PITCH = %d)"
                         % add_car_sounds.BETA_IDLE_PITCH)
    ap.add_argument("--idle-seconds", type=float, default=3.0)
    ap.add_argument("--gears", type=int, default=6)
    ap.add_argument("--gear-seconds", type=float, default=1.4)
    ap.add_argument("--rate", type=int, default=44100, help="output sample rate")
    ap.add_argument("--width", type=int, default=760)
    ap.add_argument("--height", type=int, default=560)
    ap.add_argument("--yaw", type=float, default=math.degrees(YAW))
    ap.add_argument("--elev", type=float, default=math.degrees(ELEV))
    ap.add_argument("--selftest", action="store_true",
                    help="build the car and the soundtrack, print the result, no window")
    args = ap.parse_args(argv)

    if args.list:
        d = json.load(open(args.pool))
        for i, it in enumerate(d["items"]):
            print("%2d  %s" % (i, it["name"]))
        return 0

    name, verts, faces, normals, wheels = load_car(args.pool, args.car)
    if not wheels:
        print("  (no wheel placement for %s - drawing the body alone)" % name)

    voices = add_car_sounds.read_d1_voices(args.sound)
    if voices is None:
        raise SystemExit("cannot read %s as a D1 voice bank" % args.sound)
    if voices.get("kind") == "sbk":
        engine_index = add_car_sounds.SOUND_SBK_ENGINE
        idle_index = add_car_sounds.SOUND_SBK_IDLE
    else:
        engine_index = add_car_sounds.D1_ENGINE_SAMPLE[0]
        idle_index = add_car_sounds.D1_ENGINE_SAMPLE[1]
    samples = load_voice_samples(voices, engine_index, idle_index)

    raw_rate = voices["entries"][engine_index][3]
    engine_rate = raw_rate * args.engine_pitch // 100
    idle_rate = voices["entries"][idle_index][3] * args.idle_pitch // 100
    sched, total = make_schedule(args.idle_seconds, args.gear_seconds,
                                 args.gears, engine_rate, idle_rate)

    t_synth = time.perf_counter()
    pcm = synthesize(sched, samples, args.rate)
    synth_s = time.perf_counter() - t_synth
    print("driveby: %s  engine %d @ %.0f Hz (%d%%), idle %d @ %.0f Hz (%d%%)"
          % (name, engine_index, engine_rate, args.engine_pitch,
             idle_index, idle_rate, args.idle_pitch))
    print("         %.1f s idle + %d x %.1f s gears = %.1f s  (synth %.2f s)"
          % (args.idle_seconds, args.gears, args.gear_seconds, total, synth_s))

    if args.selftest:
        return selftest(args, pcm, total)

    fd, wav_path = tempfile.mkstemp(prefix="driveby_", suffix=".wav")
    os.close(fd)
    write_wav(wav_path, pcm, args.rate)

    import tkinter as tk
    root = tk.Tk()
    root.title("driveby - %s" % name)
    root.configure(bg="#0d1117")
    root.resizable(False, False)
    View(root, name, verts, faces, normals, wheels,
         sched, total, wav_path, args, engine_rate)
    try:
        root.mainloop()
    finally:
        try:
            os.unlink(wav_path)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
