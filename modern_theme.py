# modern_theme.py
import tkinter as tk
from tkinter import ttk

def apply_modern_style(root: tk.Misc) -> None:
    s = ttk.Style(root)

    # alege o temă disponibilă, cu fallback-uri
    for cand in ("vista", "clam", "alt", "default"):
        if cand in s.theme_names():
            s.theme_use(cand)
            break

    # scalare UI (opțional, ignoră dacă nu se poate)
    try:
        root.call("tk", "scaling", 1.15)
    except tk.TclError:
        pass

    # paletă simplă
    BG = "#f5f7fb"
    FG = "#1f2937"
    CARD_BG = "#ffffff"
    ACCENT = "#2563eb"
    ACCENT_HOVER = "#1d4ed8"

    root.configure(bg=BG)

    # global
    s.configure(".", background=BG, foreground=FG, font=("Segoe UI", 10))

    # frames
    s.configure("Main.TFrame", background=BG)
    s.configure("Card.TFrame", background=CARD_BG)

    # labels
    s.configure("Heading.TLabel", background=BG, foreground=FG, font=("Segoe UI Semibold", 14))
    s.configure("Subheading.TLabel", background=BG, foreground="#6b7280")

    # buttons
    s.configure("TButton", padding=8)
    s.map("TButton", relief=[("pressed", "sunken"), ("!pressed", "raised")])
    s.configure("Accent.TButton", foreground="white", background=ACCENT)
    s.map(
        "Accent.TButton",
        background=[("active", ACCENT_HOVER), ("pressed", ACCENT_HOVER), ("disabled", "#a5b4fc")],
        foreground=[("disabled", "#e5e7eb")]
    )

    # treeview
    s.configure(
        "Treeview",
        background="#ffffff",
        fieldbackground="#ffffff",
        foreground=FG,
        rowheight=26,
        bordercolor="#e5e7eb",
        lightcolor="#e5e7eb",
        darkcolor="#e5e7eb",
    )
    s.configure("Treeview.Heading", font=("Segoe UI Semibold", 10), background=BG)
    s.map("Treeview",
          background=[("selected", "#e0e7ff")],
          foreground=[("selected", "#111827")])
