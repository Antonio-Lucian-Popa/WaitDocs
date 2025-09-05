# login_window.py
import tkinter as tk
from tkinter import ttk, messagebox
from api import ApiClient, ApiError
from paths import resource_path  # pentru icon


INPUT_FONT = ("Segoe UI", 13)

class LoginWindow(tk.Toplevel):
    """
    Fereastră modală de login.
    La succes: setează client.tokens și apelează on_success(client)
    """
    def __init__(self, master, client: ApiClient, on_success):
        super().__init__(master)
        self.client = client
        self.on_success = on_success
        self.title("Autentificare")
        self.resizable(False, False)
        self.configure(bg="#f5f6f8")
        try:
            self.iconbitmap(resource_path("assets/waitdocs.ico"))
        except Exception:
            pass
        self.grab_set()
        self.transient(master)

        # --------- stiluri mai mari pentru login ----------
        s = ttk.Style(self)
        # dacă ai deja modern_theme -> asta doar adaugă stiluri dedicate loginului
        s.configure("LoginHeading.TLabel", font=("Segoe UI Semibold", 16))
        s.configure("LoginLabel.TLabel",   font=("Segoe UI", 11))
        s.configure("Login.TEntry",        font=INPUT_FONT)   # <- mărimea textului în inputuri
        s.configure("Login.TButton",       padding=10)

        self.username = tk.StringVar()
        self.password = tk.StringVar()

        frm = ttk.Frame(self, padding=18)
        frm.grid(row=0, column=0, sticky="nsew")
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="Autentificare", style="LoginHeading.TLabel")\
            .grid(row=0, column=0, columnspan=2, pady=(0, 12))

        ttk.Label(frm, text="Username", style="LoginLabel.TLabel")\
            .grid(row=1, column=0, sticky="w", pady=(2, 2))
        ent_user = ttk.Entry(frm, textvariable=self.username, width=30, style="Login.TEntry", font=INPUT_FONT)
        ent_user.grid(row=1, column=1, pady=4, sticky="ew")

        ttk.Label(frm, text="Parolă", style="LoginLabel.TLabel")\
            .grid(row=2, column=0, sticky="w", pady=(2, 2))
        ent_pass = ttk.Entry(frm, textvariable=self.password, show="•", width=30, style="Login.TEntry", font=INPUT_FONT)
        ent_pass.grid(row=2, column=1, pady=4, sticky="ew")

        self.btn = ttk.Button(frm, text="Conectează-te", style="Login.TButton", command=self.do_login)
        self.btn.grid(row=3, column=0, columnspan=2, pady=(12, 0), sticky="ew")

        self.bind("<Return>", lambda e: self.do_login())
        ent_user.focus_set()

    def do_login(self):
        user = self.username.get().strip()
        pwd = self.password.get().strip()
        if not user or not pwd:
            messagebox.showwarning("Date lipsă", "Introdu username și parolă.")
            return
        self.btn.configure(state="disabled", text="Se conectează...")
        self.update_idletasks()
        try:
            self.client.login(user, pwd)
        except ApiError as e:
            messagebox.showerror("Autentificare eșuată", str(e))
            self.btn.configure(state="normal", text="Conectează-te")
            return
        except Exception as e:
            messagebox.showerror("Eroare", str(e))
            self.btn.configure(state="normal", text="Conectează-te")
            return
        # succes
        self.destroy()
        self.on_success(self.client)
