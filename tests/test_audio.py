"""Tests for audio utilities, time string parsing, and edge smoothing."""

import unittest
import struct
from src.audio import (
    parse_time_str,
    format_time_str,
    find_ffmpeg,
    smooth_chunk_edges,
    find_nearest_zero_crossing,
    generate_synthetic_pcm,
)


class TestAudioUtils(unittest.TestCase):
    def test_parse_time_str(self):
        self.assertEqual(parse_time_str(45), 45.0)
        self.assertEqual(parse_time_str("45"), 45.0)
        self.assertEqual(parse_time_str("00:45"), 45.0)
        self.assertEqual(parse_time_str("01:30"), 90.0)
        self.assertEqual(parse_time_str("01:00:00"), 3600.0)
        self.assertEqual(parse_time_str("02:15:30"), 8130.0)
        self.assertEqual(parse_time_str("12.5"), 12.5)

    def test_format_time_str(self):
        self.assertEqual(format_time_str(45.0), "00:45")
        self.assertEqual(format_time_str(90.0), "01:30")
        self.assertEqual(format_time_str(3600.0), "60:00")

    def test_find_ffmpeg(self):
        # On this machine, FFmpeg is installed and available
        path = find_ffmpeg()
        self.assertIsNotNone(path)
        self.assertTrue(path.lower().endswith("ffmpeg.exe") or "ffmpeg" in path.lower())

    def test_synthetic_pcm_generation(self):
        pcm = generate_synthetic_pcm(duration_sec=1.0, sample_rate=8000, freq=440.0)
        self.assertEqual(len(pcm), 8000)
        # Check signed range
        samples = struct.unpack("8000b", pcm)
        for s in samples:
            self.assertTrue(-128 <= s <= 127)

    def test_smooth_chunk_edges(self):
        # Create a block of full positive samples (+100)
        data = struct.pack("100b", *([100] * 100))
        smoothed = smooth_chunk_edges(data, fade_samples=16)
        self.assertEqual(len(smoothed), 100)

        samples = struct.unpack("100b", smoothed)
        # First sample should be close to 0
        self.assertEqual(samples[0], 0)
        # Middle samples should be unchanged (+100)
        self.assertEqual(samples[50], 100)
        # Last sample should be close to 0
        self.assertEqual(samples[-1], 0)

    def test_apply_linear_crossfade(self):
        from src.audio import apply_linear_crossfade
        data = struct.pack("100b", *([100] * 100))
        # Fade in 20 samples, fade out 20 samples
        faded = apply_linear_crossfade(data, fade_in_samples=20, fade_out_samples=20)
        samples = struct.unpack("100b", faded)
        self.assertEqual(samples[0], 0)
        self.assertEqual(samples[10], 50)
        self.assertEqual(samples[20], 100)
        self.assertEqual(samples[50], 100)
        self.assertEqual(samples[80], 100)
        self.assertEqual(samples[90], 50)
        self.assertEqual(samples[99], 5)  # 100 * (1 - 19/20) = 5

    def test_find_nearest_zero_crossing(self):
        # Create a waveform transitioning from -50 to +50 around index 20
        raw_list = [-50] * 20 + [50] * 20
        # Byte packing
        data = struct.pack(f"{len(raw_list)}b", *raw_list)
        idx = find_nearest_zero_crossing(data, target_idx=18, window=10)
        self.assertTrue(19 <= idx <= 20)


if __name__ == "__main__":
    unittest.main()
