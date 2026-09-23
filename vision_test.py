import requests
import base64
import json

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "gemma3:4b"


def image_to_base64(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode("utf-8")


def ask_vision():

    # Convert screenshot to base64
    image_base64 = image_to_base64("screenshot.png")

    prompt = """
You are MovieMind, a local movie assistant.

Look at the movie screenshot and the subtitle extracted by OCR.

Subtitle:
"He stole the Space Stone from me..."

Explain what is happening in this scene.

Give a short answer in simple English.
Do not describe your reasoning.
Do not repeat the subtitle.
Focus on the scene and the meaning of the dialogue.
"""

    data = {
        "model": MODEL,
        "messages": [
            {
                "role": "user",
                "content": prompt,
                "images": [image_base64]
            }
        ],
        "stream": False,
        "options": {
            "temperature": 0.2,
            "num_predict": 100
        }
    }

    response = requests.post(
        OLLAMA_URL,
        json=data
    )

    response.raise_for_status()

    result = response.json()

    answer = result["message"]["content"]

    print("\n==============================")
    print("       🧠 MovieMind AI")
    print("==============================")
    print(answer)
    print("==============================")


if __name__ == "__main__":
    ask_vision()