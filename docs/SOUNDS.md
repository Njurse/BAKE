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

## Level: measured against the game's own banks

`JERICHO_SOUND_DIAG=1` makes `sound.c` log the level of every bank as it loads -
the level's own car banks and an imported one both come through
`LoadSoundBank`/`LoadSoundBankDynamic`, so the comparison is in the units the
mixer sees:

    sound level: bank 3 samples 3 payload 29008 rate 16000 loop 1 peak 32749 (100%) rms 9557

Measured on CHICAGO's own car banks: **peak 32749-32767 (100%), rms 9557-12629
(29-39%), crest factor 2.6-3.4**. The bake normalises to `SOUND_PEAK = 0.999`
(peak), which is what retail does; the old 90% was quieter than retail, not
safer. The Firebird's samples now measure:

| sample | peak | rms | crest |
| --- | --- | --- | --- |
| idle | 100% | 33% | 3.0 (retail 2.6-3.4) |
| horn | 100% | 36% | 2.8 |
| engine | 99% | **49%** | **2.0** |

The engine loop is the outlier, and it is the source: `sounds/engine.wav` is
already limited (crest 2.0 means its peaks are flat-topped), so 4-bit ADPCM has
the least headroom exactly where the ear is most sensitive. The bake cannot
un-limit a limited recording - re-export that loop with more dynamic range (or
run it through the bake with less compression) if it still sounds crushed.

## Budgets

- **Rate**: capped at 11025 Hz (the SPU takes 4000..44100; car banks sound right
  lower). `sounds.engine_pct` / `idle_pct` still scale the source rate the way
  the Driver 1 beta knobs do.
- **Size**: 8 KiB per sample. `LoadSoundBankDynamic` advances the SPU write
  address by the payload with no bounds check, so an oversized bank corrupts SPU
  RAM rather than being refused. Retail car banks are 2-4 KiB per car. A longer
  sample is trimmed and the bake prints the trim.

## Verifying

    python carbank_bake.py bank.json -o out ...        # bake (prints the byte cost)
    play_chicago.bat test 250                          # headless take-a-drive

and grep the log for

    gamesnd: resident slot 5 uses the imported model bank (FIREBIRD model 1) -> block 7

which is the engine saying it took the bank's samples for that slot. If the line
is absent, either the segment is malformed (`carbank_bake` would have refused it)
or the model slot is not an imported one.
