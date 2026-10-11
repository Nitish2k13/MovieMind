"""MovieMind Qt player UI prototype.

This is an isolated PySide6/VLC UI migration prototype. It does not replace
moviemind_player.py yet; AI/Ollama/Easter-egg features remain in the existing
Tkinter application until they can be migrated and tested.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QObject, QEvent
from PySide6.QtGui import QAction, QKeySequence, QCursor
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QMainWindow,
    QMenu, QMessageBox, QPushButton, QSizePolicy, QSlider, QVBoxLayout,
    QWidget,
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
        self.duration_ms = 0
        self.dragging_seek = False
        self._fullscreen_controls_timer = QTimer(self)
        self._fullscreen_controls_timer.setSingleShot(True)
        self._fullscreen_controls_timer.setInterval(2200)
        self._fullscreen_controls_timer.timeout.connect(self._hide_fullscreen_controls)
        self._last_cursor_pos = QCursor.pos()
        self._cursor_poll = QTimer(self)
        self._cursor_poll.setInterval(120)
        self._cursor_poll.timeout.connect(self._poll_fullscreen_cursor)
        self._cursor_poll.start()
        self._build_ui()
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
        self.fullscreen_title = QLabel("", self.video_surface)
        self.fullscreen_title.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.fullscreen_title.setStyleSheet(
            "QLabel { color:#EAF3FF; background:rgba(5,15,29,220); "
            "border:1px solid #376BA3; border-radius:9px; "
            "padding:8px 13px; font-size:12px; font-weight:700; }"
        )
        self.fullscreen_title.hide()
        self.video_surface.setMinimumSize(560, 320)
        self.video_surface.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.video_surface.setStyleSheet("background:#02060c; border:1px solid #1b3552; border-radius:14px;")
        left.addWidget(self.video_surface, 1)

        deck = QFrame()
        self.deck = deck
        deck.setObjectName("controlDeck")
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
        for name, detail in [
            ("◉  Explain scene", "Understand what is happening"),
            ("❝  Explain dialogue", "Unpack the line and subtext"),
            ("⌕  Hidden details", "Research possible Easter eggs"),
            ("✳  Ask a question", "Ask about the movie"),
        ]:
            tile = QFrame()
            tile.setStyleSheet("QFrame {background:#10223a; border:1px solid #1b3858; border-radius:10px;} QFrame:hover {background:#142d4b; border-color:#348be8;}")
            tile_layout = QVBoxLayout(tile)
            tile_layout.setContentsMargins(12, 10, 12, 10)
            name_label = QLabel(name)
            name_label.setStyleSheet("font-weight:650;")
            detail_label = QLabel(detail)
            detail_label.setObjectName("muted")
            detail_label.setStyleSheet("font-size:11px; color:#8ca7c8;")
            tile_layout.addWidget(name_label)
            tile_layout.addWidget(detail_label)
            side.addWidget(tile)
        side.addStretch()
        self.status = QLabel("●  PLAYER READY")
        self.status.setObjectName("status")
        side.addWidget(self.status)
        note = QLabel("Local AI integration will be migrated after playback and controls are verified.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        note.setStyleSheet("font-size:10px;")
        side.addWidget(note)
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
                if not self.dragging_seek:
                    self.seek.blockSignals(True)
                    self.seek.setValue(round(position / duration * 1000))
                    self.seek.blockSignals(False)
            self.current_time.setText(clock(position))
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
        self.dragging_seek = False
        if self.player and self.duration_ms:
            self.player.set_time(int(self.duration_ms * self.seek.value() / 1000))

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
                action.triggered.connect(lambda checked=False, tid=int(track_id): setter(tid))
        except Exception as exc:
            menu.addAction(f"Could not read tracks: {exc}").setEnabled(False)
        menu.exec(button.mapToGlobal(button.rect().bottomLeft()))

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
            self._fullscreen_controls_timer.stop()
            self.deck.hide()
            self.deck.setParent(None)
            self.left_layout.addWidget(self.deck)
            self.fullscreen_title.hide()
            self.showNormal()
            self.outer_layout.setContentsMargins(24, 18, 24, 22)
            self.header.show()
            self.sidebar.show()
            self.deck.show()
            self.setFocus()
        else:
            self.outer_layout.setContentsMargins(0, 0, 0, 0)
            self.header.hide()
            self.sidebar.hide()
            self.left_layout.removeWidget(self.deck)
            self.deck.setParent(self.video_surface)
            self.deck.setGeometry(self._overlay_deck_geometry())
            self.deck.hide()
            self.fullscreen_title.show()
            self._position_fullscreen_title()
            self.showFullScreen()
            self.video_surface.setFocus()
        self.update()

    def _position_fullscreen_title(self) -> None:
        if not self.current_path:
            self.fullscreen_title.hide()
            return
        self.fullscreen_title.adjustSize()
        width = min(self.fullscreen_title.sizeHint().width(), max(220, self.video_surface.width() - 32))
        self.fullscreen_title.setGeometry(self.video_surface.width() - width - 24, 20, width, 38)
        self.fullscreen_title.raise_()

    def _overlay_deck_geometry(self):
        margin = 22
        height = min(112, max(88, self.video_surface.height() // 7))
        return self.video_surface.rect().adjusted(
            margin, self.video_surface.height() - height - margin,
            -margin, -margin
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.isFullScreen():
            if self.deck.parent() is self.video_surface:
                self.deck.setGeometry(self._overlay_deck_geometry())
            self._position_fullscreen_title()

    def _poll_fullscreen_cursor(self) -> None:
        if not self.isFullScreen():
            self._last_cursor_pos = QCursor.pos()
            return
        current = QCursor.pos()
        if current != self._last_cursor_pos:
            self._last_cursor_pos = current
            self._show_fullscreen_controls()

    def _show_fullscreen_controls(self) -> None:
        if not self.isFullScreen():
            return
        self.deck.show()
        self.deck.raise_()
        self._fullscreen_controls_timer.start()

    def _hide_fullscreen_controls(self) -> None:
        if self.isFullScreen():
            self.deck.hide()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.MouseMove and self.isFullScreen():
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
