import mss
from PIL import Image
import keyboard
import pytesseract


def capture_screen():
    # Capture screen
    with mss.MSS() as sct:
        monitor = sct.monitors[1]

        screenshot = sct.grab(monitor)

        image = Image.frombytes(
            "RGB",
            screenshot.size,
            screenshot.rgb
        )

        image.save("screenshot.png")

    print("\n📸 Screenshot captured!")

    # OCR
    text = pytesseract.image_to_string(
        image,
        lang="eng"
    ).strip()

    print("\n🔤 OCR RESULT:")
    print("------------------------------")

    if text:
        print(text)
    else:
        print("No text detected.")

    print("------------------------------")


print("================================")
print("       🎬 MovieMind")
print("================================")
print("Press F8 to capture + read.")
print("Press ESC to exit.")
print()

keyboard.add_hotkey("f8", capture_screen)

keyboard.wait("esc")

print("\nMovieMind closed.")