"""clutmap.py - which OBJ materials use which texture regions / CLUT rows, per model variant.

Regions come from the bake's own UV islands (clean + damaged + low OBJ, u*255,
(1-v)*255) and coverage raster, exactly as carbank_page.plan_islands sees them.
Variants, as the engine draws them:
  clean    the HD model, its own polys + UVs
  low      the LOD model, its own polys + UVs
  damaged  the damaged block is the clean POLYS with dented vertices
           (bake.build_damaged_record), so the damaged OBJ's own UVs are never
           drawn; they still join the bake's planning. Damage TEXTURE is the
           dent shift: a dented HD poly samples u + 64*n (denting.c), read
           through its clean CLUT row - "dent" below.
  wheels   handling.json wheels.clean / damaged / fast sprites (own CLUT rows).
Ids whose polys (or dent art) share texels are one palette group, as the
planner does. Colour counts are RGB555-snapped (fourbpp.snap555).

usage: py -3 clutmap.py <car_dir> [--bank DIR] [--variant LIST] [--overlay]
                        [--fix] [--use-fixed] [--check-bake]
  --variant     comma list of clean,low,damaged,dent,wheels (default all) for the report/check
  --overlay     write textures/clutmap_<set>.png with each id's area tinted
  --fix         quantize every palette group (incl. dent art) over 16 colours to
                <=16 (OKLab k-means; keeps the transparent key, protect and
                paint_body colours) -> PAGE_<n>.fixed.png + textures/clutfix_<set>_compare.png;
                wheel sprites over 16 -> <sprite>.fixed.png. A source already named
                .fixed.png is rewritten after a one-time copy to <name>.prefix.png.
  --use-fixed   report on PAGE_<n>.fixed.png where one exists
  --check-bake  decode build/pages/PAGE_<n>.bin through each poly's baked CLUT row
                (clean, low, dent +64/+128) and diff against the source page;
                writes textures/bakecheck_<set>.png (source | decoded clean+dent)
"""
import argparse, glob, json, os, re, sys, warnings
warnings.filterwarnings('ignore', category=DeprecationWarning)
import numpy as np
from PIL import Image
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, "..", "common")]
from fourbpp import snap555
import fourbpp, pages1, carbank_page, obj2geom

W = H = 256
VARIANTS = ("clean", "damaged", "low")


def load_variants(car, h):
    """{variant: [(set, tex id, uv pts, material)]} - poly order kept."""
    out = {}
    for k in VARIANTS:
        if not isinstance(h.get(k), str): continue
        path = os.path.join(car, h[k])
        _v, uvs, faces, names = obj2geom.parse_obj(path, with_materials=True)
        polys = out.setdefault(k, [])
        for (_vi, ti), name in zip(faces, names):
            try: page = obj2geom.material_to_page(name, path)
            except ValueError: page = None
            pts = [(int(round(uvs[t][0] * 255)) & 0xFF, int(round((1.0 - uvs[t][1]) * 255)) & 0xFF)
                   for t in ti if 0 <= t < len(uvs)]
            polys.append((page[0], page[1] & 0xFF, pts, name) if page and len(pts) >= 3 else (None, None, pts, name))
    return out


def islands_of(V, st, variants=VARIANTS):
    isl = {}
    for k in variants:
        for s_, tid, pts, _n in V.get(k, []):
            if s_ == st: isl.setdefault(tid, []).append(pts)
    return isl


def cover(lists, shift=0):
    m = bytearray(W * H)
    for uvs in lists:
        if shift and max(p[0] for p in uvs) + shift >= W: continue
        carbank_page._cover_poly(m, W, H, [(p[0] + shift, p[1]) for p in uvs] if shift else uvs)
    return np.frombuffer(bytes(m), np.uint8).astype(bool)


def tobool(b):
    return np.frombuffer(bytes(b), np.uint8).astype(bool)


def snap_arr(rgb):
    """numpy version of fourbpp.snap555 over an (N,3) uint8 array."""
    s = (np.minimum(31, (rgb.astype(np.int32) + 4) >> 3) << 3)
    bump = (s.sum(1) == 0) & (rgb.astype(np.int32).sum(1) != 0)
    s[bump] = 8
    return s.astype(np.uint8)

def _lin(c):
    c = c / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)

def oklab(rgb):
    l = _lin(rgb.astype(np.float64)) @ np.array([[0.4122214708, 0.2119034982, 0.0883024619],
                                                  [0.5363325363, 0.6806995451, 0.2817188376],
                                                  [0.0514459929, 0.1073969566, 0.6299787005]])
    l = np.cbrt(l)
    return l @ np.array([[0.2104542553, 1.9779984951, 0.0259040371],
                         [0.7936177850, -2.4285922050, 0.7827717662],
                         [-0.0040720468, 0.4505937099, -0.8086757660]])

def oklab_to_rgb(lab):
    l = lab @ np.array([[1.0, 1.0, 1.0],
                        [0.3963377774, -0.1055613458, -0.0894841775],
                        [0.2158037573, -0.0638541728, -1.2914855480]])
    l = l ** 3
    c = l @ np.array([[4.0767416621, -1.2684380046, -0.0041960863],
                      [-3.3077115913, 2.6097574011, -0.7034186147],
                      [0.2309699292, -0.3413193965, 1.7076147010]])
    c = np.clip(c, 0, 1)
    c = np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)
    return np.clip(np.round(c * 255), 0, 255).astype(np.uint8)

def cielab(rgb):
    xyz = _lin(rgb.astype(np.float64)) @ np.array([[0.4124, 0.2126, 0.0193],
                                                   [0.3576, 0.7152, 0.1192],
                                                   [0.1805, 0.0722, 0.9505]])
    xyz /= np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[:, 1] - 16, 500 * (f[:, 0] - f[:, 1]), 200 * (f[:, 1] - f[:, 2])], 1)

def kmeans(pts, w, k, iters=40):
    """Weighted k-means, deterministic k-means++ (farthest-weighted) seeding."""
    k = min(k, len(pts))
    c = [pts[np.argmax(w)]]
    for _ in range(1, k):
        d = np.min(((pts[:, None] - np.array(c)[None]) ** 2).sum(2), 1) * w
        c.append(pts[np.argmax(d)])
    c = np.array(c)
    for _ in range(iters):
        a = np.argmin(((pts[:, None] - c[None]) ** 2).sum(2), 1)
        n = np.array([np.average(pts[a == j], 0, w[a == j]) if (a == j).any() else c[j] for j in range(k)])
        if np.allclose(n, c): break
        c = n
    return c

def quantize(rgb, reserved, key, budget=16):
    """rgb (N,3) region texels -> (N,3) new texels using <=budget RGB555 colours.
    `reserved` colours present stay exact; texels equal to `key` are untouched."""
    out = snap_arr(rgb)
    keyed = np.all(rgb == key, 1) if key is not None else np.zeros(len(rgb), bool)
    fixed = keyed.copy()
    pal = []
    for r in reserved:
        hit = np.all(rgb == r, 1) & ~keyed
        if hit.any():
            out[hit] = r; fixed |= hit; pal.append(tuple(int(v) for v in r))
    free = budget - len(pal) - (1 if keyed.any() else 0)
    rest = ~fixed
    if rest.any() and free > 0:
        u, inv, cnt = np.unique(out[rest], axis=0, return_inverse=True, return_counts=True)
        inv = inv.reshape(-1)
        if len(u) > free:
            lab = oklab(u)
            cen = snap_arr(oklab_to_rgb(kmeans(lab, np.sqrt(cnt.astype(np.float64)), free)))
            keep = set(pal) | ({tuple(snap555(key))} if key is not None else set())
            cand = np.unique(np.array([c for c in map(tuple, cen) if c not in keep] or [tuple(u[0])], np.uint8), axis=0)
            full = np.array(pal + [tuple(c) for c in cand], np.uint8) if pal else cand
            m = np.argmin(((lab[:, None] - oklab(full)[None]) ** 2).sum(2), 1)
            out[rest] = full[m][inv]
    out[keyed] = rgb[keyed]
    return out

def ncols(arr, m):
    return len(np.unique(snap_arr(arr[m]), axis=0)) if m.any() else 0


def hexc(s_):
    return tuple(int(s_[i:i + 2], 16) for i in (0, 2, 4))


def page_plan(src, isl):
    """(palette groups [(members, cover)], dent shadows, shift) as the planner makes them."""
    pix = [tuple(p) for p in src.tolist()]
    pc = carbank_page.page_coverage(W, H, isl)
    shadows, shift = carbank_page.dent_shadows(pix, W, H, isl, pc)
    return [(m, tobool(c)) for m, c in carbank_page._linked_sets(W, H, isl, shadows)], shadows, shift


def save_fixed(img0, png, new_img):
    base, ext = os.path.splitext(png)
    out = (base if base.endswith(".fixed") else base + ".fixed") + ext
    if os.path.normcase(out) == os.path.normcase(png):
        bak = base[:-len(".fixed")] + ".prefix" + ext
        if not os.path.exists(bak): img0.save(bak); print(f"source kept as {bak}")
    new_img.save(out); print(f"fixed -> {out}")
    return out


def fix_page(car, st, png, tex, isl):
    img0 = Image.open(png)
    alpha = img0.convert("RGBA").getchannel("A") if "A" in img0.getbands() or (img0.mode == "P" and "transparency" in img0.info) else None
    src = np.array(img0.convert("RGB")).reshape(-1, 3)
    key = hexc(tex["transparent"]) if isinstance(tex.get("transparent"), str) else None
    reserved = [hexc(c) for c in (tex.get("protect") or []) + (tex.get("paint_body") or [])]
    groups, shadows, shift = page_plan(src, isl)
    own = {t: cover(l) for t, l in isl.items()}
    new = src.copy()
    print(f"\n== --fix page set {st}  {os.path.relpath(png, car)}  key={tex.get('transparent')} "
          f"reserved={['%02X%02X%02X' % c for c in reserved]}  dent shift={shift}")
    print(f"{'palette group (ids)':40} {'before':>6} {'after':>5} {'meandE':>7} {'maxdE':>6}")
    changed = False
    for members, gm in groups:
        if ncols(src, gm) <= 16: continue
        new[gm] = quantize(src[gm], reserved, key)
        changed = True
        dE = np.sqrt(((cielab(src[gm]) - cielab(new[gm])) ** 2).sum(1))
        print(f"{'+'.join('id%d' % t for t in members)[:40]:40} {ncols(src, gm):6} {ncols(new, gm):5} {dE.mean():7.2f} {dE.max():6.1f}")
        for t in members:
            for lab, m in (("", own[t]), (" dent", tobool(shadows[t]) if t in shadows else None)):
                if m is None or not m.any(): continue
                d = np.sqrt(((cielab(src[m]) - cielab(new[m])) ** 2).sum(1))
                print(f"  id{t}{lab:33} {ncols(src, m):6} {ncols(new, m):5} {d.mean():7.2f} {d.max():6.1f}")
    if not changed:
        print("  every palette group already <=16 snapped colours - nothing written"); return
    out_img = Image.fromarray(new.reshape(H, W, 3), "RGB")
    if alpha is not None: out_img.putalpha(alpha)
    save_fixed(img0, png, out_img)
    cmp_ = Image.new("RGB", (W * 4 + 8, H * 2), (40, 40, 40))
    cmp_.paste(Image.fromarray(src.reshape(H, W, 3)).resize((W * 2, H * 2), Image.NEAREST), (0, 0))
    cmp_.paste(out_img.convert("RGB").resize((W * 2, H * 2), Image.NEAREST), (W * 2 + 8, 0))
    cp = os.path.join(car, "textures", f"clutfix_{st}_compare.png"); cmp_.save(cp)
    print(f"compare (before | after) -> {cp}")


def wheels_report(car, h, fix):
    print("\n== wheels (each sprite gets its own CLUT row; >16 is quantized by the bake)")
    # wheels live under textures now; a top-level block is the older spelling
    for var, w in sorted((((h.get("textures") or {}).get("wheels")) or h.get("wheels") or {}).items()):
        if not isinstance(w, dict) or "png" not in w: continue
        png = os.path.normpath(os.path.join(car, w["png"]))
        if not os.path.exists(png): print(f"  {var:8} {w['png']}  (missing)"); continue
        img0 = Image.open(png); rgba = np.array(img0.convert("RGBA")).reshape(-1, 4)
        rgb = rgba[:, :3]
        opaque = rgba[:, 3] > 0
        if opaque.all(): opaque = np.any(snap_arr(rgb) != snap_arr(rgb[:1]), 1)   # keyed: corner = backdrop
        n = ncols(rgb, opaque)
        print(f"  {var:8} set {w.get('set')}  {os.path.relpath(png, car):40} {n:3} colours{'  <-- >16' if n > 16 else ''}")
        if fix and n > 16:
            new = rgba.copy(); new[opaque, :3] = quantize(rgb[opaque], [], None)
            save_fixed(img0, png, Image.fromarray(new.reshape(img0.size[1], img0.size[0], 4), "RGBA"))


def check_bake(car, h, V, bank, sets, want):
    from carbank_bake import _id_at
    reps = glob.glob(os.path.join(bank, "build", "*.report.json"))
    if not reps: print("no build/*.report.json - nothing to check"); return
    man = json.load(open(reps[0].replace(".report.json", ".json")))
    pages = {p["set"]: p for p in man.get("pages") or []}
    for st in sets:
        binp = os.path.join(bank, "build", "pages", "PAGE_%d.bin" % st)
        tex = h.get("textures", {}).get(str(st), {})
        if not os.path.exists(binp) or "png" not in tex: continue
        blob = open(binp, "rb").read()
        rows, raw = pages1.read_page(blob, 0, len(blob))
        idx = np.frombuffer(fourbpp.decode_indices(raw), np.uint8)
        rgb = [np.array(fourbpp.clut_rgb(r), int) for r in rows]
        zero = [np.array([r[i * 2] | r[i * 2 + 1] << 8 for i in range(16)]) == 0 for r in rows]
        rects = pages.get(st, {}).get("clut_rects") or [{"id": 0, "rect": [0, 0, 256, 256], "src": 0}]
        src = np.array(Image.open(os.path.normpath(os.path.join(car, tex["png"]))).convert("RGB")).reshape(-1, 3)
        snap = snap_arr(src).astype(int)
        key = hexc(tex["transparent"]) if isinstance(tex.get("transparent"), str) else None
        iskey = np.all(src == key, 1) if key else np.zeros(len(src), bool)
        isl = islands_of(V, st)
        _g, _sh, shift = page_plan(src, isl)
        pc = tobool(carbank_page.page_coverage(W, H, isl))
        art = ~pc & np.any(snap != snap[0], 1)
        for r in rects:                      # a composited wheel replaced the source texels there
            if r.get("kind") == "wheel":
                x0, y0, x1, y1 = r["rect"]; wm = np.zeros((H, W), bool); wm[y0:y1, x0:x1] = True; art &= ~wm.reshape(-1)
        dec = snap // 3
        print(f"\n== --check-bake page set {st}  {os.path.relpath(binp, bank)}  ({len(rows)} rows; planner dent shift {shift})")
        print(f"{'variant':10} {'id':>4} {'texels':>7} {'bad>24':>7} {'maxerr':>6}  rows")
        jobs = [(k, 0) for k in ("clean", "low") if k in want and k in V]
        if "damaged" in want and "damaged" in V:
            print("  damaged: draws the clean polys + UVs with dented vertices (same texels as clean)")
        if "dent" in want and "clean" in V: jobs += [("dent+64", 64), ("dent+128", 128)]
        for lab, sh in jobs:
            stats = {}
            for s_, tid, pts, _n in V["clean" if sh else lab]:
                if s_ != st: continue
                if sh and max(p[0] for p in pts) + sh >= W: continue
                rid = _id_at({"uv": pts, "id": tid}, rects)
                c = np.flatnonzero(cover([pts], sh))
                if sh: c = c[art[c]]          # only damage art, not another panel
                if not len(c): continue
                got = rgb[rid][idx[c]]; tr = zero[rid][idx[c]]
                err = np.abs(got - snap[c]).max(1)
                err[tr & iskey[c]] = 0; err[tr & ~iskey[c]] = 255; err[~tr & iskey[c]] = 255
                if lab == "clean" or sh == shift: dec[c] = np.where(tr[:, None], (255, 0, 255), got)
                e = stats.setdefault(tid, [0, 0, 0, set()]); e[0] += len(c); e[1] += int((err > 24).sum()); e[2] = max(e[2], int(err.max())); e[3].add(rid)
            for tid, (n, bad, mx, ids) in sorted(stats.items()):
                print(f"{lab:10} {tid:4} {n:7} {bad:7} {mx:6}  {sorted(ids)}{('  <-- wrong colours' if not sh or sh == shift else '  (shift not planned: engine-side)') if bad else ''}")
        im = Image.new("RGB", (W * 4 + 8, H * 2), (40, 40, 40))
        im.paste(Image.fromarray(snap.reshape(H, W, 3).astype(np.uint8)).resize((W * 2, H * 2), Image.NEAREST), (0, 0))
        im.paste(Image.fromarray(dec.reshape(H, W, 3).astype(np.uint8)).resize((W * 2, H * 2), Image.NEAREST), (W * 2 + 8, 0))
        out = os.path.join(car, "textures", f"bakecheck_{st}.png"); im.save(out); print(f"compare (source | decoded clean + dent) -> {out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("car_dir"); ap.add_argument("--bank"); ap.add_argument("--overlay", action="store_true")
    ap.add_argument("--fix", action="store_true"); ap.add_argument("--use-fixed", action="store_true")
    ap.add_argument("--check-bake", action="store_true")
    ap.add_argument("--variant", default="clean,low,damaged,dent,wheels")
    a = ap.parse_args()
    want = set(a.variant.split(","))
    car = os.path.abspath(a.car_dir)
    h = json.load(open(os.path.join(car, "handling.json")))
    bank = a.bank or os.path.dirname(os.path.dirname(car))
    V = load_variants(car, h)
    sets = sorted({p[0] for k in V for p in V[k] if p[0] is not None})
    if a.check_bake:
        check_bake(car, h, V, bank, sets, want); return
    reps = glob.glob(os.path.join(bank, "build", "*.report.json"))
    warns = json.load(open(reps[0])).get("bake_warnings", []) if reps else []
    moves = {}
    for w in warns:
        m = re.match(r"page set (\d+): texture move .*?'tex_id': (\d+).*?-> .*?'tex_id': (\d+), 'clut_row': (\d+)", w)
        if m: moves.setdefault((int(m[1]), int(m[2])), set()).add(int(m[4]))
    print("polys per variant: " + ", ".join(f"{k} {len(V[k])}" for k in VARIANTS if k in V))
    if "clean" in V and "damaged" in V:
        c, d = V["clean"], V["damaged"]
        same = sum(1 for x, y in zip(c, d) if x[0] == y[0] and x[1] == y[1] and x[2] == y[2])
        print(f"damaged OBJ vs clean: {len(d)} vs {len(c)} polys{'  <-- the damaged block reuses the clean polys; counts must match' if len(d) != len(c) else ''}; "
              f"{same} with the same page/id/UVs (the rest are planned but never drawn)")
    for st in sets:
        tex = h.get("textures", {}).get(str(st), {})
        png = os.path.normpath(os.path.join(car, tex["png"])) if "png" in tex else None
        if png and a.use_fixed and not a.fix:
            fx = os.path.splitext(png)[0] + ".fixed" + os.path.splitext(png)[1]
            if os.path.exists(fx): png = fx
        img = Image.open(png).convert("RGB") if png and os.path.exists(png) else None
        isl = islands_of(V, st)
        if a.fix:
            if img: fix_page(car, st, png, tex, isl)
            continue
        arr = np.array(img).reshape(-1, 3) if img else None
        per = {k: {t: cover(l) for t, l in islands_of(V, st, (k,)).items()} for k in VARIANTS if k in V}
        drawn = {t: (per.get("clean", {}).get(t, np.zeros(W * H, bool)) | per.get("low", {}).get(t, np.zeros(W * H, bool))) for t in isl}
        anydrawn = np.zeros(W * H, bool)
        for m in drawn.values(): anydrawn |= m
        print(f"\n== page set {st}  {os.path.relpath(png, car) if png else '(no png in handling.json)'}")
        print(f"{'id':>4} {'clean':>11} {'low':>11} {'damaged':>11} {'colours':>7}  clut rows (last bake)  [polys/texels]")
        for t in sorted(isl):
            cells = []
            for k in VARIANTS:
                n = sum(1 for p in V.get(k, []) if p[0] == st and p[1] == t)
                cells.append(f"{n}/{int(per.get(k, {}).get(t, np.zeros(1, bool)).sum())}" if k in V else "-")
            cols = ncols(arr, drawn[t] if drawn[t].any() else per.get("damaged", {}).get(t, drawn[t])) if img else 0
            flags = []
            if cols > 16: flags.append("<-- >16")
            dm = per.get("damaged", {}).get(t)
            if dm is not None and dm.any():
                if not drawn[t].any(): flags.append("damaged-only id: never drawn")
                elif (dm & ~anydrawn).any(): flags.append(f"{int((dm & ~anydrawn).sum())} damaged-only texels (never drawn)")
            print(f"{t:4} {cells[0]:>11} {cells[2]:>11} {cells[1]:>11} {cols:7}  {sorted({t} | moves.get((st, t), set()))}  {'; '.join(flags)}")
        if img:
            groups, shadows, shift = page_plan(arr, isl)
            if "dent" in want:
                print(f"dent art (u+64 / u+128 from clean+low polys, on texels no poly covers): planner shift {shift}")
                for t in sorted(isl):
                    lst = islands_of(V, st, ("clean",)).get(t, [])
                    row = []
                    for sh in (64, 128):
                        m = cover(lst, sh) if lst else np.zeros(W * H, bool)
                        on_art = m & ~tobool(carbank_page.page_coverage(W, H, isl)) & np.any(snap_arr(arr) != snap_arr(arr[:1]), 1)
                        other = m & anydrawn & ~drawn[t]
                        row.append(f"+{sh}: art {int(on_art.sum()):5} on-other-panels {int(other.sum()):5}")
                    print(f"  id{t:<3} " + "   ".join(row))
            for members, cov in groups:
                n = ncols(arr, cov)
                if len(members) > 1 or members[0] in shadows:
                    print(f"palette group: ids {members}{' + dent art' if any(t in shadows for t in members) else ''} -> one row, {n} colours"
                          + ("  <-- >16 (planner drops the dent art, then splits)" if n > 16 else ""))
        if a.overlay and img:
            ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            for n, t in enumerate(sorted(isl)):
                mk = Image.fromarray((cover(isl[t]).reshape(H, W) * 255).astype(np.uint8)).convert("1")
                ov.paste(Image.new("RGBA", (W, H), ((n * 97) % 256, (n * 57 + 80) % 256, (n * 151 + 40) % 256, 140)), (0, 0), mk)
            out = os.path.join(car, "textures", f"clutmap_{st}.png")
            Image.alpha_composite(img.convert("RGBA"), ov).save(out); print(f"overlay -> {out}")
    if "wheels" in want: wheels_report(car, h, a.fix)
    if a.fix: return
    print("\nrows shared by several moved regions (likely wrong colours):",
          sorted({(s_, r) for (s_, t), rs in moves.items() for r in rs
                  if s_ in sets and sum(1 for (s2, t2), r2 in moves.items() if s2 == s_ and r in r2) > 1}) or "none")


if __name__ == "__main__":
    sys.exit(main())
