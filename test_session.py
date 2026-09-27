import time

from session_manager import MovieSessionManager


session = MovieSessionManager()


def checkpoint():
    print()
    print("🎬 TEST CHECKPOINT")
    print("This is where MovieMind will analyze the movie.")
    print()


def paused():
    print("⏸️ TEST: MovieMind paused")


def resumed():
    print("▶️ TEST: MovieMind resumed")


def stopped(reason):
    print(f"🛑 TEST: Session ended → {reason}")


session.on_checkpoint = checkpoint
session.on_pause = paused
session.on_resume = resumed
session.on_stop = stopped


print()
print("================================")
print("     MovieMind Session Test")
print("================================")
print()
print("Make sure VLC is OPEN before starting.")
print()
print("Press ENTER to start...")
input()


session.start()


try:

    while session.active:

        time.sleep(1)

        status = session.get_status()

        print(
            f"\rStatus: {status} | "
            f"Movie time: "
            f"{session.format_time(session.active_movie_seconds)}",
            end="",
            flush=True
        )

except KeyboardInterrupt:

    print()
    print("Keyboard interrupt.")

    session.stop(
        "Test interrupted by user."
    )