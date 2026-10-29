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
