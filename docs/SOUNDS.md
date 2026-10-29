# SOUNDS — custom car samples into a bank

A bank can carry its own engine, idle and horn (or police siren). This is the
sequence a car's sound takes: WAV on disk → PSX VAG/ADPCM in the bank →
`LoadSoundBankDynamic` → a voice.

## What the engine expects

`bank.json` declares `sounds`; the bake writes **one dynamic bank per model
slot** into `LUMP_CAR_SOUNDS` (segment type 201):

    int 1
    int 13                         the model slot count
    int offset[13]                 per model, relative to the SEGMENT body, -1 = none
    ... per model:
        int 3                       three samples, and the reader rejects anything else
        SAMPLE_DATA[3]              { address, length, loop, samplerate }
        VAG payload                 addresses are relative to this payload

`loop` is a **boolean** (`sound.c`: `chan->flags |= samp->loop ? CHAN_LOOP : 0`),
not a loop point. `LUMP_CAR_SIRENS` (202) is 13 flags, bit N = model N is a cop
car; `models.c GetCarImportSiren` reads that, `CarHasSiren` uses it.

The samples are always **engine, idle, third** — the third is the horn for a
civilian car and the siren for a cop. There is no fourth.

## The encoder

`tools/common/vag.py` is the whole format, both directions:

- a block is 16 bytes = 1 shift/filter byte, 1 flag byte, 28 nibbles;
- a sample is `(nibble << 12) >> shift` plus the filter's prediction from the
  previous two samples;
- `encode()` searches **every filter and every shift per block** and keeps the
  pair whose *reconstruction* is closest. That is slower than the closed-form
  encoders and measurably better on engine loops.

Measured on this pipeline: a 440 Hz sine at 11025 Hz encodes at ~54 dB SNR, a
sawtooth at ~33 dB. Deterministic — the same WAV always produces the same bytes,
which is what lets `FIREBIRD.json` name a WAV and still rebuild the bank exactly.

    python tools/common/wav2vag.py engine.wav -o engine.vag --loop
    python tools/common/wav2vag.py engine.vag --decode -o engine.wav

## The flags byte is load-bearing

Bit 0 = loop end, bit 1 = repeat, bit 2 = loop start. A stream whose last block
carries **no end marker keeps decoding past its own end**; on PsyCross that walks
off the sample and writes outside the host's SPU buffer — a hard crash with no
useful dump, several seconds after the sound loaded. The encoder writes `0x03` on
the last block of a looping sample and `0x01` on a one-shot for exactly this
reason. If a sample plays as silence or kills the game, look here first.

## The knobs: what a bank can say about a sample

Every sample is either a bare path or an object carrying that path as `wav`.
Both are legal; the long form is what `carbank_bake --complete` writes:

```json
"sounds": {
  "engine": { "wav": "sounds/engine.wav", "level": 0.999, "rms": 0.34,
              "normalise": true, "force": false, "gain_db": 0.0,
              "rate": null, "rate_pct": 100, "pitch": 2.0,
              "loop": "auto", "loop_from": 0.0, "loop_to": null,
              "trim": null, "fade_out": 0.0 },
  "idle":   { "wav": "sounds/idle.wav" },
  "horn":   { "wav": "sounds/horn.wav", "loop": false },
  "cop": false,
  "engine_pct": 100, "idle_pct": 100, "loop_from": 0.0,
  "peak": 0.999, "rms": 0.34, "max_rate": 16000, "sample_bytes": 16384
}
```

| key | default | what it does |
| --- | --- | --- |
| `level` | the block's `peak` (0.999) | the peak **ceiling**, 0.05..1.0. A bank sample has no gain of its own - the channel volume is the same for every car - so the level knobs ARE its volume |
| `rms` | the block's `rms` (0.34) | the **loudness** target, 0.05..1.0, or `0` for peak-only. Retail's own car samples measure 29-39% rms |
| `normalise` | `true` | `false` keeps the source's own level; then `gain_db` is the only level control |
| `force` | `false` | rescale even when the sample is already at `level` (see the dead band below) |
| `gain_db` | `0.0` | relative gain, applied before the level |
| `rate` | `null` (= the source rate) | exact output frequency, 4000..44100; wins over `rate_pct` |
| `rate_pct` | `100` | the source rate as a percentage, the Driver 1 beta knob |
| `loop` | by role (engine/idle `true`, third `false`) | mark the VAG as looping; `"auto"` also searches for the loop points whose join is quietest |
| `loop_from` | the block's `loop_from` (0) | seconds into the sample where the loop starts - the attack before it plays once |
| `loop_to` | `null` (the end) | seconds; cut the sample there and end the loop on it |
| `pitch` | `1.0` | scales ONLY the declared rate, so the pitch the game asks for lands on the recording - see below |
| `trim` | `null` | `[start, end]` seconds to keep - drops silence, and buys byte budget back |
| `fade_out` | `0.0` | linear fade over the last N seconds, so a one-shot does not click |

Block-wide: `peak`, `rms`, `max_rate` (byte-budget ceiling, default 16000)
and `sample_bytes` (per-sample cap, default 16384).

### Pitch: the game asks for less than the recording

`gamesnd.c` drives a car's two loops by a pitch that depends on revs:

    pitch = revs / 4 + 4096   (idle)     -> ~1.0x  .. 1.25x of the declared rate
    pitch = revs / 4 + 1500   (revving)  -> ~0.37x .. 0.62x

so a rev loop declared at the rate it was recorded at plays two octaves down
when the car is moving, and what is left in the audible band is the encoder's
noise - the "driving on gravel" sound. `pitch` multiplies the rate FIELD only
(the bytes are untouched, so nothing is re-encoded): the Firebird's engine
loop declares `pitch: 2.0`, which puts it at 0.73x-1.23x - 108-183 Hz instead
of 55-92 Hz. The idle sample is asked for at ~1.0x and needs no pitch.

### Loops have to meet

The SPU cannot crossfade: the loop wraps from the end of the block stream
back to the marker, so the waveform must join itself. `loop: "auto"` searches
block-aligned pairs and keeps the one whose join is quietest (value AND
slope), trimming the tail to it. On the Firebird that took the engine's join
from a jump of 10479 counts to 26 (played back: 228 after quantisation) and
the idle's from 2480 to 441. A join of a few hundred out of 32767 is
inaudible; the old one was 22% of full scale, once per cycle.

### Encoder noise

The block encoder feeds its own quantisation error forward (`NOISE_SHAPING`),
which is decoder-compatible - only the choice of nibble changes. Measured on
the Firebird's engine loop, SNR by band:

| shaping | 0-1 kHz | 1-3 kHz | 3-8 kHz |
| --- | --- | --- | --- |
| 0.0 | 40.8 dB | 24.6 dB | 15.2 dB |
| 0.5 | 42.7 dB | 26.4 dB | 13.0 dB |
| 0.7 | 43.6 dB | 26.9 dB | 11.8 dB |

0.5 is the default: about +2 dB where an engine lives, about -2 dB above
3 kHz where the sample has almost no content. A sample already on the ADPCM
lattice (a decoded VAG) is unaffected either way. Lowering the encode rate
was measured and is worse (34.7 dB at 16 kHz against 26.3 dB at 8 kHz).

They are applied in a fixed order, and the order is audible:

    dc offset -> resample -> trim -> fade_out -> gain_db -> level -> byte budget
    -> encode

Trimming before the fade puts the fade on the new tail; the fade before the
level stops it from dragging the peak down and making the level scale
everything up; and the level last means `gain_db` is a relative trim against a
fixed target.

### Amplitude: peak is the ceiling, rms is the loudness

Peak alone cannot set a level, and treating it as if it can is what made the
Firebird sound loud and crushed. The engine loop measures **peak 99% with
rms 49%** - crest factor 2.0 - because the recording has already been
limited. Pushing that to 100% peak put its rms 10 points above retail's
29-39%, i.e. about **+3 dB**, on a waveform whose transients were already
flat. Loudness, not peak, is the thing a listener compares.

So the gain is the smaller of two moves: reach the `rms` target, or reach the
`peak` ceiling. A dynamic recording is stopped by the ceiling and lands at
full scale exactly like retail's; a hot or limited one is pulled *down* to
retail's loudness instead of being pushed up on top of it. The Firebird's
three samples came out at:

| sample | peak | rms | crest | what happened |
| --- | --- | --- | --- | --- |
| engine | 71% | 34% | 2.1 | pulled down 3.2 dB to retail loudness |
| idle | 97% | 32% | 3.0 | inside both bands, left untouched |
| horn | 97% | 34% | 2.8 | inside both bands, left untouched |

Retail's own car banks: peak 100%, rms 29-39%, crest 2.6-3.4.

DC offset is removed first, because an offset is silent but eats headroom -
a sample sitting 300 high can only reach 32467 before it clips, and every
peak measurement is then reading the offset rather than the signal. The bake
also reports a source that is already clipped, and says so when a sample's
crest factor is under 2.2: no level can undo a limited recording, and
normalising it only makes the crush louder.

### The level dead band

`normalise` leaves a sample alone when it is already within **6%** of `level`.
That is not laziness: most car sounds are decoded VAGs, and a decoded PSX sample
sits exactly ON the ADPCM lattice. Scaling it by 3% moves every value off the
lattice and forces a re-quantisation, which measures ~30 dB instead of ~76 dB.
Measured on the Firebird's three samples, the gains the target would have applied
were 1.7%, 3.3% and 5.2% - all inside the band, so all three are used as they
are, and the idle went from 32.6 dB to 79.3 dB. Use `force: true` when the exact
level is the point.

## Level: measured against the game's own banks

`JERICHO_SOUND_DIAG=1` makes `sound.c` log the level of every bank as it loads -
the level's own car banks and an imported one both come through
`LoadSoundBank`/`LoadSoundBankDynamic`, so the comparison is in the units the
mixer sees:

    sound level: bank 3 samples 3 payload 29008 rate 16000 loop 1 peak 32749 (100%) rms 9557

Measured on CHICAGO's own car banks: **peak 32749-32767 (100%), rms 9557-12629
(29-39%), crest factor 2.6-3.4**. The bake normalises to `peak 0.999`, which is
what retail does; the old 90% was quieter than retail, not safer. The Firebird's
samples now measure:

| sample | bytes | rate | peak | rms | crest |
| --- | --- | --- | --- | --- | --- |
| idle | 13392 | 16000 | 100% | 33% | 3.0 (retail 2.6-3.4) |
| horn | 3008 | 16000 | 100% | 36% | 2.8 |
| engine | 8384 | 16000 | 99% | **49%** | **2.0** |

The engine loop is the outlier, and it is the source: `sounds/engine.wav` is
already limited (crest 2.0 means its peaks are flat-topped), so 4-bit ADPCM has
the least headroom exactly where the ear is most sensitive. The bake cannot
un-limit a limited recording - re-export that loop with more dynamic range, or
`trim` it to the part that sounds right, if it still sounds crushed.

## Completing a declaration

A bank rarely states every knob. That is convenient to write and impossible to
check by reading, so:

    python carbank_bake.py bank.json --complete --dry-run   # list what is missing
    python carbank_bake.py bank.json --complete             # write it in

It only *adds*: a value already in the file is never touched, a bare path becomes
the long form, and a sample the bank does not declare stays undeclared (the bake
warns about those, it does not invent them). Re-baking afterwards is a no-op -
that is the guarantee, and `test_sound_knobs.py` asserts it by baking a minimal
declaration and a completed one and comparing the bytes.

## Budgets

- **Rate**: `sounds.max_rate`, default **16000 Hz** - retail's own car banks are
  16000, and the SPU budget is sized for it. The SPU itself takes
  4000..44100. `engine_pct` / `idle_pct` still scale the source rate the way the
  Driver 1 beta knobs do.
- **Size**: `sounds.sample_bytes`, default **16384 per sample**.
  `LoadSoundBankDynamic` advances the SPU write address by the payload with no
  bounds check, so an oversized bank corrupts SPU RAM rather than being refused
  (on this build: an access violation a few frames later, with a dump that
  points somewhere useless). Retail car banks are 2-4 KiB per car. A longer
  sample is trimmed and the bake prints the trim.

## Verifying

    python carbank_bake.py bank.json -o out ...        # bake (prints the byte cost)
    python wav2vag.py sounds/engine.wav -o e.vag --loop --level 0.8   # one file
    play_chicago.bat test 250                          # headless take-a-drive

`wav2vag.py` takes the same knobs in the same order, so a single file can be
auditioned with the exact settings the bake will use. Then grep the log for

    gamesnd: resident slot 5 uses the imported model bank (FIREBIRD model 1) -> block 7

which is the engine saying it took the bank's samples for that slot. If the line
is absent, either the segment is malformed (`carbank_bake` would have refused it)
or the model slot is not an imported one.
