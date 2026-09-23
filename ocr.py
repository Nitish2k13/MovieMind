import pytesseract
from PIL import Image


def extract_text(image_path):
    image = Image.open(image_path)

    text = pytesseract.image_to_string(
        image,
        lang="eng"
    )

    return text.strip()


if __name__ == "__main__":
    text = extract_text("screenshot.png")

    print("\n==============================")
    print("        OCR RESULT")
    print("==============================")

    if text:
        print(text)
    else:
        print("No text detected.")

    print("==============================")