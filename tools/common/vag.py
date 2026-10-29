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
# The loudness those same banks measure: rms 9557-12629 of 32767 = 29-39%, so
# the middle of the band is the target. Peak alone is not loudness - a limited
# recording reaches 100% peak with an rms half again as high, and sounds loud
# and squashed rather than loud and punchy.
DEFAULT_RMS = 0.34

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


def stats(samples):
    """Peak, RMS, crest factor, DC offset and clipping for a sample.

    This is the "how loud is this really" measurement the level needs. Peak alone
    is not loudness: a limited recording can sit at 100% peak while its RMS is
    half again what retail's is, and it will sound loud AND squashed. Crest
    factor (peak/RMS) is the tell - retail's own car samples measure 2.6-3.4,
    and anything near 2.0 has had its transients flattened already.
    """
    n = len(samples)

    if not n:
        return {"n": 0, "peak": 0, "rms": 0.0, "crest": 0.0, "dc": 0.0,
                "clipped": 0, "longest_run": 0}

    total = sum(samples)
    dc = total / float(n)
    peak = max(abs(v) for v in samples)
    rms = (sum(float(v) * v for v in samples) / n) ** 0.5
    pinned = 32700
    clipped = sum(1 for v in samples if abs(v) >= pinned)
    longest = run = 0

    for v in samples:
        if abs(v) >= pinned:
            run += 1
            longest = max(longest, run)
        else:
            run = 0

    return {"n": n, "peak": peak, "rms": rms, "dc": dc,
            "crest": (peak / rms) if rms else 0.0,
            "clipped": clipped, "longest_run": longest}


def remove_dc(samples):
    """Subtract the mean.

    A DC offset is silent but it eats headroom: a sample offset by 300 can only
    reach 32467 before it clips, and every level measurement - peak especially -
    is then reading the offset rather than the signal.
    """
    if not samples:
        return list(samples)

    dc = int(round(sum(samples) / float(len(samples))))

    if dc == 0:
        return list(samples)

    return [max(-32768, min(32767, v - dc)) for v in samples]


def level(samples, peak=DEFAULT_PEAK, rms=None, tolerance=0.06, force=False):
    """Set a sample's level: match the RMS loudness, never cross the peak ceiling.

    A bank's samples have no gain of their own - the engine's channel volume is
    the same -10000..0 for every car - so this IS the sample's volume.

    Two measurements, two jobs:

    * `peak` is the ceiling. It is what clips, and retail runs its own car
      banks right up to it (peak 32749-32767, measured with
      JERICHO_SOUND_DIAG=1), so it is a limit, not a target.
    * `rms` is the loudness. Retail's own samples sit at 29-39% of full scale
      (crest 2.6-3.4). A peak-normalised sample whose crest is only 2.0 lands
      at 49% instead - measurably ~3 dB hot, and since its transients are
      already flattened it sounds loud and squashed rather than loud and punchy.

    So the gain is the smaller of "reach the RMS target" and "reach the peak
    ceiling". A dynamic source is limited by the ceiling and lands at full
    scale, exactly like retail's; a hot or limited source is pulled *down* to
    retail's loudness instead of being pushed to full scale on top of it.

    The dead band still applies, and it is not laziness: many car sounds are
    decoded VAGs, and a decoded PSX sample sits exactly ON the ADPCM lattice.
    Scaling it by 3% moves every value off the lattice and forces a
    re-quantisation, which measures ~30 dB instead of ~76 dB. A sample already
    inside both bands is used as it is. `force` scales anyway.

    Returns (samples, stats_before, applied_gain). gain 1.0 means untouched.
    """
    before = stats(samples)

    if not samples or before["peak"] == 0:
        return list(samples), before, 1.0

    gain = 1.0

    if rms:
        if before["rms"] > 0:
            gain = (float(rms) * 32767.0) / before["rms"]
    else:
        gain = (float(peak) * 32767.0) / before["peak"]

    # the ceiling always wins: loudness is a preference, clipping is a defect
    ceiling = float(peak) * 32767.0

    if before["peak"] * gain > ceiling:
        gain = ceiling / float(before["peak"])

    if not force and abs(gain - 1.0) <= tolerance:
        return list(samples), before, 1.0

    return scale(samples, gain), before, gain


def normalise(samples, peak=DEFAULT_PEAK, tolerance=0.06, force=False):
    """Peak-only level. Kept for callers that want exactly that."""
    out, _before, gain = level(samples, peak=peak, rms=None,
                               tolerance=tolerance, force=force)
    return out, _before["peak"], gain


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


# How much of the previous quantisation error is fed back into the next
# residual. 0 is plain rounding. A first-order loop pushes the error towards the
# high end of the band and leaves the low end cleaner; measured on the Firebird's
# engine loop it is worth a few dB in 0-1 kHz, which is where a car engine lives.
NOISE_SHAPING = 0.5


def _encode_block(samples, prev1, prev2):
    """One 16-byte block: the best (filter, shift) over 28 samples."""
    best = None

    for filt, (c1, c2) in enumerate(COEFS):
        for shift in range(13):
            step_bits = 12 - shift
            err = 0
            shaped = 0.0
            s1, s2 = prev1, prev2
            nibbles = []

            for s in samples:
                predicted = (s1 * c1 + s2 * c2 + 32) >> 6
                residual = s - predicted
                # error feedback: aim at what the last step got wrong, so the
                # loop's own error cancels instead of accumulating
                q = _quantise(int(round(residual + shaped)), step_bits)
                decoded = _clamp16(predicted + q * (1 << step_bits)
                                   if step_bits > 0 else predicted + q)
                step_error = s - decoded
                shaped = NOISE_SHAPING * step_error
                err += step_error * step_error
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


def seam_error(samples, start, end):
    """How badly the waveform jumps from `end` back to `start`.

    Value first, then slope: a join that matches in level but not in direction
    still clicks, just less. Both are in sample units, so the number is directly
    comparable across candidates.
    """
    n = len(samples)

    if not (0 <= start < n) or not (0 <= end < n):
        return 1 << 30

    a = samples[start]
    b = samples[end]
    delta = abs(a - b)

    if start + 1 < n and end + 1 < n:
        delta += abs((samples[start + 1] - a) - (samples[end + 1] - b))

    return delta


def find_loop(samples, search_from=0.0, rate=DEFAULT_RATE, align=SAMPLES_PER_BLOCK,
              tail=0.02):
    """The block-aligned (start, end) whose join is quietest, or None.

    `search_from` is the earliest loop start (seconds) - the attack before it is
    played once and never repeated, which is what `loop_from` is for. `tail`
    keeps the loop end off the very last sample so the marker block can carry it.
    The engine can only loop at the block stream's end (the marker is on the last
    block), so the END of the loop is the end of the sample; the pair found here
    therefore also decides how much to trim off the tail.
    """
    n = len(samples)

    if n < 4 * align:
        return None

    first = max(align, int(float(search_from or 0.0) * (rate or DEFAULT_RATE)))
    first = ((first + align - 1) // align) * align
    keep_tail = max(align, int(float(tail or 0.0) * (rate or DEFAULT_RATE)))
    last_end = n - keep_tail

    if last_end <= first:
        return None

    best = None

    # The loop END is the end of the last block in the stream, because that is
    # where the marker rides - so only block-aligned ends are candidates. Scoring
    # an arbitrary end measures a join that will never be played: the encoder
    # pads to the block boundary and the loop then runs over the padding.
    for loop_end in range(((last_end + align - 1) // align) * align, n + 1, align):
        end = loop_end - 1

        for start in range(first, min(n - align, first + 40 * align), align):
            score = seam_error(samples, start, end)

            if best is None or score < best[0]:
                best = (score, start, end)

    if best is None:
        return None

    return {"start": best[1], "end": best[2], "seam": best[0],
            "seconds": (best[2] - best[1]) / float(rate or DEFAULT_RATE)}


def encode(samples, loop=False, loop_from=0, loop_end=None):
    """16-bit samples -> VAG blocks (padded to a whole number of blocks).

    Returns (payload, sample_count, rms_error). The payload is the raw block
    stream a D2 sound bank embeds: no VAGp header, no per-sample directory.
    """
    flags_loop = bool(loop)
    padded = list(samples)

    # The loop END is the end of the block stream (the marker rides the last
    # block), so a chosen loop end is applied by cutting the tail off here. See
    # find_loop: a join that does not meet itself clicks once per cycle.
    if loop_end is not None and 0 < loop_end < len(padded):
        padded = padded[:loop_end]
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
