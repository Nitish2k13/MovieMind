import mss
from PIL import Image
import keyboard
import pytesseract
import requests
import base64
import re
import tkinter as tk
from tkinter import simpledialog, messagebox


# ==========================================
# CONFIGURATION
# ==========================================

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "gemma3:4b"


# ==========================================
# MOVIE MEMORY
# ==========================================

movie_memory = {
    "characters": {},
    "objects": [],
    "recent_dialogue": [],
    "scene_summary": ""
}


# ==========================================
# CURRENT FRAME CACHE
# ==========================================

current_frame = {
    "subtitle": "",
    "scene_analysis": "",
    "dialogue_meaning": "",
    "characters": [],
    "objects": [],
    "scene": ""
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
# GEMMA VISION
# ==========================================

def analyze_current_frame(subtitle):

    image_base64 = image_to_base64(
        "screenshot.png"
    )

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

    prompt = f"""
You are MovieMind, a local offline movie assistant.

Analyze the CURRENT movie screenshot.

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

1. The CURRENT screenshot is the primary evidence.

2. Previous memory is only supporting context.

3. Never let previous memory override the current
   screenshot.

4. Identify specific fictional characters when
   reasonably confident.

5. Do not invent character names.

6. If a character cannot be identified confidently,
   say that the identity is uncertain.

7. Explain the current scene.

8. Explain what the current dialogue means.

9. Keep the response concise.

10. Do not show reasoning.

Use EXACTLY this format:

ANSWER:
<short scene explanation>

DIALOGUE:
<meaning of the current subtitle>

MEMORY:
Characters: <comma-separated names>
Objects: <comma-separated important objects>
Scene: <one short sentence>
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
        json=data,
        timeout=120
    )

    response.raise_for_status()

    result = response.json()

    return result["message"]["content"]


# ==========================================
# PARSE VISION RESPONSE
# ==========================================

def parse_vision_response(response):

    answer = ""
    dialogue = ""
    characters = []
    objects = []
    scene = ""

    answer_match = re.search(
        r"ANSWER:\s*(.*?)(?=\nDIALOGUE:|\nMEMORY:|\Z)",
        response,
        re.IGNORECASE | re.DOTALL
    )

    if answer_match:
        answer = answer_match.group(1).strip()

    dialogue_match = re.search(
        r"DIALOGUE:\s*(.*?)(?=\nMEMORY:|\Z)",
        response,
        re.IGNORECASE | re.DOTALL
    )

    if dialogue_match:
        dialogue = dialogue_match.group(1).strip()

    memory_match = re.search(
        r"MEMORY:\s*(.*)",
        response,
        re.IGNORECASE | re.DOTALL
    )

    if memory_match:

        memory_text = memory_match.group(1)

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

        scene_match = re.search(
            r"Scene:\s*(.*)",
            memory_text,
            re.IGNORECASE | re.DOTALL
        )

        if scene_match:
            scene = scene_match.group(1).strip()

    return (
        answer,
        dialogue,
        characters,
        objects,
        scene
    )


# ==========================================
# UPDATE MOVIE MEMORY
# ==========================================

def update_memory(
    subtitle,
    characters,
    objects,
    scene
):

    for character in characters:

        character = character.strip()

        if not character:
            continue

        if character.lower() in [
            "unknown",
            "uncertain",
            "unknown character",
            "none",
            "n/a"
        ]:
            continue

        if character not in movie_memory["characters"]:
            movie_memory["characters"][character] = 1
        else:
            movie_memory["characters"][character] += 1

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

    if (
        subtitle
        and subtitle != "No subtitle detected."
    ):

        movie_memory["recent_dialogue"].append(
            subtitle
        )

    movie_memory["recent_dialogue"] = (
        movie_memory["recent_dialogue"][-5:]
    )

    if scene:
        movie_memory["scene_summary"] = scene


# ==========================================
# UPDATE FRAME CACHE
# ==========================================

def update_frame_cache(
    subtitle,
    answer,
    dialogue,
    characters,
    objects,
    scene
):

    current_frame["subtitle"] = subtitle
    current_frame["scene_analysis"] = answer
    current_frame["dialogue_meaning"] = dialogue
    current_frame["characters"] = characters
    current_frame["objects"] = objects
    current_frame["scene"] = scene


# ==========================================
# TEXT-ONLY GEMMA
# ==========================================

def ask_text_gemma(prompt):

    data = {
        "model": MODEL,
        "messages": [
            {
                "role": "user",
                "content": prompt
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


# ==========================================
# INTERACTIVE ANSWERS
# ==========================================

def get_answer(choice, extra_input=""):

    subtitle = current_frame["subtitle"]
    scene = current_frame["scene_analysis"]
    dialogue = current_frame["dialogue_meaning"]
    characters = current_frame["characters"]
    objects = current_frame["objects"]

    trusted_characters = [
        name
        for name, count in movie_memory["characters"].items()
        if count >= 2
    ]

    # --------------------------------------
    # SCENE
    # --------------------------------------

    if choice == "scene":

        return scene


    # --------------------------------------
    # DIALOGUE
    # --------------------------------------

    if choice == "dialogue":

        return dialogue


    # --------------------------------------
    # WORD
    # --------------------------------------

    if choice == "word":

        prompt = f"""
You are MovieMind.

Explain this word in the context of the
current movie scene.

WORD:
{extra_input}

CURRENT SUBTITLE:
{subtitle}

CURRENT SCENE:
{scene}

CURRENT DIALOGUE MEANING:
{dialogue}

Give:

1. Simple meaning
2. Meaning in this scene
3. Short example if useful

Keep the answer concise.
Do not show reasoning.
"""

        return ask_text_gemma(prompt)


    # --------------------------------------
    # QUESTION
    # --------------------------------------

    if choice == "question":

        prompt = f"""
You are MovieMind, a local offline movie assistant.

Answer the user's question using the current
movie context.

CURRENT SUBTITLE:
{subtitle}

CURRENT SCENE:
{scene}

CURRENT DIALOGUE:
{dialogue}

CURRENT CHARACTERS:
{characters}

CURRENT OBJECTS:
{objects}

PREVIOUS MOVIE MEMORY:
{movie_memory["scene_summary"]}

TRUSTED CHARACTERS:
{trusted_characters}

USER QUESTION:
{extra_input}

Rules:

- Use available evidence.
- Do not invent facts.
- If something is unknown, say so.
- Keep the answer concise.
- Do not show reasoning.
"""

        return ask_text_gemma(prompt)


# ==========================================
# BASIC TKINTER UI
# ==========================================

def show_movie_mind_window():

    window = tk.Tk()

    window.title("🎬 MovieMind")

    window.geometry("520x520")

    window.resizable(False, False)


    # --------------------------------------
    # Title
    # --------------------------------------

    title = tk.Label(
        window,
        text="🎬 MovieMind",
        font=("Arial", 20, "bold")
    )

    title.pack(
        pady=(15, 5)
    )


    subtitle_label = tk.Label(
        window,
        text="What would you like to know?",
        font=("Arial", 11)
    )

    subtitle_label.pack(
        pady=(0, 15)
    )


    # --------------------------------------
    # Answer area
    # --------------------------------------

    answer_box = tk.Text(
        window,
        height=12,
        width=58,
        wrap="word",
        font=("Arial", 10)
    )

    answer_box.pack(
        padx=15,
        pady=10
    )


    answer_box.insert(
        "1.0",
        current_frame["scene_analysis"]
    )

    answer_box.config(
        state="disabled"
    )


    # ======================================
    # DISPLAY ANSWER
    # ======================================

    def display_answer(answer):

        answer_box.config(
            state="normal"
        )

        answer_box.delete(
            "1.0",
            tk.END
        )

        answer_box.insert(
            "1.0",
            answer
        )

        answer_box.config(
            state="disabled"
        )


    # ======================================
    # SCENE
    # ======================================

    def scene_clicked():

        display_answer(
            get_answer("scene")
        )


    # ======================================
    # DIALOGUE
    # ======================================

    def dialogue_clicked():

        display_answer(
            get_answer("dialogue")
        )


    # ======================================
    # WORD
    # ======================================

    def word_clicked():

        word = simpledialog.askstring(
            "Explain Word",
            "Enter the word:",
            parent=window
        )

        if not word:
            return

        display_answer(
            get_answer(
                "word",
                word
            )
        )


    # ======================================
    # QUESTION
    # ======================================

    def question_clicked():

        question = simpledialog.askstring(
            "Ask Question",
            "Enter your question:",
            parent=window
        )

        if not question:
            return

        display_answer(
            get_answer(
                "question",
                question
            )
        )


    # ======================================
    # BUTTONS
    # ======================================

    button_frame = tk.Frame(window)

    button_frame.pack(
        pady=5
    )


    scene_button = tk.Button(
        button_frame,
        text="Explain Scene",
        width=18,
        command=scene_clicked
    )

    scene_button.grid(
        row=0,
        column=0,
        padx=5,
        pady=5
    )


    dialogue_button = tk.Button(
        button_frame,
        text="Explain Dialogue",
        width=18,
        command=dialogue_clicked
    )

    dialogue_button.grid(
        row=0,
        column=1,
        padx=5,
        pady=5
    )


    word_button = tk.Button(
        button_frame,
        text="Explain Word",
        width=18,
        command=word_clicked
    )

    word_button.grid(
        row=1,
        column=0,
        padx=5,
        pady=5
    )


    question_button = tk.Button(
        button_frame,
        text="Ask Question",
        width=18,
        command=question_clicked
    )

    question_button.grid(
        row=1,
        column=1,
        padx=5,
        pady=5
    )


    # --------------------------------------
    # CLOSE
    # --------------------------------------

    close_button = tk.Button(
        window,
        text="Close",
        width=15,
        command=window.destroy
    )

    close_button.pack(
        pady=15
    )


    window.mainloop()


# ==========================================
# MOVIEMIND ACTION
# ==========================================

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
    # ONE VISION ANALYSIS
    # --------------------------------------

    print("\n🧠 Analyzing current scene...")


    try:

        raw_response = analyze_current_frame(
            subtitle
        )

    except Exception as error:

        print("\n❌ Ollama error:")
        print(error)

        return


    # --------------------------------------
    # PARSE
    # --------------------------------------

    (
        answer,
        dialogue,
        characters,
        objects,
        scene
    ) = parse_vision_response(
        raw_response
    )


    # --------------------------------------
    # CACHE
    # --------------------------------------

    update_frame_cache(
        subtitle,
        answer,
        dialogue,
        characters,
        objects,
        scene
    )


    # --------------------------------------
    # MEMORY
    # --------------------------------------

    update_memory(
        subtitle,
        characters,
        objects,
        scene
    )


    print("\n🧠 Frame analysis complete.")

    print("🪟 Opening MovieMind UI...")


    # --------------------------------------
    # OPEN UI
    # --------------------------------------

    show_movie_mind_window()


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