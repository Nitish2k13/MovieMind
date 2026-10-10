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
        self.duration_ms = 0
        self.dragging_seek = False
        self.fullscreen = False
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
        self.root.bind("<Escape>", self._escape_fullscreen)
        self.root.bind("<space>", self._space_shortcut)
        self.root.bind("<Control-o>", lambda _event: self.open_file())
        self.root.bind("<F>", lambda _event: self.toggle_fullscreen())
        self.root.after(250, self._attach_video_surface)
        self._schedule_tick()

        if vlc is None:
            self.root.after(100, self._show_missing_binding)

    def _build_ui(self) -> None:
        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=16, pady=(12, 8))

        tk.Label(
            header, text="🎬 MovieMind", bg=BG, fg=TEXT,
            font=("Segoe UI", 19, "bold")
        ).pack(side="left")

        self.file_label = tk.Label(
            header, text="No movie opened", bg=BG, fg=MUTED,
            font=("Segoe UI", 10), anchor="e"
        )
        self.file_label.pack(side="right", fill="x", expand=True, padx=(18, 0))

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=16, pady=(0, 10))

        self.video_frame = tk.Frame(body, bg="#050608", highlightthickness=1,
                                    highlightbackground="#303645")
        self.video_frame.pack(side="left", fill="both", expand=True)

        # Reserved space for the embedded AI companion. No AI is started in Phase 1.
        companion = tk.Frame(body, bg=PANEL, width=290)
        companion.pack(side="right", fill="y", padx=(12, 0))
        companion.pack_propagate(False)

        tk.Label(
            companion, text="MOVIEMIND AI", bg=PANEL, fg=TEXT,
            font=("Segoe UI", 12, "bold")
        ).pack(anchor="w", padx=14, pady=(16, 6))
        tk.Label(
            companion,
            text="Your movie companion will live here. Automatic movie detection, "
                 "temporary context memory, and questions will be added after the "
                 "player foundation is tested.",
            bg=PANEL, fg=MUTED, wraplength=250, justify="left",
            font=("Segoe UI", 10)
        ).pack(anchor="w", padx=14, pady=(0, 12))

        self.status_label = tk.Label(
            companion, text="PLAYER READY", bg="#242a38", fg="#c4b5fd",
            font=("Segoe UI", 9, "bold"), padx=10, pady=7, anchor="w"
        )
        self.status_label.pack(fill="x", padx=14, pady=(4, 8))

        tk.Label(
            companion, text="Phase 1: Playback only", bg=PANEL, fg=MUTED,
            font=("Segoe UI", 9)
        ).pack(anchor="w", padx=14, pady=(0, 12))

        controls = tk.Frame(self.root, bg=BG)
        controls.pack(fill="x", padx=16, pady=(0, 14))

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

        buttons = tk.Frame(self.root, bg=BG)
        buttons.pack(fill="x", padx=16, pady=(0, 16))

        self._button(buttons, "Open Movie", self.open_file, primary=True).pack(side="left")
        self._button(buttons, "Play / Pause", self.toggle_play).pack(side="left", padx=(8, 0))
        self._button(buttons, "Stop", self.stop).pack(side="left", padx=(8, 0))
        self._button(buttons, "Subtitles…", self.open_subtitle).pack(side="left", padx=(8, 0))
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
            self.file_label.config(text=Path(path).name)
            self.status_label.config(text="MOVIE LOADED", fg="#c4b5fd")
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
                self.status_label.config(text=f"SUBTITLES: {Path(path).name}", fg="#c4b5fd")
        except Exception as exc:
            messagebox.showerror("Subtitle error", str(exc), parent=self.root)

    def _set_volume(self, value: str) -> None:
        if self.player:
            try:
                self.player.audio_set_volume(int(float(value)))
            except Exception:
                pass

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
                if not self.dragging_seek and self.duration_ms > 0:
                    position = max(0.0, min(1000.0, current_ms / self.duration_ms * 1000))
                    self.seek_scale.set(position)
                self.time_label.config(
                    text=f"{self._format_time(current_ms)} / {self._format_time(self.duration_ms)}"
                )
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

    def toggle_fullscreen(self) -> None:
        self.fullscreen = not self.fullscreen
        self.root.attributes("-fullscreen", self.fullscreen)
        if self.fullscreen:
            self.video_frame.focus_set()

    def _escape_fullscreen(self, _event=None) -> None:
        if self.fullscreen:
            self.fullscreen = False
            self.root.attributes("-fullscreen", False)

    def _space_shortcut(self, event=None) -> str:
        # Avoid toggling playback when the user is typing into a future chat input.
        focused = self.root.focus_get()
        if focused and focused.winfo_class() in ("Entry", "Text", "TEntry"):
            return "break"
        self.toggle_play()
        return "break"

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
