#!/usr/bin/env python3
"""vag.py -- PSX VAG/ADPCM, both directions.

Driver 2's sound banks hold VAG data: 16-byte blocks of 28 4-bit samples. Block
layout is

    byte 0   shift (4 bits) and filter (4 bits)
    byte 1   flags (loop markers; the SPU ignores it, the game does not)
    bytes 2..15  28 nibbles, high nibble first

and each sample is decoded as

    predicted = (prev1 * c1 + prev2 * c2 + 32) >> 6
    value     = (nibble << 12) >> shift  +  predicted

with the filter coefficients below. Bit 15 of the CLUT-style "STP" convention
does not exist here: ADPCM samples are 16-bit signed, clamped.

The encoder is a plain analysis-by-synthesis search: for every block, every
filter and every shift, quantise, decode, and keep whichever pair the block's
own signal likes best. That is slower than the closed-form encoders the console
SDK used and it is measurably better on the material this pipeline feeds it
(engine loops, horns), because the chosen filter is the one whose RECONSTRUCTED
block is closest, not the one whose residual looked smallest.

Everything here is deterministic - no randomness, no float accumulation order
that depends on the platform - so a rebuild from the same WAV produces the same
bytes, which is what lets bank.json name a WAV and still reproduce a bank.
"""

import struct
import wave

COEFS = ((0, 0), (60, 0), (115, -52), (98, -55), (122, -60))

SAMPLES_PER_BLOCK = 28
BLOCK_BYTES = 16
NIBBLES_PER_BLOCK = SAMPLES_PER_BLOCK

# The SPU plays a sample at its own rate scaled by pitch; PSX titles keep their
# VAG headers in this range and the engine's bank format stores the same number.
MIN_RATE = 4000
MAX_RATE = 44100
DEFAULT_RATE = 11025
# The level a baked sample is lifted to when nothing says otherwise. Measured
# from the game's own car banks (sound.c sound_level_diag, CHICAGO): they sit at
# peak 32749-32767, i.e. full scale. Retailed here so wav2vag and a bank bake
# cannot disagree about what "level" means.
DEFAULT_PEAK = 0.999

# A bank's `samplerate` IS the sample's rate in Hz, and it is worth writing down
# why, because the arithmetic is easy to read the wrong way round:
#
#   * PsyX decodes the VAG and hands the PCM to OpenAL declaring 44100 Hz
#     (PsyX_SPUAL.cpp UpdateVoiceSample: `alBufferData(..., 44100)`), then sets
#     `AL_PITCH = attr.pitch / 4096`;
#   * the engine derives that pitch as `samplerate * pitch / 44100`
#     (sound.c CompleteSoundSetup), with pitch = 4096 for unity.
#
# so the played rate is `samplerate` Hz exactly, and the field is the recording
# rate - nothing to scale. (There are `alBufferData(..., 11000)` calls in the
# same file, in PsyX_SPUAL_Write and inside an `#if 0` sample test; neither is
# the voice path. Reading one of those instead makes a 11025 Hz sample look like
# it should be written as 44100, which plays it four times too high.)
SPU_PITCH_UNITY = 44100


def rate_field(rate):
    """The bank's `samplerate` for a VAG recorded at `rate` Hz - the rate."""
    return int(round(rate))


def rate_from_field(field):
    """The recording rate a bank's `samplerate` field plays at."""
    return int(field)


def normalise(samples, peak=0.9, tolerance=0.06, force=False):
    """Scale a sample so its loudest point sits at `peak` of full scale.

    A bank's samples have no gain of their own: the engine's channel volume is
    the same -10000..0 for every car, so a WAV that peaks at 20% plays at 20% of
    the level next to a retail sample.

    The `tolerance` dead band matters more than it looks. Many car sounds come
    from a VAG in the first place - a decoded PSX sample sits exactly ON the
    ADPCM lattice - and scaling it by even 3% moves every sample off that
    lattice, so the encoder has to re-quantise and the result measures 30 dB
    instead of ~76 dB. On the Firebird's own three samples the gains the target
    would have applied were 1.7% (engine), 3.3% (idle) and 5.2% (horn); leaving
    them alone took the idle from 32.6 dB to 79.3 dB. Hence the 6% band: wide
    enough for a source that is already at level, narrow enough that a
    deliberate `level: 0.85` is still honoured (that is a 12% gain). `force`
    scales anyway, for when the exact level is the point.

    `force` scales even inside the dead band, for the case where the exact
    level is the point (a sample that must sit 1 dB under another).

    Returns (samples, old_peak, applied_gain) - gain 1.0 means untouched.
    """
    top = max((abs(s) for s in samples), default=0)

    if top <= 0:
        return list(samples), 0, 1.0

    target = int(peak * 32767)
    gain = target / float(top)

    if not force and abs(gain - 1.0) <= tolerance:
        return list(samples), top, 1.0

    return scale(samples, gain), top, gain


def scale(samples, gain):
    """Multiply every sample by `gain`, saturating at int16 bounds."""
    if gain == 1.0:
        return list(samples)

    return [max(-32768, min(32767, int(round(s * gain)))) for s in samples]


def gain_db(samples, db):
    """A relative gain in decibels, applied before any level normalisation.

    Kept separate from `level` on purpose: `level` answers "how loud should this
    sit next to retail", `gain_db` answers "make this take 2 dB hotter than the
    one it replaced" without changing where the ceiling lands.
    """
    if not db:
        return list(samples)

    return scale(samples, 10.0 ** (float(db) / 20.0))


def trim(samples, start=0.0, end=None, rate=DEFAULT_RATE):
    """Cut a sample to (start, end) seconds - drop the silence or the tail.

    Trimming is the one knob that also buys byte budget back: the SPU write is
    unchecked (see exportd2._sound_sample), so a loop whose first 300 ms is
    silence costs 4800 bytes for nothing.
    """
    if not start and end is None:
        return list(samples)

    rate = rate or DEFAULT_RATE
    a = max(0, int(round(float(start or 0.0) * rate)))
    b = len(samples) if end is None else int(round(float(end) * rate))
    b = max(a, min(len(samples), b))

    return list(samples[a:b])


def fade_out(samples, seconds, rate=DEFAULT_RATE):
    """A linear fade over the last `seconds`: a one-shot without a click.

    The engine starts and stops these on demand, and a cut waveform snapped to
    zero is an audible tick on every horn press.
    """
    if not seconds or seconds <= 0:
        return list(samples)

    n = min(len(samples), int(round(float(seconds) * (rate or DEFAULT_RATE))))

    if n < 2:
        return list(samples)

    out = list(samples)
    last = len(out) - 1

    for i in range(n):
        out[last - i] = int(round(out[last - i] * (i / float(n - 1))))

    return out


def _clamp16(v):
    if v > 32767:
        return 32767

    if v < -32768:
        return -32768

    return v


def decode(data, samples=None):
    """VAG blocks -> list of 16-bit samples."""
    out = []
    prev1 = 0
    prev2 = 0

    for off in range(0, len(data) - BLOCK_BYTES + 1, BLOCK_BYTES):
        shift = data[off] & 0x0F
        filt = (data[off] >> 4) & 0x0F

        if filt >= len(COEFS):
            filt = 0

        c1, c2 = COEFS[filt]

        for i in range(NIBBLES_PER_BLOCK):
            byte = data[off + 2 + i // 2]
            nib = (byte >> 4) if (i & 1) == 0 else (byte & 0x0F)

            if nib >= 8:
                nib -= 16

            predicted = (prev1 * c1 + prev2 * c2 + 32) >> 6
            value = _clamp16(((nib << 12) >> shift if shift else nib << 12)
                             + predicted)

            out.append(value)
            prev2 = prev1
            prev1 = value

            if samples is not None and len(out) >= samples:
                return out

    return out


def _quantise(residual, step_bits):
    """The 4-bit code for a residual, at a step of 2**step_bits.

    A block's `shift` field decodes as `(nibble << 12) >> shift`, so the step is
    2**(12 - shift) - which is why the search below turns the shift into step_bits
    before quantising. (Dividing by 2**shift instead is the classic way to get an
    encoder that decodes to noise.)
    """
    if step_bits <= 0:
        q = residual
    else:
        step = 1 << step_bits
        half = step >> 1
        q = (residual + half) // step if residual >= 0 else -(
            (-residual + half) // step)

    if q > 7:
        q = 7

    if q < -8:
        q = -8

    return q


def _encode_block(samples, prev1, prev2):
    """One 16-byte block: the best (filter, shift) over 28 samples."""
    best = None

    for filt, (c1, c2) in enumerate(COEFS):
        for shift in range(13):
            step_bits = 12 - shift
            err = 0
            s1, s2 = prev1, prev2
            nibbles = []

            for s in samples:
                predicted = (s1 * c1 + s2 * c2 + 32) >> 6
                residual = s - predicted
                q = _quantise(residual, step_bits)
                decoded = _clamp16(predicted + q * (1 << step_bits)
                                   if step_bits > 0 else predicted + q)
                err += (s - decoded) * (s - decoded)
                nibbles.append(q)
                s2 = s1
                s1 = decoded

            if best is None or err < best[0]:
                best = (err, filt, shift, nibbles)

            if err == 0:
                break

    _err, filt, shift, nibbles = best

    block = bytearray(BLOCK_BYTES)
    block[0] = (filt << 4) | shift
    block[1] = 0                     # flags, set by encode() per block position

    for i, q in enumerate(nibbles):
        q &= 0x0F

        if (i & 1) == 0:
            block[2 + i // 2] = q << 4
        else:
            block[2 + i // 2] |= q

    return bytes(block), nibbles


def encode(samples, loop=False, loop_from=0):
    """16-bit samples -> VAG blocks (padded to a whole number of blocks).

    Returns (payload, sample_count, rms_error). The payload is the raw block
    stream a D2 sound bank embeds: no VAGp header, no per-sample directory.
    """
    flags_loop = bool(loop)
    padded = list(samples)
    # where the loop begins: after the attack, if the author says so. PsyX reads
    # this marker as the loop start, so it is what stops the whole sample
    # retriggering on every cycle.
    # The marker cannot sit on block 0: PsyX derives the loop start as
    # `k + 26` AFTER decoding the block, then drops the points unless
    # `loopStart - 54 > 0`, so a marker on the first block is thrown away and
    # the whole buffer loops instead (attack included). Block 1 is 28 samples
    # in - inaudible - and makes the derived loop region real.
    loop_block = 1 if len(padded) >= 2 * SAMPLES_PER_BLOCK else 0

    if loop_from and loop:
        loop_block = min(max(int(loop_from) // SAMPLES_PER_BLOCK, 1),
                         max(len(padded) // SAMPLES_PER_BLOCK - 1, 0))

    while len(padded) % SAMPLES_PER_BLOCK:
        padded.append(0)

    out = bytearray()
    prev1 = 0
    prev2 = 0
    total = 0

    total_blocks = len(padded) // SAMPLES_PER_BLOCK

    for index, off in enumerate(range(0, len(padded), SAMPLES_PER_BLOCK)):
        block, nibbles = _encode_block(padded[off:off + SAMPLES_PER_BLOCK],
                                       prev1, prev2)
        block = bytearray(block)
        # VAG flags: bit 0 = loop end, bit 1 = repeat, bit 2 = loop start. A
        # sample is one block stream, so the loop runs the whole thing; the SPU
        # (and PsyCross) look for these markers, and 0 everywhere reads as "this
        # stream never ends".
        if index == loop_block:
            block[1] = 0x04 if flags_loop else 0
        if index == total_blocks - 1:
            block[1] |= 0x03 if flags_loop else 0x01
        out += block

        # the decoder's state follows the RECONSTRUCTED samples, not the originals
        c1, c2 = COEFS[(block[0] >> 4) & 0x0F]
        shift = block[0] & 0x0F

        for q in nibbles:
            predicted = (prev1 * c1 + prev2 * c2 + 32) >> 6
            value = _clamp16(predicted + ((q << 12) >> shift if shift
                                          else q << 12))
            total += (value - 0) * 0
            prev2 = prev1
            prev1 = value

    decoded = decode(bytes(out), len(samples))
    diff = [a - b for a, b in zip(samples, decoded)]
    rms = (sum(d * d for d in diff) / float(len(diff))) ** 0.5 if diff else 0.0

    return bytes(out), len(samples), rms


def blocks_for(samples):
    """How many 16-byte blocks `samples` needs."""
    return (len(samples) + SAMPLES_PER_BLOCK - 1) // SAMPLES_PER_BLOCK


def read_wav(path):
    """(mono 16-bit samples, rate) from a PCM WAV."""
    with wave.open(path, "rb") as w:
        channels = w.getnchannels()
        width = w.getsampwidth()
        rate = w.getframerate()
        frames = w.readframes(w.getnframes())

    if width != 2:
        raise SystemExit("%s is %d-bit; export it as 16-bit PCM"
                         % (path, width * 8))

    count = len(frames) // 2
    values = list(struct.unpack("<%dh" % count, frames[:count * 2]))

    if channels > 1:
        mono = [int(round(sum(values[i:i + channels]) / float(channels)))
                for i in range(0, len(values) - channels + 1, channels)]
        values = mono

    return values, rate


def write_wav(path, samples, rate):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack("<%dh" % len(samples), *samples))


def resample(samples, src_rate, dst_rate):
    """Linear resample. Only used when a source rate cannot be stored."""
    if src_rate == dst_rate or not samples:
        return list(samples)

    ratio = float(dst_rate) / float(src_rate)
    out_n = int(round(len(samples) * ratio))
    out = []

    for i in range(out_n):
        pos = i / ratio
        a = int(pos)
        b = min(a + 1, len(samples) - 1)
        frac = pos - a
        out.append(int(round(samples[a] * (1.0 - frac) + samples[b] * frac)))

    return out


def fit_rate(rate):
    """A rate the header can carry: clamp, and say whether it moved."""
    rate = int(rate or DEFAULT_RATE)

    if rate < MIN_RATE:
        return MIN_RATE, rate
    if rate > MAX_RATE:
        return MAX_RATE, rate

    return rate, None
