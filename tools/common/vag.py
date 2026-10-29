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


def encode(samples, loop=False):
    """16-bit samples -> VAG blocks (padded to a whole number of blocks).

    Returns (payload, sample_count, rms_error). The payload is the raw block
    stream a D2 sound bank embeds: no VAGp header, no per-sample directory.
    """
    flags_loop = bool(loop)
    padded = list(samples)

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
        if index == 0:
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
