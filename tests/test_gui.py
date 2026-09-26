"""Unit tests for ModConverterGUI widgets, sliders, title handling, and path normalization."""

import unittest
import tkinter as tk
import os
from unittest.mock import patch
from src.gui import ModConverterGUI
from src.converter import ConversionConfig
from src.mod_writer import to_ascii_safe


class TestGUI(unittest.TestCase):
    def setUp(self):
        self.load_patch = patch("src.gui.load_config", return_value={})
        self.save_patch = patch("src.gui.save_config")
        self.load_patch.start()
        self.save_patch.start()

        self.root = tk.Tk()
        self.root.withdraw()  # Do not display window during tests
        self.app = ModConverterGUI(self.root)

    def tearDown(self):
        try:
            self.root.destroy()
        except Exception:
            pass
        self.load_patch.stop()
        self.save_patch.stop()

    def test_gui_initialization_defaults(self):
        self.assertEqual(self.app.start_entry.get(), "00:00")
        self.assertEqual(self.app.duration_entry.get(), "60")
        self.assertEqual(self.app.start_scale.get(), 0.0)
        self.assertEqual(self.app.duration_scale.get(), 60.0)

    def test_start_slider_and_entry_sync(self):
        # Move start slider
        self.app._on_start_slider_move(75.0)
        self.assertEqual(self.app.start_entry.get(), "01:15")

        # Edit start entry
        self.app.start_entry.delete(0, tk.END)
        self.app.start_entry.insert(0, "02:30")
        self.app._on_start_entry_changed()
        self.assertEqual(self.app.start_scale.get(), 150.0)

    def test_duration_slider_and_entry_sync(self):
        # Move duration slider
        self.app._on_duration_slider_move(90.0)
        self.assertEqual(self.app.duration_entry.get(), "90")

        # Edit duration entry
        self.app.duration_entry.delete(0, tk.END)
        self.app.duration_entry.insert(0, "45")
        self.app._on_duration_entry_changed()
        self.assertEqual(self.app.duration_scale.get(), 45.0)

    def test_song_title_character_limit_and_config(self):
        # Title should truncate to 20 characters
        long_title = "THIS IS A VERY LONG TRACK TITLE EXCEEDING TWENTY CHARACTERS"
        self.app.title_var.set(long_title)
        self.assertEqual(len(self.app.title_var.get()), 20)
        self.assertEqual(self.app.title_var.get(), long_title[:20])

        # Cyrillic title transliterates safely
        cyrillic_title = "Песня года"
        self.app.title_var.set(cyrillic_title)
        cfg = self.app._build_config()
        self.assertEqual(cfg.title, to_ascii_safe(cyrillic_title)[:20])

    def test_path_normalization_slashes(self):
        # Input with forward slashes (like filedialog returns) should normalize to backslashes
        test_path = "C:/Music/Track/Song.mp3"
        self.app.input_entry.delete(0, tk.END)
        self.app.input_entry.insert(0, test_path)
        self.app._on_input_entry_changed()

        expected = os.path.normpath(test_path)
        self.assertEqual(self.app.input_entry.get(), expected)
        # Output MOD path should also use the same system slash
        out_val = self.app.output_entry.get()
        self.assertEqual(out_val, os.path.normpath(out_val))
        self.assertNotIn("/", out_val if os.sep == "\\" else "\\")


if __name__ == "__main__":
    unittest.main()
