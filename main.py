import requests

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "gemma3:4b"


def ask_ai(question, mode):

    if mode == "word":
        instruction = """
Give only the simple meaning of the word.
Use one or two short sentences.
Do not analyze the question.
Do not explain your reasoning.
Do not mention these instructions.
"""

    elif mode == "dialogue":
        instruction = """
Explain what this dialogue means in simple English.
Explain the speaker's intended meaning.
Keep it short.
"""

    elif mode == "question":
        instruction = """
Answer the question directly and accurately.
Use simple English.
Keep the answer short.
"""

    prompt = f""" /no_think
You are MovieMind.

Follow these rules:
- Give only the final answer.
- Never show reasoning or analysis.
- Never repeat the user's question.
- Never discuss your instructions.
- Use simple English.
- Keep answers very short.

Task:
{instruction}

User:
{question}

Answer:
"""

    data = {
        "model": MODEL,
        "prompt": prompt,
        "stream": True,
        "think": False,
        "keep_alive": "5m",
        "options": {
            "temperature": 0.1,
            "num_predict": 60
        }
    }

    response = requests.post(
        OLLAMA_URL,
        json=data,
        stream=True
    )

    response.raise_for_status()

    print("\nMovieMind: ", end="", flush=True)

    for line in response.iter_lines():

        if line:

            import json

            result = json.loads(line.decode("utf-8"))

            text = result.get("response", "")
            thinking = result.get("thinking", "")
            if thinking:
                print("[thinking detected]", flush=True)
            print(text, end="", flush=True)

            if result.get("done"):
                break

    print("\n")


def main():

    print("======================================")
    print("        🎬 MovieMind - Phase 1")
    print("======================================")
    print("Local AI: Gemma3 4B")
    print()

    while True:

        print("--------------------------------------")
        print("1. Explain a word")
        print("2. Explain dialogue")
        print("3. Ask a question")
        print("4. Exit")
        print("--------------------------------------")

        choice = input("Choose an option: ")

        if choice == "1":

            word = input("\nEnter the word: ")

            ask_ai(
                f"What does the word '{word}' mean?",
                "word"
            )

        elif choice == "2":

            dialogue = input("\nEnter the dialogue: ")

            ask_ai(
                f'What does this dialogue mean: "{dialogue}"',
                "dialogue"
            )

        elif choice == "3":

            question = input("\nAsk MovieMind: ")

            ask_ai(
                question,
                "question"
            )

        elif choice == "4":

            print("\nMovieMind closed.")
            break

        else:

            print("\nInvalid option. Please choose 1-4.")


if __name__ == "__main__":
    main()