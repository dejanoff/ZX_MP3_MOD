"""Tests for converter engine, memory budgeting, and pattern generation."""

import unittest
import os
from src.converter import (
    ConversionConfig,
    AnalysisResult,
    analyze_conversion,
    convert_audio_to_mod,
    parse_memory_target_bytes,
    resolve_quality_and_period,
)
from src.mod_writer import validate_mod_file


class TestConverter(unittest.TestCase):
    def test_parse_memory_target_bytes(self):
        self.assertEqual(parse_memory_target_bytes("512 KB"), 512 * 1024)
        self.assertEqual(parse_memory_target_bytes("1 MB"), 1024 * 1024)
        self.assertEqual(parse_memory_target_bytes("2 MB"), 2048 * 1024)
        self.assertEqual(parse_memory_target_bytes("4 MB"), 4096 * 1024)
        self.assertEqual(parse_memory_target_bytes("Unlimited"), 0)

    def test_resolve_quality_and_period(self):
        note, period, rate = resolve_quality_and_period("NORMAL", 60.0, 1024 * 1024)
        self.assertEqual(note, "C-2")
        self.assertEqual(period, 428)
        self.assertEqual(rate, 8287)

        note, period, rate = resolve_quality_and_period("LOW", 60.0, 1024 * 1024)
        self.assertEqual(note, "G-1")
        self.assertEqual(period, 570)
        self.assertEqual(rate, 6223)

        note, period, rate = resolve_quality_and_period("OPTIMAL", 60.0, 1024 * 1024)
        self.assertEqual(note, "D-2")
        self.assertEqual(period, 381)
        self.assertEqual(rate, 9309)

        note, period, rate = resolve_quality_and_period("HIGH", 60.0, 1024 * 1024)
        self.assertEqual(note, "F-2")
        self.assertEqual(period, 320)
        self.assertEqual(rate, 11084)

    def test_auto_quality_fits_1mb(self):
        # 60s at 1MB in AUTO mode prioritizes OPTIMAL (D-2, ~9309 Hz) for stutter-free General Sound playback
        note, period, rate = resolve_quality_and_period("AUTO", 60.0, 1024 * 1024)
        self.assertEqual(note, "D-2")

        # But 150s at 1MB cannot fit OPTIMAL (150 * 9309 ≈ 1396 KB), should downshift to NORMAL or LOW
        note, period, rate = resolve_quality_and_period("AUTO", 150.0, 1024 * 1024)
        self.assertIn(note, ["NORMAL", "C-2", "LOW", "G-1", "F-1"])

    def test_analysis_60_seconds(self):
        config = ConversionConfig(
            duration_sec=60.0,
            quality="NORMAL",
            memory_target="1 MB",
        )
        res = analyze_conversion(config)
        self.assertEqual(res.duration_sec, 60.0)
        self.assertEqual(res.sample_rate, 8287)
        self.assertEqual(res.samples_required, 8)  # 60s / 7.68s = 7.81 -> 8 samples
        self.assertTrue(res.fits_memory)
        self.assertLess(res.estimated_mod_bytes, 1024 * 1024)

    def test_synthetic_conversion_centered(self):
        out_mod = "test_centered.mod"
        try:
            config = ConversionConfig(
                output_path=out_mod,
                duration_sec=10.0,
                quality="LOW",
                output_mode="CENTERED",
                synthetic=True,
            )
            report = convert_audio_to_mod(config)
            self.assertTrue(report["success"])
            self.assertTrue(os.path.isfile(out_mod))

            val = validate_mod_file(out_mod)
            self.assertTrue(val["is_valid"])
            self.assertEqual(val["active_samples"], 2)  # 10s / 7.68s = 2 samples
        finally:
            if os.path.exists(out_mod):
                os.remove(out_mod)

    def test_synthetic_conversion_one_channel(self):
        out_mod = "test_one_channel.mod"
        try:
            config = ConversionConfig(
                output_path=out_mod,
                duration_sec=5.0,
                quality="HIGH",
                output_mode="ONE_CHANNEL",
                synthetic=True,
            )
            report = convert_audio_to_mod(config)
            self.assertTrue(report["success"])

            val = validate_mod_file(out_mod)
            self.assertTrue(val["is_valid"])
            self.assertEqual(val["active_samples"], 1)  # 5s fits in 1 pattern (7.68s)
        finally:
            if os.path.exists(out_mod):
                os.remove(out_mod)

    def test_synthetic_conversion_ping_pong(self):
        from src.protracker import decode_cell
        out_mod = "test_ping_pong.mod"
        try:
            config = ConversionConfig(
                output_path=out_mod,
                duration_sec=16.0,  # ~3 samples (0..7.68, 7.68..15.36, 15.36..16.0)
                quality="OPTIMAL",  # D-2 (381)
                output_mode="SEAMLESS_PING_PONG",
                synthetic=True,
            )
            report = convert_audio_to_mod(config)
            self.assertTrue(report["success"])

            val = validate_mod_file(out_mod)
            self.assertTrue(val["is_valid"])
            self.assertEqual(val["active_samples"], 3)
            self.assertEqual(val["pattern_count"], 3)

            # Inspect binary pattern cells:
            with open(out_mod, "rb") as f:
                mod_bytes = f.read()

            # Pattern offset starts after header (1084 bytes)
            # Pattern 0:
            pat0_offset = 1084
            # Row 0, Ch 0 (first 4 bytes)
            s0, p0, c0, par0 = decode_cell(mod_bytes[pat0_offset : pat0_offset + 4])
            self.assertEqual(s0, 1)        # Sample 1
            self.assertEqual(p0, 381)      # Period for D-2
            self.assertEqual(c0, 0x0C)     # Command C (Set Volume)
            self.assertEqual(par0, 0x40)   # 64 (Full volume)

            # Pattern 1 (offset: 1084 + 1024 = 2108):
            pat1_offset = 1084 + 1024
            # Row 0, Ch 3 (4 channels per row = 16 bytes per row, Ch 3 is bytes 12..16):
            s1_ch3, p1_ch3, c1_ch3, par1_ch3 = decode_cell(mod_bytes[pat1_offset + 12 : pat1_offset + 16])
            self.assertEqual(s1_ch3, 2)    # Sample 2
            self.assertEqual(p1_ch3, 381)  # Period for D-2
            self.assertEqual(c1_ch3, 0x0C)
            self.assertEqual(par1_ch3, 0x40)

            # Pattern 1, Row 1 (row 1 offset is +16 bytes), Ch 0:
            s1_r1_ch0, p1_r1_ch0, c1_r1_ch0, par1_r1_ch0 = decode_cell(mod_bytes[pat1_offset + 16 : pat1_offset + 20])
            self.assertEqual(c1_r1_ch0, 0x0C)  # Command C (Set Volume)
            self.assertEqual(par1_r1_ch0, 0x00) # 0 (Mute previous Ch 0)

            # Pattern 2 (offset: 1084 + 2048 = 3132):
            pat2_offset = 1084 + 2048
            # Row 0, Ch 0: Sample 3 triggered on Ch 0
            s2_ch0, p2_ch0, c2_ch0, par2_ch0 = decode_cell(mod_bytes[pat2_offset : pat2_offset + 4])
            self.assertEqual(s2_ch0, 3)    # Sample 3
            self.assertEqual(p2_ch0, 381)
            self.assertEqual(c2_ch0, 0x0C)
            self.assertEqual(par2_ch0, 0x40)

            # Pattern 2, Row 1, Ch 3: Mute previous Ch 3
            s2_r1_ch3, p2_r1_ch3, c2_r1_ch3, par2_r1_ch3 = decode_cell(mod_bytes[pat2_offset + 16 + 12 : pat2_offset + 16 + 16])
            self.assertEqual(c2_r1_ch3, 0x0C)
            self.assertEqual(par2_r1_ch3, 0x00)

        finally:
            if os.path.exists(out_mod):
                os.remove(out_mod)


if __name__ == "__main__":
    unittest.main()
