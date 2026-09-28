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
# =========================================================
# REPLY LANGUAGE
# =========================================================

response_language = "Tanglish"


def get_language_instruction():
    """
    Creates the language instruction used by every AI request.
    """

    if response_language == "English":

        return """
Respond completely in clear, natural English.

Keep character names, movie names, places,
and proper nouns in their original form.
"""

    elif response_language == "Tamil":

        return """
Respond in natural Tamil.

Use Tamil Unicode script.
Do not respond entirely in English.

Keep character names, movie names, places,
and proper nouns in their original form when appropriate.
"""

    else:

        return """
Respond in natural Tanglish.

Tanglish means Tamil written using English/Latin letters.

Example:
"Tony, Doctor Strange kitta pesitu irukkaan.
Rendu perum enemy-a stop panna plan pannitu irukkaanga."

IMPORTANT:
- Use Tamil words written using English letters.
- Do NOT use Tamil Unicode characters.
- Do NOT answer entirely in English.
- Use natural conversational Tamil-English mixed speech.
- Character names, movie names, places and proper nouns
  can remain in English.
- Avoid overly formal Tamil.
- Explain things like you are casually explaining the movie
  to a friend.
"""


# ==========================================
# MOVIE MEMORY
# ==========================================

movie_memory = {
    "characters": {},
    "objects": [],
    "recent_dialogue": [],
    "scene_summary": "",
    "visual_observations": []
}

# Safe visual memory: observations are created only when the user presses F8.
# We store compact metadata + a tiny visual signature, NOT full screenshots.
# This keeps storage and battery usage extremely low.
MAX_VISUAL_OBSERVATIONS = 300
MAX_VISUAL_CONTEXT = 30


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

# The answer currently visible in the UI.
current_displayed_answer = ""


# ==========================================
# LIGHTWEIGHT VISUAL MEMORY
# ==========================================

def make_visual_signature(image):
    """Create a tiny screen fingerprint without saving the full image."""

    small = image.convert("L").resize((32, 18))
    pixels = list(small.getdata())
    average = sum(pixels) / len(pixels)

    # One byte per pixel after normalization.
    signature = bytes(
        max(0, min(255, int(pixel - average + 128)))
        for pixel in pixels
    )

    return signature.hex()


def visual_signature_difference(signature_a, signature_b):
    """Return a simple 0..1 difference score for two signatures."""

    if not signature_a or not signature_b:
        return 1.0

    try:
        a = bytes.fromhex(signature_a)
        b = bytes.fromhex(signature_b)
    except ValueError:
        return 1.0

    if len(a) != len(b) or not a:
        return 1.0

    difference = sum(abs(x - y) for x, y in zip(a, b))
    return difference / (255 * len(a))


def build_visual_memory_context():
    """Build a small text summary for Gemma without sending old screenshots."""

    observations = movie_memory.get("visual_observations", [])[-MAX_VISUAL_CONTEXT:]

    if not observations:
        return "No previous visual observations yet."

    lines = []

    for observation in observations:
        index = observation.get("index", "?")
        objects = ", ".join(observation.get("objects", [])) or "none"
        characters = ", ".join(observation.get("characters", [])) or "none"
        scene = observation.get("scene", "")
        subtitle = observation.get("subtitle", "")

        lines.append(
            f"F8 observation #{index}: Characters=[{characters}] | "
            f"Objects=[{objects}] | Scene={scene} | Subtitle={subtitle}"
        )

    return "\n".join(lines)


def save_visual_observation(image, subtitle, characters, objects, scene):
    """Store only compact visual metadata; do not save another full screenshot."""

    signature = make_visual_signature(image)

    previous = movie_memory.get("visual_observations", [])
    index = len(previous) + 1

    similar_observations = []

    for observation in previous[-MAX_VISUAL_CONTEXT:]:
        difference = visual_signature_difference(
            signature,
            observation.get("signature", "")
        )

        if difference < 0.12:
            similar_observations.append({
                "index": observation.get("index", "?"),
                "difference": round(difference, 3),
                "objects": observation.get("objects", []),
                "characters": observation.get("characters", [])
            })

    observation = {
        "index": index,
        "signature": signature,
        "subtitle": subtitle,
        "characters": list(characters),
        "objects": list(objects),
        "scene": scene,
        "similar_previous": similar_observations[-5:]
    }

    movie_memory["visual_observations"].append(observation)
    movie_memory["visual_observations"] = (
        movie_memory["visual_observations"][-MAX_VISUAL_OBSERVATIONS:]
    )

    return similar_observations


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

    language_instruction = get_language_instruction()

    prompt = f"""
You are MovieMind, a local offline movie assistant.

{language_instruction}

Analyze the CURRENT movie screenshot and the subtitle.

The user wants a genuinely useful movie explanation, not a
one-line visual description.

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

PREVIOUS VISUAL OBSERVATIONS:
{build_visual_memory_context()}

IMPORTANT RULES:

1. The CURRENT screenshot is the primary visual evidence.

2. Previous memory is supporting context.

3. Never let previous memory override clear current evidence.

4. Identify specific fictional characters when reasonably confident.

5. Do not invent character names or plot facts.

6. If identity is uncertain, say so naturally.

7. Explain WHO is present, WHAT they are doing,
   WHO is speaking to whom when reasonably identifiable,
   WHAT the conflict/problem is, and WHY the scene matters.

8. Explain the dialogue in simple language instead of merely
   repeating the subtitle.

9. Connect the current scene to the previous scene when the
   available memory supports that connection.

10. The ANSWER should normally be 5-7 natural sentences.
    It may be slightly longer when needed for context.
    Do not make it unnecessarily verbose.

11. Do not show hidden reasoning or chain-of-thought.

12. If the user later asks about a hidden detail, reference,
    callback, foreshadowing, or Easter egg, use only evidence
    available from the current frame, subtitle, memory, and
    your known movie knowledge. Clearly distinguish a likely
    interpretation from something directly visible.

13. Previous visual observations are only hints. If the same
    object, character, symbol, costume, or visual element appears
    to recur, mention it as a possible visual callback only when
    the evidence supports it. Do not claim filmmaker intent.

Use EXACTLY this format:

ANSWER:
<5-7 sentence useful scene explanation>

DIALOGUE:
<clear explanation of what the current subtitle means and why
it matters in this scene>

MEMORY:
Characters: <comma-separated names>
Objects: <comma-separated important objects>
Scene: <one useful sentence describing the event>
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

    language_instruction = get_language_instruction()

    full_prompt = f"""
You are MovieMind, an offline movie assistant.

{language_instruction}

Give a useful, context-aware answer.

The user does NOT want a one-line answer.
For factual questions about characters, events, relationships,
objects, or plot points, give enough context for a normal movie
viewer to understand the answer.

Normally answer in 4-7 sentences. Use more sentences when the
question genuinely needs more context.

For a question such as "Who is Iron Man?", do not answer only
"Tony Stark is Iron Man." Explain who Tony Stark is, his role in
the movie, relevant relationships, and why he matters in the
current situation when that information is available.

If the user asks for visual hidden details or Easter eggs, focus
on things visible in the current scene: objects, symbols,
posters, background details, costumes, repeated visual elements,
and visual callbacks. Do not invent a hidden detail.

Use available evidence from the prompt and make reasonable
inferences when supported.

Do not simply say "I don't know" when useful information exists.
If one specific detail is genuinely unavailable, explain what
can be established and what cannot.

Do not show reasoning.

USER REQUEST:
{prompt}
"""

    data = {
        "model": MODEL,
        "messages": [
            {
                "role": "user",
                "content": full_prompt
            }
        ],
        "stream": False,
        "options": {
            "temperature": 0.2,
            "num_predict": 320
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


def translate_existing_answer(answer):
    """
    Re-express an already generated MovieMind answer in the
    currently selected language without changing its meaning.
    """

    language_instruction = get_language_instruction()

    prompt = f"""
Re-express the following MovieMind answer in the selected
reply language.

{language_instruction}

IMPORTANT:
- Preserve the original facts and meaning.
- Do not add new information.
- Do not remove important details.
- Keep character names, movie names, and proper nouns unchanged
  when appropriate.
- If Tanglish is selected, use Tamil written in English letters.
- Do not explain what you are doing.
- Return only the rewritten answer.

EXISTING ANSWER:
{answer}
"""

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
            "temperature": 0.15,
            "num_predict": 320
        }
    }

    response = requests.post(
        OLLAMA_URL,
        json=data,
        timeout=120
    )

    response.raise_for_status()

    result = response.json()

    return result["message"]["content"].strip()


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
2. Meaning in this movie scene
3. Why the word matters here
4. A short example if useful

Answer in 3-5 sentences.
Do not show reasoning.
"""

        return ask_text_gemma(prompt)


    # --------------------------------------
    # QUESTION
    # --------------------------------------

    if choice == "question":

        prompt = f"""
Answer the user's question using ALL useful movie context below.

CURRENT SUBTITLE:
{subtitle}

CURRENT SCENE EXPLANATION:
{scene}

CURRENT DIALOGUE MEANING:
{dialogue}

CURRENT CHARACTERS:
{characters}

CURRENT OBJECTS:
{objects}

PREVIOUS MOVIE MEMORY / LAST KNOWN SCENE:
{movie_memory["scene_summary"]}

RECENT DIALOGUE:
{movie_memory["recent_dialogue"]}

TRUSTED CHARACTERS:
{trusted_characters}

PREVIOUS VISUAL OBSERVATIONS:
{build_visual_memory_context()}

USER QUESTION:
{extra_input}

Answer as if you are a knowledgeable friend explaining the
movie to someone who wants to genuinely understand it.

Do not give a one-line answer unless the question is truly
simple and needs only one line.

For character questions, explain who the character is, their
role, relevant relationships, and why they matter in the
current situation when that information is available.

For "why" questions, explain the cause and the relevant
previous event or motivation when supported.

For questions about VISUAL hidden details or Easter eggs,
focus on what can actually be seen in the current frame:
objects, symbols, posters, background details, costumes,
logos, repeated visual elements, and visual callbacks.

If the supplied movie memory contains timestamped visual
observations, compare the current scene with those observations
to identify a possible repeated object or visual callback.

Do not invent an Easter egg. If the evidence is insufficient,
say that it is only a possibility or that the current evidence
is not enough.

For story references or previous events, use the supplied movie
memory and clearly distinguish known facts from interpretation.

If the exact answer cannot be established, answer the parts
that CAN be established instead of replying only "I don't know."

Aim for 4-7 useful sentences, with additional detail when
the question requires it.

Do not show reasoning.
"""

        return ask_text_gemma(prompt)


# ==========================================
# BASIC TKINTER UI
# ==========================================
def choose_reply_language(parent):

    global response_language

    language_window = tk.Toplevel(parent)

    language_window.title("🌐 Reply Language")
    language_window.geometry("360x300")
    language_window.resizable(False, False)

    language_window.transient(parent)
    language_window.grab_set()

    title = tk.Label(
        language_window,
        text="🌐 Choose Reply Language",
        font=("Segoe UI", 16, "bold")
    )

    title.pack(pady=(20, 10))

    subtitle = tk.Label(
        language_window,
        text="How should MovieMind reply?",
        font=("Segoe UI", 10)
    )

    subtitle.pack(pady=(0, 15))

    selected_language = tk.StringVar(
        value=response_language
    )

    languages = [
        "English",
        "Tamil",
        "Tanglish"
    ]

    for language in languages:

        radio = tk.Radiobutton(
            language_window,
            text=language,
            variable=selected_language,
            value=language,
            font=("Segoe UI", 11),
            anchor="w"
        )

        radio.pack(
            fill="x",
            padx=70,
            pady=3
        )

    def apply_language():

        global response_language

        response_language = selected_language.get()

        language_window.destroy()

    apply_button = tk.Button(
        language_window,
        text="Apply",
        command=apply_language,
        font=("Segoe UI", 10, "bold"),
        width=12
    )

    apply_button.pack(
        pady=18
    )

    parent.wait_window(language_window)
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

        global current_displayed_answer

        current_displayed_answer = answer

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
    # REPLY LANGUAGE
    # --------------------------------------

    language_button = tk.Button(
        window,
        text=f"🌐 Reply Language: {response_language}",
        width=25
    )

    language_button.pack(
        pady=(8, 5)
    )

    def change_language_and_refresh():

        global current_displayed_answer

        old_language = response_language

        choose_reply_language(window)

        language_button.config(
            text=f"🌐 Reply Language: {response_language}"
        )

        # If the user changed language after receiving an answer,
        # immediately rewrite the existing visible answer.
        if (
            current_displayed_answer
            and response_language != old_language
        ):
            try:
                translated = translate_existing_answer(
                    current_displayed_answer
                )
                display_answer(translated)

            except Exception as error:
                messagebox.showerror(
                    "MovieMind",
                    f"Could not change the current answer language.\n\n{error}"
                )

    language_button.config(
        command=change_language_and_refresh
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


    # --------------------------------------
    # VISUAL MEMORY
    # --------------------------------------

    similar_visuals = save_visual_observation(
        image,
        subtitle,
        characters,
        objects,
        scene
    )

    if similar_visuals:
        print("\n🔎 Possible visually similar earlier observations:")
        for item in similar_visuals[-3:]:
            print(
                f"   #{item['index']} | difference={item['difference']} | "
                f"objects={item['objects']} | characters={item['characters']}"
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
print("Each F8 also stores a tiny visual memory record (no full screenshot archive).")
print("Press ESC to exit.")
print()


keyboard.add_hotkey(
    "f8",
    movie_mind
)


keyboard.wait("esc")


print("\nMovieMind closed.")