"""
MovieMind Image-Embedding Match Test
------------------------------------
Compares two local images using pretrained DINOv2-small image embeddings.
This is a separate experiment; it does not modify MovieMind V2.

First run downloads pretrained model weights (~85 MB, approximate; network needed).
After the model is cached, inference can run locally/offline.

Install:
    python -m pip install pillow torch timm

Run:
    python moviemind_embedding_match_test.py

Important:
- Cosine similarity is a ranking signal, NOT a probability or proof of identity.
- Test both a known-same-object pair and an unrelated pair.
"""
import os
import tkinter as tk
from tkinter import filedialog, messagebox
from PIL import Image, ImageTk
import torch
import torch.nn.functional as F
import timm
from timm.data import resolve_model_data_config
from timm.data.transforms_factory import create_transform

MODEL_NAME = "vit_small_patch14_dinov2.lvd142m"
PREVIEW_SIZE = (440, 300)


class EmbeddingMatcher:
    def __init__(self, status_callback):
        self.status_callback = status_callback
        self.model = None
        self.transform = None
        self.device = "cuda" if torch.cuda.is_available() else "cpu"

    def load_model(self):
        if self.model is not None:
            return
        self.status_callback(
            "Loading pretrained DINOv2-small model. First run may download model weights; please wait..."
        )
        self.model = timm.create_model(MODEL_NAME, pretrained=True, num_classes=0)
        self.model.eval().to(self.device)
        config = resolve_model_data_config(self.model)
        self.transform = create_transform(**config, is_training=False)

    def embed(self, path):
        self.load_model()
        image = Image.open(path).convert("RGB")
        tensor = self.transform(image).unsqueeze(0).to(self.device)
        with torch.inference_mode():
            vector = self.model(tensor)
            vector = F.normalize(vector, p=2, dim=-1)
        return vector.cpu()


def cosine_similarity(path_a, path_b, matcher):
    vec_a = matcher.embed(path_a)
    vec_b = matcher.embed(path_b)
    return float(F.cosine_similarity(vec_a, vec_b).item())


class App:
    def __init__(self, root):
        self.root = root
        root.title("MovieMind — Image Embedding Match Test")
        root.geometry("1050x780")
        root.minsize(800, 600)
        self.path_a = None
        self.path_b = None
        self.matcher = EmbeddingMatcher(self.set_status)

        tk.Label(root, text="MovieMind — Semantic/Visual Embedding Test",
                 font=("Segoe UI", 15, "bold")).pack(pady=(14, 4))
        tk.Label(root, text="Local DINOv2-small embeddings • no API key • images stay on your computer",
                 font=("Segoe UI", 10)).pack(pady=(0, 12))

        controls = tk.Frame(root)
        controls.pack(fill="x", padx=12)
        tk.Button(controls, text="1. Choose current screenshot",
                  command=lambda: self.choose("a"), width=28).grid(row=0, column=0, padx=5, pady=5)
        tk.Button(controls, text="2. Choose reference image",
                  command=lambda: self.choose("b"), width=26).grid(row=0, column=1, padx=5, pady=5)
        tk.Button(controls, text="Compare embeddings",
                  command=self.compare, width=20).grid(row=0, column=2, padx=5, pady=5)

        self.label_a = tk.Label(root, text="Current screenshot: not selected", anchor="w")
        self.label_a.pack(fill="x", padx=18)
        self.label_b = tk.Label(root, text="Reference image: not selected", anchor="w")
        self.label_b.pack(fill="x", padx=18, pady=(2, 8))

        frame = tk.Frame(root)
        frame.pack(fill="x", padx=10)
        self.preview_a = tk.Label(frame, text="Current screenshot preview", relief="groove",
                                  width=52, height=16)
        self.preview_a.grid(row=0, column=0, padx=5, sticky="n")
        self.preview_b = tk.Label(frame, text="Reference image preview", relief="groove",
                                  width=52, height=16)
        self.preview_b.grid(row=0, column=1, padx=5, sticky="n")

        self.status = tk.Label(root,
            text="Choose two images, then click Compare embeddings.",
            justify="left", anchor="w", wraplength=990)
        self.status.pack(fill="x", padx=18, pady=12)
        tk.Label(root, text=(
            "Test protocol: compare the two dagger images first, then compare the two different scenes. "
            "A useful model should rank the known-similar pair higher than the unrelated pair."
        ), justify="left", wraplength=990).pack(fill="x", padx=18, pady=(0, 10))

    def set_status(self, text):
        self.status.config(text=text)
        self.root.update_idletasks()

    def choose(self, which):
        path = filedialog.askopenfilename(
            title="Choose image",
            filetypes=[("Image files", "*.png *.jpg *.jpeg *.webp *.bmp"), ("All files", "*.*")]
        )
        if not path:
            return
        if which == "a":
            self.path_a = path
            label, preview = self.label_a, self.preview_a
            label.config(text=f"Current screenshot: {os.path.basename(path)}")
        else:
            self.path_b = path
            label, preview = self.label_b, self.preview_b
            label.config(text=f"Reference image: {os.path.basename(path)}")
        try:
            image = Image.open(path).convert("RGB")
            image.thumbnail(PREVIEW_SIZE, Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(image)
            preview.config(image=photo, text="", width=0, height=0)
            preview.image = photo
        except Exception as exc:
            messagebox.showerror("Could not open image", str(exc), parent=self.root)

    def compare(self):
        if not self.path_a or not self.path_b:
            messagebox.showinfo("Choose two images", "Select both images first.", parent=self.root)
            return
        try:
            self.set_status("Preparing image embeddings. First run may download model weights...")
            score = cosine_similarity(self.path_a, self.path_b, self.matcher)
            self.set_status(
                f"Cosine similarity: {score:.4f}\n"
                "This is a similarity score, not a probability. Do not use one pair alone to set a threshold.\n"
                "Compare the score for the dagger pair against the score for the unrelated-scene pair. "
                "The dagger pair should rank higher if the embeddings capture the relevant similarity."
            )
        except Exception as exc:
            messagebox.showerror(
                "Embedding comparison failed",
                f"{type(exc).__name__}: {exc}\n\n"
                "Check the installation and internet connection for the first model download.",
                parent=self.root
            )
            self.set_status("Embedding comparison failed. See the error dialog.")


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
