"""Tests for protracker constants, periods, timing, and cell encoding."""

import unittest
from src.protracker import (
    PAL_CLOCK,
    PERIOD_TABLE,
    PERIOD_TO_NOTE,
    QUALITY_PRESETS,
    period_to_frequency,
    frequency_to_nearest_note_period,
    get_tick_rate,
    get_row_duration,
    get_pattern_duration,
    encode_cell,
    decode_cell,
)


class TestProTracker(unittest.TestCase):
    def test_pal_clock(self):
        self.assertAlmostEqual(PAL_CLOCK, 3546895.0, places=1)

    def test_period_to_frequency(self):
        # C-2 has period 428
        freq_c2 = period_to_frequency(428)
        self.assertAlmostEqual(freq_c2, 8287.1378, places=3)

        # F-2 has period 320
        freq_f2 = period_to_frequency(320)
        self.assertAlmostEqual(freq_f2, 11084.0468, places=3)

        # G-1 has period 570
        freq_g1 = period_to_frequency(570)
        self.assertAlmostEqual(freq_g1, 6222.6228, places=3)

    def test_frequency_to_nearest_note_period(self):
        # 8000 Hz should map closest to C-2 (428) or B-1 (453)
        note, period, freq = frequency_to_nearest_note_period(8000)
        self.assertEqual(period, 453)  # 3546895/8000 = 443.36 -> closest in table is 453 (7829.8 Hz) or 428 (8287.1 Hz). |453-443.36|=9.64, |428-443.36|=15.36
        self.assertEqual(note, "B-1")

        # 8300 Hz should map closest to C-2 (428)
        note, period, freq = frequency_to_nearest_note_period(8300)
        self.assertEqual(note, "C-2")
        self.assertEqual(period, 428)

        # 11000 Hz should map closest to F-2 (320)
        note, period, freq = frequency_to_nearest_note_period(11000)
        self.assertEqual(note, "F-2")
        self.assertEqual(period, 320)

    def test_tracker_timing(self):
        # At BPM 125, tick rate is 50 Hz (VBlank)
        self.assertEqual(get_tick_rate(125), 50.0)

        # At Speed 6, row duration is 6 / 50 = 0.12 s
        self.assertAlmostEqual(get_row_duration(6, 125), 0.12, places=4)

        # 64 rows = 64 * 0.12 = 7.68 s
        self.assertAlmostEqual(get_pattern_duration(64, 6, 125), 7.68, places=4)

    def test_cell_encoding_decoding(self):
        # Test Sample 1, Period 428 (0x01AC), Effect 0x0B (Position Jump), Param 0x02
        sample_num = 1
        period = 428
        effect_cmd = 0x0B
        effect_param = 0x02

        cell_bytes = encode_cell(sample_num, period, effect_cmd, effect_param)
        self.assertEqual(len(cell_bytes), 4)

        dec_s, dec_p, dec_cmd, dec_param = decode_cell(cell_bytes)
        self.assertEqual(dec_s, sample_num)
        self.assertEqual(dec_p, period)
        self.assertEqual(dec_cmd, effect_cmd)
        self.assertEqual(dec_param, effect_param)

    def test_cell_encoding_high_sample_number(self):
        # Test Sample 31 (0x1F), Period 856 (0x0358), Effect 0x0F, Param 0x06
        sample_num = 31
        period = 856
        effect_cmd = 0x0F
        effect_param = 0x06

        cell_bytes = encode_cell(sample_num, period, effect_cmd, effect_param)
        dec_s, dec_p, dec_cmd, dec_param = decode_cell(cell_bytes)
        self.assertEqual(dec_s, 31)
        self.assertEqual(dec_p, 856)
        self.assertEqual(dec_cmd, 0x0F)
        self.assertEqual(dec_param, 0x06)


if __name__ == "__main__":
    unittest.main()
