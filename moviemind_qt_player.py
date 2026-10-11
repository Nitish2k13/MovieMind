"""MovieMind's PySide6 player UI with the existing offline AI companion.

The legacy Tkinter implementation remains unchanged as the reference while
features are migrated incrementally. The Easter-egg web research pipeline is
still pending migration; normal AI modes use local Ollama only.
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
import time
from collections import deque
from datetime import datetime
from io import BytesIO
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    Image = None
try:
    import pytesseract
except ImportError:
    pytesseract = None

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
    easter_egg_finished = Signal(str, str, str)


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
        self.visual_memory = deque(maxlen=1440)
        self._visual_memory_lock = threading.Lock()
        self._visual_sample_in_flight = False
        self._visual_last_signature = ""
        self._visual_last_subtitle_signature = ""
        self._visual_last_subtitle = ""
        self._visual_last_subtitle_ocr = 0.0
        self._visual_last_full_ocr = 0.0
        self._visual_last_saved = 0.0
        self._visual_monitor = QTimer(self)
        self._visual_monitor.setInterval(2000)
        self._visual_monitor.timeout.connect(self._sample_visual_memory)
        self._visual_monitor.start()
        self._pending_snapshot_paths: set[str] = set()
        self._language_refresh_pending = False
        self._language_refresh_timer = QTimer(self)
        self._language_refresh_timer.setSingleShot(True)
        self._language_refresh_timer.setInterval(900)
        self._language_refresh_timer.timeout.connect(self._refresh_answer_for_language)
        self.easter_egg_history: list[dict[str, str]] = []
        self.ai_bridge = AIResultBridge(self)
        self.ai_bridge.finished.connect(self._finish_ai_request)
        self.ai_bridge.easter_egg_finished.connect(self._finish_easter_egg_research)
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
                # Keep VLC's automatic snapshot preview/filename OSD off-screen.
                # Subtitle tracks remain enabled; this only suppresses VLC's own OSD overlays.
                self.instance = vlc.Instance(
                    "--no-video-title-show",
                    "--no-snapshot-preview",
                    "--no-osd",
                    "--avcodec-hw=none",
                )
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
        self.load_subtitle_button = QPushButton("Load SRT")
        self.load_subtitle_button.clicked.connect(self.open_subtitle)
        controls.addWidget(self.load_subtitle_button)
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
        self.fullscreen_title = QLabel(
            "", self, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus
        )
        self.fullscreen_title.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.fullscreen_title.setStyleSheet(
            "QLabel { background:rgba(7,17,31,220); color:#eaf3ff; "
            "border:1px solid #28496e; border-radius:6px; "
            "padding:0px 3px; font-size:13px; font-weight:700; }"
        )
        self.fullscreen_title.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.fullscreen_title.hide()
        self.sidebar = QFrame()
        self.sidebar.setObjectName("sidepanel")
        self.sidebar.setFixedWidth(330)
        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(0)

        self.side_scroll = QScrollArea()
        self.side_scroll.setObjectName("sideScroll")
        self.side_scroll.setWidgetResizable(True)
        self.side_scroll.setFrameShape(QFrame.NoFrame)
        self.side_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.side_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.side_scroll.setStyleSheet(
            "QScrollArea { background:transparent; border:0; } "
            "QScrollArea > QWidget > QWidget { background:transparent; }"
        )
        sidebar_layout.addWidget(self.side_scroll)

        side_content = QWidget()
        side_content.setObjectName("sideContent")
        side_content.setStyleSheet("QWidget#sideContent { background:transparent; }")
        side = QVBoxLayout(side_content)
        side.setContentsMargins(18, 18, 18, 18)
        side.setSpacing(10)

        side_eyebrow = QLabel("MOVIEMIND AI")
        side_eyebrow.setObjectName("eyebrow")
        side.addWidget(side_eyebrow)
        title = QLabel("Your movie companion")
        title.setStyleSheet("font-size:20px; font-weight:750;")
        side.addWidget(title)
        desc = QLabel(
            "Understand scenes, decode dialogue, and discover details without losing the moment. "
            "MovieMind can make mistakes; watching from the beginning can provide better context "
            "for more accurate responses."
        )
        desc.setObjectName("muted")
        desc.setWordWrap(True)
        side.addWidget(desc)

        side.addSpacing(4)
        side.addWidget(QLabel("CURRENT MOVIE"))
        self.title_value = QLabel("No movie loaded")
        self.title_value.setWordWrap(True)
        self.title_value.setStyleSheet("font-size:14px; font-weight:650;")
        side.addWidget(self.title_value)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color:#1b3552;")
        side.addWidget(line)

        language_row = QHBoxLayout()
        language_row.addWidget(QLabel("REPLY LANGUAGE"))
        self.language_combo = QComboBox()
        self.language_combo.addItems(["English", "Tamil", "Tanglish"])
        self.language_combo.setCurrentText("English")
        self.language_combo.currentTextChanged.connect(self._language_changed)
        language_row.addWidget(self.language_combo, 1)
        side.addLayout(language_row)
        side.addWidget(QLabel("CHOOSE AN ACTION"))

        answer_heading = QLabel("MOVIEMIND RESPONSE")
        answer_heading.setObjectName("eyebrow")
        side.addWidget(answer_heading)
        self.answer_area = QTextEdit()
        self.answer_area.setReadOnly(True)
        self.answer_area.setPlaceholderText("Choose an action above to get started.")
        self.answer_area.setMinimumHeight(70)
        self.answer_area.setMaximumHeight(110)
        self.answer_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.answer_area.setStyleSheet(
            "QTextEdit { background:#081526; color:#eaf3ff; border:1px solid #1b3858; "
            "border-radius:8px; padding:8px; font-size:12px; }"
        )
        side.addWidget(self.answer_area)


        self.current_mode = "Ask a question"
        self.mode_buttons = {}
        for mode, label, detail in [
            ("Movie overview", "▣  Movie overview", "Cast, story, runtime and box office"),
            ("Explain scene", "◉  Explain scene", "Analyze the current frame"),
            ("Explain dialogue", "❝  Explain dialogue", "Explain the current subtitle"),
            ("Explain a word", "Aa  Explain a word", "Meaning in movie context"),
            ("Ask a question", "✳  Ask any question", "Ask about this movie"),
            ("Easter eggs / Hidden details", "⌕  Hidden details · ONLINE", "Online research; may take longer"),
        ]:
            tile = QPushButton(label + "\n" + detail)
            tile.setMinimumHeight(54)
            tile.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            tile.setStyleSheet(
                "QPushButton { text-align:left; white-space:normal; background:#10223a; "
                "border:1px solid #1b3858; border-radius:9px; padding:9px 11px; } "
                "QPushButton:hover { background:#142d4b; border-color:#348be8; } "
                "QPushButton[selected=\"true\"] { background:#16365a; border-color:#348be8; }"
            )
            tile.clicked.connect(lambda checked=False, selected=mode: self._select_mode(selected))
            self.mode_buttons[mode] = tile
            side.addWidget(tile)


        self.ai_input = QLineEdit()
        self.ai_input.setPlaceholderText("Ask a question about this movie…")
        self.ai_input.returnPressed.connect(self._submit_ai_input)
        self.ai_input.hide()
        side.addWidget(self.ai_input)

        self.ai_submit = QPushButton("Ask MovieMind")
        self.ai_submit.setObjectName("primary")
        self.ai_submit.clicked.connect(self._submit_ai_input)
        self.ai_submit.hide()
        side.addWidget(self.ai_submit)

        side.addSpacing(8)
        self.status = QLabel("●  PLAYER READY")
        self.status.setObjectName("status")
        side.addWidget(self.status)
        self.side_scroll.setWidget(side_content)
        content.addWidget(self.sidebar)

    def _select_mode(self, mode: str) -> None:
        self.current_mode = mode
        for selected, button in self.mode_buttons.items():
            button.setProperty("selected", selected == mode)
            button.style().unpolish(button)
            button.style().polish(button)
        needs_input = mode in ("Ask a question", "Explain a word")
        self.ai_input.setVisible(needs_input)
        self.ai_submit.setVisible(needs_input)
        if mode == "Ask a question":
            self.ai_input.setPlaceholderText("Ask a question about this movie…")
            self.ai_submit.setText("Ask MovieMind")
            self.ai_input.setFocus()
        elif mode == "Explain a word":
            self.ai_input.setPlaceholderText("Enter a word or phrase…")
            self.ai_submit.setText("Explain word")
            self.ai_input.setFocus()
        elif mode == "Easter eggs / Hidden details":
            self._start_online_research()
        else:
            self._ask_ai()

    def _start_online_research(self) -> None:
        """Start the existing Easter-egg research pipeline without changing its research logic."""
        if not self.movie_title:
            self.answer_area.setPlainText("Open a movie and check its title first.")
            return
        if not self.player or not self.current_path:
            self.answer_area.setPlainText("Open a movie before researching Easter eggs.")
            return
        if self.ai_request_running:
            self._set_status("ANOTHER MOVIEMIND REQUEST IS RUNNING")
            return
        snapshot_path = str(Path(tempfile.gettempdir()) / f"moviemind_easteregg_{uuid.uuid4().hex}.png")
        try:
            result = self.player.video_take_snapshot(0, snapshot_path, 0, 0)
            if result == -1:
                self.answer_area.setPlainText("Could not capture the current frame. Please try again.")
                return
        except Exception as exc:
            self.answer_area.setPlainText(f"Could not capture the current frame: {exc}")
            return
        self._pending_snapshot_paths.add(snapshot_path)
        self.ai_request_running = True
        self.answer_area.setPlainText(
            "Inspecting the current frame and researching possible Easter eggs online…\n"
            "This can take a little longer than offline modes."
        )
        self._set_status("EASTER EGG RESEARCH…")
        threading.Thread(
            target=self._run_easter_egg_research,
            args=(snapshot_path, self.movie_title, self.reply_language),
            daemon=True,
        ).start()

    def _finish_easter_egg_research(self, result: str, error: str, movie_title: str) -> None:
        self.ai_request_running = False
        if error:
            self.answer_area.setPlainText(error)
            self._set_status("EASTER EGG RESEARCH FAILED")
            return
        self.easter_egg_history.append({
            "movie": movie_title,
            "time": time.strftime("%H:%M:%S"),
            "result": result or "No result returned.",
        })
        self._render_easter_egg_history()
        self._set_status("ONLINE EASTER EGG RESEARCH COMPLETE")

    def _render_easter_egg_history(self) -> None:
        """Display findings and source links in the Qt response panel."""
        import html
        self.answer_area.setReadOnly(False)
        self.answer_area.clear()
        blocks = []
        for index, item in enumerate(reversed(self.easter_egg_history), start=1):
            if index > 1:
                blocks.append("<hr>")
            heading = f"Discovery {len(self.easter_egg_history) - index + 1} · {item['movie']} · {item['time']}"
            body = html.escape(item["result"])
            body = re.sub(
                r"(https?://[^\\s\\]\\)>,]+)",
                lambda match: '<a href="' + html.escape(match.group(0).rstrip('.'), quote=True) + '">' +
                    html.escape(match.group(0).rstrip('.')) + '</a>' +
                    html.escape(match.group(0)[len(match.group(0).rstrip('.')):]),
                body,
            )
            body = re.sub(r"(?im)^Status:\\s*(CONFIRMED)\\b", r'<span style="color:#4ade80"><b>Status: \\1</b></span>', body)
            body = re.sub(r"(?im)^Status:\\s*(POSSIBLE)\\b", r'<span style="color:#facc15"><b>Status: \\1</b></span>', body)
            body = re.sub(r"(?im)^Status:\\s*(NOT VERIFIED|UNVERIFIED)\\b", r'<span style="color:#f87171"><b>Status: \\1</b></span>', body)
            blocks.append(f'<p style="color:#c4b5fd"><b>{html.escape(heading)}</b></p><pre style="white-space:pre-wrap;font-family:inherit">{body}</pre>')
        self.answer_area.setHtml("\\n".join(blocks))
        self.answer_area.setReadOnly(True)

    def _mode_changed(self, mode: str) -> None:
        needs_input = mode == "Ask a question"
        self.ai_input.setVisible(needs_input)
        self.ai_submit.setVisible(needs_input)
        if mode == "Ask a question":
            self.ai_input.setPlaceholderText("Ask a question about this movie…")
            self.ai_submit.setText("Ask MovieMind")
            self.answer_area.setPlaceholderText("Ask about the movie. Normal questions run locally.")
        elif mode == "Explain a word":
            self.ai_input.setPlaceholderText("Enter a word or phrase…")
            self.ai_submit.setText("Explain word")
            self.answer_area.setPlaceholderText("Enter a word or phrase from the movie.")
        elif mode == "Explain scene":
            self.answer_area.setPlainText("Analyzing the current frame locally…")
        elif mode == "Explain dialogue":
            self.answer_area.setPlainText("Explaining the current subtitle locally…")
        elif mode == "Movie overview":
            self.answer_area.setPlainText("Preparing the offline movie overview…")
        if mode in ("Movie overview", "Explain scene", "Explain dialogue"):
            QTimer.singleShot(0, self._ask_ai)

    def open_subtitle(self) -> None:
        if not self.player or not self.current_path:
            QMessageBox.information(self, "Subtitles", "Open a movie first.")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Load subtitle file", str(Path(self.current_path).parent),
            "Subtitle files (*.srt *.ass *.ssa *.vtt *.sub);;All files (*.*)"
        )
        if not path:
            return
        try:
            result = self.player.video_set_subtitle_file(path)
            if result == -1:
                QMessageBox.warning(self, "Subtitle loading", "VLC did not accept this subtitle file.")
                return
            self.subtitle_context = self._parse_subtitle_file(path)
            self._set_status(f"SUBTITLES LOADED · {len(self.subtitle_context)} TIMED LINES")
            if not self.subtitle_context:
                self.answer_area.setPlainText(
                    "Subtitles were passed to VLC, but no timestamped SRT/VTT context could be parsed. "
                    "Dialogue explanations may have limited context for this file format."
                )
        except Exception as exc:
            QMessageBox.critical(self, "Subtitle error", str(exc))

    def _language_changed(self, language: str) -> None:
        if language == self.reply_language:
            return
        self.reply_language = language
        if not self.movie_title or not self.current_path:
            self._set_status(f"REPLY LANGUAGE: {language.upper()}")
            return
        if self.current_mode == "Easter eggs / Hidden details":
            self.answer_area.setPlainText(
                f"Language switched to {language}. Select Hidden details again to rerun the online research in this language."
            )
            self._set_status(f"LANGUAGE SWITCHED TO {language.upper()}")
            return
        self._language_refresh_pending = True
        self.answer_area.setPlainText(
            f"Switching response language to {language}…\\n"
            "MovieMind will refresh the current answer locally. The fan may briefly speed up while the AI works; this is normal. "
            "If you switch languages several times, only the last selection will be processed."
        )
        self._set_status(f"LANGUAGE SWITCHED · REFRESHING IN {language.upper()}")
        # Debounce rapid toggles so Gemma runs once for the final selected language.
        self._language_refresh_timer.start()

    def _refresh_answer_for_language(self) -> None:
        if not self._language_refresh_pending:
            return
        if self.ai_request_running:
            # Wait for the current local inference to finish, then refresh only once.
            self._language_refresh_timer.start(700)
            return
        if self.current_mode == "Easter eggs / Hidden details":
            self._language_refresh_pending = False
            return
        self._language_refresh_pending = False
        self._ask_ai()

    def _submit_ai_input(self) -> None:
        mode = self.current_mode
        if mode in ("Ask a question", "Explain a word") and not self.ai_input.text().strip():
            self.answer_area.setPlainText(
                "Type a question." if mode == "Ask a question" else "Type the word or phrase to explain."
            )
            return
        self._ask_ai()

    @staticmethod
    def _parse_subtitle_file(path: str) -> list[dict[str, object]]:
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
        except OSError:
            return []
        records = []
        for block in re.split(r"\n\s*\n", raw.strip()):
            lines = [line.strip() for line in block.splitlines() if line.strip()]
            timestamp_index = next((i for i, line in enumerate(lines) if "-->" in line), None)
            if timestamp_index is None:
                continue
            match = timestamp_re.search(lines[timestamp_index])
            if not match:
                continue
            text_lines = [
                re.sub(r"<[^>]+>", "", re.sub(r"\{[^}]*\}", "", line)).strip()
                for line in lines[timestamp_index + 1:]
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

    def _current_subtitle(self, current_ms: int) -> str:
        active = [
            item for item in self.subtitle_context
            if int(item["start_ms"]) <= current_ms <= int(item["end_ms"])
        ]
        if active:
            return " ".join(str(item["text"]) for item in active[-2:])
        nearby = sorted(
            self.subtitle_context,
            key=lambda item: min(
                abs(current_ms - int(item["start_ms"])),
                abs(current_ms - int(item["end_ms"]))
            )
        )
        if nearby:
            distance = min(
                abs(current_ms - int(nearby[0]["start_ms"])),
                abs(current_ms - int(nearby[0]["end_ms"]))
            )
            if distance <= 2500:
                return str(nearby[0]["text"])
        return ""

    @staticmethod
    def _language_instruction(language: str) -> str:
        if language == "Tamil":
            return (
                "Answer in natural Tamil using Tamil Unicode script. Keep movie titles, "
                "character names, and proper nouns in their original form where appropriate."
            )
        if language == "Tanglish":
            return (
                "IMPORTANT: Write in spoken Tamil transliterated into English/Latin letters "
                "(Tanglish), as Tamil speakers casually speak in conversation. Do NOT answer "
                "in English. Use Tamil words, Tamil grammar, and Tamil sentence order, but spell "
                "the Tamil sounds with English letters only. Use natural colloquial forms such as "
                "'irukku', 'pannraan', 'aagum', 'illa', 'appuram', and 'adhunaala' where they fit. "
                "Do not use Tamil Unicode script. Keep movie titles and character names in their "
                "original form. Check that the explanation is predominantly Tanglish."
            )
        return "Answer in clear, natural English."

    def _ask_ai(self) -> None:
        mode = self.current_mode
        user_input = self.ai_input.text().strip()
        if mode == "Easter eggs / Hidden details":
            self._mode_changed(mode)
            return
        if mode in ("Ask a question", "Explain a word") and not user_input:
            self._submit_ai_input()
            return
        if not self.movie_title or not self.current_path:
            self.answer_area.setPlainText("Open a movie first.")
            return
        if self.ai_request_running:
            self._set_status("AI REQUEST ALREADY RUNNING")
            return

        try:
            current_ms = max(0, self.player.get_time()) if self.player else 0
        except Exception:
            current_ms = 0
        nearby = [
            item for item in self.subtitle_context
            if int(item["start_ms"]) <= current_ms + 45000
            and int(item["end_ms"]) >= max(0, current_ms - 45000)
        ]
        words = {
            word.lower().strip(".,!?;:\"'()[]{}")
            for word in user_input.split()
            if len(word.strip(".,!?;:\"'()[]{}")) > 3
        } if mode in ("Ask a question", "Explain a word") else set()
        matching = [
            item for item in self.subtitle_context
            if words and any(word in str(item["text"]).lower() for word in words)
        ]
        selected, seen = [], set()
        for item in nearby[-14:] + matching[:8]:
            key = (item["start_ms"], item["text"])
            if key not in seen:
                seen.add(key)
                selected.append(item)
        context = "\n".join(
            f"[{clock(int(item['start_ms']))}] {item['text']}"
            for item in selected[-18:]
        ) or "No timestamped subtitle context is available."
        current_subtitle = self._current_subtitle(current_ms)
        with self._visual_memory_lock:
            recent_visual_records = list(self.visual_memory)[-3:]
        visual_subtitle = ""
        for visual_record in reversed(recent_visual_records):
            record_position = int(visual_record.get("position_ms", -1))
            candidate = str(visual_record.get("subtitle", "")).strip()
            if candidate and record_position >= 0 and abs(record_position - current_ms) <= 4000:
                visual_subtitle = candidate
                break
        if not current_subtitle and visual_subtitle:
            current_subtitle = visual_subtitle
        if not current_subtitle:
            current_subtitle = "No subtitle line is active at this exact playback time."
        if mode == "Explain dialogue":
            active_subtitles = [
                item for item in self.subtitle_context
                if int(item["start_ms"]) <= current_ms <= int(item["end_ms"])
                and str(item.get("text", "")).strip()
            ]
            if not active_subtitles and not visual_subtitle:
                self.answer_area.setPlainText("Pick a scene with subtitle to explain the dialogue.")
                self.answer_area.setMinimumHeight(110)
                self.answer_area.setMaximumHeight(180)
                self._set_status("SUBTITLE REQUIRED")
                return
        memory = "\n".join(
            f"[{item['time']}] User asked ({item['mode']}): {item['question']}\n"
            f"MovieMind answered: {item['answer'][:700]}"
            for item in self.recent_qa_memory[-5:]
        ) or "No earlier MovieMind conversation in this session."
        visual_memory_context = self._build_visual_memory_context()

        if mode == "Movie overview":
            try:
                media_length_ms = self.player.get_length() if self.player else -1
            except Exception:
                media_length_ms = -1
            if media_length_ms is None or media_length_ms <= 0:
                media_length_ms = self.duration_ms
            actual_runtime = clock(media_length_ms) if media_length_ms and media_length_ms > 0 else "Unavailable from the media player"
            task = (
                "Create a helpful offline overview of the movie named above using only your pretrained "
                "knowledge, with these headings: Runtime, Director, Main Cast, Overview, Genre, "
                "Production Budget, Worldwide Box Office Collection. Express budget and worldwide "
                "theatrical gross in US dollars ($), preserving commonly reported USD figures. "
                "Do NOT convert any budget or box-office figures to Indian rupees. Do not confuse budget, "
                "domestic collection, worldwide collection, or profit. If a figure is uncertain, write "
                "'Not reliably available from offline knowledge'. "
                "Use the supplied local file runtime as authoritative for this file, not necessarily the official cut. "
                "The Runtime heading MUST contain exactly the supplied VLC runtime below and MUST NOT include any alternate, official, theatrical, extended, or guessed runtime. "
                "Do not calculate or append a second runtime in parentheses. "
                "Do not browse, call an API, or claim facts were verified online. Keep it concise and spoiler-light.\n"
                f"AUTHORITATIVE RUNTIME — copy this value exactly into the Runtime heading: {actual_runtime}."
            )
            question_for_memory = "Movie overview"
        elif mode == "Explain scene":
            task = (
                "Give a useful scene explanation, not just a list of visible objects. Use up to 4 "
                "sentences: (1) identify the characters only when supported by the frame and movie "
                "context; (2) explain their immediate situation/action; (3) explain how the exact "
                "current subtitle relates to the interaction, if it genuinely fits; (4) briefly state "
                "the dramatic significance only when supported by reliable movie knowledge. For "
                "Avengers: Infinity War, the line 'You're not the only one cursed with knowledge' "
                "is said by Thanos to Doctor Strange on Titan; if the current frame and subtitle match "
                "that moment, explain their conflict over knowledge and the future without inventing "
                "extra actions. Do not assume every frame belongs to that moment. Never call a person "
                "Rocket, Quill, Stark, Strange, or Thanos solely from a subtitle or clothing color. "
                "Do not invent objects, motivations, or plot events. If the frame alone cannot establish "
                "identity or action, state what is uncertain instead of guessing."
            )
            question_for_memory = "Explain the scene"
        elif mode == "Explain dialogue":
            task = (
                "Explain the CURRENT subtitle in at most 3 short sentences. First quote or refer "
                "to the exact available subtitle. Explain its plain meaning and immediate implication "
                "without inventing a different scene, object, character, or motive. Do not assign a "
                "speaker unless the supplied subtitle/context explicitly identifies one or it is "
                "otherwise reliably established. A line addressing a character does not identify the "
                "speaker. Use nearby subtitles only if they are temporally relevant and consistent "
                "with the current line. If speaker or context is uncertain, say so briefly. Never "
                "replace this scene with a familiar plot from another movie."
            )
            question_for_memory = "Explain the current dialogue"
        elif mode == "Explain a word":
            task = (
                f"Explain the word or phrase '{user_input}' in this format:\n"
                "Simple meaning: <plain meaning>\n"
                "In this movie: <meaning in the current dialogue/scene, if supported>. Keep it concise. "
                "If the movie context does not establish a special meaning, say so rather than inventing one."
            )
            question_for_memory = user_input
        else:
            task = (
                f"Answer this question about the movie generally: {user_input}\n"
                "Answer from general movie knowledge available in the local model. Do not assume the user asks "
                "about the current scene unless stated. Do not browse or call an online service. If uncertain, "
                "state that briefly rather than inventing a specific fact."
            )
            question_for_memory = user_input

        language = self.reply_language
        language_instruction = self._language_instruction(language)
        prompt = f"""You are MovieMind, a local movie companion running on the user's computer.
MANDATORY RESPONSE LANGUAGE: {language}
{language_instruction}
The response must be entirely in the selected response language, except movie titles, names, and unavoidable technical terms. This rule applies to every mode, including Movie overview, Explain scene, Explain dialogue, Explain a word, and Ask a question. Do not default to English when Tamil or Tanglish is selected.

MOVIE: {self.movie_title}
PLAYBACK TIME: {clock(current_ms)}
USER MODE: {mode}

CURRENT SUBTITLE:
{current_subtitle}

TIMESTAMPED SUBTITLE CONTEXT:
{context}

AUTOMATIC LIGHTWEIGHT VISUAL MEMORY (sampled frames, visual-change fingerprints and selective OCR; treat OCR as uncertain evidence):
{visual_memory_context}

RECENT CONVERSATION MEMORY (may contain previous model mistakes; treat as clues, not proof):
{memory}

OFFLINE AND EVIDENCE RULES:
- All normal assistant modes must work locally through Ollama; never browse, call web APIs, or access the network.
- For scene/dialogue answers, the image is primary evidence and current/nearby subtitles are supporting evidence.
- Movie overview and general questions are about the movie generally, not automatically the current scene.
- Automatic visual memory is a compact history of sampled frames and selective OCR; it is not a guaranteed list of recognized characters or objects.
- Use it to connect recent visual changes and visible text across the movie, but never treat OCR or a prior answer as certain proof.
- Memory is for continuity, not proof; prior answers may be wrong.
- Never invent character identities, actions, motivations, credits, or plot events.
- If identity, object, speaker, or context is uncertain, say so instead of guessing.
- Distinguish direct evidence from interpretation and keep answers concise.
- Do not expose hidden reasoning or repeat the user's question.

TASK:
{task}

FINAL LANGUAGE CHECK: Before answering, ensure the complete response follows the selected language instruction above. For Tamil, use Tamil Unicode script. For Tanglish, use colloquial Tamil written only with Latin letters. For English, use English.

Return only the answer, with no meta commentary.
"""
        snapshot_path = None
        if mode in ("Explain scene", "Explain dialogue"):
            try:
                snapshot_path = str(Path(tempfile.gettempdir()) / f"moviemind_frame_{uuid.uuid4().hex}.png")
                self.player.video_take_snapshot(0, snapshot_path, 0, 0)
                self._pending_snapshot_paths.add(snapshot_path)
            except Exception:
                snapshot_path = None
        request_context = {
            "mode": mode, "question": question_for_memory,
            "language": language, "time": clock(current_ms),
            "authoritative_runtime": actual_runtime if mode == "Movie overview" else "",
            "visual_frames": self._recent_visual_images(2) if mode in ("Explain scene", "Explain dialogue") else [],
        }
        self.ai_request_running = True
        self.ai_submit.setEnabled(False)
        self.answer_area.setPlainText("Thinking locally in offline mode…")
        self._set_status("ASKING LOCAL AI")
        threading.Thread(
            target=self._run_local_ai,
            args=(prompt, snapshot_path, request_context),
            daemon=True,
        ).start()

    def _run_easter_egg_research(self, snapshot_path: str, movie_title: str, language: str) -> None:
        import base64
        import json
        import re
        import time
        import urllib.parse
        import urllib.request
        import urllib.error

        result = None
        error = None
        try:
            # Easter egg research requires internet; normal AI modes remain local/offline.
            connectivity_check = urllib.request.Request(
                "https://www.google.com/generate_204",
                headers={"User-Agent": "MovieMind/1.0"},
            )
            try:
                with urllib.request.urlopen(connectivity_check, timeout=4):
                    pass
            except Exception:
                raise RuntimeError(
                    "You appear to be offline. Easter egg research needs an internet connection; "
                    "connect to the internet and select this mode again."
                )
            for _ in range(20):
                if os.path.exists(snapshot_path) and os.path.getsize(snapshot_path) > 0:
                    break
                time.sleep(0.1)
            if not os.path.exists(snapshot_path) or os.path.getsize(snapshot_path) == 0:
                raise RuntimeError("The player did not produce a readable frame. Try again.")
            with open(snapshot_path, "rb") as frame_file:
                image_b64 = base64.b64encode(frame_file.read()).decode("ascii")
            vision_prompt = (
                f"Movie: {movie_title}. Inspect this exact frame and list at most 3 visually distinctive "
                "details that could plausibly be researched as a movie-specific Easter egg. Prioritize unusual "
                "symbols, recognizable props, logos, artwork, posters, readable signs, repeated motifs, or objects "
                "with a specific franchise connection. Do NOT list generic furniture (ordinary sofas, lamps, "
                "bookshelves, carpets, stairs) unless a unique marking or design makes it specifically meaningful. "
                "Ignore subtitles and player UI. First describe what is plainly visible; do not infer hidden meaning. "
                "Only state a color when it is clear in the frame; if uncertain, omit the color. Never invent a blue "
                "lamp or other color/detail that is not plainly visible. Do not identify characters unless highly "
                "confident from visual evidence and movie context. Return one short line per clue, or say no "
                "distinctive movie-specific clue is visible. These are candidates, not confirmed Easter eggs."
            )
            payload = json.dumps({
                "model": "gemma3:4b", "prompt": vision_prompt, "images": [image_b64],
                "stream": False, "keep_alive": "30m",
                "options": {"temperature": 0.1, "num_predict": 160, "num_ctx": 2048},
            }).encode("utf-8")
            request = urllib.request.Request(
                "http://localhost:11434/api/generate", data=payload,
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with urllib.request.urlopen(request, timeout=120) as response:
                clues = json.loads(response.read().decode("utf-8")).get("response", "").strip()
            if not clues:
                clues = "No distinctive clue could be identified confidently from this frame."
            # Search each candidate independently with the movie title, so evidence can be tied
            # to a specific clue rather than diluted by one giant combined query.
            clue_lines = [line.strip(" -•0123456789.\\t") for line in clues.splitlines() if line.strip()]
            clue_lines = [line for line in clue_lines if len(line) > 8][:3]
            if not clue_lines:
                clue_lines = [clues[:180]]
            snippets = []
            for clue in clue_lines:
                # Keep the displayed clue intact, but remove visual-report filler from search queries.
                # This helps image results focus on the actual object instead of phrases like
                # "is visible" or "is present in the scene".
                search_clue = re.sub(
                    r"(?i)\b(?:is|are|was|were)\s+(?:clearly\s+)?"
                    r"(?:visible|present|seen|shown)(?:\s+(?:in|on|within)\s+"
                    r"(?:the\s+)?(?:scene|image|frame|screen))?\b",
                    "",
                    clue,
                )
                search_clue = re.sub(r"(?i)\b(?:easter\s+egg|hidden\s+detail)\b", "", search_clue)
                search_clue = re.sub(r"\s+", " ", search_clue).strip(" .,:;-")
                if not search_clue:
                    search_clue = clue
                search_phrases = [f'"{movie_title}" "{search_clue}" Easter egg explanation']
                # Numbers may encode a real-world date or tribute. Run a separate, targeted
                # research query rather than assuming the number's meaning from the frame.
                if re.search(r"\d", search_clue):
                    search_phrases.append(
                        f'"{movie_title}" "{search_clue}" cast crew birthday anniversary tribute death date'
                    )
                for search_phrase in search_phrases:
                    search_url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(search_phrase)
                    search_req = urllib.request.Request(
                        search_url, headers={"User-Agent": "Mozilla/5.0 (compatible; MovieMind/1.0)"},
                    )
                    try:
                        with urllib.request.urlopen(search_req, timeout=10) as response:
                            html = response.read().decode("utf-8", errors="replace")
                        blocks = re.findall(
                            r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?'
                            r'<(?:a|div)[^>]+class="result__snippet"[^>]*>(.*?)</(?:a|div)>',
                            html, flags=re.I | re.S,
                        )
                        clean = lambda value: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value)).strip()
                        if blocks:
                            for url, title_html, snippet_html in blocks[:3]:
                                snippets.append(
                                    f"QUERY CLUE: {clue}\nSEARCH QUERY: {search_phrase}\n"
                                    f"Title: {clean(title_html)}\nURL: {url}\n"
                                    f"Snippet: {clean(snippet_html)}"
                                )
                        else:
                            fallback_url = "https://www.google.com/search?tbm=isch&q=" + urllib.parse.quote(
                                f"{movie_title} {search_clue}"
                            )
                            snippets.append(
                                f"QUERY CLUE: {clue}\nSEARCH QUERY: {search_phrase}\n"
                                f"No usable direct result links were returned. "
                                f"Use this Google Images link to inspect the clue: {fallback_url}"
                            )
                    except Exception as exc:
                        fallback_url = "https://www.google.com/search?tbm=isch&q=" + urllib.parse.quote(
                            f"{movie_title} {search_clue}"
                        )
                        snippets.append(
                            f"QUERY CLUE: {clue}\nSEARCH QUERY: {search_phrase}\n"
                            f"Search provider error: {type(exc).__name__}: {exc}\n"
                            f"Inspect this clue in Google Images: {fallback_url}"
                        )
            web_evidence = "\n\n".join(snippets) or "No usable web results were returned by the search provider."
            synthesis_prompt = (
                f"You are MovieMind's careful Easter-egg researcher. Reply in {language}. Movie: {movie_title}.\n"
                f"VISIBLE CLUE CANDIDATES (the vision model can be wrong):\n{clues}\n\n"
                f"WEB SEARCH RESULTS (unverified snippets):\n{web_evidence}\n\n"
                "Return at most 3 findings in plain text, without Markdown bold markers (**), using this format: "
                "Clue: ...\nPossible connection: ...\nEvidence: ...\nStatus: CONFIRMED / POSSIBLE / NOT VERIFIED. "
                "Do not pad the answer with ordinary set dressing. A sofa, lamp, bookshelf, carpet, or staircase "
                "is not an Easter egg by itself; include it only if a reliable result links that specific visible "
                "detail to this exact movie. Keep visual observation separate from interpretation: the vision model "
                "only proposes what may be visible, and its description is NOT external evidence. CONFIRMED requires "
                "a credible source that explicitly supports the exact detail in this exact movie. A generic franchise "
                "page, a search query, a similar-looking emblem, or an unsupported fan theory is not enough. "
                "If snippets are vague, contradictory, or do not mention the specific clue, use NOT VERIFIED. "
                "A Google Images link is only a way to inspect pictures and is NEVER evidence for a claim. "
                "If only a Google Images link is available and no credible source supports the connection, "
                "the status MUST be NOT VERIFIED and the possible connection should be described as unknown. "
                "Never use CONFIRMED just because the visual clue itself is visible; confirmation is about "
                "the claimed Easter-egg meaning, not the existence of an object in the frame. "
                "Use POSSIBLE only when a source provides a plausible but incomplete connection. For visible "
                "numbers, dates, digital clocks, signs, or license plates, investigate whether the exact number "
                "is documented as a cast/crew birthday, anniversary, death date, memorial, tribute, or other "
                "film-related reference. Search results that merely repeat the number are not proof. Do not match "
                "a date to a person's biography unless a credible source supports both the exact date and the "
                "connection to this movie or scene. A clock reading such as 201:19:15 may be a timestamp or "
                "fictional display, not a real-world date; do not force a date interpretation. Never infer a "
                "Power Stone, Nova Corps, creator intention, or other specific meaning from color/shape alone. "
                "Do not treat the visual model's guess as evidence. Include source titles and URLs when available. "
                "If a direct article URL is missing, preserve the provided Google Images URL for that clue; "
                "label it 'Google Images' rather than presenting it as an article proving the claim. Never invent URLs. "
                "If no distinctive clue has evidence, say that plainly and suggest pausing on a clearer frame. "
                "Keep the answer concise and stop after the third finding."
            )
            payload = json.dumps({
                "model": "gemma3:4b", "prompt": synthesis_prompt, "stream": False,
                "keep_alive": "30m",
                "options": {"temperature": 0.1, "num_predict": 240, "num_ctx": 2048},
            }).encode("utf-8")
            request = urllib.request.Request(
                "http://localhost:11434/api/generate", data=payload,
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with urllib.request.urlopen(request, timeout=120) as response:
                result = json.loads(response.read().decode("utf-8")).get("response", "").strip()
            if not result:
                result = f"Visible candidates:\n{clues}\n\nResearch did not return a final summary."
        except urllib.error.URLError as exc:
            error = f"Could not reach local Ollama or the web search service: {exc}"
        except Exception as exc:
            error = f"Easter egg research failed: {exc}"
        finally:
            try:
                if os.path.exists(snapshot_path):
                    os.remove(snapshot_path)
            except OSError:
                pass
            self._pending_snapshot_paths.discard(snapshot_path)

        self.ai_bridge.easter_egg_finished.emit(result or "", error or "", movie_title)

    def _run_local_ai(self, prompt: str, snapshot_path: str | None, request_context: dict[str, str]) -> None:
        result, error = "", ""
        try:
            request_data = {
                "model": "gemma3:4b", "prompt": prompt, "stream": False,
                "keep_alive": "30m", "options": {"temperature": 0.15, "num_predict": 220},
            }
            visual_images = request_context.get("visual_frames", [])
            if snapshot_path:
                for _ in range(15):
                    if os.path.exists(snapshot_path) and os.path.getsize(snapshot_path) > 0:
                        break
                    time.sleep(0.1)
                if os.path.exists(snapshot_path) and os.path.getsize(snapshot_path) > 0:
                    with open(snapshot_path, "rb") as image_file:
                        request_data["images"] = [base64.b64encode(image_file.read()).decode("ascii")]
            if visual_images:
                request_data["images"] = request_data.get("images", []) + visual_images[-2:]
            payload = json.dumps(request_data).encode("utf-8")
            request = urllib.request.Request(
                "http://localhost:11434/api/generate", data=payload,
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with urllib.request.urlopen(request, timeout=120) as response:
                data = json.loads(response.read().decode("utf-8"))
            result = data.get("response", "").strip() or "The local model returned an empty answer."
        except urllib.error.URLError as exc:
            error = (
                "MovieMind's local AI is unavailable. Start Ollama and check 'ollama list'. "
                "The required model is gemma3:4b. Details: " + str(exc)
            )
        except Exception as exc:
            error = f"Local AI request failed: {exc}"
        finally:
            if snapshot_path:
                try:
                    if os.path.exists(snapshot_path):
                        os.remove(snapshot_path)
                except OSError:
                    pass
                self._pending_snapshot_paths.discard(snapshot_path)
        self.ai_bridge.finished.emit(result, error, request_context)

    def _finish_ai_request(self, result: str, error: str, context: object) -> None:
        self.ai_request_running = False
        self.ai_submit.setEnabled(True)
        if self._language_refresh_pending and context.get("language") != self.reply_language:
            self.answer_area.setPlainText(
                f"Switching response language to {self.reply_language}…\\n"
                "The fan may briefly speed up while the local AI refreshes the answer; this is normal."
            )
            self._set_status(f"LANGUAGE SWITCHED · REFRESHING IN {self.reply_language.upper()}")
            self._language_refresh_timer.start(200)
            return
        if error:
            self.answer_area.setPlainText(error)
            self.answer_area.setMinimumHeight(150)
            self.answer_area.setMaximumHeight(260)
            self._set_status("LOCAL AI UNAVAILABLE")
            return
        if context.get("mode") == "Movie overview":
            authoritative_runtime = str(context.get("authoritative_runtime", "")).strip()
            if authoritative_runtime and authoritative_runtime != "Unavailable from the media player":
                runtime_line = f"Runtime: {authoritative_runtime}"
                runtime_pattern = re.compile(r"(?im)^\s*(?:\*\*)?runtime(?:\*\*)?\s*:\s*.*$")
                if runtime_pattern.search(result):
                    result = runtime_pattern.sub(runtime_line, result, count=1)
                else:
                    result = runtime_line + "\n" + result
        # Render model output as clean plain text; do not show Markdown decoration such as **bold**.
        result = re.sub(r"(?<!\*)\*\*(.*?)\*\*(?!\*)", r"\1", result)
        result = re.sub(r"(?m)^\s*#{1,6}\s+", "", result)
        self.answer_area.setPlainText(result)
        self.answer_area.setMinimumHeight(190)
        self.answer_area.setMaximumHeight(320)
        self._set_status("ANSWER READY")
        self.recent_qa_memory.append({
            "time": context.get("time", "unknown"),
            "mode": context.get("mode", "question"),
            "question": context.get("question", ""),
            "answer": result,
        })
        self.recent_qa_memory = self.recent_qa_memory[-8:]

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
            self.movie_title = clean_title
            self.subtitle_context.clear()
            self.recent_qa_memory.clear()
            with self._visual_memory_lock:
                self.visual_memory.clear()
            self._visual_last_signature = ""
            self._visual_last_subtitle_signature = ""
            self._visual_last_subtitle = ""
            self._visual_last_subtitle_ocr = 0.0
            self._visual_last_full_ocr = 0.0
            self._visual_last_saved = 0.0
            self.ai_input.clear()
            self.answer_area.setPlainText("New movie session started. Ask about the current scene or dialogue.")
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

    @staticmethod
    def _visual_signature(image) -> str:
        small = image.convert("L").resize((32, 18))
        pixels = list(small.getdata())
        average = sum(pixels) / max(1, len(pixels))
        return bytes(max(0, min(255, int(pixel - average + 128))) for pixel in pixels).hex()

    @staticmethod
    def _visual_difference(signature_a: str, signature_b: str) -> float:
        if not signature_a or not signature_b:
            return 1.0
        try:
            a, b = bytes.fromhex(signature_a), bytes.fromhex(signature_b)
        except ValueError:
            return 1.0
        if not a or len(a) != len(b):
            return 1.0
        return sum(abs(x - y) for x, y in zip(a, b)) / (255 * len(a))

    def _sample_visual_memory(self) -> None:
        """Request a lightweight VLC frame sample every two seconds; no AI inference runs here."""
        if not self.player or not self.current_path or self._visual_sample_in_flight:
            return
        try:
            if self.player.get_state() != vlc.State.Playing:
                return
        except Exception:
            return
        path = str(Path(tempfile.gettempdir()) / f"moviemind_memory_{uuid.uuid4().hex}.png")
        try:
            result = self.player.video_take_snapshot(0, path, 0, 0)
            if result == -1:
                return
        except Exception:
            return
        self._visual_sample_in_flight = True
        threading.Thread(target=self._process_visual_memory_snapshot, args=(path,), daemon=True).start()

    def _process_visual_memory_snapshot(self, path: str) -> None:
        try:
            # LibVLC writes the snapshot asynchronously.
            for _ in range(8):
                if os.path.exists(path) and os.path.getsize(path) > 0:
                    break
                time.sleep(0.08)
            if not Image or not os.path.exists(path) or os.path.getsize(path) <= 0:
                return
            with Image.open(path) as source:
                image = source.convert("RGB")
            now = time.monotonic()
            thumbnail = image.copy()
            thumbnail.thumbnail((480, 270), Image.Resampling.LANCZOS)
            signature = self._visual_signature(thumbnail)
            frame_difference = self._visual_difference(signature, self._visual_last_signature)

            subtitle = self._visual_last_subtitle
            subtitle_region = image.crop((0, int(image.height * 0.65), image.width, int(image.height * 0.96)))
            subtitle_signature = self._visual_signature(subtitle_region)
            subtitle_changed_visually = (
                not self._visual_last_subtitle_signature
                or self._visual_difference(subtitle_signature, self._visual_last_subtitle_signature) >= 0.012
            )
            if pytesseract and (
                subtitle_changed_visually or now - self._visual_last_subtitle_ocr >= 3.0
            ):
                try:
                    subtitle = re.sub(
                        r"\s+", " ",
                        pytesseract.image_to_string(
                            subtitle_region, lang="eng", config="--psm 6"
                        )
                    ).strip()[:350]
                except Exception:
                    # Tesseract is optional; visual sampling still works without OCR.
                    pass
                self._visual_last_subtitle_ocr = now

            full_text = ""
            full_ocr_due = (
                not self._visual_last_full_ocr
                or (frame_difference >= 0.045 and now - self._visual_last_full_ocr >= 8.0)
                or now - self._visual_last_full_ocr >= 20.0
            )
            if pytesseract and full_ocr_due:
                try:
                    small = image.copy()
                    small.thumbnail((960, 540), Image.Resampling.LANCZOS)
                    full_text = re.sub(
                        r"\s+", " ",
                        pytesseract.image_to_string(small, lang="eng", config="--psm 11")
                    ).strip()[:500]
                except Exception:
                    pass
                self._visual_last_full_ocr = now

            subtitle_changed = subtitle != self._visual_last_subtitle
            should_store = (
                not self._visual_last_signature
                or frame_difference >= 0.055
                or subtitle_changed
                or bool(full_text)
                or now - self._visual_last_saved >= 5.0
            )
            if should_store:
                buffer = BytesIO()
                thumbnail.save(buffer, format="JPEG", quality=45, optimize=True)
                try:
                    position_ms = max(0, self.player.get_time()) if self.player else 0
                except Exception:
                    position_ms = 0
                record = {
                    "timestamp": datetime.now().strftime("%H:%M:%S"),
                    "position_ms": position_ms,
                    "signature": signature,
                    "subtitle": subtitle,
                    "full_frame_text": full_text,
                    "image_jpeg": buffer.getvalue(),
                }
                with self._visual_memory_lock:
                    self.visual_memory.append(record)
                self._visual_last_saved = now

            self._visual_last_signature = signature
            self._visual_last_subtitle_signature = subtitle_signature
            self._visual_last_subtitle = subtitle
        except Exception as exc:
            print(f"[MovieMind visual memory] {type(exc).__name__}: {exc}")
        finally:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass
            self._visual_sample_in_flight = False

    def _build_visual_memory_context(self) -> str:
        with self._visual_memory_lock:
            records = list(self.visual_memory)[-12:]
        if not records:
            return "No automatic visual samples recorded yet."
        lines = []
        for item in records:
            timestamp = item.get("timestamp", "unknown")
            position = clock(int(item.get("position_ms", 0)))
            subtitle = str(item.get("subtitle", "")).strip() or "(no subtitle OCR)"
            text = str(item.get("full_frame_text", "")).strip()
            line = f"[wall time {timestamp}; movie time {position}] Subtitle OCR: {subtitle}"
            if text:
                line += f" | Other visible text OCR: {text[:180]}"
            lines.append(line)
        return "\n".join(lines)

    def _recent_visual_images(self, limit: int = 2) -> list[str]:
        """Return at most two compact earlier frames from this movie session."""
        with self._visual_memory_lock:
            records = list(self.visual_memory)[-limit:]
        return [
            base64.b64encode(item["image_jpeg"]).decode("ascii")
            for item in records if item.get("image_jpeg")
        ]

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
        """Remove release metadata/language lists while preserving the film year."""
        title = Path(path).stem
        title = re.sub(r"^@?MovieBox[_ .-]*Studios?\s*", "", title, flags=re.I)
        title = re.sub(r"^@[A-Za-z0-9_.-]+\s+", "", title)
        title = re.sub(
            r"(?i)\s+(?:blu[ .-]?ray|web[ .-]?dl|web[ .-]?rip|hdrip|brrip|"
            r"dvdrip|x264|x265|hevc|1080p|720p|2160p|4k|proper|extended|"
            r"remux|h\.?264|h\.?265|\d+gb\b).*?$",
            "", title,
        )
        title = re.sub(r"\s*[-_.]+\s*(?:mkv|mp4|avi)$", "", title, flags=re.I)
        title = re.sub(r"[._]+", " ", title)
        title = re.sub(r"\s+", " ", title).strip(" -_.")
        title = re.sub(r"\s+(\(?(?:19|20)\d{2}\)?)$", lambda m: f" ({m.group(1).strip('()')})", title)
        return title or Path(path).stem


    def toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            was_maximized = self._was_maximized_before_fullscreen
            normal_geometry = self._normal_geometry_before_fullscreen
            self._fullscreen_controls_timer.stop()
            self.fullscreen_overlay.hide()
            self.fullscreen_title.hide()
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
            self.showFullScreen()
            self._position_fullscreen_overlay()
            self._position_fullscreen_title()
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

    def _position_fullscreen_title(self) -> None:
        if not self.isFullScreen() or not self.movie_title:
            self.fullscreen_title.hide()
            return
        self.fullscreen_title.setText(self.movie_title)
        self.fullscreen_title.adjustSize()
        width = min(440, max(1, self.fullscreen_title.fontMetrics().horizontalAdvance(self.movie_title) + 18))
        height = 32
        top_left = self.mapToGlobal(self.rect().topLeft())
        self.fullscreen_title.setGeometry(
            top_left.x() + self.width() - width - 24,
            top_left.y() + 18,
            width,
            height,
        )
        self.fullscreen_title.show()
        self.fullscreen_title.raise_()

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
            self._position_fullscreen_title()

    def _poll_fullscreen_cursor(self) -> None:
        current = QCursor.pos()
        if not self.isFullScreen():
            self.fullscreen_title.hide()
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
                self.fullscreen_title.hide()
        elif event.type() == QEvent.Type.WindowActivate and watched is self:
            if self.isFullScreen():
                self._last_cursor_pos = QCursor.pos()
                self._position_fullscreen_title()
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
        for snapshot_path in tuple(self._pending_snapshot_paths):
            try:
                if os.path.exists(snapshot_path):
                    os.remove(snapshot_path)
            except OSError:
                pass
        self._pending_snapshot_paths.clear()
        try:
            if hasattr(self, "timer"):
                self.timer.stop()
            if hasattr(self, "_visual_monitor"):
                self._visual_monitor.stop()
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
