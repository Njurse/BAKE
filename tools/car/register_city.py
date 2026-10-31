"""register_city.py NAME <LEV> <game exe dir> [--use MODEL]

Adds `city_N = NAME, <LEV>, <LCF>` to <exe dir>\\JERICHO\\CONFIG\\cities.ini
(paths relative to the game's DRIVER2 folder), unless NAME already has a row -
then only its paths are refreshed, so the city index (11 + N) never moves.
--use MODEL also points carhacks.ini's "import = 5:<city>:<MODEL>" at it.
"""
import os
import re
import sys


def use_car(exedir, city, model):
    """carhacks.ini: import = 5:<city>:<model> (resident slot 5 = that car)."""
    ini = os.path.join(exedir, "JERICHO", "CONFIG", "carhacks.ini")
    lines = (open(ini, encoding="utf-8", errors="replace").read().splitlines()
             if os.path.isfile(ini) else [])
    want = "import = 5:%d:%d" % (city, model)
    out, done, cross = [], False, False
    for ln in lines:
        if re.match(r"\s*import\s*=\s*5:", ln):
            if done:
                continue
            ln, done = want, True
        if re.match(r"\s*cross_city_vehicles\s*=", ln):
            ln, cross = "cross_city_vehicles = 1", True
        out.append(ln)
    if not cross:
        out.append("cross_city_vehicles = 1")
    if not done:
        out.append(want)
    open(ini, "w", encoding="utf-8").write("\n".join(out) + "\n")
    print("  carhacks.ini: %s" % want)


def main(argv):
    use = None
    if "--use" in argv:
        i = argv.index("--use")
        use = int(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    if len(argv) != 4 or not argv[3]:
        print("  (no game folder - cities.ini not touched)")
        return 0
    name, lev, exedir = argv[1].upper(), os.path.abspath(argv[2]), argv[3]
    ini = os.path.join(exedir, "JERICHO", "CONFIG", "cities.ini")
    if not os.path.isfile(ini):
        print("  no %s - register %s by hand" % (ini, name))
        return 0
    data = os.path.join(exedir, "DRIVER2")
    rel = os.path.relpath(lev, data)
    row = "%s, %s, %s" % (name, rel, os.path.splitext(rel)[0] + ".LCF")
    lines = open(ini, encoding="utf-8", errors="replace").read().splitlines()
    used = set()
    for i, ln in enumerate(lines):
        m = re.match(r"\s*city_(\d+)\s*=\s*([^,]+)", ln)
        if not m:
            continue
        used.add(int(m.group(1)))
        if m.group(2).strip().upper() == name:
            new = "city_%s = %s" % (m.group(1), row)
            if ln.strip() != new:
                lines[i] = new
                open(ini, "w", encoding="utf-8").write("\n".join(lines) + "\n")
            print("  cities.ini: city_%s = %s (city %d)"
                  % (m.group(1), name, 11 + int(m.group(1))))
            if use is not None:
                use_car(exedir, 11 + int(m.group(1)), use)
            return 0
    free = next(n for n in range(16) if n not in used)
    lines.append("city_%d = %s" % (free, row))
    open(ini, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("  cities.ini: added city_%d = %s (city %d)" % (free, name, 11 + free))
    if use is not None:
        use_car(exedir, 11 + free, use)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
