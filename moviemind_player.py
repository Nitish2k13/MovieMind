"""
MovieMind Player — Phase 1
--------------------------
A separate media-player prototype using VLC's playback engine.
This file does not modify moviemind_visual_memory_v2.py.

Requirements:
  1. Install VLC desktop player (64-bit) from https://www.videolan.org/vlc/
  2. Install the Python binding: python -m pip install python-vlc
  3. Run: python moviemind_player.py

This is the playback foundation only. AI context collection and the companion
panel will be integrated in later phases after playback is verified.
"""
from __future__ import annotations

import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path

try:
    import vlc
except ImportError:
    vlc = None


APP_TITLE = "MovieMind Player"
BG = "#101218"
PANEL = "#191d27"
TEXT = "#f3f4f6"
MUTED = "#a5adbd"
ACCENT = "#8b5cf6"


class MovieMindPlayer:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1180x760")
        self.root.minsize(850, 560)
        self.root.configure(bg=BG)

        self.instance = None
        self.player = None
        self.current_path: str | None = None
        self.movie_title = ""
        self.subtitle_context: list[dict[str, object]] = []
        self.ai_request_running = False
        self.duration_ms = 0
        self.dragging_seek = False
        self.fullscreen = False
        self._fullscreen_overlay = None
        self._overlay_hide_id = None
        self._overlay_osd_id = None
        self._overlay_seek = None
        self._overlay_time = None
        self._overlay_volume = None
        self._overlay_osd = None
        self._after_id = None

        if vlc is not None:
            try:
                self.instance = vlc.Instance("--no-video-title-show")
                self.player = self.instance.media_player_new()
            except Exception as exc:
                messagebox.showerror(
                    "VLC initialization failed",
                    "MovieMind could not initialize VLC. Make sure the VLC desktop "
                    "application is installed and its architecture matches Python.\n\n"
                    f"Details: {exc}",
                    parent=root,
                )

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        # Bind shortcuts at the application level so they work regardless of
        # which non-text control currently has focus.
        self.root.bind_all("<Escape>", self._escape_fullscreen, add="+")
        self.root.bind_all("<space>", self._space_shortcut, add="+")
        self.root.bind_all("<Control-o>", self._shortcut_open, add="+")
        self.root.bind_all("<F>", self._shortcut_fullscreen, add="+")
        self.root.bind_all("<Left>", self._shortcut_left, add="+")
        self.root.bind_all("<Right>", self._shortcut_right, add="+")
        self.root.bind_all("<Up>", self._shortcut_up, add="+")
        self.root.bind_all("<Down>", self._shortcut_down, add="+")
        self.root.bind_all("<Motion>", self._fullscreen_mouse_motion, add="+")
        self.root.bind_all("<Configure>", self._fullscreen_geometry_changed, add="+")
        self.root.after(250, self._attach_video_surface)
        self._schedule_tick()

        if vlc is None:
            self.root.after(100, self._show_missing_binding)

    def _build_ui(self) -> None:
        self.header = tk.Frame(self.root, bg=BG)
        self.header.pack(fill="x", padx=16, pady=(12, 8))
        header = self.header

        tk.Label(
            header, text="🎬 MovieMind", bg=BG, fg=TEXT,
            font=("Segoe UI", 19, "bold")
        ).pack(side="left")

        self.file_label = tk.Label(
            header, text="No movie opened", bg=BG, fg=MUTED,
            font=("Segoe UI", 10), anchor="e"
        )
        self.file_label.pack(side="right", fill="x", expand=True, padx=(18, 0))

        self.body = tk.Frame(self.root, bg=BG)
        self.body.pack(fill="both", expand=True, padx=16, pady=(0, 10))
        body = self.body

        self.video_frame = tk.Frame(body, bg="#050608", highlightthickness=1,
                                    highlightbackground="#303645")
        self.video_frame.pack(side="left", fill="both", expand=True)

        # Reserved space for the embedded AI companion. No AI is started in Phase 1.
        self.companion = tk.Frame(body, bg=PANEL, width=290)
        self.companion.pack(side="right", fill="y", padx=(12, 0))
        self.companion.pack_propagate(False)
        companion = self.companion

        tk.Label(
            companion, text="MOVIEMIND AI", bg=PANEL, fg=TEXT,
            font=("Segoe UI", 12, "bold")
        ).pack(anchor="w", padx=14, pady=(16, 6))
        tk.Label(
            companion, text="MOVIE TITLE", bg=PANEL, fg=MUTED,
            font=("Segoe UI", 9, "bold")
        ).pack(anchor="w", padx=14, pady=(10, 4))
        self.title_var = tk.StringVar(value="")
        self.title_entry = tk.Entry(
            companion, textvariable=self.title_var, bg="#252a36", fg=TEXT,
            insertbackground=TEXT, relief="flat", font=("Segoe UI", 10)
        )
        self.title_entry.pack(fill="x", padx=14, pady=(0, 6), ipady=6)
        self._button(companion, "Set / Correct Title", self.set_movie_title).pack(
            anchor="w", padx=14, pady=(0, 10)
        )
        self.context_label = tk.Label(
            companion, text="Subtitle context: not loaded", bg=PANEL, fg=MUTED,
            wraplength=250, justify="left", font=("Segoe UI", 9)
        )
        self.context_label.pack(anchor="w", padx=14, pady=(0, 12))
        tk.Label(
            companion, text="ASK ABOUT THIS MOVIE", bg=PANEL, fg=TEXT,
            font=("Segoe UI", 10, "bold")
        ).pack(anchor="w", padx=14, pady=(4, 6))
        self.question_var = tk.StringVar(value="")
        self.question_entry = tk.Entry(
            companion, textvariable=self.question_var, bg="#252a36", fg=TEXT,
            insertbackground=TEXT, relief="flat", font=("Segoe UI", 10)
        )
        self.question_entry.pack(fill="x", padx=14, pady=(0, 6), ipady=6)
        self._button(companion, "Ask MovieMind", self.ask_movie).pack(
            anchor="w", padx=14, pady=(0, 8)
        )
        self.answer_label = tk.Label(
            companion, text="Load an external subtitle file, then ask a question.",
            bg=PANEL, fg=MUTED, wraplength=250, justify="left",
            font=("Segoe UI", 9), anchor="nw"
        )
        self.answer_label.pack(fill="both", expand=True, anchor="w", padx=14, pady=(0, 12))

        self.status_label = tk.Label(
            companion, text="PLAYER READY", bg="#242a38", fg="#c4b5fd",
            font=("Segoe UI", 9, "bold"), padx=10, pady=7, anchor="w"
        )
        self.status_label.pack(fill="x", padx=14, pady=(4, 8))

        tk.Label(
            companion, text="Phase 1: Playback only", bg=PANEL, fg=MUTED,
            font=("Segoe UI", 9)
        ).pack(anchor="w", padx=14, pady=(0, 12))

        self.controls = tk.Frame(self.root, bg=BG)
        self.controls.pack(fill="x", padx=16, pady=(0, 14))
        controls = self.controls

        self.time_label = tk.Label(
            controls, text="00:00 / 00:00", bg=BG, fg=MUTED,
            font=("Consolas", 9), width=15
        )
        self.time_label.pack(side="left", padx=(0, 8))

        self.seek_scale = tk.Scale(
            controls, from_=0, to=1000, orient="horizontal", showvalue=False,
            resolution=1, bg=BG, fg=TEXT, troughcolor="#34394a",
            activebackground=ACCENT, highlightthickness=0, bd=0,
            command=self._seek_changed
        )
        self.seek_scale.pack(side="left", fill="x", expand=True, padx=(0, 12))
        self.seek_scale.bind("<ButtonPress-1>", self._begin_seek)
        self.seek_scale.bind("<ButtonRelease-1>", self._end_seek)

        self.buttons = tk.Frame(self.root, bg=BG)
        self.buttons.pack(fill="x", padx=16, pady=(0, 16))
        buttons = self.buttons

        self._button(buttons, "Open Movie", self.open_file, primary=True).pack(side="left")
        self._button(buttons, "Play / Pause", self.toggle_play).pack(side="left", padx=(8, 0))
        self.audio_button = self._button(buttons, "Audio Track ▾", self.show_audio_menu)
        self.audio_button.pack(side="left", padx=(8, 0))
        self.subtitle_button = self._button(buttons, "Subtitles ▾", self.show_subtitle_menu)
        self.subtitle_button.pack(side="left", padx=(8, 0))
        self._button(buttons, "Load Subtitle…", self.open_subtitle).pack(side="left", padx=(8, 0))
        self._button(buttons, "Fullscreen", self.toggle_fullscreen).pack(side="left", padx=(8, 0))

        tk.Label(buttons, text="Volume", bg=BG, fg=MUTED).pack(side="left", padx=(20, 6))
        self.volume_scale = tk.Scale(
            buttons, from_=0, to=100, orient="horizontal", showvalue=True,
            length=135, bg=BG, fg=TEXT, troughcolor="#34394a",
            activebackground=ACCENT, highlightthickness=0, bd=0,
            command=self._set_volume
        )
        self.volume_scale.set(80)
        self.volume_scale.pack(side="left")

    def _button(self, parent, label: str, command, primary: bool = False) -> tk.Button:
        return tk.Button(
            parent, text=label, command=command,
            bg=ACCENT if primary else "#252a36",
            fg="white", activebackground="#6d43d8" if primary else "#343b4b",
            activeforeground="white", relief="flat", bd=0,
            padx=13, pady=9, cursor="hand2", font=("Segoe UI", 9, "bold")
        )

    def _show_missing_binding(self) -> None:
        if vlc is None:
            messagebox.showerror(
                "python-vlc is not installed",
                "Install the Python binding with:\n\n"
                "python -m pip install python-vlc\n\n"
                "Also install the VLC desktop application, then restart MovieMind.",
                parent=self.root,
            )
            self.status_label.config(text="VLC BINDING MISSING", fg="#fca5a5")

    def _attach_video_surface(self) -> None:
        if not self.player:
            return
        try:
            handle = self.video_frame.winfo_id()
            if sys.platform.startswith("win"):
                self.player.set_hwnd(handle)
            elif sys.platform.startswith("linux"):
                self.player.set_xwindow(handle)
            elif sys.platform == "darwin":
                self.player.set_nsobject(handle)
        except Exception as exc:
            self.status_label.config(text="VIDEO SURFACE ERROR", fg="#fca5a5")
            print(f"[MovieMind Player] Could not attach video surface: {exc}")

    def open_file(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root,
            title="Open a movie",
            filetypes=[
                ("Video files", "*.mp4 *.mkv *.avi *.mov *.wmv *.webm *.m4v *.mpeg *.mpg *.ts"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        if not self.player or not self.instance:
            self._show_missing_binding()
            return
        try:
            media = self.instance.media_new_path(str(Path(path).resolve()))
            self.player.set_media(media)
            self.current_path = path
            self.duration_ms = 0
            detected_title = self._title_from_filename(path)
            self.movie_title = detected_title
            self.title_var.set(detected_title)
            self.subtitle_context.clear()
            self.context_label.config(text="Subtitle context: not loaded")
            self.file_label.config(text=Path(path).name)
            self.status_label.config(text="MOVIE LOADED · CHECK TITLE", fg="#c4b5fd")
            self._attach_video_surface()
            result = self.player.play()
            if result == -1:
                raise RuntimeError("VLC could not start playback for this file.")
            self.root.after(400, self._attach_video_surface)
        except Exception as exc:
            self.status_label.config(text="OPEN FAILED", fg="#fca5a5")
            messagebox.showerror("Could not open movie", str(exc), parent=self.root)

    def toggle_play(self) -> None:
        if not self.player:
            self._show_missing_binding()
            return
        try:
            if self.player.is_playing():
                self.player.pause()
                self.status_label.config(text="PAUSED", fg="#fcd34d")
            elif self.current_path:
                # VLC's pause() toggles state; play() resumes a paused player.
                self.player.play()
                self.status_label.config(text="PLAYING", fg="#86efac")
            else:
                self.open_file()
        except Exception as exc:
            messagebox.showerror("Playback error", str(exc), parent=self.root)

    def stop(self) -> None:
        if self.player:
            self.player.stop()
        self.status_label.config(text="STOPPED" if self.current_path else "PLAYER READY",
                                 fg=MUTED)

    def open_subtitle(self) -> None:
        if not self.player or not self.current_path:
            messagebox.showinfo("Subtitles", "Open a movie first.", parent=self.root)
            return
        path = filedialog.askopenfilename(
            parent=self.root,
            title="Load subtitle file",
            filetypes=[("Subtitle files", "*.srt *.ass *.ssa *.vtt *.sub"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            result = self.player.video_set_subtitle_file(path)
            if result == -1:
                messagebox.showwarning(
                    "Subtitle loading",
                    "VLC did not accept this subtitle file. Check its format and try again.",
                    parent=self.root,
                )
            else:
                self.subtitle_context = self._parse_subtitle_file(path)
                self.context_label.config(
                    text=f"Subtitle context: {len(self.subtitle_context)} timestamped lines cached"
                )
                self.status_label.config(text=f"SUBTITLES: {Path(path).name}", fg="#c4b5fd")
        except Exception as exc:
            messagebox.showerror("Subtitle error", str(exc), parent=self.root)

    def _set_volume(self, value: str) -> None:
        if self.player:
            try:
                self.player.audio_set_volume(max(0, min(100, int(float(value)))))
            except Exception:
                pass

    def change_volume(self, delta: int) -> str:
        """Change volume from keyboard shortcuts: Up/Down = +/- 5%."""
        current = int(self.volume_scale.get())
        target = max(0, min(100, current + delta))
        self.volume_scale.set(target)
        self._set_volume(str(target))
        self._show_fullscreen_osd(f"Volume  {target}%")
        return "break"

    def seek_by(self, seconds: int) -> str:
        """Skip backward/forward with the left/right arrow keys."""
        if self.player and self.current_path:
            try:
                current = self.player.get_time()
                if current is None or current < 0:
                    current = 0
                duration = self.player.get_length()
                target = max(0, current + seconds * 1000)
                if duration and duration > 0:
                    target = min(target, duration)
                self.player.set_time(target)
                self._show_fullscreen_osd(
                    f"{'+' if seconds > 0 else ''}{seconds}s  ·  {self._format_time(target)}"
                )
            except Exception:
                pass
        return "break"

    @staticmethod
    def _track_name(value) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)

    def _show_track_menu(self, button, menu) -> None:
        try:
            menu.tk_popup(button.winfo_rootx(), button.winfo_rooty() + button.winfo_height())
        finally:
            menu.grab_release()

    def show_audio_menu(self) -> None:
        menu = tk.Menu(self.root, tearoff=0, bg=PANEL, fg=TEXT,
                       activebackground=ACCENT, activeforeground="white")
        if not self.player or not self.current_path:
            menu.add_command(label="Open a movie first", state="disabled")
        else:
            try:
                tracks = self.player.audio_get_track_description() or []
                current_id = self.player.audio_get_track()
                if not tracks:
                    menu.add_command(label="No audio tracks reported by VLC", state="disabled")
                else:
                    for track_id, track_name in tracks:
                        label = self._track_name(track_name)
                        if int(track_id) == int(current_id):
                            label = "✓ " + label
                        menu.add_command(
                            label=label,
                            command=lambda tid=int(track_id): self._select_audio_track(tid)
                        )
            except Exception as exc:
                menu.add_command(label=f"Could not read audio tracks: {exc}", state="disabled")
        self._show_track_menu(self.audio_button, menu)

    def _select_audio_track(self, track_id: int) -> None:
        if not self.player:
            return
        try:
            result = self.player.audio_set_track(track_id)
            if result == -1:
                messagebox.showwarning(
                    "Audio track", "VLC could not switch to that audio track.", parent=self.root
                )
            else:
                self.status_label.config(text="AUDIO TRACK CHANGED", fg="#c4b5fd")
        except Exception as exc:
            messagebox.showerror("Audio track error", str(exc), parent=self.root)

    def show_subtitle_menu(self) -> None:
        menu = tk.Menu(self.root, tearoff=0, bg=PANEL, fg=TEXT,
                       activebackground=ACCENT, activeforeground="white")
        if not self.player or not self.current_path:
            menu.add_command(label="Open a movie first", state="disabled")
        else:
            try:
                tracks = self.player.video_get_spu_description() or []
                current_id = self.player.video_get_spu()
                menu.add_command(label="✓ Disable subtitles" if current_id == -1 else "Disable subtitles",
                                 command=lambda: self._select_subtitle_track(-1))
                if tracks:
                    menu.add_separator()
                    for track_id, track_name in tracks:
                        track_id = int(track_id)
                        if track_id < 0:
                            continue
                        label = self._track_name(track_name)
                        if track_id == int(current_id):
                            label = "✓ " + label
                        menu.add_command(
                            label=label,
                            command=lambda tid=track_id: self._select_subtitle_track(tid)
                        )
                else:
                    menu.add_command(label="No embedded subtitle tracks reported", state="disabled")
                menu.add_separator()
                menu.add_command(label="Load external subtitle file…", command=self.open_subtitle)
            except Exception as exc:
                menu.add_command(label=f"Could not read subtitle tracks: {exc}", state="disabled")
                menu.add_command(label="Load external subtitle file…", command=self.open_subtitle)
        self._show_track_menu(self.subtitle_button, menu)

    def _select_subtitle_track(self, track_id: int) -> None:
        if not self.player:
            return
        try:
            result = self.player.video_set_spu(track_id)
            if result == -1:
                messagebox.showwarning(
                    "Subtitles", "VLC could not switch to that subtitle track.", parent=self.root
                )
            else:
                self.status_label.config(
                    text="SUBTITLES OFF" if track_id == -1 else "SUBTITLE TRACK CHANGED",
                    fg="#c4b5fd"
                )
        except Exception as exc:
            messagebox.showerror("Subtitle error", str(exc), parent=self.root)

    def _begin_seek(self, _event=None) -> None:
        self.dragging_seek = True

    def _seek_changed(self, value: str) -> None:
        if not self.dragging_seek or not self.player or self.duration_ms <= 0:
            return
        try:
            position = max(0.0, min(1.0, float(value) / 1000.0))
            self.player.set_position(position)
        except Exception:
            pass

    def _end_seek(self, _event=None) -> None:
        self.dragging_seek = False
        self._seek_changed(str(self.seek_scale.get()))

    def _schedule_tick(self) -> None:
        self._tick()
        self._after_id = self.root.after(500, self._schedule_tick)

    def _tick(self) -> None:
        if not self.player:
            return
        try:
            current_ms = self.player.get_time()
            duration_ms = self.player.get_length()
            if duration_ms and duration_ms > 0:
                self.duration_ms = duration_ms
            if current_ms is not None and current_ms >= 0:
                position = None
                if self.duration_ms > 0:
                    position = max(0.0, min(1000.0, current_ms / self.duration_ms * 1000))
                    if not self.dragging_seek:
                        self.seek_scale.set(position)
                time_text = f"{self._format_time(current_ms)} / {self._format_time(self.duration_ms)}"
                self.time_label.config(text=time_text)
                if self._overlay_time is not None:
                    try:
                        self._overlay_time.config(text=time_text)
                    except tk.TclError:
                        pass
                if self._overlay_seek is not None and not self.dragging_seek and position is not None:
                    try:
                        self._overlay_seek.set(position)
                    except tk.TclError:
                        pass
            if self.player.is_playing():
                self.status_label.config(text="PLAYING", fg="#86efac")
        except Exception:
            # UI timer must not crash playback when VLC reports transient state.
            pass

    @staticmethod
    def _format_time(milliseconds: int | None) -> str:
        if milliseconds is None or milliseconds < 0:
            milliseconds = 0
        total_seconds = int(milliseconds / 1000)
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"

    @staticmethod
    def _title_from_filename(path: str) -> str:
        """Guess a human-readable title from a filename; user can correct it."""
        import re

        stem = Path(path).stem
        stem = re.sub(r"[._]+", " ", stem)
        # Remove common release tags, resolutions, codecs and source labels.
        stem = re.sub(
            r"(?i)\b(2160p|1080p|720p|480p|4k|uhd|hdr10?|bluray|blu.?ray|web.?dl|"
            r"webrip|hdtv|x264|x265|h264|h265|hevc|aac|proper|repack|yts|rarbg)\b.*$",
            "", stem
        )
        # If a year is present, retain the title and year but discard following tags.
        match = re.search(r"^(.*?)(?:\s*\(?((?:19|20)\d{2})\)?)(?:\s|$)", stem)
        if match:
            stem = f"{match.group(1).strip()} ({match.group(2)})"
        return re.sub(r"\s+", " ", stem).strip(" -_()") or Path(path).stem

    def set_movie_title(self) -> None:
        title = self.title_var.get().strip()
        if not title:
            messagebox.showinfo("Movie title", "Enter a movie title first.", parent=self.root)
            return
        self.movie_title = title
        self.status_label.config(text="MOVIE TITLE SET", fg="#c4b5fd")

    @staticmethod
    def _parse_subtitle_file(path: str) -> list[dict[str, object]]:
        """Parse SRT/VTT timestamps into lightweight in-memory context records."""
        import re

        timestamp_re = re.compile(
            r"(?P<start>\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*"
            r"(?P<end>\d{2}:\d{2}:\d{2}[,.]\d{3})"
        )

        def to_ms(value: str) -> int:
            hours, minutes, rest = value.replace(",", ".").split(":")
            seconds, millis = rest.split(".")
            return ((int(hours) * 3600 + int(minutes) * 60 + int(seconds)) * 1000
                    + int(millis.ljust(3, "0")[:3]))

        try:
            raw = Path(path).read_text(encoding="utf-8-sig", errors="replace")
        except OSError as exc:
            print(f"[MovieMind Player] Could not read subtitle context: {exc}")
            return []

        blocks = re.split(r"\n\s*\n", raw.strip())
        records: list[dict[str, object]] = []
        for block in blocks:
            lines = [line.strip() for line in block.splitlines() if line.strip()]
            timestamp_index = next(
                (i for i, line in enumerate(lines) if "-->" in line), None
            )
            if timestamp_index is None:
                continue
            match = timestamp_re.search(lines[timestamp_index])
            if not match:
                continue
            text_lines = lines[timestamp_index + 1:]
            text_lines = [
                re.sub(r"<[^>]+>", "", re.sub(r"\{[^}]*\}", "", line)).strip()
                for line in text_lines
            ]
            text_lines = [line for line in text_lines if line and not re.fullmatch(r"\d+", line)]
            text = " ".join(text_lines).strip()
            if text:
                records.append({
                    "start_ms": to_ms(match.group("start")),
                    "end_ms": to_ms(match.group("end")),
                    "text": text,
                })
        return records

    def ask_movie(self) -> None:
        question = self.question_var.get().strip()
        if not question:
            self.answer_label.config(text="Type a question first.", fg=MUTED)
            return
        if not self.movie_title:
            self.answer_label.config(text="Open a movie and check its title first.", fg=MUTED)
            return
        if self.ai_request_running:
            return

        try:
            current_ms = self.player.get_time() if self.player else 0
            current_ms = current_ms if current_ms and current_ms > 0 else 0
        except Exception:
            current_ms = 0

        # Prefer dialogue near the current scene, then add keyword-matching lines
        # from the rest of the subtitle cache. Keep the prompt small and local.
        nearby = [
            item for item in self.subtitle_context
            if int(item["start_ms"]) <= current_ms + 45_000
            and int(item["end_ms"]) >= max(0, current_ms - 45_000)
        ]
        words = {
            word.lower().strip(".,!?;:\"'()[]{}")
            for word in question.split() if len(word.strip(".,!?;:\"'()[]{}")) > 3
        }
        matching = [
            item for item in self.subtitle_context
            if words and any(word in str(item["text"]).lower() for word in words)
        ]
        selected = []
        seen = set()
        for item in nearby[-14:] + matching[:10]:
            key = (item["start_ms"], item["text"])
            if key not in seen:
                seen.add(key)
                selected.append(item)
        context = "\n".join(
            f"[{self._format_time(int(item['start_ms']))}] {item['text']}"
            for item in selected[-22:]
        )
        if not context:
            context = (
                "No subtitle text has been loaded into MovieMind. The user may be asking "
                "a general language question. Answer general meanings when possible, but "
                "do not pretend to know the exact line or visual scene."
            )

        prompt = (
            "You are MovieMind, a concise movie companion. Answer the user's question "
            "helpfully. Use supplied subtitle evidence when present. If there is no subtitle "
            "evidence, answer general word/phrase meanings where possible and clearly note "
            "when the exact scene context is unavailable. Never invent what is happening "
            "visually. Keep the answer to 2-4 sentences.\n"
            f"Movie title: {self.movie_title}\n"
            f"Playback time: {self._format_time(current_ms)}\n"
            f"Question: {question}\n"
            f"Timestamped subtitle context:\n{context}"
        )
        self.ai_request_running = True
        self.answer_label.config(text="Thinking locally…", fg="#c4b5fd")
        self.status_label.config(text="ASKING LOCAL AI…", fg="#c4b5fd")
        import threading
        threading.Thread(target=self._run_local_ai, args=(prompt,), daemon=True).start()

    def _run_local_ai(self, prompt: str) -> None:
        import json
        import urllib.error
        import urllib.request

        result = None
        error = None
        try:
            payload = json.dumps({
                "model": "gemma3:4b",
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.2, "num_predict": 160}
            }).encode("utf-8")
            request = urllib.request.Request(
                "http://localhost:11434/api/generate",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(request, timeout=90) as response:
                data = json.loads(response.read().decode("utf-8"))
            result = data.get("response", "").strip() or "The local model returned an empty answer."
        except urllib.error.URLError as exc:
            error = (
                "Could not reach Ollama at localhost:11434. Start Ollama and confirm "
                "gemma3:4b is installed. Details: " + str(exc)
            )
        except Exception as exc:
            error = f"Local AI request failed: {exc}"

        def finish() -> None:
            self.ai_request_running = False
            try:
                if error:
                    self.answer_label.config(text=error, fg="#fca5a5")
                    self.status_label.config(text="LOCAL AI UNAVAILABLE", fg="#fca5a5")
                else:
                    self.answer_label.config(text=result, fg=TEXT)
                    self.status_label.config(text="ANSWER READY", fg="#86efac")
            except tk.TclError:
                pass

        try:
            self.root.after(0, finish)
        except tk.TclError:
            pass

    def toggle_fullscreen(self) -> None:
        # Keep the VLC HWND alive and use a separate overlay window for controls.
        self.fullscreen = not self.fullscreen
        if self.fullscreen:
            self.header.pack_forget()
            self.companion.pack_forget()
            self.controls.pack_forget()
            self.buttons.pack_forget()
            self.body.pack_configure(fill="both", expand=True, padx=0, pady=0)
            self.video_frame.pack_configure(fill="both", expand=True)
            self.root.attributes("-fullscreen", True)
            self.root.after_idle(self._finish_fullscreen_transition)
        else:
            self._destroy_fullscreen_overlay()
            self.root.attributes("-fullscreen", False)
            self.header.pack(fill="x", padx=16, pady=(12, 8))
            self.body.pack_configure(fill="both", expand=True, padx=16, pady=(0, 10))
            self.companion.pack(side="right", fill="y", padx=(12, 0))
            self.controls.pack(fill="x", padx=16, pady=(0, 14))
            self.buttons.pack(fill="x", padx=16, pady=(0, 16))
            self.root.after_idle(self._finish_fullscreen_transition)

    def _finish_fullscreen_transition(self) -> None:
        try:
            self.root.update_idletasks()
            if self.fullscreen:
                self._create_fullscreen_overlay()
                self._show_fullscreen_overlay()
        except tk.TclError:
            pass

    def _create_fullscreen_overlay(self) -> None:
        if self._fullscreen_overlay is not None:
            try:
                if self._fullscreen_overlay.winfo_exists():
                    return
            except tk.TclError:
                pass

        overlay = tk.Toplevel(self.root)
        overlay.overrideredirect(True)
        overlay.attributes("-topmost", True)
        overlay.configure(bg="#11141d")
        try:
            overlay.attributes("-alpha", 0.94)
        except tk.TclError:
            pass
        self._fullscreen_overlay = overlay

        top = tk.Frame(overlay, bg="#11141d")
        top.pack(fill="x", padx=14, pady=(8, 2))
        self._overlay_osd = tk.Label(
            top, text="", bg="#11141d", fg="#ffffff",
            font=("Segoe UI", 11, "bold"), anchor="e"
        )
        self._overlay_osd.pack(side="right")
        self._overlay_volume = tk.Scale(
            top, from_=0, to=100, orient="horizontal", showvalue=True,
            length=130, bg="#11141d", fg="#ffffff", troughcolor="#3a4052",
            activebackground=ACCENT, highlightthickness=0, bd=0,
            command=self._overlay_set_volume
        )
        self._overlay_volume.set(self.volume_scale.get())
        self._overlay_volume.pack(side="right", padx=(8, 14))
        tk.Label(top, text="VOLUME", bg="#11141d", fg="#cbd5e1",
                 font=("Segoe UI", 8, "bold")).pack(side="right")

        bottom = tk.Frame(overlay, bg="#11141d")
        bottom.pack(fill="x", padx=14, pady=(2, 10))
        self._overlay_time = tk.Label(
            bottom, text="00:00 / 00:00", bg="#11141d", fg="#e5e7eb",
            font=("Consolas", 9), width=15
        )
        self._overlay_time.pack(side="left", padx=(0, 10))
        self._overlay_seek = tk.Scale(
            bottom, from_=0, to=1000, orient="horizontal", showvalue=False,
            resolution=1, bg="#11141d", fg=TEXT, troughcolor="#3a4052",
            activebackground=ACCENT, highlightthickness=0, bd=0,
            command=self._overlay_seek_changed
        )
        self._overlay_seek.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self._overlay_seek.bind("<ButtonPress-1>", self._begin_seek)
        self._overlay_seek.bind("<ButtonRelease-1>", self._end_overlay_seek)

        actions = tk.Frame(overlay, bg="#11141d")
        actions.pack(fill="x", padx=14, pady=(0, 10))
        self._button(actions, "−10s", lambda: self.seek_by(-10)).pack(side="left")
        self._button(actions, "Play / Pause", self.toggle_play, primary=True).pack(
            side="left", padx=(8, 0)
        )
        self._button(actions, "+10s", lambda: self.seek_by(10)).pack(side="left", padx=(8, 0))
        self._button(actions, "Audio ▾", self.show_audio_menu).pack(side="left", padx=(8, 0))
        self._button(actions, "Subtitles ▾", self.show_subtitle_menu).pack(side="left", padx=(8, 0))
        self._button(actions, "Exit Fullscreen", self.toggle_fullscreen).pack(
            side="right"
        )

        overlay.bind("<Motion>", self._fullscreen_mouse_motion, add="+")
        overlay.bind("<Escape>", self._escape_fullscreen, add="+")
        overlay.bind("<space>", self._space_shortcut, add="+")
        overlay.bind("<Left>", self._shortcut_left, add="+")
        overlay.bind("<Right>", self._shortcut_right, add="+")
        overlay.bind("<Up>", self._shortcut_up, add="+")
        overlay.bind("<Down>", self._shortcut_down, add="+")
        self._position_fullscreen_overlay()

    def _position_fullscreen_overlay(self) -> None:
        overlay = self._fullscreen_overlay
        if not self.fullscreen or overlay is None:
            return
        try:
            if not overlay.winfo_exists():
                return
            width = max(640, self.root.winfo_width())
            height = 148
            x = self.root.winfo_rootx()
            y = self.root.winfo_rooty() + max(0, self.root.winfo_height() - height)
            overlay.geometry(f"{width}x{height}+{x}+{y}")
        except tk.TclError:
            pass

    def _fullscreen_geometry_changed(self, _event=None) -> None:
        if self.fullscreen:
            self.root.after_idle(self._position_fullscreen_overlay)

    def _fullscreen_mouse_motion(self, event=None) -> None:
        if not self.fullscreen:
            return
        self._show_fullscreen_overlay()
        if self._overlay_hide_id is not None:
            try:
                self.root.after_cancel(self._overlay_hide_id)
            except tk.TclError:
                pass
        self._overlay_hide_id = self.root.after(2600, self._hide_fullscreen_overlay)

    def _show_fullscreen_overlay(self) -> None:
        if not self.fullscreen:
            return
        self._create_fullscreen_overlay()
        self._position_fullscreen_overlay()
        try:
            self._fullscreen_overlay.deiconify()
            self._fullscreen_overlay.lift()
        except tk.TclError:
            return
        if self._overlay_hide_id is not None:
            try:
                self.root.after_cancel(self._overlay_hide_id)
            except tk.TclError:
                pass
        self._overlay_hide_id = self.root.after(2600, self._hide_fullscreen_overlay)

    def _hide_fullscreen_overlay(self) -> None:
        self._overlay_hide_id = None
        if self.fullscreen and self._fullscreen_overlay is not None:
            try:
                self._fullscreen_overlay.withdraw()
            except tk.TclError:
                pass

    def _destroy_fullscreen_overlay(self) -> None:
        for attr in ("_overlay_hide_id", "_overlay_osd_id"):
            task_id = getattr(self, attr, None)
            if task_id is not None:
                try:
                    self.root.after_cancel(task_id)
                except tk.TclError:
                    pass
                setattr(self, attr, None)
        if self._fullscreen_overlay is not None:
            try:
                self._fullscreen_overlay.destroy()
            except tk.TclError:
                pass
        self._fullscreen_overlay = None
        self._overlay_seek = self._overlay_time = self._overlay_volume = self._overlay_osd = None

    def _overlay_seek_changed(self, value: str) -> None:
        if self._overlay_seek is None or not self.dragging_seek:
            return
        self._seek_changed(value)

    def _end_overlay_seek(self, _event=None) -> None:
        self.dragging_seek = False
        if self.player and self.duration_ms > 0 and self._overlay_seek is not None:
            try:
                self.player.set_position(max(0.0, min(1.0, self._overlay_seek.get() / 1000.0)))
            except Exception:
                pass

    def _overlay_set_volume(self, value: str) -> None:
        self._set_volume(value)
        if hasattr(self, "volume_scale"):
            self.volume_scale.set(value)
        self._show_fullscreen_osd(f"Volume  {int(float(value))}%")

    def _show_fullscreen_osd(self, message: str) -> None:
        if not self.fullscreen:
            return
        self._show_fullscreen_overlay()
        if self._overlay_osd is not None:
            self._overlay_osd.config(text=message)
        if self._overlay_osd_id is not None:
            try:
                self.root.after_cancel(self._overlay_osd_id)
            except tk.TclError:
                pass
        self._overlay_osd_id = self.root.after(1800, self._clear_fullscreen_osd)

    def _clear_fullscreen_osd(self) -> None:
        self._overlay_osd_id = None
        if self._overlay_osd is not None:
            try:
                self._overlay_osd.config(text="")
            except tk.TclError:
                pass

    def _escape_fullscreen(self, _event=None) -> None:
        if self.fullscreen:
            self.toggle_fullscreen()

    def _is_text_entry_focused(self) -> bool:
        focused = self.root.focus_get()
        return bool(focused and focused.winfo_class() in ("Entry", "Text", "TEntry"))

    def _space_shortcut(self, event=None) -> str:
        # Preserve normal spaces while typing in title/question fields.
        if self._is_text_entry_focused():
            return None
        self.toggle_play()
        return "break"

    def _shortcut_open(self, _event=None) -> str:
        self.open_file()
        return "break"

    def _shortcut_fullscreen(self, _event=None) -> str:
        if not self._is_text_entry_focused():
            self.toggle_fullscreen()
            return "break"
        return None

    def _shortcut_left(self, _event=None) -> str:
        if not self._is_text_entry_focused():
            return self.seek_by(-5)
        return None

    def _shortcut_right(self, _event=None) -> str:
        if not self._is_text_entry_focused():
            return self.seek_by(5)
        return None

    def _shortcut_up(self, _event=None) -> str:
        if not self._is_text_entry_focused():
            return self.change_volume(5)
        return None

    def _shortcut_down(self, _event=None) -> str:
        if not self._is_text_entry_focused():
            return self.change_volume(-5)
        return None

    def close(self) -> None:
        if self._after_id is not None:
            try:
                self.root.after_cancel(self._after_id)
            except Exception:
                pass
        if self.player:
            try:
                self.player.stop()
                self.player.release()
            except Exception:
                pass
        if self.instance:
            try:
                self.instance.release()
            except Exception:
                pass
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    MovieMindPlayer(root)
    root.mainloop()


if __name__ == "__main__":
    main()
