import mss
from PIL import Image
import keyboard
import pytesseract
import requests
import base64
import json
import re


OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "gemma3:4b"


# ==========================================
# MOVIEMIND MEMORY
# ==========================================

movie_memory = {
    "characters": [],
    "objects": [],
    "recent_dialogue": [],
    "scene_summary": ""
}


# ==========================================
# SCREEN CAPTURE
# ==========================================

def capture_screen():

    with mss.MSS() as sct:

        monitor = sct.monitors[1]

        screenshot = sct.grab(monitor)

        image = Image.frombytes(
            "RGB",
            screenshot.size,
            screenshot.rgb
        )

        image.save("screenshot.png")

    return image


# ==========================================
# OCR
# ==========================================
def extract_text(image):

    width, height = image.size

    # Crop the lower part of the movie screen
    # where subtitles normally appear.
    subtitle_area = image.crop(
        (
            0,
            int(height * 0.65),
            width,
            int(height * 0.95)
        )
    )

    text = pytesseract.image_to_string(
        subtitle_area,
        lang="eng",
        config="--psm 6"
    )

    return text.strip()


# ==========================================
# IMAGE → BASE64
# ==========================================

def image_to_base64(image_path):

    with open(image_path, "rb") as image_file:

        return base64.b64encode(
            image_file.read()
        ).decode("utf-8")


# ==========================================
# GEMMA
# ==========================================

def ask_gemma(subtitle):

    image_base64 = image_to_base64(
        "screenshot.png"
    )


    memory_text = f"""
Characters:
{movie_memory["characters"]}

Important objects:
{movie_memory["objects"]}

Recent dialogue:
{movie_memory["recent_dialogue"]}

Previous scene:
{movie_memory["scene_summary"]}
"""


    prompt = f"""
You are MovieMind, a local AI movie assistant.

Analyze the CURRENT movie screenshot.

Use three sources:

1. The current screenshot
2. The current subtitle
3. Previous movie context, if available

CURRENT SUBTITLE:
{subtitle}


PREVIOUS MOVIE CONTEXT:
{memory_text}


IMPORTANT:

The current screenshot is the most important source.

Previous memory is only additional context.
Do not blindly trust previous memory if the
current screenshot contradicts it.

Identify specific fictional characters when you
can confidently identify them.

Do not invent character names.

If the character identity is uncertain, say so.

Explain what is happening in the CURRENT scene
and explain the meaning of the current dialogue.

Give the user a short natural answer.

Do NOT show reasoning.

Do NOT repeat the subtitle.


After the answer, create an updated memory.

Your response MUST use exactly this structure:

ANSWER:
<short answer for the user>

MEMORY:
Characters: <comma-separated character names>
Objects: <comma-separated important objects>
Scene: <one short sentence describing the current situation>
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

            "num_predict": 220
        }
    }


    response = requests.post(
        OLLAMA_URL,
        json=data
    )

    response.raise_for_status()

    result = response.json()

    return result["message"]["content"]


# ==========================================
# PARSE GEMMA RESPONSE
# ==========================================

def parse_response(response):

    answer = ""

    characters = []
    objects = []
    scene = ""


    # --------------------------------------
    # ANSWER
    # --------------------------------------

    answer_match = re.search(
        r"ANSWER:\s*(.*?)(?=\nMEMORY:|\Z)",
        response,
        re.IGNORECASE | re.DOTALL
    )

    if answer_match:

        answer = answer_match.group(1).strip()


    # --------------------------------------
    # MEMORY
    # --------------------------------------

    memory_match = re.search(
        r"MEMORY:\s*(.*)",
        response,
        re.IGNORECASE | re.DOTALL
    )

    if memory_match:

        memory_text = memory_match.group(1)


        # Characters

        char_match = re.search(
            r"Characters:\s*(.*?)(?=\nObjects:|\Z)",
            memory_text,
            re.IGNORECASE | re.DOTALL
        )

        if char_match:

            characters = [
                x.strip()
                for x in char_match.group(1).split(",")
                if x.strip()
            ]


        # Objects

        object_match = re.search(
            r"Objects:\s*(.*?)(?=\nScene:|\Z)",
            memory_text,
            re.IGNORECASE | re.DOTALL
        )

        if object_match:

            objects = [
                x.strip()
                for x in object_match.group(1).split(",")
                if x.strip()
            ]


        # Scene

        scene_match = re.search(
            r"Scene:\s*(.*)",
            memory_text,
            re.IGNORECASE | re.DOTALL
        )

        if scene_match:

            scene = scene_match.group(1).strip()


    return answer, characters, objects, scene


# ==========================================
# UPDATE MEMORY
# ==========================================

def update_memory(
    subtitle,
    characters,
    objects,
    scene
):

    # --------------------------------------
    # Characters
    # --------------------------------------

    for character in characters:

        if character not in movie_memory["characters"]:

            movie_memory["characters"].append(
                character
            )


    # --------------------------------------
    # Objects
    # --------------------------------------

    for obj in objects:

        if obj not in movie_memory["objects"]:

            movie_memory["objects"].append(
                obj
            )


    # --------------------------------------
    # Recent dialogue
    # --------------------------------------

    if subtitle and subtitle != "No subtitle detected.":

        movie_memory["recent_dialogue"].append(
            subtitle
        )


    # Keep only the latest 5 subtitles

    movie_memory["recent_dialogue"] = (
        movie_memory["recent_dialogue"][-5:]
    )


    # --------------------------------------
    # Current scene
    # --------------------------------------

    if scene:

        movie_memory["scene_summary"] = scene


# ==========================================
# MOVIEMIND ACTION
# ==========================================

def movie_mind():

    print("\n📸 Capturing screen...")

    image = capture_screen()


    print("🔤 Reading subtitle...")

    subtitle = extract_text(image)


    if not subtitle:

        subtitle = "No subtitle detected."


    print("\n📝 OCR:")
    print("--------------------------------")

    print(subtitle)

    print("--------------------------------")


    print("\n🧠 Asking Gemma 3...")


    raw_response = ask_gemma(
        subtitle
    )


    answer, characters, objects, scene = (
        parse_response(raw_response)
    )


    # --------------------------------------
    # ANSWER FIRST
    # --------------------------------------

    print("\n🎬 MOVIEMIND")
    print("================================")

    if answer:

        print(answer)

    else:

        print(raw_response)

    print("================================")


    # --------------------------------------
    # MEMORY AFTER ANSWER
    # --------------------------------------

    update_memory(
        subtitle,
        characters,
        objects,
        scene
    )


    print("\n🧠 Memory updated.")


# ==========================================
# START
# ==========================================

print("================================")
print("       🎬 MovieMind")
print("================================")
print("Press F8 to analyze the movie.")
print("Press ESC to exit.")
print()


keyboard.add_hotkey(
    "f8",
    movie_mind
)


keyboard.wait("esc")


print("\nMovieMind closed.")