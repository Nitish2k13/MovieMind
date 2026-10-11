"""MovieMind Qt player UI prototype.

This is an isolated PySide6/VLC UI migration prototype. It does not replace
moviemind_player.py yet; AI/Ollama/Easter-egg features remain in the existing
Tkinter application until they can be migrated and tested.
"""
from __future__ import annotations

import re
import sys
import os
import json
import base64
import tempfile
import threading
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QObject, QEvent, Signal
from PySide6.QtGui import QAction, QKeySequence, QCursor
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QMainWindow,
    QMenu, QMessageBox, QPushButton, QSizePolicy, QSlider, QVBoxLayout,
    QWidget, QFrame, QComboBox, QLineEdit, QTextEdit, QScrollArea,
)

try:
    import vlc
except ImportError:
    vlc = None


STYLESHEET = """
QMainWindow, QWidget#root { background: #07111f; color: #eaf3ff; }
QFrame#topbar { background: #0b1728; border-bottom: 1px solid #19314f; }
QFrame#surface { background: #050a12; border: 1px solid #18314f; border-radius: 14px; }
QFrame#sidepanel { background: #0c192b; border: 1px solid #1a3454; border-radius: 16px; }
QFrame#controlDeck { background: #0c1a2d; border: 1px solid #1b385a; border-radius: 16px; }
QLabel { color: #eaf3ff; background: transparent; }
QLabel#eyebrow { color: #72b6ff; font-size: 10px; font-weight: 700; letter-spacing: 2px; }
QLabel#muted { color: #8ca7c8; }
QLabel#brand { color: #f4f8ff; font-size: 21px; font-weight: 800; }
QLabel#status { color: #8dbfff; font-size: 11px; font-weight: 700; }
QPushButton {
  background: #142844; color: #dcecff; border: 1px solid #244467;
  border-radius: 10px; padding: 10px 14px; font-size: 12px; font-weight: 600;
}
QPushButton:hover { background: #1b385c; border-color: #4c9dff; }
QPushButton:pressed { background: #0c5fc4; }
QPushButton#primary { background: #1675e8; color: white; border: 1px solid #3995ff; }
QPushButton#primary:hover { background: #2e8dff; }
QPushButton#iconButton { min-width: 42px; max-width: 42px; min-height: 40px; max-height: 40px; padding: 0; font-size: 16px; border-radius: 12px; }
QSlider::groove:horizontal { height: 4px; background: #29415e; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #2588ff; border-radius: 2px; }
QSlider::add-page:horizontal { background: #203650; border-radius: 2px; }
QSlider::handle:horizontal {
  background: #f5faff; border: 3px solid #2588ff; width: 13px; height: 13px;
  margin: -7px 0; border-radius: 8px;
}
QSlider::handle:horizontal:hover { background: #ffffff; border-color: #70b5ff; width: 16px; }
QMenu { background: #10213a; color: #eaf3ff; border: 1px solid #2a4b70; padding: 6px; }
QMenu::item { padding: 9px 22px; border-radius: 6px; }
QMenu::item:selected { background: #176fd0; }
"""


class AIResultBridge(QObject):
    finished = Signal(str, str, object)


def clock(ms: int) -> str:
    seconds = max(0, ms // 1000)
    hours, rem = divmod(seconds, 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{hours}:{minutes:02}:{seconds:02}" if hours else f"{minutes:02}:{seconds:02}"


class MovieMindQtPlayer(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("MovieMind")
        self.resize(1440, 900)
        self.setMinimumSize(980, 640)
        self.setStyleSheet(STYLESHEET)
        self.current_path: str | None = None
        self.movie_title = ""
        self.subtitle_context: list[dict[str, object]] = []
        self.recent_qa_memory: list[dict[str, str]] = []
        self.reply_language = "English"
        self.ai_request_running = False
        self._pending_snapshot_paths: set[str] = set()
        self.ai_bridge = AIResultBridge(self)
        self.ai_bridge.finished.connect(self._finish_ai_request)
        self.duration_ms = 0
        self.dragging_seek = False
        self._pending_seek_ms: int | None = None
        self._pending_seek_checks = 0
        self._track_menu_open = False
        self._was_maximized_before_fullscreen = False
        self._normal_geometry_before_fullscreen = None
        self._fullscreen_controls_timer = QTimer(self)
        self._fullscreen_controls_timer.setSingleShot(True)
        self._fullscreen_controls_timer.setInterval(5000)
        self._fullscreen_controls_timer.timeout.connect(self._hide_fullscreen_controls)
        self._last_cursor_pos = QCursor.pos()
        self._cursor_poll = QTimer(self)
        self._cursor_poll.setInterval(120)
        self._cursor_poll.timeout.connect(self._poll_fullscreen_cursor)
        self._cursor_poll.start()
        self._build_ui()
        self.installEventFilter(self)
        self.video_surface.setMouseTracking(True)
        self.video_surface.installEventFilter(self)
        self.centralWidget().setMouseTracking(True)
        self.centralWidget().installEventFilter(self)
        self.deck.setMouseTracking(True)
        self.deck.installEventFilter(self)

        self.instance = None
        self.player = None
        if vlc is not None:
            try:
                # Do not force an audio backend; let VLC select the system output.
                self.instance = vlc.Instance("--no-video-title-show", "--avcodec-hw=none")
                self.player = self.instance.media_player_new()
                if sys.platform.startswith("win"):
                    self.player.set_hwnd(int(self.video_surface.winId()))
                elif sys.platform == "darwin":
                    self.player.set_nsobject(int(self.video_surface.winId()))
                else:
                    self.player.set_xwindow(int(self.video_surface.winId()))
            except Exception as exc:
                QMessageBox.critical(self, "VLC initialization failed", str(exc))

        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self._refresh_position)
        self.timer.start()
        self._set_status("PLAYER READY")

        self._add_app_shortcut(QKeySequence.Open, self.open_movie)
        self._add_app_shortcut(QKeySequence("Space"), self.toggle_play)
        self._add_app_shortcut(QKeySequence("F"), self.toggle_fullscreen)
        self._add_app_shortcut(QKeySequence("Escape"), self._escape_fullscreen)
        self._add_app_shortcut(QKeySequence("Left"), lambda: self.skip(-10))
        self._add_app_shortcut(QKeySequence("Right"), lambda: self.skip(10))
        self._add_app_shortcut(QKeySequence("Up"), lambda: self.volume.setValue(min(100, self.volume.value() + 5)))
        self._add_app_shortcut(QKeySequence("Down"), lambda: self.volume.setValue(max(0, self.volume.value() - 5)))

    def _add_app_shortcut(self, sequence, callback) -> None:
        action = QAction(self)
        action.setShortcut(sequence)
        action.setShortcutContext(Qt.ApplicationShortcut)
        action.triggered.connect(callback)
        self.addAction(action)

    def _escape_fullscreen(self) -> None:
        if self.isFullScreen():
            self.toggle_fullscreen()

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        self.outer_layout = outer
        outer.setContentsMargins(24, 18, 24, 22)
        outer.setSpacing(16)

        header = QFrame()
        self.header = header
        header.setObjectName("topbar")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 15)
        brand = QLabel("◈  MovieMind")
        brand.setObjectName("brand")
        header_layout.addWidget(brand)
        header_layout.addStretch()
        self.movie_name = QLabel("YOUR CINEMA, WITH INTELLIGENCE")
        self.movie_name.setObjectName("eyebrow")
        header_layout.addWidget(self.movie_name)
        outer.addWidget(header)

        content = QHBoxLayout()
        content.setSpacing(16)
        outer.addLayout(content, 1)

        left = QVBoxLayout()
        self.left_layout = left
        left.setSpacing(14)
        content.addLayout(left, 1)

        self.video_surface = QFrame()
        self.video_surface.setObjectName("surface")
        self.video_surface.setMinimumSize(560, 320)
        self.video_surface.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.video_surface.setStyleSheet("background:#02060c; border:1px solid #1b3552; border-radius:14px;")
        left.addWidget(self.video_surface, 1)

        deck = QFrame()
        self.deck = deck
        deck.setObjectName("controlDeck")
        deck.setAttribute(Qt.WA_StyledBackground, True)
        deck.setAutoFillBackground(True)
        deck.setMouseTracking(True)
        deck_layout = QVBoxLayout(deck)
        deck_layout.setContentsMargins(18, 14, 18, 16)
        deck_layout.setSpacing(12)

        seek_row = QHBoxLayout()
        self.current_time = QLabel("00:00")
        self.current_time.setObjectName("muted")
        self.current_time.setMinimumWidth(48)
        self.seek = QSlider(Qt.Horizontal)
        self.seek.setRange(0, 1000)
        self.seek.setTracking(False)
        self.seek.sliderPressed.connect(self._seek_start)
        self.seek.sliderReleased.connect(self._seek_end)
        self.seek.valueChanged.connect(self._seek_preview)
        self.total_time = QLabel("00:00")
        self.total_time.setObjectName("muted")
        self.total_time.setMinimumWidth(48)
        self.total_time.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        seek_row.addWidget(self.current_time)
        seek_row.addWidget(self.seek, 1)
        seek_row.addWidget(self.total_time)
        deck_layout.addLayout(seek_row)

        controls = QHBoxLayout()
        controls.setSpacing(9)
        self.open_button = QPushButton("＋  Open movie")
        self.open_button.setObjectName("primary")
        self.open_button.clicked.connect(self.open_movie)
        controls.addWidget(self.open_button)
        self.back_button = self._icon_button("↶", "Back 10 seconds", lambda: self.skip(-10))
        controls.addWidget(self.back_button)
        self.play_button = self._icon_button("▶", "Play / pause", self.toggle_play)
        self.play_button.setObjectName("primary")
        controls.addWidget(self.play_button)
        self.forward_button = self._icon_button("↷", "Forward 10 seconds", lambda: self.skip(10))
        controls.addWidget(self.forward_button)
        controls.addSpacing(8)
        self.audio_button = QPushButton("♫  Audio")
        self.audio_button.clicked.connect(self.show_audio_menu)
        controls.addWidget(self.audio_button)
        self.subtitle_button = QPushButton("CC  Subtitles")
        self.subtitle_button.clicked.connect(self.show_subtitle_menu)
        controls.addWidget(self.subtitle_button)
        controls.addStretch()
        controls.addWidget(QLabel("VOL"))
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(80)
        self.volume.setFixedWidth(115)
        self.volume.valueChanged.connect(self._set_volume)
        controls.addWidget(self.volume)
        self.volume_value = QLabel("80%")
        self.volume_value.setObjectName("muted")
        self.volume_value.setMinimumWidth(38)
        controls.addWidget(self.volume_value)
        self.fullscreen_button = self._icon_button("⛶", "Toggle fullscreen", self.toggle_fullscreen)
        controls.addWidget(self.fullscreen_button)
        deck_layout.addLayout(controls)
        left.addWidget(deck)

        # Use a native overlay only for the bottom control strip. A full-window
        # transparent overlay can cover VLC's native video surface on Windows.
        self.fullscreen_overlay = QWidget(
            self, Qt.Tool | Qt.FramelessWindowHint
        )
        self.fullscreen_overlay.setAttribute(Qt.WA_NativeWindow, True)
        self.fullscreen_overlay.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.fullscreen_overlay.setAttribute(Qt.WA_StyledBackground, True)
        self.fullscreen_overlay.setStyleSheet(
            "background:#0c1a2d; border:1px solid #1b385a; border-radius:12px;"
        )
        self.fullscreen_overlay.setMouseTracking(True)
        self.fullscreen_overlay.installEventFilter(self)
        self.fullscreen_title = QLabel("", self.fullscreen_overlay)
        self.fullscreen_title.hide()
        self.sidebar = QFrame()
        self.sidebar.setObjectName("sidepanel")
        self.sidebar.setFixedWidth(300)
        side = QVBoxLayout(self.sidebar)
        side.setContentsMargins(20, 22, 20, 20)
        side.setSpacing(13)
        side_eyebrow = QLabel("MOVIEMIND AI")
        side_eyebrow.setObjectName("eyebrow")
        side.addWidget(side_eyebrow)
        title = QLabel("Your movie companion")
        title.setStyleSheet("font-size:20px; font-weight:750;")
        side.addWidget(title)
        desc = QLabel("Understand scenes, decode dialogue, and discover details without losing the moment.")
        desc.setObjectName("muted")
        desc.setWordWrap(True)
        side.addWidget(desc)

        side.addSpacing(6)
        side.addWidget(QLabel("CURRENT MOVIE"))
        self.title_value = QLabel("No movie loaded")
        self.title_value.setWordWrap(True)
        self.title_value.setStyleSheet("font-size:14px; font-weight:650;")
        side.addWidget(self.title_value)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color:#1b3552;")
        side.addWidget(line)
        side.addWidget(QLabel("ASSISTANT MODES"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItems([
            "Movie overview", "Ask a question", "Explain scene",
            "Explain dialogue", "Explain a word", "Easter eggs / Hidden details",
        ])
        self.mode_combo.setCurrentText("Ask a question")
        self.mode_combo.currentTextChanged.connect(self._mode_changed)
        side.addWidget(self.mode_combo)

        language_row = QHBoxLayout()
        language_row.addWidget(QLabel("REPLY LANGUAGE"))
        self.language_combo = QComboBox()
        self.language_combo.addItems(["English", "Tamil", "Tanglish"])
        self.language_combo.currentTextChanged.connect(self._language_changed)
        language_row.addWidget(self.language_combo, 1)
        side.addLayout(language_row)

        self.ai_input = QLineEdit()
        self.ai_input.setPlaceholderText("Ask about the movie…")
        self.ai_input.returnPressed.connect(self._submit_ai_input)
        side.addWidget(self.ai_input)

        self.ai_submit = QPushButton("Ask MovieMind")
        self.ai_submit.setObjectName("primary")
        self.ai_submit.clicked.connect(self._submit_ai_input)
        side.addWidget(self.ai_submit)

        self.answer_area = QTextEdit()
        self.answer_area.setReadOnly(True)
        self.answer_area.setPlaceholderText("MovieMind's answer will appear here.")
        self.answer_area.setMinimumHeight(180)
        self.answer_area.setStyleSheet(
            "QTextEdit { background:#081526; color:#eaf3ff; border:1px solid #1b3858; "
            "border-radius:8px; padding:8px; font-size:12px; }"
        )
        side.addWidget(self.answer_area, 1)

        self.mode_buttons = {}
        for mode, label, detail in [
            ("Explain scene", "◉  Explain scene", "Analyze the current frame"),
            ("Explain dialogue", "❝  Explain dialogue", "Explain the current subtitle"),
            ("Easter eggs / Hidden details", "⌕  Hidden details", "Research possible Easter eggs"),
            ("Ask a question", "✳  Ask a question", "Ask about the movie"),
        ]:
            tile = QPushButton(label + "\n" + detail)
            tile.setStyleSheet(
                "QPushButton { text-align:left; white-space:normal; background:#10223a; "
                "border:1px solid #1b3858; border-radius:9px; padding:9px; } "
                "QPushButton:hover { background:#142d4b; border-color:#348be8; }"
            )
            tile.clicked.connect(lambda checked=False, selected=mode: self._select_mode(selected))
            self.mode_buttons[mode] = tile
            side.addWidget(tile)

        side.addStretch()
        self.status = QLabel("●  PLAYER READY")
        self.status.setObjectName("status")
        side.addWidget(self.status)
        content.addWidget(self.sidebar)

    def _icon_button(self, text: str, tooltip: str, callback) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("iconButton")
        button.setToolTip(tooltip)
        button.clicked.connect(callback)
        return button

    def _set_status(self, text: str) -> None:
        self.status.setText("●  " + text)

    def open_movie(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open movie", str(Path.home()),
            "Video files (*.mkv *.mp4 *.avi *.mov *.wmv *.webm *.m4v);;All files (*.*)"
        )
        if not path:
            return
        if not self.player or vlc is None:
            QMessageBox.warning(self, "VLC unavailable", "Install VLC desktop and python-vlc first.")
            return
        try:
            media = self.instance.media_new(path)
            self.player.set_media(media)
            result = self.player.play()
            if result == -1:
                raise RuntimeError("VLC could not start playback.")
            self.current_path = path
            clean_title = self._trim_movie_title(path)
            self.title_value.setText(clean_title)
            self.movie_name.setText(clean_title.upper()[:48])
            self.fullscreen_title.setText(clean_title)
            self._set_status("OPENING MOVIE")
            QTimer.singleShot(350, self._reattach_video)
        except Exception as exc:
            QMessageBox.critical(self, "Could not open movie", str(exc))

    def _reattach_video(self) -> None:
        if not self.player:
            return
        try:
            if sys.platform.startswith("win"):
                self.player.set_hwnd(int(self.video_surface.winId()))
            elif sys.platform == "darwin":
                self.player.set_nsobject(int(self.video_surface.winId()))
            else:
                self.player.set_xwindow(int(self.video_surface.winId()))
        except Exception:
            pass

    def toggle_play(self) -> None:
        if not self.player:
            self._set_status("VLC NOT AVAILABLE")
            return
        if not self.current_path:
            self.open_movie()
            return
        try:
            state = self.player.get_state()
            if state == vlc.State.Playing:
                self.player.set_pause(1)
                self.play_button.setText("▶")
                self._set_status("PAUSED")
            elif state == vlc.State.Paused:
                self.player.set_pause(0)
                self.play_button.setText("Ⅱ")
                self._set_status("PLAYING")
            else:
                self.player.play()
                self.play_button.setText("Ⅱ")
                self._set_status("PLAYING")
        except Exception as exc:
            QMessageBox.warning(self, "Playback error", str(exc))

    def skip(self, seconds: int) -> None:
        if self.player and self.current_path:
            current = max(0, self.player.get_time())
            length = max(0, self.player.get_length())
            target = max(0, current + seconds * 1000)
            if length:
                target = min(target, length)
            self.player.set_time(target)

    def _refresh_position(self) -> None:
        if not self.player or not self.current_path:
            return
        try:
            position = max(0, self.player.get_time())
            duration = max(0, self.player.get_length())
            if duration > 0:
                self.duration_ms = duration
                self.total_time.setText(clock(duration))
                if self._pending_seek_ms is not None:
                    # LibVLC applies seeks asynchronously. Keep the requested thumb
                    # position until the playback clock catches up, otherwise the
                    # 250 ms refresh loop snaps the slider back to the old position.
                    if abs(position - self._pending_seek_ms) <= 2500 or self._pending_seek_checks <= 0:
                        self._pending_seek_ms = None
                    else:
                        self._pending_seek_checks -= 1
                if not self.dragging_seek:
                    shown_position = self._pending_seek_ms if self._pending_seek_ms is not None else position
                    self.seek.blockSignals(True)
                    self.seek.setValue(round(shown_position / duration * 1000))
                    self.seek.blockSignals(False)
            shown_time = self._pending_seek_ms if self._pending_seek_ms is not None else position
            self.current_time.setText(clock(shown_time))
            state = self.player.get_state()
            if state == vlc.State.Playing:
                self.play_button.setText("Ⅱ")
                self._set_status("PLAYING")
            elif state == vlc.State.Paused:
                self.play_button.setText("▶")
                self._set_status("PAUSED")
        except Exception:
            pass

    def _seek_start(self) -> None:
        self.dragging_seek = True

    def _seek_preview(self, value: int) -> None:
        if self.dragging_seek and self.duration_ms:
            self.current_time.setText(clock(int(self.duration_ms * value / 1000)))

    def _seek_end(self) -> None:
        target = int(self.duration_ms * self.seek.value() / 1000)
        self.dragging_seek = False
        if self.player and self.duration_ms:
            self._pending_seek_ms = target
            self._pending_seek_checks = 12  # up to ~3 seconds for slow keyframes/seeks
            self.current_time.setText(clock(target))
            result = self.player.set_time(target)
            if result == -1:
                # VLC may reject time-based seeking for some media; try position seeking.
                self.player.set_position(min(1.0, max(0.0, target / self.duration_ms)))

    def _set_volume(self, value: int) -> None:
        self.volume_value.setText(f"{value}%")
        if self.player:
            try:
                self.player.audio_set_volume(value)
            except Exception:
                pass

    def _track_menu(self, button: QPushButton, kind: str) -> None:
        if not self.player or not self.current_path:
            QMessageBox.information(self, "Tracks", "Open a movie first.")
            return
        menu = QMenu(button)
        try:
            if kind == "audio":
                tracks = self.player.audio_get_track_description() or []
                current = self.player.audio_get_track()
                setter = self.player.audio_set_track
            else:
                tracks = self.player.video_get_spu_description() or []
                current = self.player.video_get_spu()
                setter = self.player.video_set_spu
            if not tracks:
                menu.addAction("No tracks reported by VLC").setEnabled(False)
            for track_id, name in tracks:
                action = menu.addAction(("✓  " if int(track_id) == int(current) else "") + str(name))
                action.triggered.connect(
                    lambda checked=False, tid=int(track_id), set_track=setter:
                    self._apply_track(set_track, tid)
                )
        except Exception as exc:
            menu.addAction(f"Could not read tracks: {exc}").setEnabled(False)
        # Keep the deck visible while its popup is open; never pause playback just
        # to display the track list.
        self._track_menu_open = True
        self._fullscreen_controls_timer.stop()
        menu.ensurePolished()
        popup_pos = button.mapToGlobal(button.rect().bottomLeft())
        screen = button.screen() if hasattr(button, "screen") else None
        if screen is not None:
            available = screen.availableGeometry()
            if popup_pos.y() + menu.sizeHint().height() > available.bottom():
                popup_pos.setY(button.mapToGlobal(button.rect().topLeft()).y() - menu.sizeHint().height())
        menu.exec(popup_pos)
        self._track_menu_open = False
        if self.isFullScreen():
            self._fullscreen_controls_timer.start(5000)

    def _apply_track(self, setter, track_id: int) -> None:
        try:
            result = setter(track_id)
            if result == -1:
                self._set_status("VLC COULD NOT APPLY TRACK")
        except Exception as exc:
            self._set_status(f"TRACK CHANGE FAILED: {exc}")

    def show_audio_menu(self) -> None:
        self._track_menu(self.audio_button, "audio")

    def show_subtitle_menu(self) -> None:
        self._track_menu(self.subtitle_button, "subtitle")

    @staticmethod
    def _trim_movie_title(path: str) -> str:
        """Turn noisy release filenames into a readable movie title."""
        title = Path(path).stem
        title = re.sub(r"^@?MovieBox[_ .-]*Studios?\s*", "", title, flags=re.I)
        title = re.sub(r"^@[A-Za-z0-9_.-]+\s+", "", title)
        title = re.sub(
            r"(?i)\s+(?:blu[ .-]?ray|web[ .-]?dl|web[ .-]?rip|hdrip|brrip|"
            r"dvdrip|x264|x265|hevc|1080p|720p|2160p|4k|proper|extended|"
            r"remux|h\.?264|h\.?265)\b.*$",
            "", title,
        )
        title = re.sub(r"\s*[-_.]+\s*(?:mkv|mp4|avi)$", "", title, flags=re.I)
        title = re.sub(r"[._]+", " ", title)
        return re.sub(r"\s+", " ", title).strip(" -_.") or Path(path).stem

    def toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            was_maximized = self._was_maximized_before_fullscreen
            normal_geometry = self._normal_geometry_before_fullscreen
            self._fullscreen_controls_timer.stop()
            self.fullscreen_overlay.hide()
            self.deck.hide()
            self.deck.setParent(self.centralWidget())
            self.left_layout.addWidget(self.deck)
            self.deck.setMinimumHeight(100)
            self.deck.setMaximumHeight(16777215)
            self.outer_layout.setContentsMargins(24, 18, 24, 22)
            self.header.show()
            self.sidebar.show()
            self.showNormal()
            if was_maximized:
                self.showMaximized()
            elif normal_geometry is not None:
                self.setGeometry(normal_geometry)
            self.deck.show()
            self.deck.raise_()
            self.fullscreen_overlay.hide()
            self._was_maximized_before_fullscreen = False
            self._normal_geometry_before_fullscreen = None
            self.setFocus()
            QTimer.singleShot(0, self._restore_layout_after_fullscreen)
        else:
            self._was_maximized_before_fullscreen = self.isMaximized()
            self._normal_geometry_before_fullscreen = self.normalGeometry()
            self._fullscreen_controls_timer.stop()
            self.fullscreen_overlay.hide()
            self.outer_layout.setContentsMargins(0, 0, 0, 0)
            self.header.hide()
            self.sidebar.hide()
            self.left_layout.removeWidget(self.deck)
            self.deck.hide()
            self.deck.setParent(self.fullscreen_overlay)
            self.deck.setMinimumHeight(100)
            self.deck.setMaximumHeight(142)
            self.fullscreen_title.hide()
            self.showFullScreen()
            self._position_fullscreen_overlay()
            # Keep the panel and its child deck hidden until the cursor enters
            # the bottom zone. Showing a child of a hidden tool window is flaky
            # on Windows and can leave it detached from the visible panel.
            self.deck.hide()
            self.fullscreen_overlay.hide()
            self.video_surface.setFocus()
        self.update()

    def _restore_layout_after_fullscreen(self) -> None:
        self.centralWidget().updateGeometry()
        self.centralWidget().layout().activate()
        self.left_layout.activate()
        self.updateGeometry()
        self.update()

    def _position_fullscreen_overlay(self) -> None:
        if not self.isFullScreen():
            return
        # The native overlay occupies only the bottom control strip; the VLC
        # video surface remains unobstructed for the entire movie frame.
        strip_height = 142
        top_left = self.mapToGlobal(self.rect().topLeft())
        self.fullscreen_overlay.setGeometry(
            top_left.x(), top_left.y() + self.height() - strip_height,
            self.width(), strip_height
        )
        self.deck.setGeometry(0, 0, self.width(), strip_height)
        self.deck.setMinimumHeight(100)
        self.deck.setMaximumHeight(strip_height)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.isFullScreen():
            self._position_fullscreen_overlay()

    def _poll_fullscreen_cursor(self) -> None:
        current = QCursor.pos()
        if not self.isFullScreen():
            self._last_cursor_pos = current
            return
        local = self.mapFromGlobal(current)
        pointer_inside_player = self.rect().contains(local)
        in_bottom_zone = pointer_inside_player and local.y() >= self.height() - 170
        overlay_local = self.fullscreen_overlay.mapFromGlobal(current)
        over_deck = self.deck.isVisible() and self.deck.geometry().contains(overlay_local)
        moved = current != self._last_cursor_pos
        self._last_cursor_pos = current
        # Check the zone every tick, not only when the cursor coordinates change.
        # This covers entering fullscreen with the pointer already at the bottom.
        if in_bottom_zone and (moved or not self.fullscreen_overlay.isVisible()):
            self._show_fullscreen_controls()
        elif over_deck and moved:
            # Hide five seconds after the last pointer movement over the controls.
            self._fullscreen_controls_timer.start(5000)

    def _show_fullscreen_controls(self) -> None:
        if not self.isFullScreen() or not self.isActiveWindow():
            return
        was_hidden = not self.fullscreen_overlay.isVisible()
        if was_hidden:
            self._position_fullscreen_overlay()
            self.deck.hide()
            self.fullscreen_overlay.show()
            self.fullscreen_overlay.raise_()
            self.deck.show()
            self.deck.raise_()
        # Restart on reveal or user movement: hide after five seconds without
        # movement, unless a track menu is currently open.
        self._fullscreen_controls_timer.start(5000)

    def _hide_fullscreen_controls(self) -> None:
        if self.isFullScreen() and not self._track_menu_open:
            self.deck.hide()
            self.fullscreen_overlay.hide()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.WindowDeactivate and watched is self:
            # Losing focus must not hide the ordinary playback deck. Only hide
            # the separate fullscreen overlay so it cannot float over other apps.
            if self.isFullScreen():
                self._fullscreen_controls_timer.stop()
                self.deck.hide()
                self.fullscreen_overlay.hide()
        elif event.type() == QEvent.Type.WindowActivate and watched is self:
            if self.isFullScreen():
                self._last_cursor_pos = QCursor.pos()
        if self.isFullScreen() and self.isActiveWindow() and event.type() in (QEvent.Type.MouseMove, QEvent.Type.Enter):
            if event.type() == QEvent.Type.Enter:
                if watched is self.fullscreen_overlay:
                    self._show_fullscreen_controls()
            else:
                if watched is self.fullscreen_overlay:
                    local_pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
                    if self.fullscreen_overlay.rect().contains(local_pos):
                        self._show_fullscreen_controls()
                else:
                    local_pos = self.mapFromGlobal(QCursor.pos())
                    if self.rect().contains(local_pos) and local_pos.y() >= self.height() - 170:
                        self._show_fullscreen_controls()
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key.Key_Escape and self.isFullScreen():
            self.toggle_fullscreen()
            event.accept()
            return
        if key == Qt.Key.Key_Space:
            self.toggle_play()
            event.accept()
            return
        if key == Qt.Key.Key_Left:
            self.skip(-10)
            event.accept()
            return
        if key == Qt.Key.Key_Right:
            self.skip(10)
            event.accept()
            return
        if key == Qt.Key.Key_Up:
            self.volume.setValue(min(100, self.volume.value() + 5))
            self._set_status(f"VOLUME {self.volume.value()}%")
            event.accept()
            return
        if key == Qt.Key.Key_Down:
            self.volume.setValue(max(0, self.volume.value() - 5))
            self._set_status(f"VOLUME {self.volume.value()}%")
            event.accept()
            return
        if key == Qt.Key.Key_F:
            self.toggle_fullscreen()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        try:
            if hasattr(self, "timer"):
                self.timer.stop()
            if self.player:
                self.player.stop()
                self.player.release()
            if self.instance:
                self.instance.release()
        except Exception:
            pass
        event.accept()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("MovieMind")
    window = MovieMindQtPlayer()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
