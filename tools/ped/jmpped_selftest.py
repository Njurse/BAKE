#!/usr/bin/env python3
"""jmpped_selftest.py - archive round-trip and inspection."""

from __future__ import annotations

import os
import sys

import jmpped_archive as ja
import jmpped_images as ji


# --------------------------------------------------------------------------
# Self-test
# --------------------------------------------------------------------------

def _selftest() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "JMPPED.LEV")
    if not os.path.isfile(path):
        print("selftest: %s not found" % path)
        return 2
    arch = ja.read_archive(path)
    print("selftest: v%d, %d models, %d pages" %
          (arch.version, len(arch.models), len(arch.pages)))
    rebuilt = ja.archive_bytes(arch)
    original = open(path, "rb").read()
    if rebuilt != original:
        print("selftest: FAIL archive re-serialize is not byte-identical")
        return 1
    for i, m in enumerate(arch.models):
        if m.unsupported_polys:
            print("selftest: model %d has %d unsupported poly(s)" %
                  (i, m.unsupported_polys))
    for p in arch.pages:
        idx = ji.page_indices(p)
        cluts = ji.page_cluts(p)
        print("selftest: page set %d %s %d CLUTs, %d indices" %
              (p.set, "compressed" if p.compressed else "raw", len(cluts), len(idx)))
    print("selftest: OK (byte-identical archive; models/pages parsed)")
    return 0


def _inspect(path: str) -> int:
    arch = ja.read_archive(path)
    print("%s: v%d, %d models, %d pages%s" %
          (path, arch.version, len(arch.models), len(arch.pages),
           ", v4 ped pack" if arch.is_v4 else ""))
    for i, m in enumerate(arch.models):
        print("  %2d %-18s %4d verts %4d polys %s" %
              (i, arch.model_names[i], len(m.vertices), len(m.polys),
               "instance %d" % m.instance_number if m.instance_number >= 0 else ""))
    for p in arch.pages:
        print("  page set %3d %-10s %3d CLUTs %6d image bytes" %
              (p.set, "compressed" if p.compressed else "raw",
               len(ji.page_cluts(p)), len(p.image)))
    if arch.is_v4:
        print("  ped model index %d, %d ped page(s), %d texture(s), %d palette row(s)" %
              (arch.ped_model_index, len(arch.ped_pages),
               len(arch.ped_textures), len(arch.ped_palettes)))
    rebuilt = ja.archive_bytes(arch)
    original = open(path, "rb").read()
    print("  re-serialize: %s" % ("byte-identical" if rebuilt == original
                                  else "DIFFERS (%d vs %d bytes)" % (len(rebuilt), len(original))))
    return 0 if rebuilt == original else 1


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    if args and args[0] == "--selftest":
        raise SystemExit(_selftest())
    if len(args) == 2 and args[0] == "--inspect":
        raise SystemExit(_inspect(args[1]))
    print("usage: jmpped_selftest.py --selftest | --inspect ARCHIVE")
    raise SystemExit(2)
