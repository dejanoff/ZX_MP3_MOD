"""Portable Windows GUI for MP3/WAV to ProTracker MOD converter.

Built with Python standard library tkinter/ttk.
No external GUI dependencies required.
"""

import os
import sys
import json
import math
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText
from typing import Optional

from .version import __version__
from .protracker import QUALITY_PRESETS, period_to_frequency
from .audio import find_ffmpeg, parse_time_str, format_time_str, probe_audio_file
from .mod_writer import generate_output_mod_path, sanitize_latin_filename, to_ascii_safe
from .converter import (
    ConversionConfig,
    AnalysisResult,
    MEMORY_TARGETS,
    analyze_conversion,
    convert_audio_to_mod,
)


def get_config_path() -> str:
    """Get path to config.json for storing GUI preferences."""
    if getattr(sys, "frozen", False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, "config.json")


def load_config() -> dict:
    """Load persistent settings from config.json."""
    p = get_config_path()
    if os.path.isfile(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_config(cfg: dict) -> None:
    """Save persistent settings to config.json."""
    p = get_config_path()
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


class ModConverterGUI:
    """Tkinter-based GUI for the audio to ProTracker MOD converter."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"ZX Spectrum MP3 to ProTracker MOD Converter (General Sound) v{__version__}")
        self.root.geometry("760x720")
        self.root.minsize(660, 600)

        # Load persistent configuration
        self.config_data = load_config()
        self.last_input_dir = os.path.normpath(self.config_data.get("last_input_dir", "")) if self.config_data.get("last_input_dir") else ""
        self.last_output_dir = os.path.normpath(self.config_data.get("last_output_dir", "")) if self.config_data.get("last_output_dir") else ""

        self.total_audio_duration = 300.0
        self._updating_widgets = False
        self._title_user_edited = False

        # Style configuration
        self.style = ttk.Style()
        try:
            self.style.theme_use("vista")
        except Exception:
            pass

        self._create_widgets()
        self._restore_settings()
        self._check_ffmpeg()

    def _create_widgets(self):
        main_frame = ttk.Frame(self.root, padding="10 10 10 10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # --- 1. File Selection Frame ---
        file_frame = ttk.LabelFrame(main_frame, text=" Audio Files & Song Title ", padding="8 8 8 8")
        file_frame.pack(fill=tk.X, pady=(0, 8))

        # Input file
        ttk.Label(file_frame, text="Input Audio:").grid(row=0, column=0, sticky=tk.W, pady=3)
        self.input_entry = ttk.Entry(file_frame)
        self.input_entry.grid(row=0, column=1, sticky=tk.EW, padx=(5, 5), pady=3)
        ttk.Button(file_frame, text="Browse Audio...", command=self._browse_input).grid(row=0, column=2, pady=3)

        # Output file
        ttk.Label(file_frame, text="Output MOD:").grid(row=1, column=0, sticky=tk.W, pady=3)
        self.output_entry = ttk.Entry(file_frame)
        self.output_entry.grid(row=1, column=1, sticky=tk.EW, padx=(5, 5), pady=3)

        btn_box = ttk.Frame(file_frame)
        btn_box.grid(row=1, column=2, pady=3)
        ttk.Button(btn_box, text="Browse File...", command=self._browse_output).pack(side=tk.LEFT, padx=(0, 3))
        ttk.Button(btn_box, text="Folder...", command=self._browse_output_dir).pack(side=tk.LEFT)

        # Song Title (MOD Header, max 20 chars, displayed in Wild Player)
        ttk.Label(file_frame, text="Song Title:").grid(row=2, column=0, sticky=tk.W, pady=3)
        self.title_var = tk.StringVar(value="")
        self.title_var.trace_add("write", self._on_title_var_changed)

        self.title_entry = ttk.Entry(file_frame, textvariable=self.title_var)
        self.title_entry.grid(row=2, column=1, sticky=tk.EW, padx=(5, 5), pady=3)
        self.title_entry.bind("<Key>", lambda e: setattr(self, "_title_user_edited", True))

        self.title_count_label = ttk.Label(file_frame, text="0 / 20 chars (in player)", foreground="#555555")
        self.title_count_label.grid(row=2, column=2, sticky=tk.W, padx=(5, 0), pady=3)

        file_frame.columnconfigure(1, weight=1)

        # --- 2. Conversion Settings Frame ---
        settings_frame = ttk.LabelFrame(main_frame, text=" Conversion Settings ", padding="8 8 8 8")
        settings_frame.pack(fill=tk.X, pady=(0, 8))

        # Start position with Slider
        ttk.Label(settings_frame, text="Start Position:").grid(row=0, column=0, sticky=tk.W, pady=4)
        start_box = ttk.Frame(settings_frame)
        start_box.grid(row=0, column=1, columnspan=2, sticky=tk.EW, padx=5, pady=4)

        self.start_entry = ttk.Entry(start_box, width=8)
        self.start_entry.insert(0, "00:00")
        self.start_entry.pack(side=tk.LEFT, padx=(0, 6))

        self.start_scale = ttk.Scale(
            start_box,
            orient=tk.HORIZONTAL,
            from_=0,
            to=300,
            command=self._on_start_slider_move,
        )
        self.start_scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        self.start_scale.bind("<ButtonRelease-1>", self._on_start_slider_release)

        self.start_max_label = ttk.Label(start_box, text="(max: 05:00)", width=14)
        self.start_max_label.pack(side=tk.LEFT)

        # Duration with Slider
        ttk.Label(settings_frame, text="Duration:").grid(row=1, column=0, sticky=tk.W, pady=4)
        dur_box = ttk.Frame(settings_frame)
        dur_box.grid(row=1, column=1, columnspan=2, sticky=tk.EW, padx=5, pady=4)

        self.duration_entry = ttk.Entry(dur_box, width=8)
        self.duration_entry.insert(0, "60")
        self.duration_entry.pack(side=tk.LEFT, padx=(0, 6))

        self.duration_scale = ttk.Scale(
            dur_box,
            orient=tk.HORIZONTAL,
            from_=1,
            to=240,
            command=self._on_duration_slider_move,
        )
        self.duration_scale.set(60)
        self.duration_scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        self.duration_scale.bind("<ButtonRelease-1>", self._on_duration_slider_release)

        self.duration_max_label = ttk.Label(dur_box, text="sec (max: 240s)", width=14)
        self.duration_max_label.pack(side=tk.LEFT)

        # Quality Preset
        ttk.Label(settings_frame, text="Quality:").grid(row=2, column=0, sticky=tk.W, pady=4)
        self.quality_combo = ttk.Combobox(
            settings_frame,
            values=[
                "OPTIMAL (9309 Hz / D-2 - Recommended for GS)",
                "NORMAL (8287 Hz / C-2)",
                "LOW (6223 Hz / G-1)",
                "C#2 (8779 Hz)",
                "D#2 (9852 Hz)",
                "E-2 (10463 Hz)",
                "HIGH (11084 Hz / F-2)",
                "AUTO (Best fit for GS RAM)",
            ],
            state="readonly",
            width=38,
        )
        self.quality_combo.current(0)  # OPTIMAL
        self.quality_combo.grid(row=2, column=1, columnspan=2, sticky=tk.EW, padx=5, pady=4)

        # GS Memory Target
        ttk.Label(settings_frame, text="GS Memory:").grid(row=3, column=0, sticky=tk.W, pady=4)
        mem_box = ttk.Frame(settings_frame)
        mem_box.grid(row=3, column=1, columnspan=2, sticky=tk.EW, padx=5, pady=4)
        self.memory_combo = ttk.Combobox(
            mem_box,
            values=["1 MB", "512 KB", "2 MB", "4 MB", "Unlimited"],
            state="readonly",
            width=15,
        )
        self.memory_combo.current(0)  # 1 MB
        self.memory_combo.pack(side=tk.LEFT, padx=(0, 8))
        ttk.Label(mem_box, text="(General Sound RAM target)").pack(side=tk.LEFT)

        # Output Mode
        ttk.Label(settings_frame, text="Output Mode:").grid(row=4, column=0, sticky=tk.W, pady=4)
        self.mode_combo = ttk.Combobox(
            settings_frame,
            values=[
                "SEAMLESS PING-PONG (Mono Ch 0<->3 Overlap - Recommended for GS)",
                "ONE CHANNEL (Sequential on Ch 0)",
                "CENTERED (Dual channel Left + Right)",
            ],
            state="readonly",
            width=44,
        )
        self.mode_combo.current(0)  # SEAMLESS PING-PONG
        self.mode_combo.grid(row=4, column=1, columnspan=2, sticky=tk.EW, padx=5, pady=4)

        # Stereo Downmix
        ttk.Label(settings_frame, text="Stereo Downmix:").grid(row=5, column=0, sticky=tk.W, pady=4)
        self.downmix_combo = ttk.Combobox(
            settings_frame,
            values=[
                "Mix Stereo (0.5*L + 0.5*R) -> Mono (Recommended)",
                "Left Channel Only",
                "Right Channel Only",
            ],
            state="readonly",
            width=38,
        )
        self.downmix_combo.current(0)  # Mix Stereo
        self.downmix_combo.grid(row=5, column=1, columnspan=2, sticky=tk.EW, padx=5, pady=4)

        # Anti-click edge smoothing
        self.smooth_var = tk.BooleanVar(value=True)
        self.smooth_check = ttk.Checkbutton(
            settings_frame,
            text="Smooth chunk edges (zero-crossing micro-fade to eliminate clicks)",
            variable=self.smooth_var,
        )
        self.smooth_check.grid(row=6, column=0, columnspan=3, sticky=tk.W, pady=(6, 2))

        settings_frame.columnconfigure(1, weight=1)

        # Dynamic bindings to auto-update filename, analysis and config
        self.quality_combo.bind("<<ComboboxSelected>>", lambda e: self._on_setting_changed())
        self.mode_combo.bind("<<ComboboxSelected>>", lambda e: self._on_setting_changed())
        self.downmix_combo.bind("<<ComboboxSelected>>", lambda e: self._on_setting_changed())
        self.memory_combo.bind("<<ComboboxSelected>>", lambda e: self._on_setting_changed())
        self.duration_entry.bind("<FocusOut>", self._on_duration_entry_changed)
        self.duration_entry.bind("<Return>", self._on_duration_entry_changed)
        self.start_entry.bind("<FocusOut>", self._on_start_entry_changed)
        self.start_entry.bind("<Return>", self._on_start_entry_changed)
        self.input_entry.bind("<FocusOut>", self._on_input_entry_changed)
        self.input_entry.bind("<Return>", self._on_input_entry_changed)
        self.output_entry.bind("<FocusOut>", self._on_output_entry_changed)
        self.output_entry.bind("<Return>", self._on_output_entry_changed)
        self.title_entry.bind("<FocusOut>", lambda e: self._on_setting_changed())
        self.title_entry.bind("<Return>", lambda e: self._on_setting_changed())

        # --- 3. Action Buttons & Analysis Summary ---
        action_frame = ttk.Frame(main_frame)
        action_frame.pack(fill=tk.X, pady=(0, 6))

        self.btn_analyze = ttk.Button(action_frame, text="[ Analyze Settings ]", command=self._on_analyze)
        self.btn_analyze.pack(side=tk.LEFT, padx=(0, 8))

        self.btn_convert = ttk.Button(action_frame, text="[ Convert to MOD ]", command=self._on_convert)
        self.btn_convert.pack(side=tk.LEFT, padx=(0, 8))

        # Analysis summary box
        self.summary_frame = ttk.LabelFrame(main_frame, text=" Estimated MOD Metrics ", padding="6 6 6 6")
        self.summary_frame.pack(fill=tk.X, pady=(0, 8))

        self.summary_label = ttk.Label(
            self.summary_frame,
            text="Click [ Analyze Settings ] or select a file to preview MOD parameters.",
            foreground="#333333",
            justify=tk.LEFT,
        )
        self.summary_label.pack(fill=tk.X, anchor=tk.W)

        # --- 4. Progress and Log ---
        progress_frame = ttk.Frame(main_frame)
        progress_frame.pack(fill=tk.X, pady=(0, 4))

        self.prog_bar = ttk.Progressbar(progress_frame, orient=tk.HORIZONTAL, mode="determinate")
        self.prog_bar.pack(fill=tk.X, side=tk.TOP, pady=(0, 2))

        self.status_label = ttk.Label(progress_frame, text="Ready", font=("Segoe UI", 9))
        self.status_label.pack(side=tk.LEFT, anchor=tk.W)

        # Log ScrolledText
        log_frame = ttk.LabelFrame(main_frame, text=" Conversion Log ", padding="4 4 4 4")
        log_frame.pack(fill=tk.BOTH, expand=True)

        self.log_text = ScrolledText(log_frame, wrap=tk.WORD, height=9, font=("Consolas", 9))
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # Status Bar at bottom
        self.bottom_bar = ttk.Label(
            self.root,
            text="",
            relief=tk.SUNKEN,
            anchor=tk.W,
            padding="4 2 4 2",
            font=("Segoe UI", 8),
        )
        self.bottom_bar.pack(side=tk.BOTTOM, fill=tk.X)

    def _check_ffmpeg(self):
        """Check FFmpeg availability and update bottom status bar."""
        ff_path = find_ffmpeg()
        if ff_path:
            self.bottom_bar.config(
                text=f"FFmpeg detected: {ff_path}",
                foreground="#006600",
            )
            self._log(f"FFmpeg ready: {ff_path}")
        else:
            self.bottom_bar.config(
                text="WARNING: ffmpeg.exe not found! Place ffmpeg.exe next to this app or add to PATH.",
                foreground="#cc0000",
            )
            self._log("WARNING: ffmpeg.exe not found! Decoding MP3/FLAC/etc. requires FFmpeg.")

    def _log(self, msg: str):
        """Append message to log box."""
        self.log_text.insert(tk.END, msg + "\n")
        self.log_text.see(tk.END)

    def _on_title_var_changed(self, *args):
        val = self.title_var.get()
        if len(val) > 20:
            self.title_var.set(val[:20])
            val = self.title_var.get()
        self.title_count_label.config(text=f"{len(val)} / 20 chars (in player)")

    def _on_start_slider_move(self, val):
        if self._updating_widgets:
            return
        sec = int(round(float(val)))
        self._updating_widgets = True
        try:
            self.start_entry.delete(0, tk.END)
            self.start_entry.insert(0, format_time_str(sec))
        finally:
            self._updating_widgets = False

    def _on_start_slider_release(self, event=None):
        self._on_setting_changed()

    def _on_start_entry_changed(self, event=None):
        if self._updating_widgets:
            return
        try:
            sec = parse_time_str(self.start_entry.get())
        except Exception:
            sec = 0.0
        sec = max(0.0, sec)
        max_limit = float(self.start_scale.cget("to"))
        if sec > max_limit:
            self.start_scale.config(to=max(sec, self.total_audio_duration))
            self.start_max_label.config(text=f"(max: {format_time_str(self.start_scale.cget('to'))})")
        self._updating_widgets = True
        try:
            self.start_scale.set(sec)
        finally:
            self._updating_widgets = False
        self._on_setting_changed()

    def _on_duration_slider_move(self, val):
        if self._updating_widgets:
            return
        sec = max(1, int(round(float(val))))
        self._updating_widgets = True
        try:
            self.duration_entry.delete(0, tk.END)
            self.duration_entry.insert(0, str(sec))
        finally:
            self._updating_widgets = False

    def _on_duration_slider_release(self, event=None):
        self._on_setting_changed()

    def _on_duration_entry_changed(self, event=None):
        if self._updating_widgets:
            return
        try:
            sec = parse_time_str(self.duration_entry.get())
        except Exception:
            sec = 60.0
        sec = max(1.0, sec)
        max_limit = float(self.duration_scale.cget("to"))
        if sec > max_limit:
            self.duration_scale.config(to=max(sec, self.total_audio_duration))
            self.duration_max_label.config(text=f"sec (max: {int(self.duration_scale.cget('to'))}s)")
        self._updating_widgets = True
        try:
            self.duration_scale.set(sec)
        finally:
            self._updating_widgets = False
        self._on_setting_changed()

    def _on_input_entry_changed(self, event=None):
        val = self.input_entry.get().strip()
        if val:
            norm = os.path.normpath(val)
            if norm != val:
                self.input_entry.delete(0, tk.END)
                self.input_entry.insert(0, norm)
            if not self._title_user_edited and not self.title_var.get().strip():
                base_name = os.path.splitext(os.path.basename(norm))[0]
                self.title_var.set(to_ascii_safe(base_name)[:20].strip())
            self._auto_update_output_path()
            if os.path.isfile(norm):
                self._probe_file(norm)
            self._on_setting_changed()

    def _on_output_entry_changed(self, event=None):
        val = self.output_entry.get().strip()
        if val:
            norm = os.path.normpath(val)
            if norm != val:
                self.output_entry.delete(0, tk.END)
                self.output_entry.insert(0, norm)

    def _restore_settings(self):
        """Restore previous settings from config."""
        c = self.config_data
        if "quality" in c:
            for i, val in enumerate(self.quality_combo["values"]):
                if c["quality"] in val:
                    self.quality_combo.current(i)
                    break
        if "memory" in c and c["memory"] in self.memory_combo["values"]:
            self.memory_combo.set(c["memory"])
        if "mode" in c:
            found = False
            for i, val in enumerate(self.mode_combo["values"]):
                if c["mode"] == val or ("PING" in c["mode"].upper() and "PING" in val.upper()):
                    self.mode_combo.current(i)
                    found = True
                    break
            if not found:
                self.mode_combo.current(0)
        if "downmix" in c:
            for i, val in enumerate(self.downmix_combo["values"]):
                if c["downmix"] in val:
                    self.downmix_combo.current(i)
                    break
        if "duration" in c:
            self.duration_entry.delete(0, tk.END)
            self.duration_entry.insert(0, str(c["duration"]))
            try:
                d_val = float(c["duration"])
                self.duration_scale.set(d_val)
            except Exception:
                pass
        if "start" in c:
            self.start_entry.delete(0, tk.END)
            self.start_entry.insert(0, str(c["start"]))
            try:
                s_val = parse_time_str(c["start"])
                self.start_scale.set(s_val)
            except Exception:
                pass
        if "smooth" in c:
            self.smooth_var.set(bool(c["smooth"]))
        if "title" in c and c["title"]:
            self.title_var.set(c["title"])
            self._title_user_edited = True

    def _save_state(self):
        """Save current folder and options to config."""
        cfg = {
            "last_input_dir": self.last_input_dir,
            "last_output_dir": self.last_output_dir,
            "quality": self.quality_combo.get(),
            "memory": self.memory_combo.get(),
            "mode": self.mode_combo.get(),
            "downmix": self.downmix_combo.get(),
            "duration": self.duration_entry.get().strip(),
            "start": self.start_entry.get().strip(),
            "title": self.title_var.get().strip(),
            "smooth": self.smooth_var.get(),
        }
        self.config_data = cfg
        save_config(cfg)

    def _on_setting_changed(self):
        """Called when quality, duration, mode, start or title changes."""
        self._auto_update_output_path()
        self._on_analyze()
        self._save_state()

    def _auto_update_output_path(self):
        """Generate Latin-transliterated, settings-encoded, auto-numbered output path."""
        inp = self.input_entry.get().strip()
        if not inp:
            return
        inp = os.path.normpath(inp)

        # Resolve playback rate
        q_raw = self.quality_combo.get().strip().upper()
        if "OPTIMAL" in q_raw or "D-2" in q_raw:
            rate = 9309
        elif "NORMAL" in q_raw or "C-2" in q_raw:
            rate = 8287
        elif "LOW" in q_raw or "G-1" in q_raw:
            rate = 6223
        elif "HIGH" in q_raw or "F-2" in q_raw:
            rate = 11084
        elif "8779" in q_raw:
            rate = 8779
        elif "9852" in q_raw:
            rate = 9852
        elif "10463" in q_raw:
            rate = 10463
        else:
            rate = 9309

        mode_str = self.mode_combo.get().upper()
        if "PING" in mode_str:
            out_mode = "SEAMLESS_PING_PONG"
        elif "ONE" in mode_str:
            out_mode = "ONE_CHANNEL"
        else:
            out_mode = "CENTERED"
        try:
            dur = parse_time_str(self.duration_entry.get())
            if dur <= 0:
                dur = 60.0
        except Exception:
            dur = 60.0

        # Output folder: use last_output_dir if valid, otherwise last_input_dir or current working dir
        if self.last_output_dir and os.path.isdir(self.last_output_dir):
            out_dir = self.last_output_dir
        elif self.last_input_dir and os.path.isdir(self.last_input_dir):
            out_dir = self.last_input_dir
        else:
            out_dir = os.path.dirname(inp)

        new_mod_path = os.path.normpath(generate_output_mod_path(
            input_path=inp,
            output_dir=out_dir,
            sample_rate=rate,
            output_mode=out_mode,
            duration_sec=dur,
        ))
        self.output_entry.delete(0, tk.END)
        self.output_entry.insert(0, new_mod_path)

    def _browse_input(self):
        init_dir = self.last_input_dir if (self.last_input_dir and os.path.isdir(self.last_input_dir)) else os.getcwd()
        f = filedialog.askopenfilename(
            title="Select Audio File",
            initialdir=init_dir,
            filetypes=[
                ("All Supported Audio", "*.mp3 *.wav *.flac *.ogg *.m4a"),
                ("MP3 Audio", "*.mp3"),
                ("WAV Audio", "*.wav"),
                ("FLAC Audio", "*.flac"),
                ("OGG Audio", "*.ogg"),
                ("M4A Audio", "*.m4a"),
                ("All Files", "*.*"),
            ],
        )
        if f:
            f = os.path.normpath(f)
            self.last_input_dir = os.path.normpath(os.path.dirname(f))
            self._save_state()
            self.input_entry.delete(0, tk.END)
            self.input_entry.insert(0, f)

            # Auto-populate title if not manually edited by user
            base_name = os.path.splitext(os.path.basename(f))[0]
            if not self._title_user_edited:
                auto_title = to_ascii_safe(base_name)[:20].strip()
                self.title_var.set(auto_title)
                self._title_user_edited = False

            # Auto-generate Latin output filename with settings
            self._auto_update_output_path()

            # Probe audio duration
            self._probe_file(f)

    def _browse_output(self):
        init_dir = self.last_output_dir if (self.last_output_dir and os.path.isdir(self.last_output_dir)) else (self.last_input_dir or os.getcwd())
        curr_out = self.output_entry.get().strip()
        init_file = os.path.basename(curr_out) if curr_out else "SONG.MOD"

        f = filedialog.asksaveasfilename(
            title="Save MOD File",
            initialdir=init_dir,
            initialfile=init_file,
            defaultextension=".mod",
            filetypes=[("ProTracker MOD File", "*.mod"), ("All Files", "*.*")],
        )
        if f:
            f = os.path.normpath(f)
            self.last_output_dir = os.path.normpath(os.path.dirname(f))
            self._save_state()
            self.output_entry.delete(0, tk.END)
            self.output_entry.insert(0, f)

    def _browse_output_dir(self):
        init_dir = self.last_output_dir if (self.last_output_dir and os.path.isdir(self.last_output_dir)) else (self.last_input_dir or os.getcwd())
        d = filedialog.askdirectory(
            title="Select Export Folder for MOD Files",
            initialdir=init_dir,
        )
        if d:
            self.last_output_dir = os.path.normpath(d)
            self._save_state()
            self._auto_update_output_path()
            self._log(f"Export folder set to: {self.last_output_dir}")

    def _probe_file(self, filepath: str):
        try:
            info = probe_audio_file(filepath)
            dur = info.get("duration", 0.0)
            if dur > 0:
                self.total_audio_duration = dur
                self._log(f"Loaded '{os.path.basename(filepath)}': Total Duration = {format_time_str(dur)} ({dur:.1f}s)")

                # Update slider max ranges
                max_sec = max(10.0, math.ceil(dur))
                self.start_scale.config(to=max_sec)
                self.start_max_label.config(text=f"(max: {format_time_str(max_sec)})")

                self.duration_scale.config(to=max_sec)
                self.duration_max_label.config(text=f"sec (max: {int(max_sec)}s)")

                # If current duration exceeds file duration, adjust it
                curr_dur = parse_time_str(self.duration_entry.get())
                if curr_dur > dur:
                    self._updating_widgets = True
                    try:
                        self.duration_entry.delete(0, tk.END)
                        self.duration_entry.insert(0, str(int(dur)))
                        self.duration_scale.set(int(dur))
                    finally:
                        self._updating_widgets = False
            self._on_analyze()
        except Exception as e:
            self._log(f"Probe notice: {e}")

    def _build_config(self) -> ConversionConfig:
        inp = os.path.normpath(self.input_entry.get().strip()) if self.input_entry.get().strip() else ""
        outp = os.path.normpath(self.output_entry.get().strip()) if self.output_entry.get().strip() else ""
        if not outp:
            if inp:
                base, _ = os.path.splitext(inp)
                outp = base + ".mod"
            else:
                outp = "output.mod"

        start_s = parse_time_str(self.start_entry.get())
        dur_s = parse_time_str(self.duration_entry.get())
        if dur_s <= 0:
            dur_s = 60.0

        q_raw = self.quality_combo.get().strip().upper()
        if "OPTIMAL" in q_raw:
            q_mode = "OPTIMAL"
        elif "NORMAL" in q_raw:
            q_mode = "NORMAL"
        elif "LOW" in q_raw:
            q_mode = "LOW"
        elif "HIGH" in q_raw:
            q_mode = "HIGH"
        elif "C#2" in q_raw or "CS-2" in q_raw:
            q_mode = "8779"
        elif "D#2" in q_raw or "DS-2" in q_raw:
            q_mode = "9852"
        elif "E-2" in q_raw:
            q_mode = "10463"
        elif "AUTO" in q_raw:
            q_mode = "AUTO"
        else:
            q_mode = "OPTIMAL"

        mem_mode = self.memory_combo.get().strip()
        mode_str = self.mode_combo.get().upper()
        if "PING" in mode_str:
            out_mode = "SEAMLESS_PING_PONG"
        elif "ONE" in mode_str:
            out_mode = "ONE_CHANNEL"
        else:
            out_mode = "CENTERED"

        dm_raw = self.downmix_combo.get().strip().upper()
        if "LEFT" in dm_raw:
            dm_mode = "LEFT"
        elif "RIGHT" in dm_raw:
            dm_mode = "RIGHT"
        else:
            dm_mode = "MIX"

        smooth = self.smooth_var.get()

        # Song Title from editable field, fallback to sanitized file basename
        user_title = self.title_var.get().strip()
        if user_title:
            song_title = to_ascii_safe(user_title)[:20]
        else:
            song_title = to_ascii_safe(os.path.splitext(os.path.basename(outp))[0])[:20]

        return ConversionConfig(
            input_path=inp,
            output_path=outp,
            start_sec=start_s,
            duration_sec=dur_s,
            quality=q_mode,
            memory_target=mem_mode,
            output_mode=out_mode,
            downmix_mode=dm_mode,
            click_prevention=smooth,
            title=song_title,
        )

    def _on_analyze(self):
        try:
            config = self._build_config()
            res: AnalysisResult = analyze_conversion(config)

            status_color = "#006600" if res.fits_memory and res.samples_required <= 31 else "#cc0000"
            mem_fit_txt = "OK (Fits within GS RAM)" if res.fits_memory else "EXCEEDS GS RAM TARGET!"

            summary_text = (
                f"Title: \"{config.title}\" | Selected: {format_time_str(config.start_sec)} -> {format_time_str(config.start_sec + res.duration_sec)} "
                f"({res.duration_sec:.1f}s) | Rate: {res.sample_rate} Hz (Note {res.note_name})\n"
                f"Samples required: {res.samples_required} / 31 | Patterns: {res.patterns_required} | "
                f"PCM Size: {res.total_pcm_bytes / 1024:.1f} KB | Est. MOD: {res.estimated_mod_bytes / 1024:.1f} KB\n"
                f"Memory Target: {config.memory_target} -> {mem_fit_txt}"
            )

            if res.warnings:
                summary_text += "\nWarnings: " + "; ".join(res.warnings)

            self.summary_label.config(text=summary_text, foreground=status_color)
            self._log(f"Analyzed: {res.samples_required} samples, {res.sample_rate} Hz, {res.estimated_mod_bytes/1024:.1f} KB")

        except Exception as e:
            self.summary_label.config(text=f"Analysis error: {e}", foreground="#cc0000")
            self._log(f"Analysis error: {e}")

    def _on_convert(self):
        config = self._build_config()
        if not config.input_path:
            messagebox.showerror("Missing Input", "Please select an input audio file first.")
            return

        if not os.path.isfile(config.input_path):
            messagebox.showerror("File Not Found", f"Input file not found:\n{config.input_path}")
            return

        if not find_ffmpeg():
            messagebox.showerror(
                "FFmpeg Missing",
                "ffmpeg.exe was not found!\n\nPlease place ffmpeg.exe next to this application or install it in system PATH."
            )
            return

        # Disable buttons during conversion
        self.btn_convert.config(state=tk.DISABLED)
        self.btn_analyze.config(state=tk.DISABLED)
        self.prog_bar["value"] = 0
        self.status_label.config(text="Converting...")

        # Run conversion in background worker thread to keep GUI responsive
        threading.Thread(target=self._run_conversion_worker, args=(config,), daemon=True).start()

    def _run_conversion_worker(self, config: ConversionConfig):
        def update_progress(p: float, msg: str):
            self.root.after(0, lambda: self._update_gui_progress(p, msg))

        def log_msg(msg: str):
            self.root.after(0, lambda: self._log(msg))

        try:
            report = convert_audio_to_mod(
                config,
                progress_callback=update_progress,
                log_callback=log_msg,
            )
            self.root.after(0, lambda: self._on_conversion_finished(report, None))
        except Exception as e:
            self.root.after(0, lambda: self._on_conversion_finished(None, e))

    def _update_gui_progress(self, p: float, msg: str):
        self.prog_bar["value"] = int(p * 100)
        self.status_label.config(text=msg)

    def _on_conversion_finished(self, report: Optional[dict], error: Optional[Exception]):
        self.btn_convert.config(state=tk.NORMAL)
        self.btn_analyze.config(state=tk.NORMAL)

        if error:
            self.status_label.config(text="Conversion failed!")
            self._log(f"ERROR: {error}")
            messagebox.showerror("Conversion Failed", str(error))
        else:
            self.prog_bar["value"] = 100
            self.status_label.config(text="Finished successfully!")
            self._log(f"SUCCESS: MOD written to '{report['output_path']}'")
            messagebox.showinfo(
                "Conversion Complete",
                f"MOD created successfully!\n\n"
                f"File: {report['output_path']}\n"
                f"Size: {report['file_size'] / 1024:.1f} KB\n"
                f"Playback Rate: {report['sample_rate']} Hz (Note {report['note']})\n"
                f"Samples Used: {report['sample_count']} / 31\n"
                f"Patterns: {report['patterns_count']}\n"
                f"Memory Target: {report['memory_target']}"
            )


def launch_gui():
    """Start Tkinter event loop."""
    root = tk.Tk()
    app = ModConverterGUI(root)
    root.mainloop()


if __name__ == "__main__":
    launch_gui()
