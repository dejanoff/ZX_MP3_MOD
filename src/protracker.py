"""ProTracker MOD hardware constants, period tables, and timing calculations.

Based on Commodore Amiga PAL Paula hardware specifications and ProTracker 2.x standard.
Targeted specifically for ZX Spectrum + General Sound (GS) + Wild Player.
"""

from typing import Dict, Tuple, Optional

# Commodore Amiga PAL Paula DMA clock frequency (Hz)
PAL_CLOCK: float = 3546895.0

# Standard ProTracker note periods (Finetune = 0)
# 3 Octaves, 36 chromatic notes
PERIOD_TABLE: Dict[str, int] = {
    # Octave 1
    "C-1": 856, "C#1": 808, "D-1": 762, "D#1": 720,
    "E-1": 678, "F-1": 640, "F#1": 604, "G-1": 570,
    "G#1": 538, "A-1": 508, "A#1": 480, "B-1": 453,
    # Octave 2
    "C-2": 428, "C#2": 404, "D-2": 381, "D#2": 360,
    "E-2": 339, "F-2": 320, "F#2": 302, "G-2": 285,
    "G#2": 269, "A-2": 254, "A#2": 240, "B-2": 226,
    # Octave 3
    "C-3": 214, "C#3": 202, "D-3": 190, "D#3": 180,
    "E-3": 170, "F-3": 160, "F#3": 151, "G-3": 143,
    "G#3": 135, "A-3": 127, "A#3": 120, "B-3": 113,
}

# Reverse mapping: period -> note name
PERIOD_TO_NOTE: Dict[int, str] = {period: note for note, period in PERIOD_TABLE.items()}

# Predefined quality presets mapping to standard tracker notes:
# LOW ≈ 6223 Hz (Note G-1)
# NORMAL ≈ 8287 Hz (Note C-2 - classic tracker sample base pitch)
# C#2 ≈ 8779 Hz (Note C#2)
# OPTIMAL ≈ 9309 Hz (Note D-2 - Recommended for General Sound: crisp & stutter-free)
# D#2 ≈ 9852 Hz (Note D#2)
# E-2 ≈ 10463 Hz (Note E-2)
# HIGH ≈ 11084 Hz (Note F-2)
QUALITY_PRESETS: Dict[str, Tuple[str, int]] = {
    "LOW": ("G-1", 570),         # ~6222.62 Hz
    "NORMAL": ("C-2", 428),      # ~8287.14 Hz
    "CS-2": ("C#2", 404),        # ~8779.44 Hz
    "C#2": ("C#2", 404),         # ~8779.44 Hz
    "OPTIMAL": ("D-2", 381),     # ~9309.44 Hz (Golden mean for GS!)
    "D-2": ("D-2", 381),         # ~9309.44 Hz
    "DS-2": ("D#2", 360),        # ~9852.49 Hz
    "D#2": ("D#2", 360),         # ~9852.49 Hz
    "E-2": ("E-2", 339),         # ~10462.82 Hz
    "HIGH": ("F-2", 320),        # ~11084.05 Hz
}

# ProTracker format limits
MAX_SAMPLES: int = 31
MAX_SAMPLE_LENGTH_WORDS: int = 65535
MAX_SAMPLE_LENGTH_BYTES: int = MAX_SAMPLE_LENGTH_WORDS * 2  # 131070 bytes
ROWS_PER_PATTERN: int = 64
CHANNELS_COUNT: int = 4
HEADER_SIZE: int = 1084
PATTERN_SIZE: int = ROWS_PER_PATTERN * CHANNELS_COUNT * 4  # 1024 bytes
SIGNATURE_MK: bytes = b"M.K."

# Default tracker tempo
DEFAULT_SPEED: int = 6   # ticks per row
DEFAULT_BPM: int = 125   # beats per minute (CIA standard, gives 50 Hz tick rate)


def period_to_frequency(period: int, clock: float = PAL_CLOCK) -> float:
    """Calculate exact Amiga Paula playback frequency for a given period."""
    if period <= 0:
        return 0.0
    return clock / period


def frequency_to_nearest_note_period(freq: float, clock: float = PAL_CLOCK) -> Tuple[str, int, float]:
    """Find the nearest standard note and period for a desired frequency.

    Returns:
        (note_name, period, actual_frequency)
    """
    if freq <= 0:
        return "C-2", 428, period_to_frequency(428, clock)

    ideal_period = clock / freq
    # Find closest period in PERIOD_TABLE
    best_note = "C-2"
    best_period = 428
    best_diff = float("inf")

    for note, period in PERIOD_TABLE.items():
        diff = abs(period - ideal_period)
        if diff < best_diff:
            best_diff = diff
            best_note = note
            best_period = period

    actual_freq = period_to_frequency(best_period, clock)
    return best_note, best_period, actual_freq


def get_tick_rate(bpm: int = DEFAULT_BPM) -> float:
    """Calculate CIA tick frequency in Hz for a given BPM.

    For BPM = 125, tick rate is (125 * 2) / 5 = 50.0 Hz.
    """
    return (bpm * 2.0) / 5.0


def get_row_duration(speed: int = DEFAULT_SPEED, bpm: int = DEFAULT_BPM) -> float:
    """Calculate row duration in seconds.

    For Speed = 6, BPM = 125:
    row_duration = 6 / 50.0 = 0.12 seconds (120 ms).
    """
    tick_rate = get_tick_rate(bpm)
    return speed / tick_rate


def get_pattern_duration(rows: int = ROWS_PER_PATTERN, speed: int = DEFAULT_SPEED, bpm: int = DEFAULT_BPM) -> float:
    """Calculate duration of a pattern in seconds.

    For 64 rows, Speed = 6, BPM = 125:
    pattern_duration = 64 * 0.12 = 7.68 seconds.
    """
    return rows * get_row_duration(speed, bpm)


def encode_cell(sample_num: int, period: int, effect_cmd: int = 0, effect_param: int = 0) -> bytes:
    """Encode a single 4-byte ProTracker cell.

    Structure:
    Byte 0: (sample_num & 0xF0) | ((period >> 8) & 0x0F)
    Byte 1: period & 0xFF
    Byte 2: ((sample_num & 0x0F) << 4) | (effect_cmd & 0x0F)
    Byte 3: effect_param & 0xFF
    """
    b0 = (sample_num & 0xF0) | ((period >> 8) & 0x0F)
    b1 = period & 0xFF
    b2 = ((sample_num & 0x0F) << 4) | (effect_cmd & 0x0F)
    b3 = effect_param & 0xFF
    return bytes([b0, b1, b2, b3])


def decode_cell(data: bytes) -> Tuple[int, int, int, int]:
    """Decode a 4-byte ProTracker cell into (sample_num, period, effect_cmd, effect_param)."""
    if len(data) != 4:
        raise ValueError("ProTracker cell must be exactly 4 bytes")
    b0, b1, b2, b3 = data[0], data[1], data[2], data[3]
    sample_num = (b0 & 0xF0) | ((b2 >> 4) & 0x0F)
    period = ((b0 & 0x0F) << 8) | b1
    effect_cmd = b2 & 0x0F
    effect_param = b3
    return sample_num, period, effect_cmd, effect_param
