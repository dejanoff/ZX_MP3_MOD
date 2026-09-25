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
        self.assertEqual(res.rows_per_chunk, 32)
        self.assertEqual(res.samples_required, 16)  # 60s / 3.84s (32 rows) = 16 samples
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
            self.assertEqual(val["active_samples"], 3)  # 10s / 3.84s = 3 samples
            # Verify silent loop buffer (repeat_offset points to the end of the sample)
            for s in val["samples"][:3]:
                self.assertEqual(s["repeat_length"], 1)
                self.assertEqual(s["repeat_offset"], s["len_words"] - 1)
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
            self.assertEqual(val["active_samples"], 2)  # 5s / 3.84s = 2 samples
        finally:
            if os.path.exists(out_mod):
                os.remove(out_mod)

    def test_synthetic_conversion_ping_pong(self):
        from src.protracker import decode_cell
        out_mod = "test_ping_pong.mod"
        try:
            config = ConversionConfig(
                output_path=out_mod,
                duration_sec=16.0,  # 16s / 3.84s = 5 samples
                quality="OPTIMAL",  # D-2 (381)
                output_mode="SEAMLESS_PING_PONG",
                synthetic=True,
            )
            report = convert_audio_to_mod(config)
            self.assertTrue(report["success"])

            val = validate_mod_file(out_mod)
            self.assertTrue(val["is_valid"])
            self.assertEqual(val["active_samples"], 5)
            self.assertEqual(val["pattern_count"], 3)

            # Inspect binary pattern cells:
            with open(out_mod, "rb") as f:
                mod_bytes = f.read()

            # Pattern offset starts after header (1084 bytes)
            # Pattern 0:
            pat0_offset = 1084
            # Row 0, Ch 0 (first 4 bytes): Sample 1, Period 381, Vol C40
            s0, p0, c0, par0 = decode_cell(mod_bytes[pat0_offset : pat0_offset + 4])
            self.assertEqual(s0, 1)        # Sample 1
            self.assertEqual(p0, 381)      # Period for D-2
            self.assertEqual(c0, 0x0C)     # Command C (Set Volume)
            self.assertEqual(par0, 0x40)   # 64 (Full volume)

            # Row 0, Ch 1 (bytes 4..8): Speed 6 (F06)
            _, _, c0_ch1, par0_ch1 = decode_cell(mod_bytes[pat0_offset + 4 : pat0_offset + 8])
            self.assertEqual(c0_ch1, 0x0F)
            self.assertEqual(par0_ch1, 0x06)

            # Row 0, Ch 2 (bytes 8..12): BPM 125 (F7D)
            _, _, c0_ch2, par0_ch2 = decode_cell(mod_bytes[pat0_offset + 8 : pat0_offset + 12])
            self.assertEqual(c0_ch2, 0x0F)
            self.assertEqual(par0_ch2, 0x7D)

            # Row 32 of Pattern 0: Chunk 2 triggers on Ch 3 (16 bytes per row * 32 = +512 bytes)
            row32_offset = pat0_offset + (32 * 16)
            s_r32_ch3, p_r32_ch3, c_r32_ch3, par_r32_ch3 = decode_cell(mod_bytes[row32_offset + 12 : row32_offset + 16])
            self.assertEqual(s_r32_ch3, 2)  # Sample 2
            self.assertEqual(p_r32_ch3, 381)
            self.assertEqual(c_r32_ch3, 0x0C)
            self.assertEqual(par_r32_ch3, 0x40)

            # Row 33 of Pattern 0: Previous Ch 0 is muted (C00)
            row33_offset = pat0_offset + (33 * 16)
            _, _, c_r33_ch0, par_r33_ch0 = decode_cell(mod_bytes[row33_offset : row33_offset + 4])
            self.assertEqual(c_r33_ch0, 0x0C)
            self.assertEqual(par_r33_ch0, 0x00)

            # Pattern 1 (offset: 1084 + 1024 = 2108):
            pat1_offset = 1084 + 1024
            # Row 0, Ch 0: Sample 3 triggered on Ch 0
            s1_ch0, p1_ch0, c1_ch0, par1_ch0 = decode_cell(mod_bytes[pat1_offset : pat1_offset + 4])
            self.assertEqual(s1_ch0, 3)    # Sample 3
            self.assertEqual(p1_ch0, 381)
            self.assertEqual(c1_ch0, 0x0C)
            self.assertEqual(par1_ch0, 0x40)

            # Pattern 1, Row 1, Ch 3: Mute previous Ch 3 (C00)
            _, _, c1_r1_ch3, par1_r1_ch3 = decode_cell(mod_bytes[pat1_offset + 16 + 12 : pat1_offset + 16 + 16])
            self.assertEqual(c1_r1_ch3, 0x0C)
            self.assertEqual(par1_r1_ch3, 0x00)

        finally:
            if os.path.exists(out_mod):
                os.remove(out_mod)


if __name__ == "__main__":
    unittest.main()
