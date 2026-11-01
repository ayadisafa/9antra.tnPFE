"""Background music for the videos, composed here (numpy, no file from anywhere, so nothing to license): a chord
progression played by a soft pad, a piano-like arpeggio, a bass and light drums, in three moods, as long as the video,
with a fade at each end. The admin can give their own track instead (they must have the right to use it).
"""
from __future__ import annotations

import io
import wave

import numpy as np

RATE = 44100
MOODS = {
    "inspiring": {"name": "Inspiring", "bpm": 92, "chords": [(60, "maj"), (55, "maj"), (57, "min"), (53, "maj")], "drums": "soft",
                  "arp": [0, 1, 2, 3, 2, 1, 2, 3], "step": 0.5},
    "upbeat": {"name": "Upbeat", "bpm": 116, "chords": [(57, "min"), (53, "maj"), (60, "maj"), (55, "maj")], "drums": "full",
               "arp": [0, 2, 1, 3, 0, 2, 1, 3, 0, 2, 1, 3, 0, 2, 1, 3], "step": 0.25},
    "calm": {"name": "Calm", "bpm": 72, "chords": [(53, "maj"), (60, "maj"), (62, "min"), (58, "maj")], "drums": None,
             "arp": [0, 1, 2, 1], "step": 1.0},
}
_QUALITY = {"maj": [0, 4, 7, 12], "min": [0, 3, 7, 12]}


def _hz(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def _lowpass(signal: np.ndarray, cutoff: float) -> np.ndarray:
    from scipy.signal import butter, sosfilt

    return sosfilt(butter(2, cutoff, "low", fs=RATE, output="sos"), signal)


def _highpass(signal: np.ndarray, cutoff: float) -> np.ndarray:
    from scipy.signal import butter, sosfilt

    return sosfilt(butter(2, cutoff, "high", fs=RATE, output="sos"), signal)


def _bandpass(signal: np.ndarray, low: float, high: float) -> np.ndarray:
    from scipy.signal import butter, sosfilt

    return sosfilt(butter(2, [low, high], "band", fs=RATE, output="sos"), signal)


def _envelope(length: int, attack: float, release: float) -> np.ndarray:
    env = np.ones(length)
    a, r = max(1, int(attack * RATE)), max(1, int(release * RATE))
    env[:a] = np.linspace(0, 1, a)
    if r < length:
        env[-r:] *= np.linspace(1, 0, r)
    return env


def _add(track: np.ndarray, start: float, sound: np.ndarray, gain: float = 1.0) -> None:
    begin = int(start * RATE)
    if begin >= len(track):
        return
    end = min(len(track), begin + len(sound))
    track[begin:end] += gain * sound[: end - begin]


def _piano(midi: float, length: float) -> np.ndarray:
    t = np.arange(int(length * RATE)) / RATE
    base = _hz(midi)
    tone = np.sin(2 * np.pi * base * t) + 0.45 * np.sin(4 * np.pi * base * t) * np.exp(-t * 3) + 0.2 * np.sin(6 * np.pi * base * t) * np.exp(-t * 5)
    return tone * np.exp(-t * 2.4) * _envelope(len(t), 0.004, 0.08)


def _pad(notes: list[float], length: float) -> np.ndarray:
    t = np.arange(int(length * RATE)) / RATE
    sound = np.zeros_like(t)
    for midi in notes:
        for detune in (-0.06, 0.0, 0.07):
            base = _hz(midi + detune)
            for harmonic in range(1, 7):                 # a soft saw: few harmonics, then filtered
                sound += np.sin(2 * np.pi * base * harmonic * t + harmonic) / harmonic / 3
    sound = _lowpass(sound, 1500)
    return sound * _envelope(len(t), 0.45, 0.7)


def _bass(midi: float, length: float) -> np.ndarray:
    t = np.arange(int(length * RATE)) / RATE
    base = _hz(midi)
    tone = np.tanh(1.6 * (np.sin(2 * np.pi * base * t) + 0.3 * np.sin(4 * np.pi * base * t)))
    return tone * np.exp(-t * 1.6) * _envelope(len(t), 0.006, 0.05)


def _kick() -> np.ndarray:
    t = np.arange(int(0.35 * RATE)) / RATE
    pitch = 45 + 85 * np.exp(-t * 28)
    return np.sin(2 * np.pi * np.cumsum(pitch) / RATE) * np.exp(-t * 11)


def _clap(noise: np.random.Generator) -> np.ndarray:
    t = np.arange(int(0.22 * RATE)) / RATE
    return _bandpass(noise.standard_normal(len(t)), 900, 3200) * np.exp(-t * 22)


def _hat(noise: np.random.Generator) -> np.ndarray:
    t = np.arange(int(0.06 * RATE)) / RATE
    return _highpass(noise.standard_normal(len(t)), 7000) * np.exp(-t * 70)


def _reverb(signal: np.ndarray) -> np.ndarray:
    """A small room: a few feedback delays, mixed back quietly."""
    out = np.zeros_like(signal)
    for delay, gain in ((0.0297, 0.62), (0.0371, 0.58), (0.0411, 0.55), (0.0437, 0.52)):
        step = int(delay * RATE)
        buffer = signal.copy()
        for repeat in range(1, 9):
            shift = step * repeat
            if shift >= len(signal):
                break
            buffer[shift:] += signal[:-shift] * gain ** repeat
        out += buffer
    return _lowpass(out / 4, 5000)


def compose(seconds: float, mood: str = "inspiring", seed: int = 7) -> np.ndarray:
    """The track as a (samples, 2) array between -1 and 1."""
    spec = MOODS.get(mood) or MOODS["inspiring"]
    beat = 60.0 / spec["bpm"]
    bar = beat * 4
    total = int(seconds * RATE) + RATE
    pad, keys, bass, drums = (np.zeros(total) for _ in range(4))
    noise = np.random.default_rng(seed)
    kick, hat = _kick(), _hat(noise)
    bars = int(seconds / bar) + 2
    for index in range(bars):
        root, quality = spec["chords"][index % len(spec["chords"])]
        notes = [root + step for step in _QUALITY[quality]]
        start = index * bar
        _add(pad, start, _pad([note - 12 for note in notes[:3]], bar + 0.6), 0.5)
        for position, which in enumerate(spec["arp"]):
            when = start + position * spec["step"] * beat
            if when >= start + bar:
                break
            _add(keys, when, _piano(notes[which] + 12, 1.2), 0.32)
        for hit in (0, 2) if spec["drums"] != "full" else (0, 1, 2, 3):
            _add(bass, start + hit * beat, _bass(root - 24, beat * (2 if spec["drums"] != "full" else 1)), 0.45)
        if spec["drums"] and index >= (1 if spec["drums"] == "full" else 2):
            for hit in range(4):
                if spec["drums"] == "full" or hit in (0, 2):
                    _add(drums, start + hit * beat, kick, 0.8)
                if hit in (1, 3):
                    _add(drums, start + hit * beat, _clap(noise), 0.35 if spec["drums"] == "full" else 0.22)
                if spec["drums"] == "full":
                    _add(drums, start + hit * beat + beat / 2, hat, 0.18)
    wet = _reverb(pad + keys)
    left = 0.8 * pad + 0.95 * keys + 0.9 * bass + drums + 0.35 * wet
    right = 0.8 * np.roll(pad, int(0.011 * RATE)) + 0.85 * np.roll(keys, int(0.007 * RATE)) + 0.9 * bass + drums + 0.35 * np.roll(wet, int(0.013 * RATE))
    mix = np.stack([left, right], axis=1)[: int(seconds * RATE)]
    mix = np.tanh(mix / (np.max(np.abs(mix)) + 1e-9) * 1.3)          # gentle limiting
    mix *= 0.89 / (np.max(np.abs(mix)) + 1e-9)                       # about -1 dBFS
    fade_in, fade_out = int(0.4 * RATE), int(min(2.0, seconds / 4) * RATE)
    mix[:fade_in] *= np.linspace(0, 1, fade_in)[:, None]
    mix[-fade_out:] *= np.linspace(1, 0, fade_out)[:, None]
    return mix


def wav_bytes(samples: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
    return buffer.getvalue()


def write(path, seconds: float, mood: str = "inspiring") -> None:
    with open(path, "wb") as handle:
        handle.write(wav_bytes(compose(seconds, mood)))
