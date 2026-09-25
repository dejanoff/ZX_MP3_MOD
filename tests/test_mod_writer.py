"""Tests for ProTracker MOD writer, byte structure, and validator."""

import unittest
from src.protracker import HEADER_SIZE, PATTERN_SIZE, SIGNATURE_MK
from src.mod_writer import (
    ModSample,
    ModPattern,
    ModSong,
    validate_mod_bytes,
    ModValidationError,
    sanitize_latin_filename,
    generate_output_mod_path,
)


class TestModWriter(unittest.TestCase):
    def test_sample_header_packing(self):
        sample = ModSample(
            name="TEST_SAMPLE",
            data=b"\x00\x10\x20\x30",  # 4 bytes = 2 words
            volume=64,
            finetune=0,
            repeat_offset=0,
            repeat_length=1,
        )
        header = sample.pack_header()
        self.assertEqual(len(header), 30)

        # Check name (22 bytes, null padded)
        self.assertTrue(header.startswith(b"TEST_SAMPLE\x00"))
        # Check length in words: 2 words = 0x0002
        self.assertEqual(header[22:24], b"\x00\x02")
        # Check volume
        self.assertEqual(header[25], 64)
        # Check repeat length: 1 word = 0x0001
        self.assertEqual(header[28:30], b"\x00\x01")

    def test_pattern_packing(self):
        pat = ModPattern()
        # Row 0, Channel 0: Sample 1, Period 428
        pat.set_cell(row=0, channel=0, sample_num=1, period=428)
        packed = pat.pack()
        self.assertEqual(len(packed), PATTERN_SIZE)
        # Verify first 4 bytes
        cell = pat.get_cell(0, 0)
        self.assertEqual(cell[0], 1)
        self.assertEqual(cell[1], 428)

    def test_song_packing_and_validation(self):
        song = ModSong(title="MINIMAL TEST")
        sample_data = b"\x01\x02\x03\x04" * 100  # 400 bytes = 200 words
        song.set_sample(1, ModSample(name="CHUNK1", data=sample_data))

        pat = ModPattern()
        pat.set_cell(row=0, channel=0, sample_num=1, period=428)
        song.add_pattern(pat)
        song.order_table = [0]

        packed = song.pack()
        expected_size = HEADER_SIZE + PATTERN_SIZE + 400
        self.assertEqual(len(packed), expected_size)

        # Validate
        report = validate_mod_bytes(packed)
        self.assertTrue(report["is_valid"])
        self.assertEqual(report["title"], "MINIMAL TEST")
        self.assertEqual(report["active_samples"], 1)
        self.assertEqual(report["total_sample_bytes"], 400)
        self.assertEqual(report["pattern_count"], 1)
        self.assertEqual(report["file_size"], expected_size)

    def test_validator_detects_corrupt_signature(self):
        song = ModSong(title="CORRUPT")
        packed = bytearray(song.pack())
        # Break signature (offset 1080)
        packed[1080:1084] = b"XXXX"

        with self.assertRaises(ModValidationError) as ctx:
            validate_mod_bytes(bytes(packed))
        self.assertIn("Invalid MOD signature", str(ctx.exception))

    def test_validator_detects_truncated_file(self):
        song = ModSong(title="TRUNC")
        sample_data = b"\x10\x20" * 50  # 100 bytes
        song.set_sample(1, ModSample(name="S1", data=sample_data))
        song.add_pattern(ModPattern())
        song.order_table = [0]

        packed = song.pack()
        # Truncate by 20 bytes
        truncated = packed[:-20]

        with self.assertRaises(ModValidationError) as ctx:
            validate_mod_bytes(truncated)
        self.assertIn("truncated", str(ctx.exception).lower())

    def test_sanitize_latin_filename(self):
        # Russian title transliterates to Latin without illegal characters
        name = "Ворона - Кэнни"
        latin = sanitize_latin_filename(name)
        self.assertEqual(latin, "Vorona_Kenni")

        # Mixed / already Latin
        latin_mix = sanitize_latin_filename("Песня #1 (Remix)")
        self.assertEqual(latin_mix, "Pesnya_1_Remix")

    def test_generate_output_mod_path_and_autonumbering(self):
        import os, tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            p1 = generate_output_mod_path(
                input_path="Ворона - Кэнни.mp3",
                output_dir=tmpdir,
                sample_rate=9309,
                output_mode="ONE_CHANNEL",
                duration_sec=60.0,
            )
            # Check filename format: Vorona_Kenni_9309Hz_mono_60s.mod
            base1 = os.path.basename(p1)
            self.assertEqual(base1, "Vorona_Kenni_9309Hz_mono_60s.mod")

            # Create file at p1 to test auto-numbering
            with open(p1, "w") as f:
                f.write("dummy")

            # Generating again should append _01.mod
            p2 = generate_output_mod_path(
                input_path="Ворона - Кэнни.mp3",
                output_dir=tmpdir,
                sample_rate=9309,
                output_mode="ONE_CHANNEL",
                duration_sec=60.0,
            )
            base2 = os.path.basename(p2)
            self.assertEqual(base2, "Vorona_Kenni_9309Hz_mono_60s_01.mod")

            # Create file at p2
            with open(p2, "w") as f:
                f.write("dummy2")

            # Generating again should append _02.mod
            p3 = generate_output_mod_path(
                input_path="Ворона - Кэнни.mp3",
                output_dir=tmpdir,
                sample_rate=9309,
                output_mode="ONE_CHANNEL",
                duration_sec=60.0,
            )
            base3 = os.path.basename(p3)
            self.assertEqual(base3, "Vorona_Kenni_9309Hz_mono_60s_02.mod")


if __name__ == "__main__":
    unittest.main()
