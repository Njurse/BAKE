#!/usr/bin/env python3
"""A texture-carrying wheel model for a bank that has no wheel geometry.

The engine keeps the HOST level's wheel mesh for an imported car and reads the
imported wheel only as a texture/UV source (cars.c:2100, "its own vertices are
ignored"). It is strict about the shape all the same - models.c:2755-2775
rejects a wheel unless the first EIGHT polys are all FT4 (id & 0x1f == 21), and
WheelPolyRect reads the tread rectangle from POLY INDEX 2, with the page coming
from that poly's texture_set and the CLUT row from its texture_id.

So a bank with no wheel geometry cannot simply point at a page: it needs eight
FT4 quads whose third poly names the sprite's island. The vertices are never
drawn, so the quads may be degenerate as long as the indices are valid.
"""

import bake

WHEEL_POLYS = 8        # models.c: "the 8-quad FT4 wheel", and 8 <= n <= 24
TREAD_POLY = 2         # WheelPolyRect(&p[2], ...)


def wheel_geom(setid, island, texid=0, name="wheel"):
    """A geom record bake.build_wheel_record accepts for one wheel variant.

    `island` is (u0, v0, u1, v1) in 0..255 texel space - where the sprite sits
    in the 256x256 page. Poly 2 carries it; the other seven repeat it so every
    quad still names a real page rather than the untextured marker.
    """
    u0, v0, u1, v1 = island
    uv = [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]

    # one quad's worth of distinct vertices, reused by every poly: the engine
    # ignores them, but they must be in range for a sane record
    vertices = [(0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)]

    polys = []
    for _ in range(WHEEL_POLYS):
        polys.append({
            "kind": "FT4",
            "set": setid,
            "id": texid,
            "v": [0, 1, 2, 3],
            "uv": list(uv),
            "color": [127, 127, 127],
        })

    return {"name": name, "vertices": vertices, "polys": polys,
            "face_normals": [], "point_normals": [], "scale": 1.0}


def wheel_record(setid, island, scale=1.0, texid=0, name="wheel"):
    """The raw MODEL + FT4 stream for one wheel variant."""
    return bake.build_wheel_record(wheel_geom(setid, island, texid, name), scale)
