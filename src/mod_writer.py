"""Classic ProTracker 4-channel MOD encoder and validator.

Implements the standard 1991 ProTracker M.K. format specification from scratch in pure Python.
Strictly compatible with Amiga Paula hardware and ZX Spectrum General Sound (GS) / Wild Player.
"""

import os
import re
import struct
from typing import List, Optional, Tuple, Dict, Any
from .protracker import (
    MAX_SAMPLES,
    MAX_SAMPLE_LENGTH_WORDS,
    MAX_SAMPLE_LENGTH_BYTES,
    ROWS_PER_PATTERN,
    CHANNELS_COUNT,
    HEADER_SIZE,
    PATTERN_SIZE,
    SIGNATURE_MK,
    encode_cell,
    decode_cell,
)


class ModValidationError(Exception):
    """Raised when MOD structure fails validation."""
    pass


CYRILLIC_TO_LATIN = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'yo',
    'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
    'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
    'ф': 'f', 'х': 'kh', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'shch',
    'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya',
    'А': 'A', 'Б': 'B', 'В': 'V', 'Г': 'G', 'Д': 'D', 'Е': 'E', 'Ё': 'Yo',
    'Ж': 'Zh', 'З': 'Z', 'И': 'I', 'Й': 'Y', 'К': 'K', 'Л': 'L', 'М': 'M',
    'Н': 'N', 'О': 'O', 'П': 'P', 'Р': 'R', 'С': 'S', 'Т': 'T', 'У': 'U',
    'Ф': 'F', 'Х': 'Kh', 'Ц': 'Ts', 'Ч': 'Ch', 'Ш': 'Sh', 'Щ': 'Shch',
    'Ъ': '', 'Ы': 'Y', 'Ь': '', 'Э': 'E', 'Ю': 'Yu', 'Я': 'Ya'
}


def to_ascii_safe(text: str) -> str:
    """Convert text to safe ASCII string, transliterating Cyrillic characters."""
    translit = "".join(CYRILLIC_TO_LATIN.get(c, c) for c in text)
    # Strip any characters outside printable ASCII range (32..126)
    safe_chars = [c if (32 <= ord(c) <= 126) else "_" for c in translit]
    return "".join(safe_chars)


def sanitize_latin_filename(name: str) -> str:
    """Transliterate Cyrillic characters to Latin and return a clean, safe filename."""
    translit = "".join(CYRILLIC_TO_LATIN.get(c, c) for c in name)
    cleaned = []
    for c in translit:
        if c.isalnum() or c in ("-", "_"):
            cleaned.append(c)
        elif c in (" ", ".", ","):
            cleaned.append("_")
    res = "".join(cleaned)
    res = re.sub(r"[-_]+", "_", res).strip("_-")
    return res or "output"


def generate_output_mod_path(
    input_path: str,
    output_dir: Optional[str] = None,
    sample_rate: int = 9309,
    output_mode: str = "ONE_CHANNEL",
    duration_sec: float = 60.0,
) -> str:
    """Generate an auto-named and auto-numbered Latin output path incorporating conversion settings.

    Format:
        {output_dir}/{LatinBaseName}_{Rate}Hz_{mode}_{Duration}s.mod
        If file exists:
        {output_dir}/{LatinBaseName}_{Rate}Hz_{mode}_{Duration}s_01.mod
    """
    if not output_dir:
        if input_path:
            output_dir = os.path.dirname(os.path.abspath(input_path))
        else:
            output_dir = os.getcwd()

    if input_path:
        base_name = os.path.splitext(os.path.basename(input_path))[0]
    else:
        base_name = "song"

    latin_base = sanitize_latin_filename(base_name)

    if "PING" in output_mode.upper():
        mode_tag = "pingpong"
    elif "ONE" in output_mode.upper():
        mode_tag = "mono"
    else:
        mode_tag = "centered"
    dur_tag = f"{int(duration_sec)}s" if duration_sec == int(duration_sec) else f"{duration_sec:.1f}s"
    rate_tag = f"{sample_rate}Hz"

    base_tag = f"{latin_base}_{rate_tag}_{mode_tag}_{dur_tag}"
    candidate_path = os.path.join(output_dir, f"{base_tag}.mod")

    if not os.path.exists(candidate_path):
        return os.path.abspath(candidate_path)

    # Auto-numbering if file already exists
    counter = 1
    while True:
        num_tag = f"{base_tag}_{counter:02d}.mod"
        candidate_path = os.path.join(output_dir, num_tag)
        if not os.path.exists(candidate_path):
            return os.path.abspath(candidate_path)
        counter += 1


class ModSample:
    """Represents a single instrument sample in a ProTracker MOD."""

    def __init__(
        self,
        name: str = "",
        data: bytes = b"",
        volume: int = 64,
        finetune: int = 0,
        repeat_offset: int = 0,
        repeat_length: int = 1,
    ):
        """Initialize a MOD sample.

        Args:
            name: Sample name (max 22 characters).
            data: Raw 8-bit signed PCM data.
            volume: Playback volume (0..64). Default 64.
            finetune: Finetune value (0..15). Default 0.
            repeat_offset: Loop start in 16-bit words (big-endian). Default 0.
            repeat_length: Loop length in 16-bit words. Default 1 word (safe non-loop for Amiga Paula).
        """
        self.name = name[:22]
        # Data must have even length (16-bit word alignment)
        if len(data) % 2 != 0:
            data = data + b"\x00"
        self.data = data
        self.volume = max(0, min(64, volume))
        self.finetune = finetune & 0x0F
        self.repeat_offset = repeat_offset
        self.repeat_length = repeat_length

    @property
    def length_words(self) -> int:
        """Length in 16-bit words."""
        return len(self.data) // 2

    @property
    def length_bytes(self) -> int:
        """Length in bytes."""
        return len(self.data)

    def pack_header(self) -> bytes:
        """Pack sample header into 30 bytes."""
        ascii_name = to_ascii_safe(self.name)
        name_bytes = ascii_name.encode("ascii", errors="replace")[:22].ljust(22, b"\x00")
        len_words = self.length_words
        if len_words > MAX_SAMPLE_LENGTH_WORDS:
            raise ValueError(f"Sample length {len_words} words exceeds ProTracker limit of {MAX_SAMPLE_LENGTH_WORDS}")

        # If sample has length 0, repeat_length should be 0 or 1
        repeat_len = self.repeat_length if len_words > 0 else 0

        header = struct.pack(
            ">22sHBBHH",
            name_bytes,
            len_words,
            self.finetune,
            self.volume,
            self.repeat_offset,
            repeat_len,
        )
        return header


class ModPattern:
    """Represents a single 64-row, 4-channel ProTracker pattern (1024 bytes)."""

    def __init__(self):
        # 64 rows, 4 channels per row, each channel is 4 bytes (encoded cell)
        # Initialize with empty cells (0, 0, 0, 0)
        self.cells = [[b"\x00\x00\x00\x00" for _ in range(CHANNELS_COUNT)] for _ in range(ROWS_PER_PATTERN)]

    def set_cell(
        self,
        row: int,
        channel: int,
        sample_num: int = 0,
        period: int = 0,
        effect_cmd: int = 0,
        effect_param: int = 0,
    ) -> None:
        """Set note and effect for a specific row and channel."""
        if not (0 <= row < ROWS_PER_PATTERN):
            raise IndexError(f"Row {row} out of range (0..{ROWS_PER_PATTERN-1})")
        if not (0 <= channel < CHANNELS_COUNT):
            raise IndexError(f"Channel {channel} out of range (0..{CHANNELS_COUNT-1})")

        self.cells[row][channel] = encode_cell(sample_num, period, effect_cmd, effect_param)

    def get_cell(self, row: int, channel: int) -> Tuple[int, int, int, int]:
        """Get decoded cell (sample_num, period, effect_cmd, effect_param)."""
        return decode_cell(self.cells[row][channel])

    def pack(self) -> bytes:
        """Pack pattern into 1024 bytes."""
        out = bytearray()
        for r in range(ROWS_PER_PATTERN):
            for c in range(CHANNELS_COUNT):
                out.extend(self.cells[r][c])
        return bytes(out)


class ModSong:
    """Represents a complete classic ProTracker M.K. MOD song."""

    def __init__(self, title: str = ""):
        self.title = title[:20]
        # 31 sample slots (index 0 is Sample 1, index 30 is Sample 31)
        self.samples: List[ModSample] = [ModSample() for _ in range(MAX_SAMPLES)]
        self.order_table: List[int] = [0]
        self.restart_byte: int = 0x7F
        self.patterns: List[ModPattern] = []

    def set_sample(self, index: int, sample: ModSample) -> None:
        """Set sample at index (1..31)."""
        if not (1 <= index <= MAX_SAMPLES):
            raise IndexError(f"Sample index must be 1..{MAX_SAMPLES}, got {index}")
        self.samples[index - 1] = sample

    def add_pattern(self, pattern: ModPattern) -> int:
        """Add a pattern and return its index."""
        idx = len(self.patterns)
        self.patterns.append(pattern)
        return idx

    def pack(self) -> bytes:
        """Pack entire MOD into standard binary format."""
        out = bytearray()

        # 1. Song Title (20 bytes, null padded)
        ascii_title = to_ascii_safe(self.title)
        title_bytes = ascii_title.encode("ascii", errors="replace")[:20].ljust(20, b"\x00")
        out.extend(title_bytes)

        # 2. 31 Sample Headers (30 bytes each = 930 bytes)
        for s in self.samples:
            out.extend(s.pack_header())

        # 3. Song length in orders (1 byte)
        song_len = len(self.order_table)
        if not (1 <= song_len <= 128):
            raise ValueError(f"Order table length must be 1..128, got {song_len}")
        out.append(song_len)

        # 4. Restart byte (1 byte, traditionally 0x7F or 0x00)
        out.append(self.restart_byte & 0xFF)

        # 5. Order table (128 bytes, padded with 0)
        orders = self.order_table[:128]
        order_bytes = bytes(orders).ljust(128, b"\x00")
        out.extend(order_bytes)

        # 6. Format tag (4 bytes: "M.K.")
        out.extend(SIGNATURE_MK)

        assert len(out) == HEADER_SIZE, f"Header size {len(out)} != {HEADER_SIZE}"

        # 7. Pattern data
        for p in self.patterns:
            out.extend(p.pack())

        # 8. Sample data (concatenated 8-bit signed PCM)
        for s in self.samples:
            if s.length_words > 0:
                out.extend(s.data)

        return bytes(out)

    def save(self, filepath: str) -> None:
        """Pack and save MOD file, then validate the written file."""
        data = self.pack()
        with open(filepath, "wb") as f:
            f.write(data)
        # Validate the output file immediately
        validate_mod_bytes(data)


def validate_mod_bytes(data: bytes) -> Dict[str, Any]:
    """Validate a raw MOD byte buffer against classic ProTracker M.K. format.

    Returns:
        Dict with validation metrics:
        {
            "is_valid": bool,
            "title": str,
            "sample_count": int,
            "total_sample_bytes": int,
            "pattern_count": int,
            "song_length": int,
            "file_size": int,
            "errors": list[str],
            "warnings": list[str],
        }
    """
    errors: List[str] = []
    warnings: List[str] = []

    if len(data) < HEADER_SIZE:
        raise ModValidationError(f"File too small: {len(data)} bytes (header requires {HEADER_SIZE})")

    # 1. Signature
    tag = data[1080:1084]
    if tag != SIGNATURE_MK:
        errors.append(f"Invalid MOD signature: expected b'M.K.', got {tag!r}")

    # 2. Title
    title = data[:20].decode("ascii", errors="replace").rstrip("\x00")

    # 3. 31 Sample Headers
    samples_info = []
    total_sample_bytes = 0
    active_samples = 0

    for i in range(MAX_SAMPLES):
        offset = 20 + i * 30
        hdr = data[offset : offset + 30]
        s_name = hdr[:22].decode("ascii", errors="replace").rstrip("\x00")
        len_words, finetune, vol, rep_off, rep_len = struct.unpack(">HBBHH", hdr[22:])
        s_bytes = len_words * 2

        if s_bytes > MAX_SAMPLE_LENGTH_BYTES:
            errors.append(f"Sample {i+1} length {s_bytes} bytes exceeds maximum {MAX_SAMPLE_LENGTH_BYTES}")

        if vol > 64:
            warnings.append(f"Sample {i+1} volume {vol} exceeds standard maximum 64")

        if s_bytes > 0:
            active_samples += 1
            total_sample_bytes += s_bytes

        samples_info.append({
            "index": i + 1,
            "name": s_name,
            "len_words": len_words,
            "len_bytes": s_bytes,
            "volume": vol,
            "repeat_offset": rep_off,
            "repeat_length": rep_len,
        })

    # 4. Song Length and Restart byte
    song_len = data[950]
    restart_byte = data[951]
    if song_len < 1 or song_len > 128:
        errors.append(f"Song length {song_len} out of valid range (1..128)")

    # 5. Order Table
    order_table = list(data[952:1080])
    valid_orders = order_table[:song_len]
    max_pattern_idx = max(valid_orders) if valid_orders else 0
    pattern_count = max_pattern_idx + 1

    # 6. Verify file size
    expected_pattern_bytes = pattern_count * PATTERN_SIZE
    expected_total_size = HEADER_SIZE + expected_pattern_bytes + total_sample_bytes

    if len(data) != expected_total_size:
        diff = len(data) - expected_total_size
        if diff < 0:
            errors.append(f"File truncated! Expected {expected_total_size} bytes, got {len(data)} (missing {-diff} bytes)")
        else:
            warnings.append(f"File has extra trailing data: {diff} extra bytes")

    if errors:
        raise ModValidationError("; ".join(errors))

    return {
        "is_valid": True,
        "title": title,
        "active_samples": active_samples,
        "total_sample_bytes": total_sample_bytes,
        "pattern_count": pattern_count,
        "song_length": song_len,
        "restart_byte": restart_byte,
        "file_size": len(data),
        "errors": errors,
        "warnings": warnings,
        "samples": samples_info,
    }


def validate_mod_file(filepath: str) -> Dict[str, Any]:
    """Validate a MOD file on disk."""
    with open(filepath, "rb") as f:
        data = f.read()
    return validate_mod_bytes(data)
