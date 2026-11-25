"""rebuild_damaged: a damaged OBJ that drifted from its clean OBJ is realigned."""
import os
import random
import tempfile
import unittest

import rebuild_damaged as R

N = 5   # grid is N x N vertices


def grid(extra=False):
    """A flat panel (two materials, a UV per vertex) and, optionally, a 'light bar'
    triangle with its own material that a later edit added to the clean mesh."""
    v, vt, f = [], [], []
    for j in range(N):
        for i in range(N):
            v.append((i * 100.0, j * 100.0, 0.0))
            vt.append((i / (N - 1), j / (N - 1)))
    for j in range(N - 1):
        for i in range(N - 1):
            a, b, c, d = j * N + i, j * N + i + 1, (j + 1) * N + i + 1, (j + 1) * N + i
            m = "left" if i < (N - 1) // 2 else "right"
            f.append((m, [a, b, c]))
            f.append((m, [a, c, d]))
    if extra:
        base = len(v)
        v += [(150.0, 50.0, 40.0), (250.0, 50.0, 40.0), (200.0, 150.0, 60.0)]
        vt += [(0.1, 0.1), (0.2, 0.1), (0.15, 0.2)]
        f.append(("bar", [base, base + 1, base + 2]))
    return v, vt, f


def write(path, v, vt, f):
    with open(path, "w") as fh:
        fh.write("mtllib x.mtl\n")
        for p in v:
            fh.write("v %.4f %.4f %.4f\n" % p)
        for t in vt:
            fh.write("vt %.4f %.4f\n" % t)
        cur = None
        for m, idx in f:
            if m != cur:
                fh.write("usemtl %s\n" % m)
                cur = m
            fh.write("f " + " ".join("%d/%d" % (i + 1, i + 1) for i in idx) + "\n")


class RebuildTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.clean = os.path.join(self.d, "c.obj")
        self.dam = os.path.join(self.d, "d.obj")

    def make(self, shuffle):
        cv, cvt, cf = grid(extra=True)
        write(self.clean, cv, cvt, cf)
        gv, gvt, gf = grid()
        dented = list(gv)
        dented[12] = (200.0, 200.0, -20.0)         # a dent in the middle of the panel
        dented[13] = (300.0, 200.0, -12.0)
        order = list(range(len(dented)))
        if shuffle:
            random.Random(3).shuffle(order)        # same panel, vertices in another order
        pos = {old: new for new, old in enumerate(order)}
        write(self.dam, [dented[o] for o in order], [gvt[o] for o in order],
              [(m, [pos[i] for i in idx]) for m, idx in gf])
        return cv, dented

    def check(self, shuffle):
        cv, dented = self.make(shuffle)
        text, rep = R.rebuild(self.clean, self.dam)
        out = os.path.join(self.d, "r.obj")
        open(out, "w").write(text)
        _, _, rv, _ = R.parse_obj(out)
        self.assertEqual(len(rv), len(cv))                     # the clean vertex count
        for i in range(N * N):
            self.assertEqual(rv[i], dented[i], i)              # every panel vertex is its dented self
        for i in range(N * N, len(cv)):
            self.assertEqual(rv[i], cv[i], i)                  # the added part is left undented
        self.assertEqual(rep["undented"], 3)
        self.assertEqual(rep["refused"], 0)
        # nothing but the v lines changed: faces, UVs and materials are the clean file's
        a = [l for l in open(self.clean).read().split("\n") if not l.startswith("v ")]
        b = [l for l in text.split("\n") if not l.startswith("v ")]
        self.assertEqual(a, b)

    def test_same_order(self):
        self.check(False)

    def test_vertices_reordered(self):
        self.check(True)

    def test_check_flags_a_count_mismatch(self):
        self.make(False)
        _, _, cv, _ = R.parse_obj(self.clean)
        _, _, dv, _ = R.parse_obj(self.dam)
        self.assertNotEqual(len(cv), len(dv))

    def test_assignment_is_optimal(self):
        import numpy as np
        cost = np.array([[4.0, 1.0, 3.0], [2.0, 0.0, 5.0], [3.0, 2.0, 2.0]])
        self.assertEqual(R.assign(cost), {0: 1, 1: 0, 2: 2})   # total 1 + 2 + 2
        wide = np.array([[1.0, 9.0, 9.0, 9.0], [9.0, 9.0, 1.0, 9.0]])
        self.assertEqual(R.assign(wide), {0: 0, 1: 2})
        tall = np.array([[1.0, 9.0], [9.0, 1.0], [9.0, 9.0]])  # more damaged than clean vertices
        self.assertEqual(R.assign(tall), {0: 0, 1: 1})


if __name__ == "__main__":
    unittest.main()
