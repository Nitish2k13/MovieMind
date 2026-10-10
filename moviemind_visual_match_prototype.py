"""
MovieMind Visual Reference Matching Prototype
---------------------------------------------
A standalone, local-only prototype for comparing two user-provided images.
It does NOT search the internet or prove an Easter egg. It tests whether
OpenCV ORB feature matching can find visual similarity between two images.

Install:
    python -m pip install pillow opencv-python

Run:
    python moviemind_visual_match_prototype.py
"""
import os
import tkinter as tk
from tkinter import filedialog, messagebox
from PIL import Image, ImageTk, ImageOps
import cv2
import numpy as np


PREVIEW_SIZE = (440, 310)
MAX_DIMENSION = 1200


def load_cv_image(path):
    """Load image safely and downscale it for a lightweight comparison."""
    pil = Image.open(path).convert("RGB")
    pil.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.Resampling.LANCZOS)
    rgb = np.asarray(pil)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def compare_images(path_a, path_b):
    """Return ORB feature-match statistics and a visual match preview."""
    img_a = load_cv_image(path_a)
    img_b = load_cv_image(path_b)
    gray_a = cv2.cvtColor(img_a, cv2.COLOR_BGR2GRAY)
    gray_b = cv2.cvtColor(img_b, cv2.COLOR_BGR2GRAY)

    orb = cv2.ORB_create(nfeatures=1200)
    key_a, desc_a = orb.detectAndCompute(gray_a, None)
    key_b, desc_b = orb.detectAndCompute(gray_b, None)

    if desc_a is None or desc_b is None or len(key_a) < 2 or len(key_b) < 2:
        return {
            "score": 0.0, "good_matches": 0, "keypoints_a": len(key_a),
            "keypoints_b": len(key_b), "matches": 0, "preview": None,
            "message": "Not enough distinctive visual features were detected."
        }

    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    matches = sorted(matcher.match(desc_a, desc_b), key=lambda m: m.distance)
    good = [m for m in matches if m.distance < 55]

    # Normalize by the smaller keypoint count so image resolution does not
    # dominate. This is a heuristic, not a calibrated probability.
    denominator = max(1, min(len(key_a), len(key_b)))
    score = min(100.0, 100.0 * len(good) / denominator)

    shown = good[:60]
    preview = cv2.drawMatches(
        img_a, key_a, img_b, key_b, shown, None,
        flags=cv2.DrawMatchesFlags_NOT_DRAW_SINGLE_POINTS
    ) if shown else None

    if len(good) >= 25:
        message = "Many local visual features match. Inspect the highlighted matches; this is not proof the objects are identical."
    elif len(good) >= 8:
        message = "Some visual features match, but the evidence is weak or ambiguous."
    else:
        message = "Few distinctive features match. The images may show different objects, or the viewpoint/lighting may be too different."

    return {
        "score": score, "good_matches": len(good), "keypoints_a": len(key_a),
        "keypoints_b": len(key_b), "matches": len(matches), "preview": preview,
        "message": message
    }


class VisualMatchApp:
    def __init__(self, root):
        self.root = root
        root.title("MovieMind — Visual Reference Matching Prototype")
        root.geometry("1080x850")
        root.minsize(800, 650)
        self.current_path = None
        self.reference_path = None
        self.preview_refs = []
        self.match_photo = None

        tk.Label(root, text="Compare a movie screenshot with a reference image",
                 font=("Segoe UI", 15, "bold")).pack(pady=(14, 4))
        tk.Label(root, text="Choose two local images. Matching runs locally; no API key or upload is used.",
                 font=("Segoe UI", 10)).pack(pady=(0, 12))

        controls = tk.Frame(root)
        controls.pack(fill="x", padx=14)
        tk.Button(controls, text="1. Choose current-movie screenshot",
                  command=self.choose_current, width=31).grid(row=0, column=0, padx=5, pady=5)
        tk.Button(controls, text="2. Choose reference image",
                  command=self.choose_reference, width=27).grid(row=0, column=1, padx=5, pady=5)
        tk.Button(controls, text="Compare images",
                  command=self.compare, width=18, default="active").grid(row=0, column=2, padx=5, pady=5)

        self.current_label = tk.Label(root, text="Current screenshot: not selected", anchor="w")
        self.current_label.pack(fill="x", padx=20, pady=(5, 0))
        self.reference_label = tk.Label(root, text="Reference image: not selected", anchor="w")
        self.reference_label.pack(fill="x", padx=20, pady=(3, 8))

        self.images_frame = tk.Frame(root)
        self.images_frame.pack(fill="x", padx=14, pady=4)
        self.current_preview = tk.Label(self.images_frame, text="Current screenshot preview",
                                        relief="groove", width=54, height=17)
        self.current_preview.grid(row=0, column=0, padx=6, sticky="n")
        self.reference_preview = tk.Label(self.images_frame, text="Reference image preview",
                                          relief="groove", width=54, height=17)
        self.reference_preview.grid(row=0, column=1, padx=6, sticky="n")

        self.status = tk.Label(root, text="Choose both images, then click Compare images.",
                               justify="left", anchor="w", wraplength=1020,
                               font=("Segoe UI", 10))
        self.status.pack(fill="x", padx=20, pady=10)

        self.match_label = tk.Label(root, text="Feature-match visualization appears here",
                                    relief="groove")
        self.match_label.pack(fill="both", expand=True, padx=14, pady=(0, 14))

    def choose_current(self):
        path = filedialog.askopenfilename(title="Choose current-movie screenshot",
            filetypes=[("Image files", "*.png *.jpg *.jpeg *.webp *.bmp"), ("All files", "*.*")])
        if path:
            self.current_path = path
            self.current_label.config(text=f"Current screenshot: {os.path.basename(path)}")
            self.show_preview(path, self.current_preview)

    def choose_reference(self):
        path = filedialog.askopenfilename(title="Choose reference image",
            filetypes=[("Image files", "*.png *.jpg *.jpeg *.webp *.bmp"), ("All files", "*.*")])
        if path:
            self.reference_path = path
            self.reference_label.config(text=f"Reference image: {os.path.basename(path)}")
            self.show_preview(path, self.reference_preview)

    def show_preview(self, path, widget):
        try:
            image = Image.open(path).convert("RGB")
            image.thumbnail(PREVIEW_SIZE, Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(image)
            widget.config(image=photo, text="", width=0, height=0)
            widget.image = photo
        except Exception as exc:
            messagebox.showerror("Could not open image", str(exc), parent=self.root)

    def compare(self):
        if not self.current_path or not self.reference_path:
            messagebox.showinfo("Choose two images",
                "Select a current-movie screenshot and a reference image first.", parent=self.root)
            return
        try:
            self.status.config(text="Comparing local visual features...")
            self.root.update_idletasks()
            result = compare_images(self.current_path, self.reference_path)
            text = (
                f"Feature-match heuristic: {result['score']:.1f}% (NOT a probability of same-object identity)\n"
                f"Good local matches: {result['good_matches']} | Total keypoints: "
                f"{result['keypoints_a']} vs {result['keypoints_b']}\n"
                f"{result['message']}\n\n"
                "Limitations: ORB works best when images share visible details, texture, shape, and viewpoint. "
                "It can miss the same object from a different angle and can match unrelated objects with similar patterns."
            )
            self.status.config(text=text)
            if result["preview"] is not None:
                rgb = cv2.cvtColor(result["preview"], cv2.COLOR_BGR2RGB)
                pil = Image.fromarray(rgb)
                pil.thumbnail((1000, 430), Image.Resampling.LANCZOS)
                self.match_photo = ImageTk.PhotoImage(pil)
                self.match_label.config(image=self.match_photo, text="")
            else:
                self.match_label.config(image="", text="No match lines to display.")
                self.match_photo = None
        except Exception as exc:
            messagebox.showerror("Comparison failed", f"{type(exc).__name__}: {exc}", parent=self.root)
            self.status.config(text="Comparison failed. Check that both files are valid image files.")

def main():
    root = tk.Tk()
    VisualMatchApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
