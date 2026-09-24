import mss
from PIL import Image
import keyboard
import pytesseract
import requests
import base64
import re


# ==========================================
# CONFIGURATION
# ==========================================

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "gemma3:4b"


# ==========================================
# MOVIEMIND MEMORY
# ==========================================

movie_memory = {
    # Character name -> number of observations
    "characters": {},

    # Important objects discovered
    "objects": [],

    # Last 5 subtitles
    "recent_dialogue": [],

    # Most recent scene summary
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

    # Crop the lower part of the screen
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
# IMAGE -> BASE64
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

    # --------------------------------------
    # Prepare trusted character information
    # --------------------------------------

    trusted_characters = [
        name
        for name, count in movie_memory["characters"].items()
        if count >= 2
    ]

    possible_characters = [
        name
        for name, count in movie_memory["characters"].items()
        if count == 1
    ]

    memory_text = f"""
Trusted characters:
{trusted_characters}

Possible characters:
{possible_characters}

Character observation counts:
{movie_memory["characters"]}

Important objects:
{movie_memory["objects"]}

Recent dialogue:
{movie_memory["recent_dialogue"]}

Previous scene:
{movie_memory["scene_summary"]}
"""

    # --------------------------------------
    # Prompt
    # --------------------------------------

    prompt = f"""
You are MovieMind, a local AI movie assistant.

Analyze the CURRENT movie screenshot.

Use these sources:

1. CURRENT screenshot
2. CURRENT subtitle
3. Previous movie memory

CURRENT SUBTITLE:
{subtitle}


PREVIOUS MOVIE CONTEXT:
{memory_text}


==========================================
IMPORTANT CHARACTER RULES
==========================================

The CURRENT screenshot is the PRIMARY source.

Previous memory is only supporting context.

Do NOT blindly trust previous memory.

If the current screenshot contradicts previous
memory, follow the CURRENT screenshot.

Identify specific fictional characters only when
you have reasonable visual/contextual evidence.

Do NOT invent character names.

A character appearing once in memory is only a
possibility.

A character appearing two or more times is more
reliable context, but the current screenshot still
has priority.

If you cannot confidently identify the character,
say that the identity is uncertain.

Do not force a character name.


==========================================
SCENE UNDERSTANDING
==========================================

Explain what is happening in the CURRENT scene.

Explain what the CURRENT dialogue means.

Keep the answer short and useful.

Do NOT show reasoning.

Do NOT repeat the subtitle unnecessarily.


==========================================
MEMORY
==========================================

After answering, create updated memory information.

Only include characters you actually observe or
have strong evidence for in the CURRENT scene.

For characters, use only their names.

For objects, include only important objects.

Scene should be one short sentence describing
the CURRENT situation.


==========================================
REQUIRED RESPONSE FORMAT
==========================================

ANSWER:
<short natural answer>

MEMORY:
Characters: <comma-separated names>
Objects: <comma-separated important objects>
Scene: <one short sentence>
"""

    # --------------------------------------
    # Ollama request
    # --------------------------------------

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
        json=data,
        timeout=120
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
    # Extract ANSWER
    # --------------------------------------

    answer_match = re.search(
        r"ANSWER:\s*(.*?)(?=\nMEMORY:|\Z)",
        response,
        re.IGNORECASE | re.DOTALL
    )

    if answer_match:

        answer = answer_match.group(1).strip()


    # --------------------------------------
    # Extract MEMORY
    # --------------------------------------

    memory_match = re.search(
        r"MEMORY:\s*(.*)",
        response,
        re.IGNORECASE | re.DOTALL
    )

    if memory_match:

        memory_text = memory_match.group(1)


        # ----------------------------------
        # Characters
        # ----------------------------------

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


        # ----------------------------------
        # Objects
        # ----------------------------------

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


        # ----------------------------------
        # Scene
        # ----------------------------------

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

    # ======================================
    # CHARACTERS
    # ======================================

    for character in characters:

        character = character.strip()

        if not character:
            continue

        # Avoid meaningless responses
        if character.lower() in [
            "unknown",
            "uncertain",
            "unknown character",
            "none",
            "n/a"
        ]:
            continue

        # First observation
        if character not in movie_memory["characters"]:

            movie_memory["characters"][character] = 1

        # Existing observation
        else:

            movie_memory["characters"][character] += 1


    # ======================================
    # OBJECTS
    # ======================================

    for obj in objects:

        obj = obj.strip()

        if not obj:
            continue

        if obj.lower() in [
            "unknown",
            "none",
            "n/a"
        ]:
            continue

        if obj not in movie_memory["objects"]:

            movie_memory["objects"].append(obj)


    # ======================================
    # RECENT DIALOGUE
    # ======================================

    if (
        subtitle
        and subtitle != "No subtitle detected."
    ):

        movie_memory["recent_dialogue"].append(
            subtitle
        )


    # Keep only the latest 5 subtitles
    movie_memory["recent_dialogue"] = (
        movie_memory["recent_dialogue"][-5:]
    )


    # ======================================
    # CURRENT SCENE
    # ======================================

    if scene:

        movie_memory["scene_summary"] = scene


# ==========================================
# DISPLAY MEMORY
# ==========================================

def show_memory():

    print("\n🧠 CURRENT MEMORY")
    print("--------------------------------")

    print(
        "Characters:",
        movie_memory["characters"]
    )

    print(
        "Objects:",
        movie_memory["objects"]
    )

    print(
        "Recent dialogue:",
        movie_memory["recent_dialogue"]
    )

    print(
        "Scene:",
        movie_memory["scene_summary"]
    )

    print("--------------------------------")


# ==========================================
# MOVIEMIND ACTION
# ==========================================
def ask_interactive_gemma(
    subtitle,
    choice,
    extra_input
):

    image_base64 = image_to_base64(
        "screenshot.png"
    )


    # --------------------------------------
    # Memory
    # --------------------------------------

    trusted_characters = [
        name
        for name, count in movie_memory["characters"].items()
        if count >= 2
    ]


    possible_characters = [
        name
        for name, count in movie_memory["characters"].items()
        if count == 1
    ]


    # --------------------------------------
    # Determine mode
    # --------------------------------------

    if choice == "1":

        task = """
Explain what is happening in the current scene.

Mention specific characters when reasonably
confident.

Explain the important action and situation.
"""


    elif choice == "2":

        task = f"""
Explain the meaning of the current dialogue.

Current dialogue:
{subtitle}

Explain what the speaker means in simple language.
"""


    elif choice == "3":

        task = f"""
Explain the meaning of this word in the context
of the current movie scene.

Word:
{extra_input}

Give:
1. Simple meaning
2. Meaning in this scene
3. A short example if useful
"""


    elif choice == "4":

        task = f"""
Answer the user's question using the current
screenshot, subtitle and movie memory.

User question:
{extra_input}
"""


    prompt = f"""
You are MovieMind, a local offline movie assistant.

CURRENT SCREENSHOT:
Analyze the image carefully.

CURRENT SUBTITLE:
{subtitle}

PREVIOUS MOVIE MEMORY:

Trusted characters:
{trusted_characters}

Possible characters:
{possible_characters}

Important objects:
{movie_memory["objects"]}

Recent dialogue:
{movie_memory["recent_dialogue"]}

Previous scene:
{movie_memory["scene_summary"]}


IMPORTANT RULES:

- The current screenshot is the primary evidence.
- Previous memory is only supporting context.
- Do not blindly trust memory.
- Do not invent character names.
- If a character cannot be identified confidently,
  say so.
- Keep the answer concise.
- Do not show reasoning.


TASK:

{task}
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

            "num_predict": 180
        }
    }


    response = requests.post(
        OLLAMA_URL,
        json=data,
        timeout=120
    )


    response.raise_for_status()


    result = response.json()


    return result["message"]["content"]
def movie_mind():

    print("\n📸 Capturing screen...")

    image = capture_screen()


    # --------------------------------------
    # OCR
    # --------------------------------------

    print("🔤 Reading subtitle...")

    subtitle = extract_text(image)


    if not subtitle:

        subtitle = "No subtitle detected."


    print("\n📝 OCR:")
    print("--------------------------------")
    print(subtitle)
    print("--------------------------------")


    # --------------------------------------
    # INTERACTION LOOP
    # --------------------------------------

    while True:

        print("\n🎬 What would you like to know?")
        print("--------------------------------")
        print("1. Explain this scene")
        print("2. Explain the dialogue")
        print("3. Explain a word")
        print("4. Ask a question")
        print("5. Return to movie")
        print("--------------------------------")


        choice = input(
            "Choose an option: "
        ).strip()


        # ==================================
        # RETURN TO MOVIE
        # ==================================

        if choice == "5":

            print("\n🎬 Returning to movie...")

            return


        # ==================================
        # WORD
        # ==================================

        extra_input = ""


        if choice == "3":

            extra_input = input(
                "\nEnter the word: "
            ).strip()


            if not extra_input:

                print("\n❌ No word entered.")

                continue


        # ==================================
        # QUESTION
        # ==================================

        elif choice == "4":

            extra_input = input(
                "\nEnter your question: "
            ).strip()


            if not extra_input:

                print("\n❌ No question entered.")

                continue


        # ==================================
        # INVALID OPTION
        # ==================================

        if choice not in ["1", "2", "3", "4"]:

            print("\n❌ Invalid option.")

            continue


        # ==================================
        # ASK GEMMA
        # ==================================

        print("\n🧠 Asking Gemma 3...")


        try:

            answer = ask_interactive_gemma(
                subtitle,
                choice,
                extra_input
            )

        except Exception as error:

            print("\n❌ Ollama error:")
            print(error)

            continue


        # ==================================
        # DISPLAY ANSWER
        # ==================================

        print("\n🎬 MOVIEMIND")
        print("================================")

        print(answer)

        print("================================")


        # ==================================
        # UPDATE MEMORY
        # ==================================

        try:

            raw_context = ask_gemma(
                subtitle
            )


            _, characters, objects, scene = (
                parse_response(raw_context)
            )


            update_memory(
                subtitle,
                characters,
                objects,
                scene
            )


        except Exception as error:

            print(
                "\n⚠️ Memory update skipped:",
                error
            )


        print("\n🧠 Memory updated.")


# ==========================================
# START MOVIEMIND
# ==========================================

print("================================")
print("       🎬 MovieMind")
print("================================")
print("Press F8 to analyze the movie.")
print("Press ESC to exit.")
print()


# F8 -> MovieMind
keyboard.add_hotkey(
    "f8",
    movie_mind
)


# ESC -> Exit
keyboard.wait("esc")


print("\nMovieMind closed.")