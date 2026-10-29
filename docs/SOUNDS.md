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

The fourth field is the **declared rate in Hz**, not a pitch: `sound.c`
`CompleteSoundSetup` computes `attr.pitch = MIN(samplerate * pitch / 44100, 16383)`,
so the game's own pitch request is multiplied by it. The declaration's `pitch`
knob scales this field and nothing else (see "Pitch" below).

`loop` in the header is a **boolean** (`sound.c`: `chan->flags |= samp->loop ?
CHAN_LOOP : 0`), not a loop point - the loop points live in the VAG block flags.
`LUMP_CAR_SIRENS` (202) is 13 flags, bit N = model N is a cop car;
`models.c GetCarImportSiren` reads that, `CarHasSiren` uses it.

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

Measured on this pipeline (half-second tones, NOISE_SHAPING on): a 440 Hz
sine encodes at 58.1 dB SNR at 16 kHz and a full-bandwidth sawtooth at
20.2 dB — the difference is content, not the encoder, and it is the single
most useful thing to know before authoring (see "Sound properties").
Deterministic: the same WAV always produces the same bytes, which is what
lets FIREBIRD.json name a WAV and still rebuild the bank exactly.

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
              "rate": null, "rate_pct": 100, "pitch": 1.0,
              "loop": "auto", "loop_from": 0.0, "loop_to": null,
              "trim": null, "fade_out": 0.0 },
  "idle":   { "wav": "sounds/idle.wav" },
  "horn":   { "wav": "sounds/horn.wav", "loop": false },
  "cop": false,
  "engine_pct": 100, "idle_pct": 100, "loop_from": 0.0,
  "peak": 0.999, "rms": 0.34, "max_rate": 16000, "sample_bytes": 16384
}
```

`engine` is shown in the long form `--complete` writes (the other two gain the
same keys when it runs); `idle` and `horn` show the short form, which is what a
hand-written bank usually looks like. `loop: "auto"` and `pitch` are worth
setting by hand - they are the two knobs whose right value depends on the
sample, and both are explained below.

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

They are applied in this order, and the order is audible:

    dc offset -> resample -> trim -> fade_out -> gain_db -> level -> byte budget
    -> loop points -> encode

Trimming before the fade puts the fade on the new tail; the fade before the level
stops it dragging the peak down and making the level scale everything else up;
the level last means `gain_db` is a relative trim against a fixed target.

## Sound properties: rate, bitrate, duration

The format is 4-bit ADPCM at a fixed 16 bytes per 28 samples, so **one number
decides two costs**: the sample rate decides both the pitch and the file size.

    bytes per second = rate / 2          kbit/s = rate / 250
    seconds available = bytes / (rate / 2)

| rate | bytes/s | bitrate | longest sample in 16,384 B | when to use it |
| --- | --- | --- | --- | --- |
| 8000 Hz | 4,000 | 32 kbit/s | 4.10 s | speech, a dull horn, a bass-only loop |
| 11025 Hz | 5,512 | 44 kbit/s | 2.97 s | a muffled sample where bytes matter |
| **16000 Hz** | **8,000** | **64 kbit/s** | **2.05 s** | **the default, and what retail uses** |
| 22050 Hz | 11,025 | 88 kbit/s | 1.49 s | a bright sample that will be pitched DOWN by the game |
| 44100 Hz | 22,050 | 176 kbit/s | 0.74 s | almost never: the budget buys under a second |

The whole bank: three samples per car, so `3 x bytes` per car. The dynamic sound
region the engine hands out is **412,672 bytes**, shared with the level's own
dynamic banks, and the metadata table holds **64 samples** in total. With N
imported cars resident, a sample can average `412672 / (3N)` bytes - 137 KB for
one car (no constraint in practice), but only ~8.6 KB for sixteen resident slots.
So the 16,384-byte cap is a ceiling on a single sample, not a budget to spend
sixteen times. Three well-behaved samples spend ~24 KB, which is retail's own
order of magnitude.

### Frequency: content decides, not the number

Raising the rate only helps if there is something up there to keep. Measured with
this encoder (`tools/common/vag.py`, `NOISE_SHAPING` on) on half-second test tones:

| content | 8000 Hz | 11025 Hz | 16000 Hz |
| --- | --- | --- | --- |
| 440 Hz sine | 43.4 dB | 49.1 dB | **58.1 dB** |
| full-bandwidth sawtooth | 19.5 dB | 19.9 dB | 20.2 dB |

Two things to take from that:

- **A bright, buzzy source cannot be rescued by a higher rate.** A 4-bit
  quantiser running out of headroom loses ~38 dB on a sawtooth at *any* rate.
  Feed it band-limited material - a recorded engine, a filtered synth - and the
  rate does what you expect.
- **For a bass-heavy loop, higher rate is still better**, which is not obvious:
  at 8 kHz the loop measures 26 dB in band, at 16 kHz 34.7 dB (this is the
  Firebird's own engine loop). The rate is the sampling grid the *quantiser*
  works on; a finer grid tracks the waveform more closely even when the content
  is low.

Nyquist still applies to what you keep: nothing above `rate / 2` survives, and
the resampler that gets you there is linear, so decide the rate from the content
you have. The Firebird's engine loop has 91% of its energy below 500 Hz and
nothing above 6 kHz, so 16 kHz is generous - it was chosen for the encoder, not
for the bandwidth.

**The pitch the game will ask for counts as part of the frequency.** `gamesnd.c`
plays the rev loop at 0.37x-0.62x of the declared rate, so its content lands two
octaves down: a 16 kHz sample with a 148 Hz fundamental comes out at 55-92 Hz,
which most speakers cannot reproduce - all that is left is the encoder's noise.
That is what the `pitch` knob is for (see below): declare `2.0` and the same
bytes play 108-183 Hz. Author for the pitch you will hear, not for the file.

### Duration: loops want a second or more

- **Loops**: the ear notices a short cycle long before it notices a long one. Aim
  for **>= 1 s** (8,000 samples at 16 kHz, 8 KB). The Firebird's engine loop is
  0.85 s and its idle 1.40 s with `loop: "auto"`, close to retail's 1.67 s.
- **The attack is spent once**: `loop_from` (or `loop: "auto"`, which searches)
  keeps the first fraction of a second out of the loop. A 60-80 ms attack costs
  ~600 bytes and stops every cycle from restarting on a transient.
- **One-shots** (horn, siren) can be short - 0.2-0.4 s is plenty (1.6-3.2 KB),
  and a `fade_out` of 20-50 ms stops the cut clicking.
- **Trim before you shrink the rate.** `trim: [start, end]` drops silence at the
  original quality; halving the rate to fit a budget costs you the top octave
  instead.

Sizing a car by hand, at 16 kHz (8,000 bytes/s):

| sample | wanted | bytes |
| --- | --- | --- |
| engine loop | 1.0 s loop + 0.1 s attack | 8,800 |
| idle loop | 1.5 s | 12,000 |
| horn one-shot | 0.4 s | 3,200 |
| **per car** | | **~24,000 (24 KB)** |

For scale, retail's own three-sample car bank is **29,008 bytes**, and the Firebird
lands near 24-28 KB depending on how long its loops are trimmed. Anything in that
band is normal; a 16 KB sample on all three slots is not (see the region budget
above).

## Level: peak is the ceiling, rms is the loudness

`JERICHO_SOUND_DIAG=1` makes `sound.c` log every bank as it loads - the level's
own car banks and an imported one both come through `LoadSoundBank`/
`LoadSoundBankDynamic`, so the comparison is in the units the mixer sees:

    sound level: bank 3 samples 3 payload 29008 rate 16000 loop 1 peak 32749 (100%) rms 9557

Measured on CHICAGO's own car banks: **peak 32749-32767 (100%), rms 9557-12629
(29-39% of full scale), crest factor 2.6-3.4**. Their three-sample car bank is
29,008 bytes, so retail is not thrifty: it spends what the samples need at
16 kHz.

Peak alone cannot set a level, and treating it as if it can is what made the
Firebird sound loud and crushed. Its engine loop measures **peak 99% with rms
49%** - crest 2.0 - because the recording is already limited. Pushed to 100% peak
it sat ten points above retail's rms, about **+3 dB**, on a waveform whose
transients were already flat. Loudness, not peak, is what a listener compares.

So the gain is the smaller of two moves: reach the `rms` target, or reach the
`peak` ceiling. What that does depends only on the source:

| source shape | crest | what the bake does | the bake says |
| --- | --- | --- | --- |
| retail-like, already at level | 2.6-3.4 | nothing: it is inside both bands | nothing |
| limited/hot (crest collapsed) | ~2.0 | pulls it DOWN to the rms target | `levelled x0.69 (-3.2 dB)` |
| dynamic but quiet | 3.5+ | lifts it until the peak ceiling stops it | `levelled x1.4 (+2.9 dB)` |
| clipped in the source | any | cannot help; says so | `the source is clipped already` |

Lowering `rms` below retail's band (29-39%) is a legitimate choice - it makes the
car quieter than the level's own traffic - but it is a mix decision, not a fix for
a sample that sounds crushed. `rms: 0` disables loudness matching entirely and
leaves peak-only levelling.

DC offset is removed first, because an offset is silent but eats headroom. The
bake also reports a source that is clipped already, and says so when a sample's
crest factor is under 2.2: no level can undo a limited recording, and normalising
it only makes the crush louder. If a loop still sounds squashed, re-export it
with more dynamic range - that is the only real fix.

### The level dead band

`normalise` leaves a sample alone when it is already within **6%** of the target.
That is not laziness: most car sounds are decoded VAGs, and a decoded PSX sample
sits exactly ON the ADPCM lattice. Scaling it by 3% moves every value off the
lattice and forces a re-quantisation, which measures ~30 dB instead of ~76 dB.
On the Firebird's own samples the gains the target would have applied were 1.7%,
3.3% and 5.2% - all inside the band, so all three are used as they are, and the
idle went from 32.6 dB to 79.3 dB. `force: true` when the exact level is the point.

## Pitch: the game asks for less than the recording

`gamesnd.c` drives a car's two loops by a pitch that depends on revs:

    pitch = revs / 4 + 4096   (idle)     -> ~1.0x  .. 1.25x of the declared rate
    pitch = revs / 4 + 1500   (revving)  -> ~0.37x .. 0.62x

so a rev loop declared at the rate it was recorded at plays two octaves down when
the car is moving, and what is left in the audible band is the encoder's noise -
the "driving on gravel" sound.

`pitch` multiplies the rate FIELD only: the bytes are untouched, nothing is
re-encoded, and the declared rate in the file stays honest. The scale factor is
`1 / (pitch the game will ask for)`:

| `pitch` | rev loop plays at | a 148 Hz fundamental comes out at |
| --- | --- | --- |
| `1.0` (default) | 0.37x-0.62x | 55-92 Hz - below what most speakers reproduce |
| **`2.0`** | **0.73x-1.23x** | **108-183 Hz** |
| `2.7` | 1.0x-1.68x | 148-249 Hz - the recording itself at low revs |

`2.0` is the useful default for a rev loop: it centres the sweep on the recording
and stays inside a natural engine range. The idle sample is asked for at ~1.0x and
needs no pitch at all, so set it per sample, never bank-wide. Judge it by ear
before shipping - this is the one setting in this document that is a matter of
taste rather than measurement.

## Loops have to meet

The SPU cannot crossfade: the loop wraps from the end of the block stream back to
the marker, so the waveform must join itself. `loop: "auto"` searches
block-aligned pairs and keeps the one whose join is quietest (value AND slope),
trimming the tail to it. On the Firebird that took the engine's join from a jump
of 10,479 counts to 26 (228 once quantised) and the idle's from 2,480 to 441. A
join of a few hundred out of 32,767 is inaudible; the old one was 22% of full
scale, once per cycle.

`loop_to` ends the loop early, `loop_from` starts it late, and the encoder marks
the start on a block that is **not** the first (PsyX derives the loop start as
`k + 26` after decoding and drops points too close to zero).

## Encoder noise

The block encoder feeds its own quantisation error forward (`NOISE_SHAPING`),
which is decoder-compatible - only the choice of nibble changes. Measured on the
Firebird's engine loop, SNR by band:

| shaping | 0-1 kHz | 1-3 kHz | 3-8 kHz |
| --- | --- | --- | --- |
| 0.0 | 40.8 dB | 24.6 dB | 15.2 dB |
| **0.5** | **42.7 dB** | **26.4 dB** | 13.0 dB |
| 0.7 | 43.6 dB | 26.9 dB | 11.8 dB |

0.5 is the default: about +2 dB where an engine lives, about -2 dB above 3 kHz
where these samples have almost no content. A sample already on the ADPCM lattice
(a decoded VAG) is unaffected either way, which is why the idle measures 78-81 dB
across every band regardless.

## Budgets

- **Rate**: `sounds.max_rate`, default **16000 Hz** - retail's own car banks are
  16000. The SPU accepts 4000..44100. `engine_pct` / `idle_pct` still scale the
  source rate the way the Driver 1 beta knobs do.
- **Size**: `sounds.sample_bytes`, default **16384 per sample**.
  `LoadSoundBankDynamic` advances the SPU write address by the payload with no
  bounds check, so an oversized bank corrupts SPU RAM rather than being refused
  (on this build: an access violation a few frames later, with a dump that points
  somewhere useless). A longer sample is trimmed and the bake prints the trim.
  See "Sound properties" above for what that buys in seconds.

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

## Authoring checklist

1. **Content first**: band-limited, not limited-to-death. A crest factor under
   2.2 means the transients are gone before you start.
2. **Rate**: 16 kHz unless you have a reason; drop to 8-11 kHz only for a dull
   sample that has to be small. Nothing above half the rate survives.
3. **Duration**: loop >= 1 s, one-shots 0.2-0.4 s, and trim silence rather than
   lowering the rate.
4. **Loop**: `"loop": "auto"` and read the seam the bake reports. Over ~2,000 is
   a click you will hear.
5. **Level**: leave `rms` at 0.34 and `peak` at 0.999; use `gain_db` or
   `normalise: false` for taste. Do not reach for `force` unless you need the
   exact number, because it costs the near-lossless re-encode.
6. **Pitch**: if the sample is the rev loop, declare `pitch` so the game's
   0.37x-0.62x request lands where you recorded it - see the table above.
7. **Check it in the game**: `carbank_bake --check`, then a headless drive, then
   grep for `gamesnd: resident slot N uses the imported model bank`.

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
