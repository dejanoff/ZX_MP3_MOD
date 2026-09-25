"""Command-line interface and launcher for MP3 to ProTracker MOD converter.

Supports:
  - CLI batch conversion
  - Parameter pre-analysis (--analyze)
  - Self-testing without dependencies (--selftest)
  - Automatic fallback to Tkinter GUI if invoked without arguments
"""

import os
import sys
import argparse
from typing import Optional

from .version import __version__
from .audio import find_ffmpeg, parse_time_str, format_time_str
from .converter import (
    ConversionConfig,
    AnalysisResult,
    analyze_conversion,
    convert_audio_to_mod,
    parse_memory_target_bytes,
    resolve_quality_and_period,
)
from .mod_writer import validate_mod_file, generate_output_mod_path


def run_selftest() -> int:
    """Run built-in self-test using synthetic PCM audio (no FFmpeg needed)."""
    print("=" * 60)
    print(f"RUNNING PROTRACKER MOD CONVERTER SELF-TEST v{__version__}")
    print("=" * 60)

    test_mod_path = os.path.abspath("selftest_output.mod")
    config = ConversionConfig(
        input_path="",
        output_path=test_mod_path,
        start_sec=0.0,
        duration_sec=16.0,  # 16 seconds (~2 patterns, ~3 samples)
        quality="NORMAL",   # 8287 Hz (C-2)
        memory_target="1 MB",
        output_mode="SEAMLESS_PING_PONG",
        click_prevention=True,
        title="SELFTEST SONG",
        synthetic=True,
    )

    try:
        print("[1/3] Analyzing synthetic test audio configuration...")
        analysis = analyze_conversion(config)
        print(f"      Duration: {analysis.duration_sec:.1f}s")
        print(f"      Target Rate: {analysis.sample_rate} Hz (Note {analysis.note_name}, Period {analysis.period})")
        print(f"      Samples required: {analysis.samples_required} / 31")
        print(f"      Patterns required: {analysis.patterns_required}")
        print(f"      Est. MOD size: {analysis.estimated_mod_bytes / 1024:.1f} KB")

        print("[2/3] Generating synthetic PCM and compiling ProTracker MOD...")
        report = convert_audio_to_mod(
            config,
            log_callback=lambda msg: print(f"      [LOG] {msg}"),
        )

        print("[3/3] Performing deep binary structural validation of created MOD...")
        val = validate_mod_file(test_mod_path)
        print("      Validation details:")
        print(f"        - Title: {val['title']!r}")
        print(f"        - Active Samples: {val['active_samples']}")
        print(f"        - Total Sample Bytes: {val['total_sample_bytes']}")
        print(f"        - Patterns: {val['pattern_count']}")
        print(f"        - Song Orders: {val['song_length']}")
        print(f"        - File Size: {val['file_size']} bytes ({val['file_size']/1024:.1f} KB)")

        # Assertions
        assert val["is_valid"] is True, "Validation failed!"
        assert val["active_samples"] == analysis.samples_required, "Sample count mismatch!"
        assert val["pattern_count"] == analysis.patterns_required, "Pattern count mismatch!"
        assert val["file_size"] > 1084, "File size suspiciously small!"

        print("=" * 60)
        print("SELF-TEST PASSED: Classic ProTracker M.K. MOD created and verified successfully!")
        print(f"Test output file: {test_mod_path}")
        print("=" * 60)
        return 0

    except Exception as e:
        print(f"\nSELF-TEST FAILED with error: {e}")
        import traceback
        traceback.print_exc()
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert MP3/WAV/FLAC audio to classic ProTracker MOD for ZX Spectrum General Sound.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("input", nargs="?", help="Input audio file (MP3, WAV, FLAC, OGG, M4A)")
    parser.add_argument("output", nargs="?", help="Output MOD file (default: <input_base>.mod)")
    parser.add_argument("--start", "-s", default="0", help="Start offset in seconds or MM:SS (default: 0)")
    parser.add_argument("--duration", "-d", default="60", help="Duration in seconds (default: 60)")
    parser.add_argument(
        "--quality", "-q",
        default="OPTIMAL",
        help="Quality preset: OPTIMAL (9309 Hz / recommended for GS), NORMAL (8287 Hz), LOW (6223 Hz), HIGH (11084 Hz), AUTO or specific Hz/note",
    )
    parser.add_argument(
        "--memory", "-m",
        default="1M",
        help="GS memory target: 512k, 1m, 2m, 4m, unlimited (default: 1m)",
    )
    parser.add_argument(
        "--mode", "-o",
        choices=["ping_pong", "one_channel", "centered"],
        default="ping_pong",
        help="Audio output mode: ping_pong (Mono Ch 0<->3 Overlap - recommended for GS, eliminates stutters), one_channel (Sequential on Ch 0), centered (Dual channel Left+Right)",
    )
    parser.add_argument(
        "--downmix",
        choices=["mix", "left", "right"],
        default="mix",
        help="Stereo downmixing: mix (0.5*L + 0.5*R -> Mono), left (L only), right (R only)",
    )
    parser.add_argument("--title", "-t", default="", help="Song title (max 20 characters)")
    parser.add_argument("--no-smooth", action="store_true", help="Disable edge smoothing / click prevention")
    parser.add_argument("--analyze", action="store_true", help="Estimate resources without converting")
    parser.add_argument("--selftest", action="store_true", help="Run offline self-test and verify output MOD")
    parser.add_argument("--gui", action="store_true", help="Launch interactive graphical interface")
    parser.add_argument(
        "--version", "-v",
        action="version",
        version=f"MP3toMOD v{__version__} (ZX Spectrum General Sound)",
        help="Show program's version number and exit",
    )

    args = parser.parse_args()

    # 1. Handle Self-test
    if args.selftest:
        return run_selftest()

    # 2. If no arguments or --gui requested, launch GUI
    if args.gui or (not args.input and not args.selftest):
        try:
            from .gui import launch_gui
            launch_gui()
            return 0
        except Exception as e:
            print(f"Error launching GUI: {e}")
            print("Falling back to CLI help:")
            parser.print_help()
            return 1

    # 3. CLI Conversion / Analysis
    input_file = args.input
    if not os.path.isfile(input_file):
        print(f"Error: Input file not found: {input_file}")
        return 1

    start_sec = parse_time_str(args.start)
    dur_sec = parse_time_str(args.duration)
    if dur_sec <= 0:
        dur_sec = 60.0

    mem_target = args.memory.upper()
    if "512" in mem_target:
        mem_str = "512 KB"
    elif "2" in mem_target:
        mem_str = "2 MB"
    elif "4" in mem_target:
        mem_str = "4 MB"
    elif "UNLIMITED" in mem_target or "0" in mem_target:
        mem_str = "Unlimited"
    else:
        mem_str = "1 MB"

    if "PING" in args.mode.upper():
        out_mode = "SEAMLESS_PING_PONG"
    elif "CENTER" in args.mode.upper():
        out_mode = "CENTERED"
    else:
        out_mode = "ONE_CHANNEL"

    output_file = args.output
    if not output_file:
        mem_bytes = parse_memory_target_bytes(mem_str)
        _, _, sample_rate = resolve_quality_and_period(args.quality.upper(), dur_sec, mem_bytes)
        output_file = generate_output_mod_path(
            input_path=input_file,
            sample_rate=sample_rate,
            output_mode=out_mode,
            duration_sec=dur_sec,
        )

    downmix_mode = args.downmix.upper()
    title = args.title or os.path.splitext(os.path.basename(output_file))[0][:20]

    config = ConversionConfig(
        input_path=input_file,
        output_path=output_file,
        start_sec=start_sec,
        duration_sec=dur_sec,
        quality=args.quality.upper(),
        memory_target=mem_str,
        output_mode=out_mode,
        downmix_mode=downmix_mode,
        click_prevention=not args.no_smooth,
        title=title,
    )

    # If --analyze flag is passed
    if args.analyze:
        print("=" * 60)
        print("CONVERSION ESTIMATE & ANALYSIS")
        print("=" * 60)
        analysis = analyze_conversion(config)
        print(f"Input:             {config.input_path}")
        print(f"Selection:         {format_time_str(config.start_sec)} -> {format_time_str(config.start_sec + analysis.duration_sec)} ({analysis.duration_sec:.1f}s)")
        print(f"ProTracker Note:   {analysis.note_name} (Period: {analysis.period})")
        print(f"Playback Rate:     {analysis.sample_rate} Hz (Amiga Paula PAL)")
        print(f"Samples required:  {analysis.samples_required} / 31")
        print(f"Patterns required: {analysis.patterns_required}")
        print(f"Raw PCM size:      {analysis.total_pcm_bytes} bytes ({analysis.total_pcm_bytes / 1024:.1f} KB)")
        print(f"Est. MOD size:     {analysis.estimated_mod_bytes} bytes ({analysis.estimated_mod_bytes / 1024:.1f} KB)")
        print(f"GS Memory Target:  {config.memory_target}")
        print(f"Memory Fit:        {'OK' if analysis.fits_memory else 'EXCEEDS TARGET!'}")
        if analysis.warnings:
            print("Warnings:")
            for w in analysis.warnings:
                print(f"  - {w}")
        print("=" * 60)
        return 0

    # Execute conversion
    print("=" * 60)
    print(f"MP3 TO PROTRACKER MOD CONVERTER v{__version__}")
    print("Target: ZX Spectrum Wild Player + General Sound")
    print("=" * 60)

    try:
        report = convert_audio_to_mod(
            config,
            log_callback=lambda msg: print(f"[*] {msg}"),
        )
        print("-" * 60)
        print("CONVERSION COMPLETE:")
        print(f"  Output File:   {report['output_path']}")
        print(f"  Final Size:    {report['file_size']} bytes ({report['file_size'] / 1024:.1f} KB)")
        print(f"  Sample Rate:   {report['sample_rate']} Hz (Note {report['note']})")
        print(f"  Samples Used:  {report['sample_count']} / 31")
        print(f"  Patterns:      {report['patterns_count']}")
        print(f"  Memory Target: {report['memory_target']} ({'Fits OK' if report['fits_memory'] else 'Exceeds target'})")
        print("=" * 60)
        return 0
    except Exception as e:
        print(f"\n[ERROR] Conversion failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
