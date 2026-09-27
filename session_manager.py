import time
import threading
import subprocess
import atexit
import mss
from PIL import Image, ImageChops


class MovieSessionManager:

    # ---------------------------------------------------------
    # SETTINGS
    # ---------------------------------------------------------

    CHECKPOINT_INTERVAL = 10 * 60          # 10 minutes
    MAX_ACTIVE_TIME = 2 * 60 * 60 + 30 * 60   # 2 hours 30 minutes

    # Supported media players for the first version.
    MEDIA_PROCESSES = {
        "vlc.exe",
        "wmplayer.exe",
        "mpv.exe",
        "potplayer.exe",
        "potplayermini.exe",
        "potplayermini64.exe",
        "video.ui.exe"
    }

    def __init__(self):

        self.active = False
        self.paused = False

        # Total movie PLAYING time.
        # Paused time is not counted.
        self.active_movie_seconds = 0

        self.last_checkpoint_time = 0
        self.last_state_check = time.time()

        self.stop_reason = ""

        self.monitor_thread = None

        self.on_checkpoint = None
        self.on_pause = None
        self.on_resume = None
        self.on_stop = None

        self._lock = threading.Lock()

        # Used to determine whether the movie screen is changing.
        self.previous_frame = None

        self.sct = mss.mss()

        atexit.register(self.shutdown)


    # =========================================================
    # START SESSION
    # =========================================================

    def start(self):

        with self._lock:

            if self.active:
                print("🎬 MovieMind session is already active.")
                return

            self.active = True
            self.paused = False

            self.active_movie_seconds = 0
            self.last_checkpoint_time = time.time()
            self.last_state_check = time.time()

            self.stop_reason = ""

            self.previous_frame = self.capture_small_frame()

            print()
            print("================================")
            print("       🎬 MovieMind Session")
            print("================================")
            print("🟢 Session started.")
            print("🧠 Background movie understanding: ON")
            print("⏱️ Checkpoints: approximately every 10 minutes")
            print("⏳ Maximum active movie time: 2 hours 30 minutes")
            print()
            print("F8 → Ask MovieMind")
            print()

        self.monitor_thread = threading.Thread(
            target=self.monitor_loop,
            daemon=True
        )

        self.monitor_thread.start()


    # =========================================================
    # STOP SESSION
    # =========================================================

    def stop(self, reason="Manual stop"):

        with self._lock:

            if not self.active:
                return

            self.active = False
            self.paused = False
            self.stop_reason = reason

        print()
        print("🛑 MovieMind session stopped.")
        print(f"Reason: {reason}")
        print(
            f"Active movie time: "
            f"{self.format_time(self.active_movie_seconds)}"
        )

        if self.on_stop:
            try:
                self.on_stop(reason)
            except Exception as e:
                print(f"⚠️ Stop callback error: {e}")


    # =========================================================
    # PAUSE
    # =========================================================

    def pause(self):

        with self._lock:

            if not self.active or self.paused:
                return

            self.paused = True

        print("⏸️ Movie paused.")
        print("🧠 MovieMind memory preserved.")
        print("⏱️ Background monitoring paused.")

        if self.on_pause:
            try:
                self.on_pause()
            except Exception as e:
                print(f"⚠️ Pause callback error: {e}")


    # =========================================================
    # RESUME
    # =========================================================

    def resume(self):

        with self._lock:

            if not self.active or not self.paused:
                return

            self.paused = False

            # Restart the checkpoint timer.
            self.last_checkpoint_time = time.time()

        print("▶️ Movie resumed.")
        print("🧠 MovieMind monitoring resumed.")
        print("💾 Existing movie memory preserved.")

        if self.on_resume:
            try:
                self.on_resume()
            except Exception as e:
                print(f"⚠️ Resume callback error: {e}")


    # =========================================================
    # BACKGROUND MONITOR
    # =========================================================

    def monitor_loop(self):

        while self.active:

            try:

                time.sleep(2)

                if not self.active:
                    break

                # -------------------------------------------------
                # 1. Check whether media player still exists
                # -------------------------------------------------

                if not self.media_player_running():

                    self.stop(
                        "Media player closed or movie session ended."
                    )

                    break


                # -------------------------------------------------
                # 2. Determine whether movie is playing/paused
                # -------------------------------------------------

                playing = self.detect_movie_playing()


                if playing:

                    if self.paused:
                        self.resume()

                    # Count only active movie time.
                    now = time.time()

                    elapsed = now - self.last_state_check

                    self.active_movie_seconds += elapsed

                    self.last_state_check = now

                else:

                    if not self.paused:
                        self.pause()

                    self.last_state_check = time.time()


                # -------------------------------------------------
                # 3. Maximum active movie time
                # -------------------------------------------------

                if (
                    self.active_movie_seconds
                    >= self.MAX_ACTIVE_TIME
                ):

                    self.stop(
                        "Maximum 2 hour 30 minute session reached."
                    )

                    break


                # -------------------------------------------------
                # 4. Ten-minute checkpoint
                # -------------------------------------------------

                if not self.paused:

                    elapsed_since_checkpoint = (
                        time.time() - self.last_checkpoint_time
                    )

                    if (
                        elapsed_since_checkpoint
                        >= self.CHECKPOINT_INTERVAL
                    ):

                        self.last_checkpoint_time = time.time()

                        print()
                        print("🧠 ===============================")
                        print("🧠 MovieMind checkpoint reached")
                        print(
                            f"🧠 Movie time: "
                            f"{self.format_time(self.active_movie_seconds)}"
                        )
                        print("🧠 ===============================")

                        if self.on_checkpoint:

                            try:
                                self.on_checkpoint()

                            except Exception as e:
                                print(
                                    f"⚠️ Checkpoint callback error: {e}"
                                )


            except Exception as e:

                print(
                    f"⚠️ Session monitor error: {e}"
                )

                time.sleep(3)


    # =========================================================
    # CHECK MEDIA PLAYER PROCESS
    # =========================================================

    def media_player_running(self):

        try:

            result = subprocess.run(
                [
                    "tasklist",
                    "/FO",
                    "CSV",
                    "/NH"
                ],
                capture_output=True,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )

            output = result.stdout.lower()

            for process in self.MEDIA_PROCESSES:

                if f'"{process}"' in output:
                    return True

            return False

        except Exception as e:

            print(
                f"⚠️ Could not check media player: {e}"
            )

            # Fail-safe:
            # Don't accidentally close MovieMind because
            # process detection failed.
            return True


    # =========================================================
    # DETECT PLAYING / PAUSED
    # =========================================================

    def detect_movie_playing(self):

        """
        First-version heuristic:

        Capture a small portion of the screen twice.

        If the movie frame changes:
            → probably playing

        If the frame remains unchanged:
            → probably paused

        This is intentionally lightweight.
        """

        try:

            frame1 = self.capture_small_frame()

            time.sleep(1.2)

            frame2 = self.capture_small_frame()

            if frame1 is None or frame2 is None:
                return True

            difference = ImageChops.difference(
                frame1,
                frame2
            )

            bbox = difference.getbbox()

            # No visible change.
            if bbox is None:
                return False

            # Calculate rough difference.
            histogram = difference.histogram()

            total_difference = sum(
                value * (index % 256)
                for index, value in enumerate(histogram)
            )

            # Very small changes can be subtitles,
            # clock, mouse movement, etc.
            if total_difference < 50000:
                return False

            return True

        except Exception as e:

            print(
                f"⚠️ Playback detection error: {e}"
            )

            # Don't pause MovieMind if detection fails.
            return True


    # =========================================================
    # LIGHTWEIGHT SCREEN CAPTURE
    # =========================================================

    def capture_small_frame(self):

        try:

            monitor = self.sct.monitors[1]

            screenshot = self.sct.grab(monitor)

            image = Image.frombytes(
                "RGB",
                screenshot.size,
                screenshot.rgb
            )

            # Downscale heavily.
            image.thumbnail((320, 180))

            return image

        except Exception as e:

            print(
                f"⚠️ Screen capture error: {e}"
            )

            return None


    # =========================================================
    # STATUS
    # =========================================================

    def get_status(self):

        with self._lock:

            if not self.active:
                return "STOPPED"

            if self.paused:
                return "PAUSED"

            return "PLAYING"


    # =========================================================
    # TIME FORMAT
    # =========================================================

    @staticmethod
    def format_time(seconds):

        seconds = int(seconds)

        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60

        if hours > 0:

            return (
                f"{hours:02d}:"
                f"{minutes:02d}:"
                f"{secs:02d}"
            )

        return (
            f"{minutes:02d}:"
            f"{secs:02d}"
        )


    # =========================================================
    # SHUTDOWN
    # =========================================================

    def shutdown(self):

        if self.active:

            self.stop(
                "MovieMind process terminated."
            )

        try:
            self.sct.close()
        except Exception:
            pass