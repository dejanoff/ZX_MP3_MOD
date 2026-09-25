"""Core conversion engine: audio slicing, GS memory management, pattern generation.

Coordinates audio decoding, ProTracker frequency/timing alignment, chunk slicing,
MOD structure assembly, and post-conversion verification.
"""

import os
import math
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional, Any, Callable

from .protracker import (
    PAL_CLOCK,
    MAX_SAMPLES,
    MAX_SAMPLE_LENGTH_BYTES,
    ROWS_PER_PATTERN,
    CHANNELS_COUNT,
    HEADER_SIZE,
    PATTERN_SIZE,
    DEFAULT_SPEED,
    DEFAULT_BPM,
    QUALITY_PRESETS,
    period_to_frequency,
    frequency_to_nearest_note_period,
    get_row_duration,
)
from .mod_writer import ModSong, ModPattern, ModSample, validate_mod_file, ModValidationError
from .audio import (
    find_ffmpeg,
    parse_time_str,
    probe_audio_file,
    extract_pcm_s8,
    smooth_chunk_edges,
    apply_linear_crossfade,
    generate_synthetic_pcm,
)

# General Sound Memory configurations in bytes
MEMORY_TARGETS: Dict[str, int] = {
    "512 KB": 512 * 1024,
    "1 MB": 1024 * 1024,
    "2 MB": 2048 * 1024,
    "4 MB": 4096 * 1024,
    "Unlimited": 0,
}


@dataclass
class ConversionConfig:
    """Settings for audio to MOD conversion."""
    input_path: str = ""
    output_path: str = ""
    start_sec: float = 0.0
    duration_sec: float = 60.0
    quality: str = "OPTIMAL"          # "OPTIMAL" (9309 Hz), "NORMAL" (8287 Hz), "LOW" (6223 Hz), "HIGH" (11084 Hz), "AUTO"
    memory_target: str = "1 MB"       # "512 KB", "1 MB", "2 MB", "4 MB", "Unlimited"
    output_mode: str = "SEAMLESS_PING_PONG"  # "SEAMLESS_PING_PONG" (Recommended for GS), "ONE_CHANNEL", "CENTERED"
    downmix_mode: str = "MIX"         # "MIX" (0.5*L + 0.5*R stereo downmix), "LEFT", "RIGHT"
    click_prevention: bool = True
    title: str = "STREAMING PCM"
    synthetic: bool = False           # If True, generate synthetic test tone without input file


@dataclass
class AnalysisResult:
    """Pre-conversion analysis results."""
    duration_sec: float
    note_name: str
    period: int
    sample_rate: int
    rows_total: int
    rows_per_chunk: int
    samples_required: int
    patterns_required: int
    total_pcm_bytes: int
    estimated_mod_bytes: int
    memory_limit_bytes: int
    fits_memory: bool
    warnings: List[str] = field(default_factory=list)


def parse_memory_target_bytes(mem_str: str) -> int:
    """Convert memory target string to bytes."""
    mem_str_clean = mem_str.strip().upper()
    if "512" in mem_str_clean:
        return 512 * 1024
    elif "1" in mem_str_clean and "M" in mem_str_clean:
        return 1024 * 1024
    elif "2" in mem_str_clean and "M" in mem_str_clean:
        return 2048 * 1024
    elif "4" in mem_str_clean and "M" in mem_str_clean:
        return 4096 * 1024
    elif "UNLIMITED" in mem_str_clean or "0" in mem_str_clean:
        return 0
    return 1024 * 1024


def resolve_quality_and_period(quality_str: str, duration_sec: float, mem_bytes: int) -> Tuple[str, int, int]:
    """Resolve quality setting into (note_name, period, sample_rate).

    Supports:
        - "LOW" -> Note G-1, period 570 (~6223 Hz)
        - "NORMAL" -> Note C-2, period 428 (~8287 Hz)
        - "HIGH" -> Note F-2, period 320 (~11084 Hz)
        - "AUTO" -> dynamically selects highest quality fitting memory limit & 31 samples
        - custom frequency or note name
    """
    q_upper = quality_str.strip().upper()

    if q_upper == "AUTO":
        # Candidate list from optimal for General Sound down to lowest
        # 1. OPTIMAL: D-2 (381, ~9309 Hz - golden mean for GS Z80 mixer, no stuttering)
        # 2. NORMAL: C-2 (428, ~8287 Hz)
        # 3. LOW: G-1 (570, ~6223 Hz)
        # 4. LOWER: F-1 (640, ~5542 Hz)
        candidates = [
            ("OPTIMAL", "D-2", 381),
            ("NORMAL", "C-2", 428),
            ("LOW", "G-1", 570),
            ("LOWER", "F-1", 640),
        ]

        best_choice = ("D-2", 381, round(period_to_frequency(381)))

        for _, note, period in candidates:
            rate = round(period_to_frequency(period))
            # Calculate total PCM size
            total_pcm = int(math.ceil(duration_sec * rate))
            row_dur = get_row_duration(DEFAULT_SPEED, DEFAULT_BPM)
            total_rows = int(math.ceil(duration_sec / row_dur))

            # Slicing calculation: start with safe 32 rows (3.84s) for GS Z80 64KB safety
            rows_per_chunk = 32
            num_chunks = int(math.ceil(total_rows / rows_per_chunk))

            # If 32 rows exceeds 31 samples, try 64 rows if it safely fits in GS 64KB limit
            if num_chunks > MAX_SAMPLES:
                if (65 * row_dur * rate) <= 65534:
                    rows_per_chunk = 64
                    num_chunks = int(math.ceil(total_rows / rows_per_chunk))
                elif (128 * row_dur * rate) <= MAX_SAMPLE_LENGTH_BYTES:
                    rows_per_chunk = 64
                    num_chunks = int(math.ceil(total_rows / rows_per_chunk))

            # Patterns count
            num_patterns = int(math.ceil(total_rows / ROWS_PER_PATTERN))
            est_mod_size = HEADER_SIZE + (num_patterns * PATTERN_SIZE) + total_pcm

            if num_chunks <= MAX_SAMPLES:
                if mem_bytes == 0 or est_mod_size <= mem_bytes:
                    return note, period, rate
                else:
                    best_choice = (note, period, rate)

        # If none fit perfectly, return the best candidate found
        return best_choice

    if q_upper in QUALITY_PRESETS:
        note, period = QUALITY_PRESETS[q_upper]
        return note, period, round(period_to_frequency(period))

    # Try matching note or frequency directly
    try:
        freq = float(q_upper)
        note, period, actual_freq = frequency_to_nearest_note_period(freq)
        return note, period, round(actual_freq)
    except ValueError:
        pass

    # Default fallback: NORMAL (C-2, 428)
    return "C-2", 428, round(period_to_frequency(428))


def analyze_conversion(config: ConversionConfig) -> AnalysisResult:
    """Analyze conversion settings and estimate resource usage."""
    warnings: List[str] = []
    mem_bytes = parse_memory_target_bytes(config.memory_target)
    duration = max(0.1, config.duration_sec)

    note, period, rate = resolve_quality_and_period(config.quality, duration, mem_bytes)
    row_dur = get_row_duration(DEFAULT_SPEED, DEFAULT_BPM)
    total_rows = int(math.ceil(duration / row_dur))

    # Determine chunk size in rows.
    # To strictly avoid General Sound Z80 16-bit register overflow and clipping at 64 KB (65534 bytes),
    # we prefer 32 rows per chunk (3.84s). At 9309 Hz, 32+1 rows = ~36.8 KB (fits comfortably under 64 KB).
    # If duration exceeds 31 samples (~119s), we try 64 rows if safe under 64 KB.
    GS_SAFE_SAMPLE_BYTES = 65534
    rows_per_chunk = 32
    num_chunks = int(math.ceil(total_rows / rows_per_chunk))

    # If 32 rows exceeds 31 samples, try 64 rows (if sample length fits 64 KB limit)
    if num_chunks > MAX_SAMPLES:
        max_chunk_bytes_64 = int(math.ceil(65 * row_dur * rate))
        if max_chunk_bytes_64 <= GS_SAFE_SAMPLE_BYTES:
            rows_per_chunk = 64
            num_chunks = int(math.ceil(total_rows / rows_per_chunk))
        elif max_chunk_bytes_64 <= MAX_SAMPLE_LENGTH_BYTES:
            rows_per_chunk = 64
            num_chunks = int(math.ceil(total_rows / rows_per_chunk))
            warnings.append(
                f"Sample size ({max_chunk_bytes_64 / 1024:.1f} KB) exceeds General Sound 64 KB Z80 limit! "
                f"Please reduce duration to under 119s or choose a lower sample rate."
            )

    if num_chunks > MAX_SAMPLES:
        warnings.append(
            f"Required samples ({num_chunks}) exceeds classic ProTracker limit of {MAX_SAMPLES}! "
            f"Please reduce duration (max ~{int(MAX_SAMPLES * rows_per_chunk * row_dur)}s at this quality)."
        )

    # Patterns count
    patterns_count = int(math.ceil(total_rows / ROWS_PER_PATTERN))
    if patterns_count > 128:
        warnings.append(f"Required patterns ({patterns_count}) exceeds standard order limit of 128!")

    total_pcm = int(math.ceil(duration * rate))
    is_ping_pong = ("PING" in config.output_mode.upper())
    if is_ping_pong and num_chunks > 1:
        # Each handover between chunks adds 1 row of overlap PCM
        overlap_pcm = int(math.ceil((num_chunks - 1) * row_dur * rate))
        total_pcm += overlap_pcm

    est_mod_size = HEADER_SIZE + (patterns_count * PATTERN_SIZE) + total_pcm

    fits_mem = True
    if mem_bytes > 0 and est_mod_size > mem_bytes:
        fits_mem = False
        excess_kb = (est_mod_size - mem_bytes) / 1024.0
        warnings.append(
            f"Estimated MOD size ({est_mod_size / 1024:.1f} KB) exceeds General Sound memory target "
            f"({mem_bytes / 1024:.0f} KB) by {excess_kb:.1f} KB!"
        )

    return AnalysisResult(
        duration_sec=duration,
        note_name=note,
        period=period,
        sample_rate=rate,
        rows_total=total_rows,
        rows_per_chunk=rows_per_chunk,
        samples_required=num_chunks,
        patterns_required=patterns_count,
        total_pcm_bytes=total_pcm,
        estimated_mod_bytes=est_mod_size,
        memory_limit_bytes=mem_bytes,
        fits_memory=fits_mem,
        warnings=warnings,
    )


def convert_audio_to_mod(
    config: ConversionConfig,
    progress_callback: Optional[Callable[[float, str], None]] = None,
    log_callback: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """Execute complete audio to MOD conversion pipeline.

    Args:
        config: ConversionConfig
        progress_callback: Optional fn(progress: float 0..1, message: str)
        log_callback: Optional fn(message: str)

    Returns:
        Dict with conversion summary and validation report.
    """
    def log(msg: str):
        if log_callback:
            log_callback(msg)

    def set_progress(p: float, msg: str):
        if progress_callback:
            progress_callback(p, msg)

    log(f"Starting conversion: {os.path.basename(config.input_path) if config.input_path else 'SYNTHETIC'}")
    set_progress(0.05, "Analyzing audio and settings...")

    # Run analysis
    analysis = analyze_conversion(config)

    for w in analysis.warnings:
        log(f"WARNING: {w}")

    if analysis.samples_required > MAX_SAMPLES:
        raise ValueError(
            f"Duration {config.duration_sec}s requires {analysis.samples_required} samples, "
            f"which exceeds ProTracker maximum of {MAX_SAMPLES} samples."
        )

    log(f"Selected Note: {analysis.note_name} (Period: {analysis.period}, Rate: {analysis.sample_rate} Hz)")
    log(f"Timing: Speed={DEFAULT_SPEED}, BPM={DEFAULT_BPM}, 1 Row = {get_row_duration(DEFAULT_SPEED, DEFAULT_BPM):.4f}s")
    log(f"Slicing: {analysis.samples_required} samples, {analysis.rows_per_chunk} rows per sample")

    # Step 1: Obtain 8-bit mono signed PCM data
    set_progress(0.15, "Decoding audio to 8-bit mono PCM...")
    pcm_data: bytes = b""

    if config.synthetic or not config.input_path:
        log("Generating synthetic test PCM audio...")
        pcm_data = generate_synthetic_pcm(
            duration_sec=analysis.duration_sec,
            sample_rate=analysis.sample_rate,
            freq=440.0,
        )
    else:
        ffmpeg_bin = find_ffmpeg()
        if not ffmpeg_bin:
            raise RuntimeError(
                "FFmpeg not found! Please place ffmpeg.exe next to the application or install it in system PATH."
            )
        log(f"Using FFmpeg: {ffmpeg_bin}")
        log(f"Extracting segment: {config.start_sec:.2f}s to {config.start_sec + analysis.duration_sec:.2f}s")

        log(f"Stereo Downmix Mode: {config.downmix_mode}")
        pcm_data = extract_pcm_s8(
            filepath=config.input_path,
            start_sec=config.start_sec,
            duration_sec=analysis.duration_sec,
            target_sample_rate=analysis.sample_rate,
            ffmpeg_bin=ffmpeg_bin,
            downmix_mode=config.downmix_mode,
        )

    actual_pcm_len = len(pcm_data)
    log(f"Extracted PCM data: {actual_pcm_len} bytes ({actual_pcm_len / 1024:.1f} KB)")

    # Step 2: Slice PCM into chunks aligned with tracker rows
    set_progress(0.40, "Slicing PCM chunks to tracker grid...")
    row_dur = get_row_duration(DEFAULT_SPEED, DEFAULT_BPM)

    song = ModSong(title=config.title)

    is_ping_pong = ("PING" in config.output_mode.upper())
    is_centered = ("CENTERED" in config.output_mode.upper())

    # We will build pattern chunks
    samples_created = []
    chunk_row_counts = []
    fade_samples = int(round(1 * row_dur * analysis.sample_rate))

    remaining_rows = analysis.rows_total
    current_row_offset = 0

    for i in range(analysis.samples_required):
        sample_idx = i + 1
        main_rows_this_chunk = min(analysis.rows_per_chunk, remaining_rows)
        chunk_row_counts.append(main_rows_this_chunk)
        remaining_rows -= main_rows_this_chunk

        # Overlap tail for ping-pong handover (1 row tail on all chunks except the last)
        tail_rows = 1 if (is_ping_pong and i < analysis.samples_required - 1) else 0
        total_rows_this_chunk = main_rows_this_chunk + tail_rows

        # Calculate exact audio byte boundaries
        start_sec = current_row_offset * row_dur
        end_sec = (current_row_offset + total_rows_this_chunk) * row_dur
        start_byte = int(round(start_sec * analysis.sample_rate))
        end_byte = int(round(end_sec * analysis.sample_rate))

        expected_bytes = end_byte - start_byte
        expected_bytes = (expected_bytes // 2) * 2  # Word alignment

        # Extract slice from PCM data
        chunk_slice = pcm_data[start_byte : start_byte + expected_bytes]
        current_row_offset += main_rows_this_chunk

        # If chunk is shorter than expected (end of stream), pad with zero (silence)
        if len(chunk_slice) < expected_bytes:
            chunk_slice = chunk_slice + (b"\x00" * (expected_bytes - len(chunk_slice)))

        # Ensure word alignment
        if len(chunk_slice) % 2 != 0:
            chunk_slice = chunk_slice + b"\x00"

        # Apply edge smoothing or crossfade
        if is_ping_pong:
            fade_in = fade_samples if i > 0 else 0
            fade_out = fade_samples if i < analysis.samples_required - 1 else 0
            chunk_slice = apply_linear_crossfade(chunk_slice, fade_in_samples=fade_in, fade_out_samples=fade_out)
            if config.click_prevention:
                if i == 0:
                    chunk_slice = smooth_chunk_edges(chunk_slice, fade_samples=16)
                elif i == analysis.samples_required - 1:
                    chunk_slice = smooth_chunk_edges(chunk_slice, fade_samples=16)
        else:
            if config.click_prevention:
                chunk_slice = smooth_chunk_edges(chunk_slice, fade_samples=16)

        # Ensure word alignment and append 2 zero bytes for a 100% silent loop buffer
        if len(chunk_slice) % 2 != 0:
            chunk_slice = chunk_slice + b"\x00"
        chunk_slice = chunk_slice + b"\x00\x00"

        total_words = len(chunk_slice) // 2
        silent_loop_offset = max(0, total_words - 1)

        # Create MOD sample with silent loop buffer at the end
        s_name = f"PCM_CHUNK_{sample_idx:02d}"
        mod_sample = ModSample(
            name=s_name,
            data=chunk_slice,
            volume=64,
            finetune=0,
            repeat_offset=silent_loop_offset,
            repeat_length=1,  # Safe silent loop for Amiga Paula / General Sound
        )
        song.set_sample(sample_idx, mod_sample)
        samples_created.append(mod_sample)

    log(f"Created {len(samples_created)} sample chunks in MOD header (Ping-Pong: {is_ping_pong})")

    # Step 3: Generate Pattern Data and Order Table
    set_progress(0.65, "Generating tracker patterns and order table...")

    # Build patterns
    # Patterns are 64 rows each.
    patterns: List[ModPattern] = []
    order_table: List[int] = []

    pattern_idx = 0
    current_pattern = ModPattern()
    row_in_pattern = 0

    for i, s_sample in enumerate(samples_created):
        sample_num = i + 1
        rows_for_this_sample = chunk_row_counts[i]

        if is_ping_pong:
            # Alternating channels: Channel 0 (Left) and Channel 3 (Left)
            # Both are Left channels in Amiga/GS hardware, eliminating any stereo wobble.
            # General Sound's Z80 mixer handles only 1 active voice >98% of playback time.
            curr_channel = 0 if (i % 2 == 0) else 3
            prev_channel = 3 if (i % 2 == 0) else 0

            # Trigger sample on row_in_pattern with full volume (C40)
            current_pattern.set_cell(
                row=row_in_pattern,
                channel=curr_channel,
                sample_num=sample_num,
                period=analysis.period,
                effect_cmd=0x0C,  # Set Volume
                effect_param=0x40,  # 64 (Full volume)
            )

            # If this is Pattern 0, Row 0: explicitly lock tracker speed (F06) and CIA BPM (F7D)
            # on unused channels to guarantee 100% stable 50 Hz tick rate across all Spectrum clones!
            if i == 0 and pattern_idx == 0 and row_in_pattern == 0:
                current_pattern.set_cell(
                    row=0,
                    channel=1,
                    effect_cmd=0x0F,  # Speed
                    effect_param=0x06,  # 6 ticks per row
                )
                current_pattern.set_cell(
                    row=0,
                    channel=2,
                    effect_cmd=0x0F,  # BPM
                    effect_param=0x7D,  # 125 BPM (50.0 Hz)
                )

            # 1 row later, mute the previous channel (C00) to cut off its finished overlap tail
            # and completely cease mixer processing on that channel
            if i > 0 and (row_in_pattern + 1) < ROWS_PER_PATTERN:
                current_pattern.set_cell(
                    row=row_in_pattern + 1,
                    channel=prev_channel,
                    sample_num=0,
                    period=0,
                    effect_cmd=0x0C,  # Set Volume
                    effect_param=0x00,  # 0 (Mute)
                )
        else:
            # Standard single channel or centered dual channel
            current_pattern.set_cell(
                row=row_in_pattern,
                channel=0,
                sample_num=sample_num,
                period=analysis.period,
            )
            if is_centered:
                current_pattern.set_cell(
                    row=row_in_pattern,
                    channel=1,
                    sample_num=sample_num,
                    period=analysis.period,
                )
            if i == 0 and pattern_idx == 0 and row_in_pattern == 0:
                current_pattern.set_cell(
                    row=0,
                    channel=2,
                    effect_cmd=0x0F,
                    effect_param=0x06,
                )
                current_pattern.set_cell(
                    row=0,
                    channel=3,
                    effect_cmd=0x0F,
                    effect_param=0x7D,
                )

        # Advance by rows_for_this_sample
        rows_left_to_advance = rows_for_this_sample
        while rows_left_to_advance > 0:
            space_in_pattern = ROWS_PER_PATTERN - row_in_pattern
            if rows_left_to_advance < space_in_pattern:
                row_in_pattern += rows_left_to_advance
                rows_left_to_advance = 0
            else:
                rows_left_to_advance -= space_in_pattern
                # Commit current pattern
                patterns.append(current_pattern)
                order_table.append(pattern_idx)
                pattern_idx += 1
                current_pattern = ModPattern()
                row_in_pattern = 0

    # If the last pattern had events but was not yet added:
    if row_in_pattern > 0 or not patterns:
        # On the final row, add pattern break or stop
        # In ProTracker, effect D00 (pattern break) or B00 (position jump to 0)
        # We can add B00 at row_in_pattern to restart or stop
        if row_in_pattern < ROWS_PER_PATTERN:
            current_pattern.set_cell(
                row=row_in_pattern,
                channel=0,
                sample_num=0,
                period=0,
                effect_cmd=0x0B,  # Position Jump
                effect_param=0x00,
            )
        patterns.append(current_pattern)
        order_table.append(pattern_idx)

    # Attach patterns and order table to song
    for p in patterns:
        song.add_pattern(p)
    song.order_table = order_table

    log(f"Generated {len(patterns)} patterns, order length {len(order_table)}")

    # Step 4: Write MOD and Validate
    set_progress(0.85, "Writing and validating ProTracker MOD file...")
    out_dir = os.path.dirname(os.path.abspath(config.output_path))
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    song.save(config.output_path)
    val_report = validate_mod_file(config.output_path)

    log("Validation PASSED successfully:")
    log(f"  - Signature: M.K.")
    log(f"  - Active Samples: {val_report['active_samples']} / 31")
    log(f"  - Total Sample PCM: {val_report['total_sample_bytes']} bytes")
    log(f"  - Patterns: {val_report['pattern_count']}")
    log(f"  - Final File Size: {val_report['file_size']} bytes ({val_report['file_size']/1024:.1f} KB)")

    set_progress(1.0, "Conversion complete!")

    return {
        "success": True,
        "output_path": os.path.abspath(config.output_path),
        "file_size": val_report["file_size"],
        "sample_count": val_report["active_samples"],
        "sample_rate": analysis.sample_rate,
        "note": analysis.note_name,
        "period": analysis.period,
        "patterns_count": val_report["pattern_count"],
        "duration_sec": analysis.duration_sec,
        "memory_target": config.memory_target,
        "fits_memory": analysis.fits_memory,
        "warnings": analysis.warnings + val_report.get("warnings", []),
    }
