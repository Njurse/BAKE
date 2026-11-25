#!/usr/bin/env python3
"""rebuild_damaged.py - make a bank car's damaged OBJ line up with its clean OBJ.

    python rebuild_damaged.py <bank.json | bank dir> [--car ID ...] [--check]
                              [--max-move 0.45] [--suffix .rebuilt]

WHY. The bake writes a car's damaged model as vertices ONLY and reuses the clean
model's polygon table (bake.build_damaged_record). The damaged OBJ must
therefore have the SAME vertices in the SAME order as the clean one. When the
clean OBJ is edited afterwards (a light bar, a fixed panel) and the damaged one
is not, every vertex after the first difference is dented toward its
neighbour's position, and the zone lists name vertices the damaged mesh does
not have. The bake does not notice: `checks.ok` stays true.

WHAT IT DOES. For every car whose damaged OBJ differs from its clean OBJ, it
matches each damaged vertex to the clean vertex it came from, then writes
`<name>_damaged<suffix>.obj`: the CLEAN OBJ verbatim (faces, UVs, groups,
materials, vertex order) with each vertex moved to its damaged position.
A clean vertex with no damaged counterpart (a part added after the dent was
made) keeps its clean position, i.e. it is simply not dented. Nothing you
authored is overwritten.

HOW IT MATCHES. Dents move vertices but never change which panels they belong
to or where those panels sit on the page, so each vertex carries a fingerprint:
the set of (material, u, v) corners that use it. The assignment minimises
  distance / half-length  +  1.5 * (1 - fingerprint overlap)  +  0.3 * index gap
so ties between look-alike vertices (mirrored left/right ones) fall to the
nearest and then to the one that kept its place in the file. A match that moved
farther than --max-move of the car's half-length is refused (the vertex stays
undented) and reported: real dents on the stock-derived cars stay under 0.25.

--check writes nothing: it prints the table and exits 1 when any car's damaged
OBJ does not match its clean OBJ vertex-for-vertex (usable from a bake script).
"""
import argparse
import collections
import json
import math
import os
import sys

try:
    import numpy as np
except Exception:                                   # pragma: no cover
    print("rebuild_damaged: needs numpy (pip install numpy)")
    sys.exit(2)

DENT_WARN = 0.25        # of half-length: the healthy cars never exceed this


# ---------------------------------------------------------------- OBJ

def parse_obj(path):
    """-> (lines, vertex line numbers, verts, corners) for one OBJ.

    corners[i] is the set of (material, (u, v)) pairs of every face corner that
    uses vertex i - the fingerprint a dent cannot change."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        lines = fh.read().split("\n")
    vline, verts, vts, corners = [], [], [], collections.defaultdict(set)
    mtl = None
    for n, line in enumerate(lines):
        p = line.split()
        if not p:
            continue
        if p[0] == "v":
            vline.append(n)
            verts.append((float(p[1]), float(p[2]), float(p[3])))
        elif p[0] == "vt":
            vts.append((float(p[1]), float(p[2]) if len(p) > 2 else 0.0))
        elif p[0] == "usemtl":
            mtl = line.split(None, 1)[1].strip()
        elif p[0] == "f":
            for tok in p[1:]:
                a = tok.split("/")
                vi = int(a[0])
                vi = vi - 1 if vi > 0 else len(verts) + vi
                uv = None
                if len(a) > 1 and a[1]:
                    ti = int(a[1])
                    ti = ti - 1 if ti > 0 else len(vts) + ti
                    if 0 <= ti < len(vts):
                        uv = (round(vts[ti][0], 3), round(vts[ti][1], 3))
                corners[vi].add((mtl, uv))
    return lines, vline, verts, corners


# ------------------------------------------------------------ assignment

def _hungarian(cost):
    """Minimum-cost assignment of every row to a distinct column (rows <= cols).
    Kuhn-Munkres with potentials, O(rows^2 * cols). -> {row: col}."""
    n, m = cost.shape
    INF = float("inf")
    u = [0.0] * (n + 1)
    v = [0.0] * (m + 1)
    p = [0] * (m + 1)
    way = [0] * (m + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [INF] * (m + 1)
        used = [False] * (m + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], INF, 0
            row = cost[i0 - 1]
            for j in range(1, m + 1):
                if not used[j]:
                    cur = row[j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j], way[j] = cur, j0
                    if minv[j] < delta:
                        delta, j1 = minv[j], j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
    return {p[j] - 1: j - 1 for j in range(1, m + 1) if p[j]}


def assign(cost):
    """{damaged j: clean i}; the smaller side is matched completely."""
    n, m = cost.shape                       # n damaged rows, m clean columns
    if n <= m:
        return _hungarian(cost)
    inv = _hungarian(cost.T)                # every clean vertex finds a damaged one
    return {j: i for i, j in inv.items()}


def match(cv, cc, dv, dc):
    """-> ({damaged j: clean i}, half-length, distance matrix / half)."""
    half = max(abs(v[2]) for v in cv) or 1.0
    C, D = np.array(cv), np.array(dv)
    dist = np.linalg.norm(D[:, None, :] - C[None, :, :], axis=2) / half
    pen = np.zeros_like(dist)
    for j in range(len(dv)):
        dj = dc.get(j, set())
        for i in range(len(cv)):
            ci = cc.get(i, set())
            union = len(ci | dj) or 1
            pen[j, i] = 1.0 - len(ci & dj) / union
    gap = np.abs(np.arange(len(dv))[:, None] - np.arange(len(cv))[None, :]) / max(len(cv), 1)
    return assign(dist + 1.5 * pen + 0.3 * gap), half, dist


# --------------------------------------------------------------- rebuild

def rebuild(clean_path, dam_path, max_move=0.45):
    """-> (new OBJ text, report). The text is the clean OBJ with damaged positions."""
    lines, vline, cv, cc = parse_obj(clean_path)
    _, _, dv, dc = parse_obj(dam_path)
    rep = {"clean": len(cv), "damaged": len(dv), "aligned": len(cv) == len(dv),
           "matched": 0, "undented": 0, "refused": 0, "dropped": 0,
           "max_move": 0.0, "over_warn": 0}
    if not cv or not dv:
        rep["error"] = "an OBJ has no vertices"
        return None, rep
    mp, half, dist = match(cv, cc, dv, dc)
    out = [None] * len(cv)
    for j, i in mp.items():
        d = float(dist[j, i])
        if d > max_move:
            rep["refused"] += 1             # farther than any dent: not the same vertex
            continue
        out[i] = dv[j]
        rep["matched"] += 1
        rep["max_move"] = max(rep["max_move"], float(np.linalg.norm(np.array(dv[j]) - np.array(cv[i])) / half))
    rep["dropped"] = len(dv) - len(mp)      # damaged vertices with no clean home
    for i in range(len(cv)):
        if out[i] is None:
            out[i] = cv[i]
            rep["undented"] += 1
    rep["over_warn"] = sum(1 for j, i in mp.items()
                           if np.linalg.norm(np.array(dv[j]) - np.array(cv[i])) / half > DENT_WARN
                           and dist[j, i] <= max_move)
    for k, n in enumerate(vline):
        tail = lines[n].split()[4:]
        lines[n] = "v %.6f %.6f %.6f" % out[k] + ((" " + " ".join(tail)) if tail else "")
    return "\n".join(lines), rep


# ------------------------------------------------------------------ bank

def load_bank(path):
    if os.path.isdir(path):
        path = os.path.join(path, "bank.json")
    with open(path, encoding="utf-8") as fh:
        return os.path.dirname(os.path.abspath(path)), json.load(fh)


def car_files(root, entry):
    """-> (label root, car dict) for a declare.cars entry (inline or handling.json path)."""
    if isinstance(entry, str):
        f = os.path.normpath(os.path.join(root, entry))
        with open(f, encoding="utf-8") as fh:
            return os.path.dirname(f), json.load(fh)
    return root, entry or {}


def run(bank_path, cars=None, check=False, max_move=0.45, suffix=".rebuilt", every=False):
    root, bank = load_bank(bank_path)
    declared = bank.get("declare", {}).get("cars", {})
    rows, bad = [], 0
    for cid, entry in declared.items():
        if cars and cid not in cars:
            continue
        croot, car = car_files(root, entry)
        cp = os.path.join(croot, car.get("clean") or "")
        dp = os.path.join(croot, car.get("damaged") or "")
        if not car.get("clean") or not os.path.exists(cp):
            rows.append((cid, "no clean OBJ", None))
            continue
        if not car.get("damaged") or os.path.normpath(dp) == os.path.normpath(cp):
            rows.append((cid, "no damaged mesh (uses the clean one)", None))
            continue
        if not os.path.exists(dp):
            rows.append((cid, "damaged OBJ missing", None))
            bad += 1
            continue
        _, _, cv, _ = parse_obj(cp)
        _, _, dv, _ = parse_obj(dp)
        if check:
            ok = len(cv) == len(dv)
            bad += (not ok)
            rows.append((cid, "ok" if ok else "MISMATCH", {"clean": len(cv), "damaged": len(dv)}))
            continue
        if len(cv) == len(dv) and not every:
            rows.append((cid, "ok (same vertex count; --all rebuilds anyway)", None))
            continue
        text, rep = rebuild(cp, dp, max_move)
        if text is None:
            rows.append((cid, rep.get("error"), rep))
            bad += 1
            continue
        out = os.path.splitext(dp)[0] + suffix + ".obj"
        with open(out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        rep["file"] = os.path.relpath(out, root)
        rows.append((cid, "written", rep))
    return rows, bad


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bank")
    ap.add_argument("--car", action="append", help="only this declare.cars id (repeatable)")
    ap.add_argument("--check", action="store_true", help="write nothing; exit 1 on any mismatch")
    ap.add_argument("--all", action="store_true", dest="every",
                    help="also rebuild cars whose vertex counts already match")
    ap.add_argument("--max-move", type=float, default=0.45,
                    help="refuse a match that moved more than this share of the half-length")
    ap.add_argument("--suffix", default=".rebuilt", help="output = <damaged name><suffix>.obj")
    a = ap.parse_args(argv)
    rows, bad = run(a.bank, a.car, a.check, a.max_move, a.suffix, a.every)
    for cid, status, r in rows:
        if r is None:
            print("%-34s %s" % (cid, status))
        elif a.check:
            print("%-34s clean %3d  damaged %3d  %s" % (cid, r["clean"], r["damaged"], status))
        else:
            note = []
            if r["undented"]:
                note.append("%d undented" % r["undented"])
            if r["refused"]:
                note.append("%d refused" % r["refused"])
            if r["dropped"]:
                note.append("%d damaged-only dropped" % r["dropped"])
            if r["over_warn"]:
                note.append("%d move >%d%% of half-length" % (r["over_warn"], DENT_WARN * 100))
            print("%-34s clean %3d  damaged %3d  max move %.2f  %s -> %s" % (
                cid, r["clean"], r["damaged"], r["max_move"],
                ", ".join(note) or "clean match", r["file"]))
    return 1 if (a.check and bad) else 0


if __name__ == "__main__":
    sys.exit(main())
