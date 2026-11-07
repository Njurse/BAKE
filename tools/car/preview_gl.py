#!/usr/bin/env python3
"""preview_gl.py - the OpenGL drawing half of the vehicle preview.

Texture uploads, the car's compiled display list, the procedural tyre and the
wheel placement. No bank or sound knowledge lives here: callers pass geometry
and images that are already resolved.
"""

import math

import pygame
from OpenGL.GL import *  # noqa: F401,F403

try:
    import numpy as np
except Exception:                       # pragma: no cover
    np = None


def gl_texture(path, transparent=None):
    """A GL texture from a page PNG. `transparent` names RGB triples to clear.

    Step 15 keeps the old behaviour: a fully black texel becomes alpha 0. The
    post-processed path replaces that guess with the bake's own transparency,
    because a genuinely black texel is art.
    """
    surface = pygame.image.load(path).convert_alpha()
    width, height = surface.get_size()

    if transparent is None:
        transparent = ((0, 0, 0),)

    if np is not None:
        rgb = pygame.surfarray.pixels3d(surface)
        alpha = pygame.surfarray.pixels_alpha(surface)

        for colour in transparent:
            alpha[(rgb[:, :, 0] == colour[0]) & (rgb[:, :, 1] == colour[1])
                  & (rgb[:, :, 2] == colour[2])] = 0

        del rgb, alpha
    else:
        for x in range(width):
            for y in range(height):
                pixel = surface.get_at((x, y))

                if (pixel.r, pixel.g, pixel.b) in transparent:
                    surface.set_at((x, y), (0, 0, 0, 0))

    data = pygame.image.tobytes(surface, "RGBA", True)
    texture = glGenTextures(1)
    glBindTexture(GL_TEXTURE_2D, texture)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST)
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, width, height, 0, GL_RGBA,
                 GL_UNSIGNED_BYTE, data)
    return texture


def gl_texture_rgba(width, height, data):
    """A GL texture from ready RGBA bytes (one upload, no colour guessing)."""
    texture = glGenTextures(1)
    glBindTexture(GL_TEXTURE_2D, texture)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST)
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, width, height, 0, GL_RGBA,
                 GL_UNSIGNED_BYTE, data)
    return texture


def build_body(verts, uvs, groups, mats, center, scale):
    """Compile the car's triangles into one display list."""
    textures, display = {}, glGenLists(1)
    glNewList(display, GL_COMPILE)
    light = (0.4, 0.8, 0.45)

    for name, tris in groups.items():
        material = mats.get(name, {"kd": (0.7, 0.7, 0.7), "tex": None})
        tex = material["tex"]

        if tex:
            if tex not in textures:
                textures[tex] = gl_texture(tex)

            glEnable(GL_TEXTURE_2D)
            glBindTexture(GL_TEXTURE_2D, textures[tex])
            base = (1.0, 1.0, 1.0)
        else:
            glDisable(GL_TEXTURE_2D)
            base = material["kd"]

        glBegin(GL_TRIANGLES)

        for tri in tris:
            points = [[(verts[v][k] - center[k]) * scale for k in range(3)]
                      for v, _ in tri]
            ux, uy, uz = [points[1][k] - points[0][k] for k in range(3)]
            vx, vy, vz = [points[2][k] - points[0][k] for k in range(3)]
            normal = (uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx)
            length = math.sqrt(sum(c * c for c in normal)) or 1.0
            shade = 0.55 + 0.45 * abs(
                sum(normal[k] * light[k] for k in range(3)) / length)
            glColor3f(base[0] * shade, base[1] * shade, base[2] * shade)

            for (v, t), point in zip(tri, points):
                if t is not None and t < len(uvs):
                    glTexCoord2f(*uvs[t])

                glVertex3f(*point)

        glEnd()

    glDisable(GL_TEXTURE_2D)
    glEndList()
    return display


def build_tyre(rad, tyre_w, segments=10):
    """A display list for the procedural tyre a wheel sprite is mounted on."""
    tyre = glGenLists(1)
    glNewList(tyre, GL_COMPILE)
    glBegin(GL_QUADS)

    for i in range(segments):
        a0 = 2 * math.pi * i / segments
        a1 = 2 * math.pi * (i + 1) / segments
        shade = 0.10 + 0.08 * abs(math.cos((a0 + a1) / 2))
        glColor3f(shade, shade, shade)

        for angle, x in ((a0, -1), (a1, -1), (a1, 0), (a0, 0)):
            glVertex3f(x * tyre_w, math.sin(angle) * rad * 0.97,
                       math.cos(angle) * rad * 0.97)

    glEnd()

    for x in (-1, 0):                       # side walls
        glColor3f(0.05, 0.05, 0.05)
        glBegin(GL_POLYGON)

        for i in range(segments):
            angle = 2 * math.pi * i / segments
            glVertex3f(x * tyre_w - 0.002 * (x + 0.5),
                       math.sin(angle) * rad * 0.97,
                       math.cos(angle) * rad * 0.97)

        glEnd()

    glEndList()
    return tyre


def draw_wheel(tyre, texture, pos, spin, rad):
    """One wheel: the tyre inboard, the sprite quad facing outward."""
    out = 1.0 if pos[0] >= 0 else -1.0
    glPushMatrix()
    glTranslatef(*pos)
    glScalef(out, 1, 1)                  # local +X points away from the car
    glRotatef(spin, 1, 0, 0)
    glCallList(tyre)                     # tyre sits inboard of the rim
    glEnable(GL_TEXTURE_2D)
    glBindTexture(GL_TEXTURE_2D, texture)
    glColor3f(1, 1, 1)
    glBegin(GL_QUADS)

    for u, v in ((0, 0), (1, 0), (1, 1), (0, 1)):
        glTexCoord2f(u, v)
        glVertex3f(0.004, (v - .5) * 2 * rad, (u - .5) * 2 * rad * out)

    glEnd()
    glDisable(GL_TEXTURE_2D)
    glPopMatrix()
