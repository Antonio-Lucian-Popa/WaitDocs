# app.py
import os
import tkinter as tk
from tkinter import ttk
from pathlib import Path
import ctypes  # <- pentru AppUserModelID (taskbar)

from config import API_BASE
from api import ApiClient
from login_window import LoginWindow
from waitdocs_window import WaitDocsWindow
from modern_theme import apply_modern_style
from paths import resource_path

BASE_DIR = Path(__file__).resolve().parent
ICON_PATH = BASE_DIR / "assets" / "waitdocs.ico"

# spune Windows-ului că aplicația e „WaitDocs” (icon corect în taskbar / pin)
try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ro.waitdocs.app")
except Exception:
    pass


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("WaitDocs")
        self.geometry("480x280")

        # păstrează calea iconului ca să o poți da tuturor ferestrelor
        self._icon_path = str(ICON_PATH)
        try:
            self.iconbitmap(resource_path("assets/waitdocs.ico"))
        except Exception:
            pass

        apply_modern_style(self)

        # client API (va încărca automat token-urile salvate dacă există)
        self.api = ApiClient(API_BASE)

        # dacă avem tokenuri valide, sari direct în aplicație
        if self.api.tokens:
            self.after(50, lambda: self.open_main(self.api))
        else:
            self.after(50, lambda: LoginWindow(self, self.api, on_success=self.open_main))

    def open_main(self, client: ApiClient):
        """Desenează fereastra principală."""
        for w in self.winfo_children():
            w.destroy()
        self.geometry("1100x650")
        self.title("WaitDocs – Documente în așteptare")

        # IMPORTANT: pasăm callback-ul care readuce login-ul după logout
        WaitDocsWindow(self, client, on_logged_out=self.show_login_again)

    def show_login_again(self):
        """Cheamă acest callback la logout: curăță UI-ul și arată login-ul."""
        # opțional: ne asigurăm că token-urile sunt curățate
        try:
            if self.api:
                self.api.logout()
        except Exception:
            pass

        # reconstruim UI-ul pentru login
        for w in self.winfo_children():
            w.destroy()
        self.geometry("480x280")
        self.title("WaitDocs – Autentificare")

        # folosește un client „curat”
        self.api = ApiClient(API_BASE)
        try:
            self.iconbitmap(resource_path("assets/waitdocs.ico"))
        except Exception:
            pass

        LoginWindow(self, self.api, on_success=self.open_main)


if __name__ == "__main__":
    App().mainloop()
