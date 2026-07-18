"""Synthesize the placeholder SFX pack into web/host/assets/sfx/.

Every clip referenced by an `sfx` key in config/moves.yaml or the
`ui.audio.events_sfx` block in config/settings.yaml is generated here as a
small mono 16-bit WAV. The clips are synthesized from scratch (stdlib only,
seeded RNG) so they are CC0-by-construction and the repo stays fully offline.

To upgrade to a curated pack (Kenney.nl, Mixkit, Freesound — CC0): drop the
replacement files over the same names, or point the YAML sfx keys at new
names. Zero code changes either way.

Run: python scripts/make_sfx.py
"""

from __future__ import annotations

import math
import random
import struct
import wave
from pathlib import Path

RATE = 22050
OUT_DIR = Path(__file__).parent.parent / "web" / "host" / "assets" / "sfx"
rng = random.Random(42)


# ---------------------------------------------------------------------------
# building blocks — every generator returns a list[float] in [-1, 1]
# ---------------------------------------------------------------------------


def silence(seconds: float) -> list[float]:
    return [0.0] * int(RATE * seconds)


def tone(freq, seconds, shape="sine", glide_to=None, vibrato_hz=0.0, vibrato_depth=0.0):
    """One oscillator note. `glide_to` slides the pitch linearly; vibrato wobbles it."""
    n = int(RATE * seconds)
    out, phase = [], 0.0
    for i in range(n):
        t = i / n
        f = freq + (glide_to - freq) * t if glide_to is not None else freq
        if vibrato_hz:
            f *= 1.0 + vibrato_depth * math.sin(2 * math.pi * vibrato_hz * i / RATE)
        phase += 2 * math.pi * f / RATE
        if shape == "sine":
            out.append(math.sin(phase))
        elif shape == "saw":
            out.append((phase / math.pi) % 2 - 1)
        else:  # square
            out.append(1.0 if math.sin(phase) >= 0 else -1.0)
    return out


def noise(seconds: float) -> list[float]:
    return [rng.uniform(-1, 1) for _ in range(int(RATE * seconds))]


def lowpass(samples: list[float], alpha: float) -> list[float]:
    """One-pole lowpass; smaller alpha = darker sound."""
    out, y = [], 0.0
    for s in samples:
        y += alpha * (s - y)
        out.append(y)
    return out


def envelope(samples, attack=0.005, decay=None, curve=3.0):
    """Attack ramp then exponential-ish decay over the rest of the clip."""
    n = len(samples)
    a = max(1, int(RATE * attack))
    d = n - a if decay is None else max(1, int(RATE * decay))
    out = []
    for i, s in enumerate(samples):
        if i < a:
            g = i / a
        else:
            g = max(0.0, 1.0 - (i - a) / d) ** curve
        out.append(s * g)
    return out


def mix(*layers: list[float]) -> list[float]:
    n = max(len(x) for x in layers)
    return [sum(x[i] for x in layers if i < len(x)) for i in range(n)]


def concat(*parts: list[float]) -> list[float]:
    out: list[float] = []
    for p in parts:
        out.extend(p)
    return out


def gain(samples: list[float], g: float) -> list[float]:
    return [s * g for s in samples]


def write_wav(name: str, samples: list[float]) -> None:
    peak = max(1e-9, max(abs(s) for s in samples))
    scale = 0.85 / peak  # normalize with headroom
    data = b"".join(struct.pack("<h", int(max(-1.0, min(1.0, s * scale)) * 32767)) for s in samples)
    with wave.open(str(OUT_DIR / f"{name}.wav"), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(RATE)
        f.writeframes(data)


# ---------------------------------------------------------------------------
# the clips
# ---------------------------------------------------------------------------


def bell(freq: float, seconds: float) -> list[float]:
    """Inharmonic partials = metallic bell strike."""
    partials = [(1.0, 1.0), (2.76, 0.6), (5.4, 0.35), (8.9, 0.2)]
    layers = [gain(tone(freq * m, seconds), g) for m, g in partials]
    return envelope(mix(*layers), attack=0.002, curve=4.0)


def taps(count: int, spacing: float, tap_len: float = 0.02, accel: float = 1.0):
    """A run of short noise taps (footsteps, drumroll); accel < 1 speeds them up."""
    out: list[float] = []
    gap = spacing
    for i in range(count):
        out.extend(envelope(lowpass(noise(tap_len), 0.25), attack=0.001, curve=2.0))
        out.extend(silence(max(0.005, gap)))
        gap *= accel
    return out


def build() -> dict[str, list[float]]:
    clips: dict[str, list[float]] = {}

    # --- move clips -------------------------------------------------------
    # punch: noise crack over a low sine thump
    clips["punch"] = envelope(
        mix(gain(lowpass(noise(0.16), 0.35), 0.8), tone(90, 0.16, glide_to=45)),
        attack=0.002,
        curve=4.0,
    )
    # whoosh: dark noise sweep with a slow attack
    clips["whoosh"] = envelope(lowpass(noise(0.32), 0.12), attack=0.08, curve=2.0)
    # zap: falling square chirp
    clips["zap"] = envelope(tone(1400, 0.22, shape="square", glide_to=180), attack=0.002)
    # boom: rumbling low noise + sub drop
    clips["boom"] = envelope(
        mix(gain(lowpass(noise(0.7), 0.06), 1.2), tone(70, 0.7, glide_to=30)),
        attack=0.004,
        curve=2.5,
    )
    # splat: wet noise burst with a pitch-wobble body
    clips["splat"] = envelope(
        mix(
            gain(lowpass(noise(0.28), 0.3), 0.7),
            tone(220, 0.28, glide_to=80, vibrato_hz=30, vibrato_depth=0.2),
        ),
        attack=0.003,
        curve=3.0,
    )
    # slurp: rising wobbly glide
    clips["slurp"] = envelope(
        tone(160, 0.45, glide_to=650, vibrato_hz=12, vibrato_depth=0.08),
        attack=0.05,
        curve=1.5,
    )
    # growl: low detuned saws, wobbling
    clips["growl"] = envelope(
        mix(
            tone(85, 0.45, shape="saw", vibrato_hz=7, vibrato_depth=0.12),
            gain(tone(113, 0.45, shape="saw", vibrato_hz=5, vibrato_depth=0.1), 0.7),
        ),
        attack=0.03,
        curve=2.0,
    )
    # grab: two quick thumps
    thump = envelope(
        mix(gain(lowpass(noise(0.09), 0.3), 0.6), tone(120, 0.09, glide_to=70)),
        attack=0.002,
        curve=3.0,
    )
    clips["grab"] = concat(thump, silence(0.06), thump)
    # sneaky: three soft low plucks (minor-ish tiptoe)
    plucks = [envelope(tone(f, 0.12), attack=0.002, curve=4.0) for f in (330, 392, 311)]
    clips["sneaky"] = concat(plucks[0], silence(0.04), plucks[1], silence(0.04), plucks[2])
    # charge: a rising rush — galloping noise over a climbing saw (CHARGE)
    clips["charge"] = envelope(
        mix(
            gain(lowpass(noise(0.42), 0.2), 0.6),
            tone(110, 0.42, glide_to=300, shape="saw"),
        ),
        attack=0.02,
        curve=1.6,
    )
    # shield: metallic ring
    clips["shield"] = bell(520, 0.45)
    # sparkle: fast ascending high arpeggio
    clips["sparkle"] = concat(
        *[envelope(tone(f, 0.09), attack=0.002, curve=3.0) for f in (880, 1175, 1568, 2093)]
    )
    # poof: soft dark puff
    clips["poof"] = envelope(lowpass(noise(0.3), 0.08), attack=0.01, curve=2.0)
    # steps: three quick footfall taps
    clips["steps"] = taps(3, spacing=0.12, tap_len=0.03)
    # honk: two-tone clown honk
    clips["honk"] = concat(
        envelope(tone(233, 0.16, shape="square", vibrato_hz=9, vibrato_depth=0.03), attack=0.005),
        envelope(tone(175, 0.2, shape="square", vibrato_hz=9, vibrato_depth=0.03), attack=0.005),
    )

    # --- event stingers -----------------------------------------------------
    # crowd_roar: big noise swell that hangs then fades
    swell = lowpass(noise(1.3), 0.18)
    n = len(swell)
    clips["crowd_roar"] = [
        s * (min(1.0, i / (n * 0.25)) * max(0.0, 1.0 - max(0.0, i - n * 0.5) / (n * 0.5)))
        for i, s in enumerate(swell)
    ]
    # sad_trombone: wah wah wah waaaah
    notes = [(311, 0.28), (294, 0.28), (277, 0.28), (262, 0.7)]
    clips["sad_trombone"] = concat(
        *[
            envelope(
                tone(f, d, shape="saw", vibrato_hz=6, vibrato_depth=0.04), attack=0.03, curve=1.2
            )
            for f, d in notes
        ]
    )
    # boing: a springy sproing — fast falling sine with heavy vibrato (REFLECT)
    clips["boing"] = envelope(
        tone(620, 0.4, glide_to=180, vibrato_hz=28, vibrato_depth=0.45, shape="sine"),
        attack=0.002,
        curve=2.5,
    )
    # comic_snap: a sharp crack + a rubbery pop (a trap springing)
    clips["comic_snap"] = concat(
        envelope(lowpass(noise(0.04), 0.5), attack=0.001, curve=3.0),
        envelope(tone(900, 0.09, glide_to=1700, shape="square"), attack=0.001, curve=2.2),
    )
    # ko_bell: boxing bell double-strike + crowd gasp
    gasp = gain(envelope(lowpass(noise(0.5), 0.15), attack=0.12, curve=1.5), 0.4)
    clips["ko_bell"] = mix(concat(bell(660, 0.5), bell(660, 0.9)), concat(silence(0.6), gasp))
    # air_horn: detuned saw stack, held
    clips["air_horn"] = envelope(
        mix(
            tone(440, 0.9, shape="saw"),
            tone(444, 0.9, shape="saw"),
            gain(tone(880, 0.9, shape="saw"), 0.5),
        ),
        attack=0.01,
        decay=0.85,
        curve=1.0,
    )
    # drumroll: accelerating taps into a crescendo
    roll = taps(28, spacing=0.055, tap_len=0.018, accel=0.93)
    n = len(roll)
    clips["drumroll"] = [s * (0.35 + 0.65 * i / n) for i, s in enumerate(roll)]
    # replay: tape-rewind warble
    clips["replay"] = envelope(
        tone(500, 0.55, glide_to=1500, vibrato_hz=18, vibrato_depth=0.25, shape="sine"),
        attack=0.01,
        curve=1.5,
    )
    return clips


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    clips = build()
    for name, samples in sorted(clips.items()):
        write_wav(name, samples)
        print(f"  {name}.wav  ({len(samples) / RATE:.2f}s)")
    print(f"{len(clips)} clips -> {OUT_DIR}")


if __name__ == "__main__":
    main()
