"""Audio decoding, probing, processing, and PCM extraction module.

Interfaces with FFmpeg to decode MP3/WAV/FLAC/OGG/M4A to 8-bit signed mono PCM.
Includes fallback synthetic PCM generator for self-testing without external tools.
"""

import os
import sys
import shutil
import subprocess
import re
import math
import struct
from typing import Optional, Tuple, Dict, Any, List


def find_ffmpeg() -> Optional[str]:
    """Find ffmpeg.exe location:

    1. Next to executable / script
    2. In current working directory
    3. In system PATH
    """
    candidates = []

    # 1. Next to executable (if frozen with PyInstaller)
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        candidates.append(os.path.join(exe_dir, "ffmpeg.exe"))
        candidates.append(os.path.join(exe_dir, "ffmpeg"))

    # 2. Next to this script file
    script_dir = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(script_dir, "ffmpeg.exe"))
    candidates.append(os.path.join(script_dir, "ffmpeg"))
    # Also parent dir of script
    parent_dir = os.path.dirname(script_dir)
    candidates.append(os.path.join(parent_dir, "ffmpeg.exe"))
    candidates.append(os.path.join(parent_dir, "ffmpeg"))

    # 3. Current working directory
    cwd = os.getcwd()
    candidates.append(os.path.join(cwd, "ffmpeg.exe"))
    candidates.append(os.path.join(cwd, "ffmpeg"))

    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return os.path.abspath(c)

    # 4. In system PATH
    which_ffmpeg = shutil.which("ffmpeg")
    if which_ffmpeg:
        return os.path.abspath(which_ffmpeg)

    return None


def parse_time_str(time_val: Any) -> float:
    """Parse time string or number into seconds.

    Supports:
        - "45" or 45 -> 45.0
        - "00:45" -> 45.0
        - "01:30" -> 90.0
        - "01:15:30" -> 4530.0
        - "12.5" -> 12.5
    """
    if isinstance(time_val, (int, float)):
        return max(0.0, float(time_val))

    s = str(time_val).strip()
    if not s:
        return 0.0

    parts = s.split(":")
    try:
        if len(parts) == 1:
            return max(0.0, float(parts[0]))
        elif len(parts) == 2:
            minutes = float(parts[0])
            seconds = float(parts[1])
            return max(0.0, minutes * 60.0 + seconds)
        elif len(parts) == 3:
            hours = float(parts[0])
            minutes = float(parts[1])
            seconds = float(parts[2])
            return max(0.0, hours * 3600.0 + minutes * 60.0 + seconds)
        else:
            raise ValueError(f"Invalid time format: {time_val}")
    except ValueError as e:
        raise ValueError(f"Could not parse time value '{time_val}': {e}")


def format_time_str(seconds: float) -> str:
    """Format seconds into MM:SS string."""
    total_sec = max(0.0, seconds)
    m = int(total_sec // 60)
    s = total_sec - (m * 60)
    if s == int(s):
        return f"{m:02d}:{int(s):02d}"
    return f"{m:02d}:{s:04.1f}"


def probe_audio_file(filepath: str, ffmpeg_bin: Optional[str] = None) -> Dict[str, Any]:
    """Probe audio file using FFmpeg to obtain duration and audio info."""
    if not os.path.isfile(filepath):
        raise FileNotFoundError(f"File not found: {filepath}")

    if ffmpeg_bin is None:
        ffmpeg_bin = find_ffmpeg()

    if not ffmpeg_bin:
        raise RuntimeError(
            "FFmpeg not found! Please place ffmpeg.exe next to the application or install it in system PATH."
        )

    # Run ffmpeg -i <file> to get metadata from stderr
    cmd = [ffmpeg_bin, "-hide_banner", "-i", filepath]
    # Hide window on Windows
    startupinfo = None
    if sys.platform == "win32":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            startupinfo=startupinfo,
            encoding="utf-8",
            errors="replace",
        )
    except Exception as e:
        raise RuntimeError(f"Error running FFmpeg probe: {e}")

    stderr = proc.stderr
    duration = 0.0
    dur_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", stderr)
    if dur_match:
        h = float(dur_match.group(1))
        m = float(dur_match.group(2))
        s = float(dur_match.group(3))
        duration = h * 3600.0 + m * 60.0 + s

    # Sample rate & channels
    sample_rate = 44100
    sr_match = re.search(r"(\d+)\s*Hz", stderr)
    if sr_match:
        sample_rate = int(sr_match.group(1))

    channels = "stereo"
    if "mono" in stderr:
        channels = "mono"

    return {
        "duration": duration,
        "sample_rate": sample_rate,
        "channels": channels,
        "filepath": filepath,
    }


def extract_pcm_s8(
    filepath: str,
    start_sec: float,
    duration_sec: float,
    target_sample_rate: int,
    ffmpeg_bin: Optional[str] = None,
    downmix_mode: str = "MIX",
) -> bytes:
    """Extract audio segment as 8-bit signed mono PCM using FFmpeg.

    Args:
        filepath: Path to audio file (MP3, WAV, FLAC, etc.)
        start_sec: Start offset in seconds.
        duration_sec: Duration in seconds.
        target_sample_rate: Output sample rate (Hz).
        ffmpeg_bin: Path to ffmpeg binary (auto-detected if None).
        downmix_mode: "MIX" (0.5*L + 0.5*R), "LEFT" (L only), "RIGHT" (R only).

    Returns:
        Raw bytes in signed 8-bit PCM format (-128..127).
    """
    if ffmpeg_bin is None:
        ffmpeg_bin = find_ffmpeg()

    if not ffmpeg_bin:
        raise RuntimeError(
            "FFmpeg not found! Please place ffmpeg.exe next to the application or install it in system PATH."
        )

    # Configure stereo to mono audio filter
    dm = downmix_mode.strip().upper()
    if dm == "LEFT":
        af_filter = "pan=mono|c0=c0"
    elif dm == "RIGHT":
        af_filter = "pan=mono|c0=c1"
    else:  # "MIX"
        af_filter = "pan=mono|c0=0.5*c0+0.5*c1"

    # Format arguments
    cmd = [
        ffmpeg_bin,
        "-hide_banner",
        "-loglevel", "error",
        "-ss", f"{start_sec:.4f}",
        "-t", f"{duration_sec:.4f}",
        "-i", filepath,
        "-vn",
        "-af", af_filter,                # Downmix stereo to mono
        "-ac", "1",                      # Force 1 channel
        "-ar", str(target_sample_rate),  # Resample to Paula rate
        "-f", "s8",                      # 8-bit signed PCM
        "-y",
        "-"                              # Output to stdout pipe
    ]

    startupinfo = None
    if sys.platform == "win32":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        startupinfo=startupinfo,
    )
    pcm_data, stderr_data = proc.communicate()

    if proc.returncode != 0:
        err_msg = stderr_data.decode("utf-8", errors="replace")
        raise RuntimeError(f"FFmpeg PCM extraction failed (code {proc.returncode}): {err_msg}")

    return pcm_data


def smooth_chunk_edges(pcm_data: bytes, fade_samples: int = 16) -> bytes:
    """Apply micro-fades at the start and end of a PCM chunk to prevent clicks.

    Fade length of 16 samples is ~1.9 ms at 8287 Hz, which eliminates high-frequency
    discontinuities without creating any perceptible audio hole.
    """
    if len(pcm_data) < fade_samples * 2:
        return pcm_data

    # Unpack signed 8-bit integers
    samples = list(struct.unpack(f"{len(pcm_data)}b", pcm_data))
    n = len(samples)

    # Fade in (start)
    for i in range(fade_samples):
        # Cosine / linear ramp
        gain = 0.5 * (1.0 - math.cos(math.pi * i / fade_samples))
        samples[i] = int(round(samples[i] * gain))

    # Fade out (end)
    for i in range(fade_samples):
        idx = n - 1 - i
        gain = 0.5 * (1.0 - math.cos(math.pi * i / fade_samples))
        samples[idx] = int(round(samples[idx] * gain))

    # Re-pack into bytes
    return struct.pack(f"{len(samples)}b", *samples)


def apply_linear_crossfade(
    pcm_data: bytes,
    fade_in_samples: int = 0,
    fade_out_samples: int = 0,
) -> bytes:
    """Apply linear crossfade ramps at chunk edges for seamless overlapping.

    fade_in_samples: ramp from 0.0 to 1.0 at start of chunk.
    fade_out_samples: ramp from 1.0 to 0.0 at end of chunk.
    """
    if len(pcm_data) == 0:
        return pcm_data

    samples = list(struct.unpack(f"{len(pcm_data)}b", pcm_data))
    n = len(samples)

    if fade_in_samples > 0:
        actual_in = min(n, fade_in_samples)
        for i in range(actual_in):
            gain = i / float(actual_in)
            samples[i] = int(round(samples[i] * gain))

    if fade_out_samples > 0:
        actual_out = min(n, fade_out_samples)
        start_idx = n - actual_out
        for i in range(actual_out):
            gain = 1.0 - (i / float(actual_out))
            samples[start_idx + i] = int(round(samples[start_idx + i] * gain))

    return struct.pack(f"{len(samples)}b", *samples)


def find_nearest_zero_crossing(pcm_data: bytes, target_idx: int, window: int = 32) -> int:
    """Find the nearest zero-crossing index around target_idx within window."""
    n = len(pcm_data)
    if target_idx <= 0:
        return 0
    if target_idx >= n:
        return n

    start = max(1, target_idx - window)
    end = min(n - 1, target_idx + window)

    best_idx = target_idx
    best_dist = float("inf")
    min_abs_val = 256

    for i in range(start, end):
        val = pcm_data[i]
        s_val = val if val < 128 else val - 256
        prev_val = pcm_data[i - 1]
        s_prev = prev_val if prev_val < 128 else prev_val - 256

        # Check for zero crossing (change of sign)
        if (s_prev <= 0 and s_val >= 0) or (s_prev >= 0 and s_val <= 0):
            closer = i if abs(s_val) < abs(s_prev) else i - 1
            dist = abs(closer - target_idx)
            val_dist = abs(s_val if closer == i else s_prev)
            if dist < best_dist or (dist == best_dist and val_dist < min_abs_val):
                best_dist = dist
                best_idx = closer
                min_abs_val = val_dist

    return best_idx


def generate_synthetic_pcm(
    duration_sec: float,
    sample_rate: int,
    freq: float = 440.0,
    amplitude: float = 0.8,
) -> bytes:
    """Generate synthetic sine wave 8-bit signed mono PCM for self-testing."""
    num_samples = int(round(duration_sec * sample_rate))
    samples = []
    max_amp = 127.0 * amplitude

    for i in range(num_samples):
        t = i / sample_rate
        # Generate a nice warm chord / melody: fundamental + 3rd harmonic
        val = math.sin(2.0 * math.pi * freq * t) * 0.7 + math.sin(2.0 * math.pi * freq * 1.5 * t) * 0.3
        s = int(round(val * max_amp))
        s = max(-128, min(127, s))
        samples.append(s)

    return struct.pack(f"{len(samples)}b", *samples)
