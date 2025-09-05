import tkinter as tk
from tkinter import ttk, messagebox
from api import ApiClient, ApiError

class MainWindow(ttk.Frame):
    """
    Placeholder pentru aplicația ta (tabelul vine în pasul următor).
    Aici demonstrăm că putem face un request autenticat.
    """
    def __init__(self, master, client: ApiClient):
        super().__init__(master, padding=10)
        self.client = client
        self.pack(fill="both", expand=True)

        ttk.Label(self, text="Ești autentificat ✔", font=("Segoe UI Semibold", 13)).pack(anchor="w", pady=(0,10))

        ttk.Button(self, text="Testează request (GET /)", command=self.test_call).pack(anchor="w")

    def test_call(self):
        try:
            # exemplu de apel – schimbă path-ul cu ceva real (de ex. /api/ping/)
            data = self.client.request("GET", "/")
            messagebox.showinfo("Răspuns", str(data)[:1000])
        except ApiError as e:
            messagebox.showerror("Eroare API", str(e))
