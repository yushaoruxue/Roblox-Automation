"""Responsive image viewer used by deployment profile previews."""

from __future__ import annotations

import os
import tkinter as tk
from PIL import Image, ImageTk


class PreviewDialog:
    def __init__(self, parent, image_path, profile_name, colors):
        self.parent = parent
        self.image_path = image_path
        self.profile_name = profile_name
        self.colors = colors
        with Image.open(image_path) as source:
            self.source_image = source.convert("RGB")
        self.photo = None
        self.render_job = None

        self.window = tk.Toplevel(parent)
        self.window.title(f"方案预览 · {profile_name}")
        self.window.geometry("920x680")
        self.window.minsize(560, 420)
        self.window.configure(bg=colors["background"])
        self.window.transient(parent)

        header = tk.Frame(self.window, bg=colors["surface"])
        header.pack(fill="x")
        tk.Label(
            header,
            text=profile_name,
            bg=colors["surface"],
            fg=colors["foreground"],
            font=("Microsoft YaHei UI", 12, "bold"),
        ).pack(side="left", padx=18, pady=(12, 2))
        tk.Label(
            header,
            text=(
                f"{self.source_image.width} × {self.source_image.height}  ·  "
                f"{os.path.basename(image_path)}"
            ),
            bg=colors["surface"],
            fg=colors["muted"],
            font=("Microsoft YaHei UI", 9),
        ).pack(side="right", padx=18, pady=12)

        self.canvas = tk.Canvas(
            self.window,
            bg="#090d14",
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack(fill="both", expand=True, padx=14, pady=14)
        self.canvas.bind("<Configure>", self.schedule_render)
        self.window.bind("<Escape>", lambda _event: self.window.destroy())
        self.window.protocol("WM_DELETE_WINDOW", self.window.destroy)
        self.window._preview_dialog = self

    def schedule_render(self, _event=None):
        if self.render_job is not None:
            self.window.after_cancel(self.render_job)
        self.render_job = self.window.after_idle(self.render)

    def render(self):
        self.render_job = None
        width = max(1, self.canvas.winfo_width() - 24)
        height = max(1, self.canvas.winfo_height() - 24)
        scale = min(
            width / self.source_image.width,
            height / self.source_image.height,
        )
        render_width = max(1, round(self.source_image.width * scale))
        render_height = max(1, round(self.source_image.height * scale))
        resized = self.source_image.resize(
            (render_width, render_height),
            Image.Resampling.LANCZOS,
        )
        self.photo = ImageTk.PhotoImage(resized)
        self.canvas.delete("all")
        self.canvas.create_image(
            self.canvas.winfo_width() // 2,
            self.canvas.winfo_height() // 2,
            image=self.photo,
            anchor="center",
        )
