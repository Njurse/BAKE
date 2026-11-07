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

This file is the CLI and the frame loop. The parts live in:

    preview_bank.py    the declaration -> paths, car and wheel layout
    preview_mesh.py    OBJ/MTL parsing and material -> (set, texture id)
    preview_gl.py      texture uploads, body display list, tyre and wheels
    preview_audio.py   the idle/rev soundtrack and gear timeline
"""
import argparse
import os
import sys
import time

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

try:
    import pygame
    from OpenGL.GL import *  # noqa: F401,F403
    from OpenGL.GLU import gluPerspective
except Exception as exc:  # pragma: no cover
    print("preview_car: skipped (%s) - pip install pygame-ce PyOpenGL numpy" % exc)
    sys.exit(0)

import preview_audio
import preview_bank
import preview_gl
import preview_mesh


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bank")
    ap.add_argument("--car")
    ap.add_argument("--timeout", type=float, default=0)
    ap.add_argument("--stop-file")
    ap.add_argument("--mute", action="store_true")
    ap.add_argument("--bake-scale", type=float,
                    help="rebake --scale (OBJ->engine units); default: 1 if the "
                         "OBJ is already engine-sized, else 265")
    ap.add_argument("--ride", type=float, default=30,
                    help="rest susCompression in engine units (handling.c seeds "
                         "14; bigger = wheels hang lower, body rides higher)")
    ap.add_argument("--yaw", type=float,
                    help="fixed yaw in degrees (no spin) - for checking shots")
    ap.add_argument("--size", default="640x400")
    ap.add_argument("--shot", help="save PNG frames into this dir at 1s,4s,8s,12s")
    ap.add_argument("--flip-y", action="store_true",
                    help="art is Y-down (engine space); default: OBJ is Y-up, as "
                         "the bake expects with --flip-y")
    a = ap.parse_args()

    bank_root, bank = preview_bank.load_bank(a.bank)
    key, root, car = preview_bank.resolve_car(bank_root, bank, a.car)

    if key is None:
        print("preview_car: no declare.cars in bank.json")
        return 0

    wroot, wcar = preview_bank.resolve_wheels(root, bank, car, root)
    objp = preview_bank.clean_model(root, car)

    if not os.path.exists(objp):
        print("preview_car: no clean model %s" % objp)
        return 0

    pygame.init()

    try:
        pygame.mixer.init(frequency=22050, size=-16, channels=1)
    except Exception:
        a.mute = True

    width, height = (int(x) for x in a.size.split("x"))
    os.environ.setdefault("SDL_VIDEO_WINDOW_POS", "40,40")
    pygame.display.gl_set_attribute(pygame.GL_DEPTH_SIZE, 24)
    pygame.display.set_mode((width, height), pygame.OPENGL | pygame.DOUBLEBUF)
    glViewport(0, 0, width, height)
    glMatrixMode(GL_PROJECTION)
    gluPerspective(35, width / height, 0.1, 100)
    glMatrixMode(GL_MODELVIEW)
    glEnable(GL_DEPTH_TEST)
    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
    glEnable(GL_ALPHA_TEST)
    glAlphaFunc(GL_GREATER, 0.5)
    glClearColor(0.18, 0.2, 0.24, 1)

    verts, uvs, groups, mats = preview_mesh.load_obj(objp)
    lo = [min(v[k] for v in verts) for k in range(3)]
    hi = [max(v[k] for v in verts) for k in range(3)]
    center = [(lo[k] + hi[k]) / 2 for k in range(3)]
    ext = [hi[k] - lo[k] for k in range(3)]
    scale = 2.0 / (max(ext) or 1)
    body = preview_gl.build_body(verts, uvs, groups, mats, center, scale)
    # the up axis is the smallest extent; long axis is the largest
    up = ext.index(min(ext))
    long_axis = ext.index(max(ext))

    wheels = {}

    for variant, wheel in (wcar.get("wheels") or {}).items():
        path = (os.path.join(wroot, wheel.get("png", ""))
                if isinstance(wheel, dict) else "")

        if path and os.path.exists(path):
            wheels[variant] = preview_gl.gl_texture(path)

    audio = preview_audio.CarAudio(root, car, a.mute)

    rad, wpos, tyre_w = preview_bank.wheel_layout(
        car, ext, center, lo, scale, a.flip_y, a.bake_scale, a.ride)
    tyre = preview_gl.build_tyre(rad, tyre_w)

    clock = pygame.time.Clock()
    shots_done = set()
    started = time.time()
    spin = 0.0
    last_title = ""

    while True:
        for event in pygame.event.get():
            if event.type == pygame.QUIT or (
                    event.type == pygame.KEYDOWN
                    and event.key in (pygame.K_ESCAPE, pygame.K_q)):
                pygame.quit()
                return 0

        now = time.time() - started

        if a.timeout and now > a.timeout:
            break

        if a.stop_file and os.path.exists(a.stop_file):
            break

        phase, speed, pitch, gear = preview_audio.state(now)

        if phase == "idle":
            audio.mix(1.0, preview_audio.PITCH_IDLE)
            wheel_key = "clean"
        elif phase == "coast":
            audio.mix(max(0.0, min(1.0,
                                1.0 - (pitch - preview_audio.PITCH_IDLE) / 0.3)),
                      pitch)
            wheel_key = "damaged"
        else:
            audio.mix(0.0, pitch)
            wheel_key = "fast" if speed > 0.15 else "clean"

        texture = wheels.get(wheel_key) or wheels.get("clean")
        spin -= speed * 60.0
        title = ("BAKE preview  %s  |  %s  |  wheel: %s  |  Esc closes"
                 % (key, phase, wheel_key))

        if title != last_title:
            pygame.display.set_caption(title)
            last_title = title

        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        glLoadIdentity()
        glTranslatef(0, -0.1, -4.2)
        glRotatef(18, 1, 0, 0)
        glRotatef(a.yaw if a.yaw is not None else now * 40.0, 0, 1, 0)
        # map the model's up axis onto GL +Y
        if up == 2:
            glRotatef(-90, 1, 0, 0)
        elif up == 0:
            glRotatef(90, 0, 0, 1)

        if up == 1 and a.flip_y:
            glScalef(1, -1, 1)                  # engine-space art is Y-down

        glCallList(body)

        if texture:
            for position in wpos:
                preview_gl.draw_wheel(tyre, texture, position, spin, rad)

        if a.shot:
            for stamp in (1, 4, 8, 12):
                if stamp not in shots_done and now >= stamp:
                    shots_done.add(stamp)
                    os.makedirs(a.shot, exist_ok=True)
                    pixels = glReadPixels(0, 0, width, height, GL_RGB,
                                          GL_UNSIGNED_BYTE)
                    image = pygame.image.frombytes(pixels, (width, height),
                                                   "RGB", True)
                    pygame.image.save(
                        image,
                        os.path.join(a.shot, "preview_%02ds_%s.png"
                                     % (stamp, phase.replace(" ", ""))))

        pygame.display.flip()
        clock.tick(60)

    pygame.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
