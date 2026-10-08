import mss
from PIL import Image
import keyboard
import pytesseract
import requests
import base64
import json
from io import BytesIO
import re
import threading
import time
from datetime import datetime
from collections import deque
from pathlib import Path
from urllib.parse import quote
import tkinter as tk
from tkinter import simpledialog, messagebox


# ==========================================
# MovieMind Visual Memory V2
# ==========================================
# V2 keeps the V1 architecture and visual-memory mechanism intact.
# Targeted changes:
# - cached-answer language switching
# - stricter evidence / hallucination controls
# - conservative OCR garbage filtering
# - clearer scene explanation requirements
# - safer question-answer grounding
#
# V1 remains untouched.
# ==========================================

# ==========================================
# CONFIGURATION
# ==========================================

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "gemma3:4b"

# Lightweight adaptive movie monitoring. No AI inference runs in this loop.
# Capture/comparison is cheap; OCR runs only when useful, with cooldowns.
MONITOR_INTERVAL_SECONDS = 1.0
SUBTITLE_OCR_FALLBACK_SECONDS = 3.0
FULL_FRAME_OCR_CHANGE_COOLDOWN_SECONDS = 8.0
FULL_FRAME_OCR_MAX_INTERVAL_SECONDS = 20.0
FRAME_STORE_INTERVAL_SECONDS = 5.0
SUBTITLE_REGION_CHANGE_THRESHOLD = 0.012
MEANINGFUL_FRAME_CHANGE_THRESHOLD = 0.055
BACKGROUND_OCR_CHANGE_THRESHOLD = 0.045
MAX_MONITOR_FRAMES = 1440  # Bounded RAM; actual retained duration depends on scene changes.
MONITOR_THUMBNAIL_SIZE = (480, 270)

auto_visual_memory = deque(maxlen=MAX_MONITOR_FRAMES)
auto_memory_lock = threading.Lock()
monitor_pause_event = threading.Event()
monitor_stop_event = threading.Event()
monitor_thread = None
# =========================================================
# REPLY LANGUAGE
# =========================================================

response_language = "English"


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
Respond in natural Tanglish (Tamil spoken naturally but written ONLY with English/Latin letters).

Tanglish is NOT English with a few Tamil words. The main explanation should use
Tamil vocabulary and Tamil conversational sentence structure, while movie names,
character names, technical terms, and necessary English words may remain in English.

Examples of the required style:
"Tony, Doctor Strange kitta pesitu irukkaan. Rendu perum enemy-a stop panna
plan pannitu irukkaanga. Indha scene-la Tony situation-a control panna try
pannraan."

Another example:
"Indha dialogue-oda meaning enna-na, avan ippo romba tension-la irukkaan.
Avanukku situation control-la illa-nu purinjiduchu, adhanala warning kudukkaan."

IMPORTANT:
- Write ALL Tamil using English/Latin letters only.
- NEVER use Tamil Unicode characters.
- Do NOT write the answer as normal English and merely insert a few Tamil words.
- Most explanatory sentences should be natural Tamil/Tanglish conversation.
- Prefer forms such as "irukkaan", "irukkaanga", "pannraan", "pannitu irukku",
  "kitta", "avanukku", "adhunaala", "enna-na", "apdi", "inga", "anga",
  "nu", "thaan", "aana", "because" where natural.
- Keep character names, movie names, places, and proper nouns in English.
- Avoid overly formal Tamil and avoid textbook-style transliteration.
- Explain the movie like you are casually explaining it to a Tamil-speaking friend.
- Do NOT translate proper nouns unnecessarily.
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

# F8 observations store compact metadata + a tiny visual signature.
# Automatic monitoring separately keeps low-resolution JPEG thumbnails in a bounded RAM buffer.
MAX_VISUAL_OBSERVATIONS = 300
MAX_VISUAL_CONTEXT = 8


# ==========================================
# CURRENT FRAME CACHE
# ==========================================

current_frame = {
    "subtitle": "",
    "scene_analysis": "",
    "dialogue_meaning": "",
    "characters": [],
    "objects": [],
    "scene": "",
    "scene_language": "",
    "dialogue_language": ""
}

# The answer currently visible in the UI.
current_displayed_answer = ""
current_displayed_mode = ""
current_displayed_input = ""


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


def build_auto_visual_memory_context(limit=30):
    """Return compact recent monitoring clues without including image bytes."""
    with auto_memory_lock:
        records = list(auto_visual_memory)[-limit:]

    if not records:
        return "No automatic monitoring observations yet."

    lines = []
    for item in records:
        timestamp = item.get("timestamp", "unknown time")
        subtitle = item.get("subtitle", "") or "(no subtitle detected)"
        background_text = item.get("full_frame_text", "")
        line = f"[{timestamp}] Subtitle: {subtitle}"
        if background_text:
            line += f" | Background OCR: {background_text[:220]}"
        lines.append(line)
    return "\n".join(lines)


def monitor_movie_loop():
    """Sample once per second; trigger OCR selectively and never run AI here."""
    last_full_ocr = 0.0
    last_subtitle_ocr = 0.0
    last_saved_frame = 0.0
    previous_signature = ""
    previous_subtitle_signature = ""
    previous_subtitle = ""

    while not monitor_stop_event.is_set():
        if monitor_pause_event.is_set():
            monitor_stop_event.wait(0.5)
            continue

        started = time.monotonic()
        try:
            image = capture_screen(save_to_file=False)
            now = time.monotonic()
            full_frame_text = ""

            # Cheap fingerprints are calculated on every sample. OCR of the subtitle
            # crop only runs when that crop changes, with a short fallback interval.
            width, height = image.size
            subtitle_region = image.crop((
                0, int(height * 0.65), width, int(height * 0.95)
            ))
            subtitle_signature = make_visual_signature(subtitle_region)
            subtitle_region_changed = (
                not previous_subtitle_signature
                or visual_signature_difference(
                    subtitle_signature, previous_subtitle_signature
                ) >= SUBTITLE_REGION_CHANGE_THRESHOLD
            )
            subtitle = previous_subtitle
            if (subtitle_region_changed
                    or now - last_subtitle_ocr >= SUBTITLE_OCR_FALLBACK_SECONDS):
                subtitle = extract_text(image)
                last_subtitle_ocr = now

            thumbnail = image.copy()
            thumbnail.thumbnail(MONITOR_THUMBNAIL_SIZE, Image.Resampling.LANCZOS)
            signature = make_visual_signature(thumbnail)
            frame_difference = (
                1.0 if not previous_signature else
                visual_signature_difference(signature, previous_signature)
            )
            meaningful_visual_change = (
                not previous_signature
                or frame_difference >= BACKGROUND_OCR_CHANGE_THRESHOLD
            )

            # Full-frame OCR is triggered by meaningful visual changes, but has an
            # 8-second cooldown and a 20-second fallback so small/static clues can
            # still be revisited without scanning the whole screen every second.
            full_ocr_due = (
                not last_full_ocr
                or (
                    meaningful_visual_change
                    and now - last_full_ocr >= FULL_FRAME_OCR_CHANGE_COOLDOWN_SECONDS
                )
                or now - last_full_ocr >= FULL_FRAME_OCR_MAX_INTERVAL_SECONDS
            )
            if full_ocr_due:
                try:
                    small_for_ocr = image.copy()
                    small_for_ocr.thumbnail((960, 540), Image.Resampling.LANCZOS)
                    full_frame_text = clean_ocr_text(
                        pytesseract.image_to_string(
                            small_for_ocr, lang="eng", config="--psm 11"
                        )
                    )
                except Exception as ocr_error:
                    full_frame_text = f"[OCR unavailable: {type(ocr_error).__name__}]"
                last_full_ocr = now

            subtitle_changed = subtitle != previous_subtitle
            frame_should_be_stored = (
                not previous_signature
                or frame_difference >= MEANINGFUL_FRAME_CHANGE_THRESHOLD
                or subtitle_changed
                or bool(full_frame_text)
                or now - last_saved_frame >= FRAME_STORE_INTERVAL_SECONDS
            )

            if frame_should_be_stored:
                buffer = BytesIO()
                thumbnail.save(buffer, format="JPEG", quality=45, optimize=True)
                record = {
                    "timestamp": datetime.now().strftime("%H:%M:%S"),
                    "signature": signature,
                    "subtitle": subtitle,
                    "full_frame_text": full_frame_text,
                    "image_jpeg": buffer.getvalue(),
                }
                with auto_memory_lock:
                    auto_visual_memory.append(record)
                last_saved_frame = now

            # Compare against the immediately previous sample, not the last saved
            # frame, so small changes accumulate without repeatedly storing frames.
            previous_signature = signature
            previous_subtitle_signature = subtitle_signature
            previous_subtitle = subtitle

        except Exception as error:
            print(f"[MovieMind monitor] {type(error).__name__}: {error}")

        elapsed = time.monotonic() - started
        monitor_stop_event.wait(max(0.05, MONITOR_INTERVAL_SECONDS - elapsed))


def start_movie_monitor():
    """Start one background sampler; safe to call more than once."""
    global monitor_thread
    if monitor_thread and monitor_thread.is_alive():
        return
    monitor_stop_event.clear()
    monitor_thread = threading.Thread(
        target=monitor_movie_loop, name="MovieMindMonitor", daemon=True
    )
    monitor_thread.start()


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

def capture_screen(save_to_file=True):

    with mss.MSS() as sct:

        monitor = sct.monitors[1]

        screenshot = sct.grab(monitor)

        image = Image.frombytes(
            "RGB",
            screenshot.size,
            screenshot.rgb
        )

        if save_to_file:
            image.save("screenshot.png")

    return image


# ==========================================
# OCR
# ==========================================

def clean_ocr_text(text):
    """
    Conservatively clean OCR output.

    We reject only strong OCR-noise signals. Short legitimate
    subtitles such as "Go!" are still allowed.
    """

    if not text:
        return ""

    lines = []

    for raw_line in text.splitlines():

        line = re.sub(r"\\s+", " ", raw_line).strip()

        if not line:
            continue

        # Timestamp-only OCR such as 21:05 or 01:23:45.
        if re.fullmatch(
            r"[\[\(]?\d{1,2}:\d{2}(?::\d{2})?[\]\)]?",
            line
        ):
            continue

        alnum_count = sum(ch.isalnum() for ch in line)

        # Only punctuation/symbols.
        if alnum_count == 0:
            continue

        # Tiny fragments containing almost no readable text.
        if len(line) <= 4 and alnum_count <= 1:
            continue

        lines.append(line)

    cleaned = " ".join(lines).strip()

    if not cleaned:
        return ""

    letter_count = sum(ch.isalpha() for ch in cleaned)

    if letter_count < 2:
        return ""

    return cleaned


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

    return clean_ocr_text(text)


# ==========================================
# IMAGE -> BASE64
# ==========================================

def image_to_base64(image_path):
    """Prepare a smaller JPEG copy for faster local vision inference."""

    image = Image.open(image_path).convert("RGB")
    image.thumbnail((960, 540), Image.Resampling.LANCZOS)

    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=65, optimize=True)

    return base64.b64encode(
        buffer.getvalue()
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

    subtitle_status = (
        "RELIABLE SUBTITLE TEXT"
        if subtitle and subtitle != "No subtitle detected."
        else "NO RELIABLE SUBTITLE TEXT WAS DETECTED"
    )

    prompt = f"""
You are MovieMind, a local offline movie assistant.

{language_instruction}

Analyze ONLY the CURRENT movie screenshot and the supplied subtitle.
The screenshot is the strongest evidence.

SUBTITLE STATUS:
{subtitle_status}

CURRENT SUBTITLE:
{subtitle if subtitle else "No reliable subtitle detected."}

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

AUTOMATIC MONITORING CLUES (sampled while watching):
{build_auto_visual_memory_context(limit=12)}

STRICT EVIDENCE RULES:

1. Evidence priority is:
   A. CURRENT SCREENSHOT
   B. CURRENT SUBTITLE, only if it is readable
   C. CURRENT visual information
   D. PREVIOUS MOVIE MEMORY

2. Previous memory is supporting context only. It must NEVER
   override what is clearly visible in the current screenshot.

3. Do NOT invent character names, identities, relationships,
   motivations, locations, plot facts, or events.

4. A subtitle mentioning a character or object does NOT prove
   additional facts about that character or object.

5. Do NOT use general movie knowledge to fill an unsupported gap.
   If a detail cannot be established from the current evidence,
   say that it is uncertain or not clear from this frame.

6. If the subtitle is marked as unreliable, do NOT treat it as
   meaningful dialogue. Explain the scene from reliable visual
   evidence instead, and say that readable subtitle text was not
   available when relevant.

7. Character identification must be based on visible evidence or
   strongly supported memory. If uncertain, use a neutral
   description such as "the character" rather than guessing.

8. Do not turn a possible interpretation into a fact. Use wording
   such as "appears to", "may", or "it is unclear" when appropriate.

9. Explain the scene usefully:
   - what is happening
   - who is visibly involved
   - what the readable dialogue means
   - why the interaction appears to be happening
   - relevant context ONLY when supported

10. The ANSWER should normally be 4-6 natural sentences.
    Do not add invented details merely to reach a sentence count.

11. The DIALOGUE section must explain the current subtitle only
    when the subtitle is reliable. Otherwise say that there is no
    reliable subtitle to interpret.

12. Previous visual observations can indicate a possible repeated
    visual element. They do NOT prove an Easter egg or filmmaker
    intent. Say "possible visual callback" when appropriate.

13. Do not show hidden reasoning or chain-of-thought.

Use EXACTLY this format:

ANSWER:
<4-6 sentence useful scene explanation>

DIALOGUE:
<clear explanation of the current subtitle, or state that no
reliable subtitle was detected>

MEMORY:
Characters: <comma-separated names; only reasonably supported names>
Objects: <comma-separated important visible objects>
Scene: <one useful sentence describing only what is supported>
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
            "temperature": 0.1,
            "num_predict": 140
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
    current_frame["scene_language"] = response_language
    current_frame["dialogue_language"] = response_language


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

EVIDENCE RULES:
- Treat the supplied current-frame information as the primary
  evidence.
- Treat previous memory as supporting context, not proof.
- For facts specifically about the CURRENT SCENE, use the supplied
  scene information as the primary evidence.
- For DIRECT GENERAL-KNOWLEDGE questions (for example, "Who is
  Iron Man?"), you MAY answer from your existing movie/world knowledge.
  Do not pretend that only the current screenshot is known.
- When a question asks about BOTH a character/topic generally and
  its role in the current scene, answer the general part first, then
  connect it to the scene using the supplied evidence.
- Never invent scene-specific names, relationships, motivations,
  events, or plot details.
- Do not turn a possibility into a fact.
- If a requested detail genuinely cannot be established, say what
  is known and what remains uncertain.

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
- This is TRANSLATION/RE-EXPRESSION only.
- Preserve exactly the same facts and uncertainty.
- Do not add new information, names, relationships, or explanations.
- Do not remove important details.
- Do not correct or reinterpret the original answer.
- Keep character names, movie names, and proper nouns unchanged
  when appropriate.
- If Tanglish is selected, rewrite the answer as natural conversational Tanglish.
- In Tanglish, the majority of the explanatory wording must be Tamil vocabulary
  written in English letters, not ordinary English with a few Tamil words.
- NEVER use Tamil Unicode characters.
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

        if (
            scene
            and current_frame.get("scene_language")
            and current_frame["scene_language"] != response_language
        ):
            return translate_existing_answer(scene)

        return scene


    # --------------------------------------
    # DIALOGUE
    # --------------------------------------

    if choice == "dialogue":

        prompt = f"""
Explain the CURRENT movie dialogue instead of repeating it.

CURRENT SUBTITLE:
{subtitle}

CURRENT SCENE EXPLANATION:
{scene}

CURRENT DIALOGUE CONTEXT:
{dialogue}

The answer must explain:
1. What the speaker actually means in simple language.
2. What the speaker is trying to communicate or imply.
3. Why this line matters in the current scene, when supported.

IMPORTANT:
- Do NOT copy the subtitle as the answer.
- Do NOT merely rephrase the same sentence.
- Explain the meaning and context.
- Use the current scene information as evidence.
- If the context is uncertain, say so instead of inventing details.

Return 2-4 useful sentences.
Do not show reasoning.
"""

        return ask_text_gemma(prompt)


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

Return EXACTLY this format:

Simple meaning -> <simple meaning of the word>

According to the scene -> <what the word means in this specific movie scene>

Keep both parts concise and useful. Do not add extra headings or sections.
Do not show reasoning.
"""

        return ask_text_gemma(prompt)


    # --------------------------------------
    # QUESTION
    # --------------------------------------

    if choice == "question":

        prompt = f"""
Answer the user's question clearly and directly.

USER QUESTION:
{extra_input}

IMPORTANT: If the question is a general factual question about a
character, object, movie, event, or concept, answer it using your
known movie/world knowledge FIRST. Do not limit the answer to what
is visible in the current screenshot.

For example, if the user asks "Who is Iron Man?", explain that Iron
Man is Tony Stark, including his background as a billionaire
industrialist/inventor, his role as Iron Man, and other relevant
well-known context. Then, if useful, explain how that character
relates to the current scene.

CURRENT SCENE CONTEXT (use this for scene-specific details):
Subtitle: {subtitle}
Scene: {scene}
Dialogue meaning: {dialogue}
Characters: {characters}
Objects: {objects}
Previous scene memory: {movie_memory["scene_summary"]}
Recent dialogue: {movie_memory["recent_dialogue"]}

RULES:
- Give the direct answer first.
- Then provide useful background/context.
- Then connect it to the current scene when relevant.
- Do not say "I understand you are asking..." or repeat the question.
- Do not invent scene-specific facts.
- If something is uncertain, say so briefly.
- Answer in 4-7 useful sentences unless the question needs more.
- Do not show reasoning.
"""

        return ask_text_gemma(prompt)


# ==========================================
# OPTIONAL ONLINE EASTER-EGG RESEARCH
# ==========================================

# Free multi-source search: no API key and no paid service.
# Public SearXNG instances are community-run and may be unavailable, so this
# layer also queries Wikimedia and Reddit directly as independent fallbacks.
_SEARXNG_INSTANCES_CACHE = None
_SEARXNG_STATIC_FALLBACKS = [
    "https://searx.be",
    "https://search.sapti.me",
    "https://searx.tiekoetter.com",
]


def _discover_searxng_instances():
    """Discover currently healthy public SearXNG instances, with static fallbacks."""
    global _SEARXNG_INSTANCES_CACHE
    if _SEARXNG_INSTANCES_CACHE is not None:
        return _SEARXNG_INSTANCES_CACHE

    instances = []
    try:
        response = requests.get(
            "https://searx.space/data/instances.json",
            headers={"User-Agent": "MovieMind/1.0 (free movie-reference research)"},
            timeout=5,
        )
        response.raise_for_status()
        payload = response.json()
        raw_instances = payload.get("instances", {})
        if isinstance(raw_instances, dict):
            for base_url, info in raw_instances.items():
                if not isinstance(info, dict):
                    continue
                status = info.get("http", {}).get("status_code")
                network_type = info.get("network_type", "normal")
                if status == 200 and network_type == "normal":
                    base_url = base_url.rstrip("/")
                    if base_url.startswith("https://") and base_url not in instances:
                        instances.append(base_url)
    except Exception:
        # The instance directory is only a convenience, not a hard dependency.
        pass

    for base_url in _SEARXNG_STATIC_FALLBACKS:
        if base_url not in instances:
            instances.append(base_url)

    _SEARXNG_INSTANCES_CACHE = instances[:8]
    return _SEARXNG_INSTANCES_CACHE


def _clean_search_text(value):
    """Remove HTML markup commonly included in search snippets."""
    value = re.sub(r"<[^>]+>", " ", str(value or ""))
    return " ".join(value.split()).strip()


def _search_searxng(query, limit=5):
    """Search public SearXNG instances using their structured JSON endpoint."""
    errors = []
    for base_url in _discover_searxng_instances():
        try:
            response = requests.get(
                base_url + "/search",
                params={"q": query, "format": "json", "language": "en-US", "safesearch": 0},
                headers={"User-Agent": "MovieMind/1.0 (free movie-reference research)", "Accept": "application/json"},
                timeout=5,
            )
            response.raise_for_status()
            payload = response.json()
            results = []
            for item in payload.get("results", []):
                title = _clean_search_text(item.get("title"))
                url = str(item.get("url", "")).strip()
                snippet = _clean_search_text(item.get("content") or item.get("snippet"))
                if title and url.startswith(("http://", "https://")):
                    results.append({"title": title, "url": url, "snippet": snippet, "provider": "SearXNG"})
                if len(results) >= limit:
                    break
            if results:
                return results, errors
            errors.append(f"{base_url}: no results in JSON response")
        except Exception as error:
            errors.append(f"{base_url}: {type(error).__name__}")
        # Keep latency bounded: at most two public instances per query.
        if len(errors) >= 2:
            break
    return [], errors


def _search_wikipedia(query, limit=4):
    """Use Wikipedia's official MediaWiki search API as a stable fallback."""
    response = requests.get(
        "https://en.wikipedia.org/w/api.php",
        params={
            "action": "query", "list": "search", "srsearch": query,
            "srnamespace": 0, "srlimit": limit, "srprop": "snippet", "format": "json",
        },
        headers={"User-Agent": "MovieMind/1.0 (movie-reference research)"},
        timeout=7,
    )
    response.raise_for_status()
    payload = response.json()
    results = []
    for item in payload.get("query", {}).get("search", []):
        title = _clean_search_text(item.get("title"))
        snippet = _clean_search_text(item.get("snippet"))
        if title:
            results.append({
                "title": title,
                "url": "https://en.wikipedia.org/wiki/" + quote(title.replace(" ", "_")),
                "snippet": snippet,
                "provider": "Wikipedia",
            })
    return results


def _search_reddit(query, limit=5):
    """Search public Reddit results for fan discussions and niche observations."""
    response = requests.get(
        "https://www.reddit.com/search.json",
        params={"q": query, "sort": "relevance", "t": "all", "limit": limit, "type": "link"},
        headers={"User-Agent": "desktop:MovieMind:1.0 (personal offline movie assistant)"},
        timeout=7,
    )
    response.raise_for_status()
    payload = response.json()
    results = []
    for child in payload.get("data", {}).get("children", []):
        item = child.get("data", {})
        title = _clean_search_text(item.get("title"))
        url = str(item.get("url_overridden_by_dest") or item.get("permalink") or "").strip()
        if url.startswith("/r/"):
            url = "https://www.reddit.com" + url
        snippet = _clean_search_text(item.get("selftext", ""))[:700]
        if title and url.startswith("https://"):
            results.append({"title": title, "url": url, "snippet": snippet, "provider": "Reddit discussion"})
    return results


def search_web_results(query, limit=8):
    """Aggregate free, keyless results from independent sources; never depend on one HTML layout."""
    results = []
    errors = []
    seen_urls = set()

    def add(items):
        for item in items:
            url = item.get("url", "").split("#", 1)[0].rstrip("/")
            if url and url not in seen_urls:
                seen_urls.add(url)
                item["url"] = url
                results.append(item)

    try:
        items, source_errors = _search_searxng(query, limit=limit)
        add(items)
        errors.extend(source_errors)
    except Exception as error:
        errors.append(f"SearXNG: {type(error).__name__}")

    # These independent sources are still queried when SearXNG returns results,
    # because community discussions often contain niche details missing from wikis.
    try:
        add(_search_reddit(query, limit=min(limit, 5)))
    except Exception as error:
        errors.append(f"Reddit: {type(error).__name__}")
    try:
        add(_search_wikipedia(query, limit=min(limit, 4)))
    except Exception as error:
        errors.append(f"Wikipedia: {type(error).__name__}")

    return results[:limit], errors


def _parse_movie_region(model_text):
    """Read an optional normalized movie-content rectangle from the vision response."""
    marker = "MOVIE_REGION_JSON:"
    if marker not in (model_text or ""):
        return None
    raw = model_text.split(marker, 1)[1].strip().splitlines()[0].strip()
    try:
        payload = json.loads(raw)
        bbox = payload.get("bbox_1000") if isinstance(payload, dict) else None
        if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
            values = [max(0, min(1000, int(float(v)))) for v in bbox]
            if values[2] > values[0] and values[3] > values[1]:
                return values
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return None


def _crop_normalized_region(image, bbox):
    """Crop a 0..1000 normalized rectangle from an image."""
    if not bbox:
        return image
    width, height = image.size
    left, top, right, bottom = bbox
    box = (
        max(0, int(left * width / 1000)),
        max(0, int(top * height / 1000)),
        min(width, int(right * width / 1000)),
        min(height, int(bottom * height / 1000)),
    )
    if box[2] <= box[0] or box[3] <= box[1]:
        return image
    return image.crop(box)


def _ocr_movie_region(image):
    """OCR only the movie-content crop and filter obvious OS/application labels."""
    try:
        small = image.copy()
        small.thumbnail((1200, 800), Image.Resampling.LANCZOS)
        raw = pytesseract.image_to_string(small, lang="eng", config="--psm 11")
        ignored_ui = ("moviemind", "windows 10", "start menu", "taskbar", "search the web")
        return "\n".join(
            line.strip() for line in raw.splitlines()
            if line.strip() and not any(label in line.lower() for label in ignored_ui)
        ).strip()
    except Exception as error:
        return f"Movie-region OCR unavailable: {type(error).__name__}"


def _parse_plate_json(model_text):
    """Extract the model's explicit plate-candidate JSON without guessing values."""
    marker = "PLATE_CANDIDATES_JSON:"
    if marker not in (model_text or ""):
        return []
    raw = model_text.split(marker, 1)[1].strip()
    raw = raw.split("\n\n", 1)[0].strip()
    if "```" in raw:
        raw = raw.replace("```json", "").replace("```", "").strip()
    start, end = raw.find("["), raw.rfind("]")
    if start < 0 or end < start:
        return []
    try:
        value = json.loads(raw[start:end + 1])
    except (json.JSONDecodeError, TypeError):
        return []
    return value if isinstance(value, list) else []


def _ocr_plate_crop(image, bbox):
    """Upscale and OCR a proposed plate crop; returns raw OCR, never an inferred plate."""
    try:
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            return ""
        width, height = image.size
        coords = [max(0, min(1000, int(float(v)))) for v in bbox]
        left, top, right, bottom = coords
        x1, y1 = int(left * width / 1000), int(top * height / 1000)
        x2, y2 = int(right * width / 1000), int(bottom * height / 1000)
        if x2 <= x1 or y2 <= y1:
            return ""
        # Add a small margin because model-proposed boxes may be tight.
        margin_x = max(2, int((x2 - x1) * 0.15))
        margin_y = max(2, int((y2 - y1) * 0.20))
        crop = image.crop((max(0, x1-margin_x), max(0, y1-margin_y),
                           min(width, x2+margin_x), min(height, y2+margin_y)))
        crop = crop.convert("L")
        crop = crop.resize((max(1, crop.width * 4), max(1, crop.height * 4)), Image.Resampling.LANCZOS)
        outputs = []
        for psm in (7, 8, 13):
            try:
                text = pytesseract.image_to_string(
                    crop, lang="eng",
                    config=f"--psm {psm} -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-"
                )
                cleaned = re.sub(r"[^A-Za-z0-9-]", "", text).upper()
                if cleaned and cleaned not in outputs:
                    outputs.append(cleaned)
            except Exception:
                continue
        return " / ".join(outputs)
    except Exception:
        return ""


def _collect_plate_clues(image, model_text, timestamp):
    """Combine visible-model candidates with OCR; preserve uncertainty and coordinates."""
    clues = []
    for item in _parse_plate_json(model_text):
        if not isinstance(item, dict):
            continue
        vehicle = str(item.get("vehicle", "vehicle")).strip()[:60] or "vehicle"
        model_reading = str(item.get("plate_text", "UNKNOWN")).strip().upper()[:24]
        confidence = str(item.get("confidence", "low")).strip().lower()[:16]
        bbox = item.get("bbox_1000")
        ocr_reading = _ocr_plate_crop(image, bbox) if bbox else ""
        # Keep only plausible alphanumeric candidates. Do not invent characters.
        model_compact = re.sub(r"[^A-Z0-9]", "", model_reading)
        ocr_compact = re.sub(r"[^A-Z0-9]", "", ocr_reading)
        candidate = (ocr_reading.split(" / ", 1)[0] if ocr_reading else (model_reading if confidence in ("high", "medium") else ""))
        candidate_compact = re.sub(r"[^A-Z0-9]", "", candidate)
        if candidate_compact and 3 <= len(candidate_compact) <= 12:
            corroboration = "OCR-readable" if ocr_compact else f"vision-only, {confidence} confidence"
            clues.append(
                f"[{timestamp}] {vehicle}: possible plate '{candidate}' ({corroboration}); "
                f"model read='{model_reading}', OCR read='{ocr_reading or 'unreadable'}'."
            )
        else:
            clues.append(
                f"[{timestamp}] {vehicle}: plate area may be visible, but characters were not "
                f"read reliably (model='{model_reading}', OCR='{ocr_reading or 'unreadable'}')."
            )
    return clues


def inspect_current_frame_for_hidden_details():
    """Run only after the online Easter Eggs button is clicked."""
    screenshot_path = "screenshot.png"
    if not Path(screenshot_path).exists():
        return (
            "", "No saved screenshot is available for this frame.",
            "No license plate could be inspected because no current screenshot was available."
        )

    image = Image.open(screenshot_path).convert("RGB")
    plate_clues = []

    # OCR the full frame here (not during ordinary F8) to look for
    # posters, signs, labels, screens, and other background text.
    try:
        full_frame_text = pytesseract.image_to_string(
            image,
            lang="eng",
            config="--psm 11"
        ).strip()
        # Exclude obvious desktop/app labels from movie evidence. The vision prompt
        # also explicitly ignores OS, player controls, and MovieMind's own interface.
        ignored_ui = ("moviemind", "windows 10", "start menu", "taskbar", "search the web")
        full_frame_text = "\n".join(
            line for line in full_frame_text.splitlines()
            if not any(label in line.lower() for label in ignored_ui)
        )
    except Exception as error:
        full_frame_text = f"Full-frame OCR was unavailable: {error}"

    # Ask the already-installed local vision model to inspect the current
    # screenshot for visible candidate details. This does not use cloud AI.
    try:
        image_base64 = image_to_base64(screenshot_path)
        prompt = f"""Inspect this screenshot for potential VISUAL Easter-egg clues inside the movie/video picture only.
Ignore MovieMind, Windows, desktop icons, taskbar, browser/application chrome, player controls, and all computer UI.

OCR CLUES (may contain unrelated UI text or OCR mistakes): {full_frame_text[:1800]}

IMPORTANT EVIDENCE RULES:
- Report observable physical details, not guesses about who a person is.
- Do NOT name actors or movie characters. Do not infer that a known cast member is present because they belong to this film.
- Do not infer a scene from the movie title, subtitles, or general plot knowledge.
- For each possible clue, describe what is visibly there and state if it is too small/unclear to read.
- Prioritize background posters/signs, logos, labels, books, newspapers, symbols, props, and readable text.
Also inspect visible cars/motorcycles for plates, but never guess unreadable characters.

First output MOVIE_REGION_JSON: followed by a JSON object with bbox_1000
([left,top,right,bottom], normalized 0-1000) around the actual movie/video picture only.
Exclude desktop, MovieMind, OS/taskbar, player controls, and application chrome. If unsure,
use [0,0,1000,1000].
Then output PLATE_CANDIDATES_JSON: followed by a JSON array. Each object must
have vehicle (short visual description, no character/actor guesses), plate_text (exactly readable characters or UNKNOWN),
confidence (high/medium/low), and bbox_1000 ([left,top,right,bottom] normalized 0-1000
coordinates of the plate area in the FULL screenshot). Use [] if no vehicle plate is visible.
Never guess unreadable plate characters or claim a plate is an Easter egg.
"""
        data = {
            "model": MODEL,
            "messages": [{
                "role": "user",
                "content": prompt,
                "images": [image_base64]
            }],
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 150}
        }
        response = requests.post(OLLAMA_URL, json=data, timeout=90)
        response.raise_for_status()
        visual_candidates = response.json()["message"]["content"].strip()
        movie_region = _parse_movie_region(visual_candidates)
        if movie_region:
            movie_crop = _crop_normalized_region(image, movie_region)
            region_ocr = _ocr_movie_region(movie_crop)
            if region_ocr:
                full_frame_text = region_ocr
        plate_clues.extend(_collect_plate_clues(image, visual_candidates, "current frame"))
    except Exception as error:
        visual_candidates = f"Local visual inspection could not complete: {error}"

    # Easter Eggs mode may inspect a small number of earlier sampled frames.
    # The background monitor itself never runs Gemma; these calls happen only on click.
    historical_candidates = []
    historical_movie_ocr = []
    with auto_memory_lock:
        saved_frames = list(auto_visual_memory)

    # Prefer frames with background OCR, then sample different points in the buffer.
    selected = []
    for item in reversed(saved_frames):
        if item.get("image_jpeg") and item.get("full_frame_text"):
            selected.append(item)
            if len(selected) >= 2:
                break
    if len(selected) < 2 and saved_frames:
        for position in (len(saved_frames) // 2, max(0, len(saved_frames) - 1)):
            item = saved_frames[position]
            if item.get("image_jpeg") and item not in selected:
                selected.append(item)
            if len(selected) >= 2:
                break

    for index, item in enumerate(selected, start=1):
        try:
            historical_image_b64 = base64.b64encode(item["image_jpeg"]).decode("utf-8")
            historical_image = Image.open(BytesIO(item["image_jpeg"])).convert("RGB")
            historical_prompt = f"""Inspect this sampled frame from {item.get('timestamp', 'an earlier time')} for VISUAL Easter-egg clues.
Focus only on the movie picture. Ignore MovieMind, Windows, desktop, taskbar, and player UI.
OCR clue (may be wrong or from UI): {item.get('full_frame_text', '')[:800]}
Do not name actors or characters, infer cast presence, or infer the scene from the subtitle/movie lore.
Describe only observable background posters/signs, logos, labels, props, symbols, and text; mark unclear details as unreadable.
Inspect visible vehicles for plates, but never guess characters or infer intent.
Output MOVIE_REGION_JSON: {{"bbox_1000":[left,top,right,bottom]}} for the movie picture, or [0,0,1000,1000] if unsure.
Then output PLATE_CANDIDATES_JSON: an array of objects with vehicle (visual description), plate_text (exact readable text or UNKNOWN), confidence (high/medium/low), and bbox_1000 coordinates in this image. Use [] if no plate is visible."""
            payload = {
                "model": MODEL,
                "messages": [{
                    "role": "user",
                    "content": historical_prompt,
                    "images": [historical_image_b64],
                }],
                "stream": False,
                "options": {"temperature": 0.1, "num_predict": 100},
            }
            response = requests.post(OLLAMA_URL, json=payload, timeout=60)
            response.raise_for_status()
            text = response.json()["message"]["content"].strip()
            historical_region = _parse_movie_region(text)
            historical_crop = _crop_normalized_region(historical_image, historical_region)
            historical_ocr = _ocr_movie_region(historical_crop)
            if historical_ocr:
                historical_movie_ocr.append(
                    f"[{item.get('timestamp', '?')}] Movie-region OCR: {historical_ocr[:500]}"
                )
            plate_clues.extend(_collect_plate_clues(
                historical_image, text, f"sampled frame {item.get('timestamp', 'unknown time')}"
            ))
            historical_candidates.append(
                f"Sampled frame at {item.get('timestamp', 'unknown time')}: {text}"
            )
        except Exception as error:
            historical_candidates.append(
                f"Sampled frame {item.get('timestamp', 'unknown time')}: "
                f"inspection unavailable ({type(error).__name__})."
            )

    if historical_candidates:
        visual_candidates += "\n\nAUTOMATICALLY MONITORED FRAME CANDIDATES:\n" + "\n".join(historical_candidates)

    # Do not feed raw full-screen monitor OCR into the Easter Egg conclusion:
    # it can contain Windows/taskbar/MovieMind UI and cause false movie details.
    # Use OCR re-run on the movie-region crops from the selected historical frames.
    if historical_movie_ocr:
        full_frame_text += "\n\nCROPPED HISTORICAL MOVIE-REGION OCR:\n" + "\n".join(historical_movie_ocr[-2:])

    plate_summary = "\n".join(plate_clues) if plate_clues else (
        "No license plate could be read reliably from the inspected frames. "
        "This does not prove that no plate Easter egg exists."
    )
    return full_frame_text, visual_candidates, plate_summary


def perform_easter_egg_research(movie_title=""):
    """Research visual Easter-egg clues only, on explicit request; do not infer cast presence."""
    subtitle = current_frame.get("subtitle", "")
    visual_history = build_visual_memory_context()
    full_frame_text, visual_candidates, plate_summary = inspect_current_frame_for_hidden_details()

    title = (movie_title or "").strip()
    if not title:
        return (
            "MOVIE TITLE REQUIRED\n\n"
            "Enter the movie name so MovieMind can search for documented visual references. "
            "The title is search context only; it is not evidence that a particular actor or character appears in this frame."
        )

    # Search only grounded visual clues. Do not seed search with scene-analysis character
    # names, the cast list, or dialogue: those can make unrelated film appearances look
    # like evidence for the current frame.
    queries = [
        f'"{title}" visual easter eggs background props posters logos signs hidden details',
    ]
    ocr_terms = []
    for line in full_frame_text.splitlines():
        cleaned = " ".join(line.split()).strip(" |:;,.\t")
        if len(cleaned) >= 4 and not cleaned.startswith("["):
            ocr_terms.append(cleaned[:90])
    # OCR is only a lead. Keep the query short and avoid searching UI noise wholesale.
    if ocr_terms:
        ocr_query = " ".join(ocr_terms[:4])[:220]
        queries.append(f'"{title}" "{ocr_query}" poster logo sign prop reference')

    plate_strings = []
    for match in re.finditer(r"possible plate '([^']+)'", plate_summary):
        value = match.group(1).strip().upper()
        compact = re.sub(r"[^A-Z0-9]", "", value)
        if 3 <= len(compact) <= 12 and compact not in plate_strings:
            plate_strings.append(compact)
    for plate in plate_strings[:3]:
        queries.append(f'"{plate}" "{title}" license plate visual easter egg')
        queries.append(f'"{plate}" "{title}" documented production reference')

    queries = list(dict.fromkeys(q.strip() for q in queries if q.strip()))
    all_results = []
    search_errors = []
    for query in queries:
        try:
            results, errors = search_web_results(query, limit=8)
            search_errors.extend(errors)
            for result in results:
                url = result.get("url", "")
                if url and url not in {item.get("url") for item in all_results}:
                    result["query"] = query
                    all_results.append(result)
        except Exception as error:
            search_errors.append(f'{query}: {type(error).__name__}: {error}')

    # Drop obviously non-visual pages (cast lists, character databases, plot summaries,
    # and post-credit/cameo articles). Such pages are especially likely to cause the
    # model to attribute a character from another scene to the current frame.
    nonvisual_title_terms = (
        "full cast", "cast list", "cast of", " cast ", "characters list", "character list",
        "plot summary", "post-credit scene", "post credits scene", "post-credit",
        "post credits", "cameo appearances", "cameo appearance",
    )
    filtered_results = []
    for item in all_results:
        title_text = str(item.get("title", "")).lower()
        if any(term in title_text for term in nonvisual_title_terms):
            continue
        filtered_results.append(item)
    all_results = filtered_results[:20]
    if not all_results:
        unique_errors = list(dict.fromkeys(search_errors))
        reason = "\n".join(unique_errors[:5]) if unique_errors else "The reachable search sources returned no usable matches."
        return (
            "ONLINE RESEARCH COULD NOT VERIFY A VISUAL EASTER EGG\n\n"
            "This does not prove that the frame contains no hidden detail. Search sources returned no usable evidence.\n\n"
            f"Local visual inspection (unverified candidates):\n{visual_candidates}\n\n"
            f"Movie-region/background OCR clues (may be wrong):\n{full_frame_text[:1000] or 'No readable text detected.'}\n\n"
            f"Plate inspection:\n{plate_summary}\n\nSearch diagnostics:\n{reason}"
        )

    source_text = "\n\n".join(
        f"[{i}] {item.get('title', 'Untitled result')}\nProvider: {item.get('provider', 'Web search')}\nURL: {item['url']}\nSnippet: {item.get('snippet', '')}"
        for i, item in enumerate(all_results, start=1)
    )
    language_instruction = get_language_instruction()
    synthesis_prompt = f"""You are MovieMind's evidence-first researcher for VISUAL Easter eggs only.
{language_instruction}

GOAL: investigate concrete visual details such as background posters, signs, logos, symbols,
props, books/newspapers, readable labels, and vehicle license plates. Do not turn this into
a general cast, plot, or dialogue-Easter-egg summary.

MOVIE TITLE (search context only, NOT proof of what is in this frame): {title}
CURRENT SUBTITLE (context only; NOT evidence of who is visible): {subtitle}
BACKGROUND/OCR TEXT (may be from UI or contain recognition errors): {full_frame_text[:2200]}
LOCAL VISUAL MODEL OUTPUT (unverified candidates; it can hallucinate): {visual_candidates[:4500]}
PLATE CANDIDATES (may be uncertain): {plate_summary}
LIGHTWEIGHT MONITORING NOTES (can contain OCR noise; not identity evidence): {visual_history[:1800]}

WEB SEARCH RESULTS (snippets are leads, not proof):
{source_text}

STRICT RULES:
- Never name an actor or movie character as present in the current frame. This feature is about visual props/details; do not infer cast presence from the movie's cast list, plot, subtitles, or an actor's known role.
- In particular, a source saying someone appears elsewhere in the film (including a post-credit scene) does NOT establish that person appears in this current frame.
- Do not use the previous scene summary or character memory as proof of what is in the screenshot.
- Only describe a physical clue if it appears in the OCR or local visual candidates, and label uncertain OCR/model output as unverified. Do not claim you personally can see a detail beyond the provided evidence.
- A web page can support the meaning of a clue only if it explicitly discusses the same prop/text/logo/plate and the same film. Generic film trivia, cast lists, and unrelated scenes do not corroborate a current-frame finding.
- If the screenshot evidence does not establish a clue, say "not enough visual evidence" rather than guessing.
- For license plates, quote the observed string and whether OCR corroborated it. Never infer an actor birthday, wedding date, or production reference from a coincidental number. State a connection only if a source explicitly links that exact plate to that reference in this film.
- Distinguish CONFIRMED (direct evidence for this exact visual clue), POSSIBLE (plausible but not confirmed), and NOT VERIFIED.
- Cite a web result as [n] only when its snippet actually supports the specific claim. Do not cite generic pages to make an unsupported claim look verified.
- If no specific visual Easter egg is supported, say so plainly. Do not fill space with general movie trivia.
- Be concise and practical.
"""

    answer = ask_text_gemma(synthesis_prompt).strip()

    # Only show sources that the synthesis actually cited. This avoids presenting a
    # generic film/cast result as if it supported a frame-specific finding.
    cited_numbers = sorted({int(n) for n in re.findall(r"\[(\d{1,2})\]", answer)
                            if 1 <= int(n) <= len(all_results)})
    if cited_numbers:
        source_list = "\n\nSources cited for the findings above:\n" + "\n".join(
            f"[{i}] {all_results[i-1].get('title', 'Untitled result')} — {all_results[i-1]['url']}"
            for i in cited_numbers
        )
    else:
        source_list = "\n\nNo search result was cited as directly verifying a frame-specific visual Easter egg."
    if search_errors:
        source_list += "\n\nSome search providers were unavailable, so research may be incomplete."
    return answer + source_list


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

    monitor_pause_event.set()
    window = tk.Tk()

    window.title("🎬 MovieMind")

    window.geometry("540x570")

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

    # The F8 result is the initial scene answer.
    global current_displayed_answer
    global current_displayed_mode
    global current_displayed_input

    current_displayed_answer = current_frame["scene_analysis"]
    current_displayed_mode = "scene"
    current_displayed_input = ""

    answer_box.config(
        state="disabled"
    )


    # ======================================
    # DISPLAY ANSWER
    # ======================================

    def display_answer(answer, mode="", user_input=""):

        global current_displayed_answer
        global current_displayed_mode
        global current_displayed_input

        current_displayed_answer = answer
        current_displayed_mode = mode
        current_displayed_input = user_input

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
            get_answer("scene"),
            "scene"
        )


    # ======================================
    # DIALOGUE
    # ======================================

    def dialogue_clicked():

        display_answer(
            get_answer("dialogue"),
            "dialogue"
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
            ),
            "word",
            word
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
            ),
            "question",
            question
        )


    # ======================================
    # EASTER EGGS / HIDDEN DETAILS (ONLINE)
    # ======================================

    def easter_eggs_clicked():

        movie_title = simpledialog.askstring(
            "Easter Eggs / Hidden Details",
            "Enter the movie name (required for accurate subtitle matching):",
            parent=window
        )
        if movie_title is None:
            return
        movie_title = movie_title.strip()
        if not movie_title:
            messagebox.showinfo(
                "Movie name required",
                "Enter the movie title so MovieMind can research the film, match the current subtitle, and then investigate hidden details.",
                parent=window
            )
            return

        answer_box.config(state="normal")
        answer_box.delete("1.0", tk.END)
        answer_box.insert("1.0", "🔎 Step 1/3: Researching the movie...\nStep 2/3: Matching the current subtitle...\nStep 3/3: Investigating frame details and possible Easter eggs...\n\nThis online research may take longer than normal offline answers.")
        answer_box.config(state="disabled")
        window.update_idletasks()

        try:
            result = perform_easter_egg_research(movie_title)
            display_answer(result, "easter_eggs", movie_title)
        except Exception as error:
            messagebox.showerror(
                "Easter Eggs / Hidden Details",
                f"Online research failed. Normal offline MovieMind features are unaffected.\n\n{error}",
                parent=window
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


    easter_eggs_button = tk.Button(
        button_frame,
        text="🔎 Easter Eggs / Hidden Details (Online)",
        width=42,
        command=easter_eggs_clicked
    )

    easter_eggs_button.grid(
        row=2,
        column=0,
        columnspan=2,
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

        old_language = response_language

        choose_reply_language(window)

        language_button.config(
            text=f"🌐 Reply Language: {response_language}"
        )

        if (
            current_displayed_answer
            and response_language != old_language
        ):
            try:
                # Cached scene/dialogue answers are translated from
                # their original cached versions, avoiding repeated
                # translation drift.
                if current_displayed_mode in ("scene", "dialogue"):

                    refreshed = get_answer(
                        current_displayed_mode,
                        current_displayed_input
                    )

                else:

                    refreshed = translate_existing_answer(
                        current_displayed_answer
                    )

                display_answer(
                    refreshed,
                    current_displayed_mode,
                    current_displayed_input
                )

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
    monitor_pause_event.clear()


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
print("Automatic lightweight monitoring is ON: 1-second sampling, change-triggered OCR, and no continuous AI inference.")
print("Up to 1,440 selected low-resolution frames stay in bounded RAM; retained duration depends on scene changes and the buffer clears on exit.")
print("Press ESC to exit.")
print()


start_movie_monitor()

keyboard.add_hotkey(
    "f8",
    movie_mind
)


keyboard.wait("esc")
monitor_stop_event.set()


print("\nMovieMind closed.")