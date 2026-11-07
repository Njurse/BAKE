#!/usr/bin/env python3
"""preview_audio.py - the idle/rev soundtrack of the vehicle preview.

The timeline is the same one the preview has always played: idle, four gear
sweeps, then a coast back to idle on the damaged wheel. The engine loop is
resampled at a sliding pitch, so the rev sounds continuous across a shift
instead of stepping between fixed-rate samples.
"""

import os

import pygame

try:
    import numpy as np
except Exception:                       # pragma: no cover
    np = None

# timeline: idle, then each gear sweeps its own rpm band (shorter, wider
# low gears; longer, narrower high gears), then a coast back to idle.
# (seconds, pitch_from, pitch_to) per gear
GEARS_T = [(1.4, 0.70, 1.55), (1.9, 0.95, 1.60), (2.4, 1.08, 1.65),
           (3.1, 1.18, 1.70)]
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
        for i, (duration, p0, p1) in enumerate(GEARS_T):
            if g < duration:
                x = g / duration
                x = 1 - (1 - x) ** 1.6      # pulls hard, flattens near the shift
                return ("gear %d" % (i + 1), (t - IDLE) / GEARS,
                        p0 + (p1 - p0) * x, i + 1)
            g -= duration

    c = (t - IDLE - GEARS) / COAST
    return ("coast", 1.0 - c,
            PITCH_IDLE + (GEARS_T[-1][2] - PITCH_IDLE) * (1 - c) ** 1.5, 0)


def sound_path(root, car, name):
    """Absolute path to sounds.<name>.wav, or None when the car has none."""
    sounds = car.get("sounds") or {}
    entry = sounds.get(name)

    if isinstance(entry, dict) and entry.get("wav"):
        return os.path.join(root, entry["wav"])

    return None


def make_sound(path, pitches):
    """{pitch: Sound} by resampling the wav (numpy) - pitch 1.0 if no numpy."""
    out = {}

    if not path or not os.path.exists(path):
        return out

    try:
        sound = pygame.mixer.Sound(path)
    except Exception as exc:
        print("preview_car: can't load %s (%s)" % (path, exc))
        return out

    if np is None:
        return {1.0: sound}

    data = pygame.sndarray.array(sound)

    for pitch in pitches:
        n = max(2, int(len(data) / pitch))
        x = np.linspace(0, len(data) - 1, n)

        if data.ndim == 1:
            resampled = np.interp(x, np.arange(len(data)), data)
        else:
            resampled = np.stack(
                [np.interp(x, np.arange(len(data)), data[:, c])
                 for c in range(data.shape[1])], 1)

        out[pitch] = pygame.sndarray.make_sound(
            np.ascontiguousarray(resampled.astype(data.dtype)))

    return out


class CarAudio(object):
    """The idle loop plus the rev loop, mixed and pitched together.

    Construct it after pygame.mixer.init(); `mute` (or a mixer that would not
    start) leaves every channel idle and every call a no-op.
    """

    def __init__(self, root, car, mute=False):
        self.mute = mute
        self.idle_channel = None
        self.engine_channel = None
        self.engine_source = None
        self.engine_fixed = {}
        self.position = 0.0
        self.last_pitch = PITCH_IDLE
        self.chunk = 0

        idle_path = sound_path(root, car, "idle")
        engine_path = sound_path(root, car, "engine")

        idle = {} if mute else make_sound(idle_path, [1.0])

        if not mute and np is not None and engine_path and os.path.exists(engine_path):
            try:
                self.engine_source = pygame.sndarray.array(
                    pygame.mixer.Sound(engine_path)).astype(np.float32)
            except Exception as exc:
                print("preview_car: engine wav (%s)" % exc)

        self.engine_fixed = ({} if (mute or self.engine_source is not None)
                             else make_sound(engine_path, [1.0]))

        if not mute:
            pygame.mixer.set_num_channels(4)

        self.idle_channel = (pygame.mixer.Channel(0)
                             if (idle and not mute) else None)
        self.engine_channel = (pygame.mixer.Channel(1)
                               if ((self.engine_source is not None
                                    or self.engine_fixed) and not mute)
                               else None)

        if self.idle_channel:
            self.idle_channel.play(idle[1.0], loops=-1)   # idle loops the run

        if self.engine_channel and self.engine_fixed:
            self.engine_channel.play(self.engine_fixed[1.0], loops=-1)

        rate = (pygame.mixer.get_init() or (22050,))[0]
        self.chunk = int(rate * 0.04)

    def _engine_chunk(self, pitch):
        """`chunk` frames from the loop at a pitch sliding last_pitch -> pitch."""
        n = len(self.engine_source)
        step = np.linspace(self.last_pitch, pitch, self.chunk, endpoint=False)
        idx = (self.position + np.cumsum(step)) % n
        self.position = float(idx[-1])
        self.last_pitch = pitch
        i0 = idx.astype(np.int64)
        i1 = (i0 + 1) % n
        frac = idx - i0

        if self.engine_source.ndim == 1:
            out = self.engine_source[i0] * (1 - frac) + self.engine_source[i1] * frac
        else:
            out = (self.engine_source[i0] * (1 - frac)[:, None]
                   + self.engine_source[i1] * frac[:, None])

        return pygame.sndarray.make_sound(np.ascontiguousarray(
            np.clip(out, -32768, 32767).astype(np.int16)))

    def mix(self, idle_volume, pitch):
        """Crossfade idle -> rev and keep the rev loop fed at `pitch`."""
        if self.idle_channel:
            self.idle_channel.set_volume(idle_volume)

        if not self.engine_channel:
            return

        self.engine_channel.set_volume(1.0 - idle_volume if self.idle_channel else 1.0)

        if self.engine_source is None:
            return

        if not self.engine_channel.get_busy():
            self.engine_channel.play(self._engine_chunk(pitch))

        while self.engine_channel.get_queue() is None:
            self.engine_channel.queue(self._engine_chunk(pitch))
            break
