import time
import threading
import subprocess
import atexit

import vlc


class MovieSessionManager:

    # =========================================================
    # SETTINGS
    # =========================================================

    CHECKPOINT_INTERVAL = 10 * 60
    MAX_ACTIVE_TIME = 2 * 60 * 60 + 30 * 60

    VLC_PROCESS = "vlc.exe"

    def __init__(self):

        self.active = False
        self.paused = False

        # Only counts actual PLAYING time.
        self.active_movie_seconds = 0

        self.last_state_check = time.time()
        self.last_checkpoint_time = time.time()

        self.stop_reason = ""

        self.monitor_thread = None

        # Callbacks
        self.on_checkpoint = None
        self.on_pause = None
        self.on_resume = None
        self.on_stop = None

        self._lock = threading.Lock()

        # VLC LibVLC instance
        self.vlc_instance = vlc.Instance()

        # VLC media player object
        self.vlc_player = self.vlc_instance.media_player_new()

        atexit.register(self.shutdown)


    # =========================================================
    # START
    # =========================================================

    def start(self):

        with self._lock:

            if self.active:
                print("🎬 MovieMind session is already active.")
                return

            self.active = True
            self.paused = False

            self.active_movie_seconds = 0

            self.last_state_check = time.time()
            self.last_checkpoint_time = time.time()

            self.stop_reason = ""

        print()
        print("================================")
        print("       🎬 MovieMind Session")
        print("================================")
        print("🟢 Session started.")
        print("🎬 VLC playback monitoring: ON")
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
    # STOP
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

        print()
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

            self.last_state_check = time.time()
            self.last_checkpoint_time = time.time()

        print()
        print("▶️ Movie resumed.")
        print("🧠 MovieMind monitoring resumed.")
        print("💾 Existing movie memory preserved.")

        if self.on_resume:

            try:
                self.on_resume()

            except Exception as e:

                print(f"⚠️ Resume callback error: {e}")


    # =========================================================
    # MAIN MONITOR
    # =========================================================

    def monitor_loop(self):

        while self.active:

            try:

                time.sleep(2)

                if not self.active:
                    break


                # -------------------------------------------------
                # VLC PROCESS CHECK
                # -------------------------------------------------

                if not self.vlc_running():

                    self.stop(
                        "VLC was closed."
                    )

                    break


                # -------------------------------------------------
                # VLC PLAYBACK STATE
                # -------------------------------------------------

                state = self.get_vlc_state()


                # =================================================
                # PLAYING
                # =================================================

                if state == "PLAYING":

                    if self.paused:

                        self.resume()


                    now = time.time()

                    elapsed = (
                        now - self.last_state_check
                    )

                    self.active_movie_seconds += elapsed

                    self.last_state_check = now


                # =================================================
                # PAUSED
                # =================================================

                elif state == "PAUSED":

                    if not self.paused:

                        self.pause()

                    self.last_state_check = time.time()


                # =================================================
                # STOPPED / ENDED
                # =================================================

                elif state in (
                    "STOPPED",
                    "ENDED",
                    "ERROR"
                ):

                    self.stop(
                        f"VLC state: {state}"
                    )

                    break


                # =================================================
                # NO MEDIA / UNKNOWN
                # =================================================

                else:

                    self.last_state_check = time.time()


                # -------------------------------------------------
                # MAXIMUM ACTIVE MOVIE TIME
                # -------------------------------------------------

                if (
                    self.active_movie_seconds
                    >= self.MAX_ACTIVE_TIME
                ):

                    self.stop(
                        "Maximum 2 hour 30 minute active "
                        "movie time reached."
                    )

                    break


                # -------------------------------------------------
                # 10-MINUTE CHECKPOINT
                # -------------------------------------------------

                if not self.paused:

                    elapsed_since_checkpoint = (
                        time.time()
                        - self.last_checkpoint_time
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
                            "🧠 Active movie time: "
                            + self.format_time(
                                self.active_movie_seconds
                            )
                        )
                        print("🧠 ===============================")

                        if self.on_checkpoint:

                            try:

                                self.on_checkpoint()

                            except Exception as e:

                                print(
                                    "⚠️ Checkpoint callback error:",
                                    e
                                )


            except Exception as e:

                print(
                    f"⚠️ Session monitor error: {e}"
                )

                time.sleep(3)


    # =========================================================
    # CHECK VLC PROCESS
    # =========================================================

    def vlc_running(self):

        try:

            result = subprocess.run(
                [
                    "tasklist",
                    "/FI",
                    "IMAGENAME eq vlc.exe"
                ],
                capture_output=True,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )

            return (
                "vlc.exe"
                in result.stdout.lower()
            )

        except Exception as e:

            print(
                f"⚠️ Could not check VLC process: {e}"
            )

            # Fail safe.
            return True


    # =========================================================
    # GET VLC STATE
    # =========================================================

    def get_vlc_state(self):

        try:

            state = self.vlc_player.get_state()

            # LibVLC state values.
            state_name = str(state).upper()

            if "PLAYING" in state_name:
                return "PLAYING"

            if "PAUSED" in state_name:
                return "PAUSED"

            if "STOPPED" in state_name:
                return "STOPPED"

            if "ENDED" in state_name:
                return "ENDED"

            if "ERROR" in state_name:
                return "ERROR"

            return "UNKNOWN"

        except Exception as e:

            print(
                f"⚠️ VLC state error: {e}"
            )

            return "UNKNOWN"


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
    # TIME
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

            self.vlc_player.release()
            self.vlc_instance.release()

        except Exception:

            pass