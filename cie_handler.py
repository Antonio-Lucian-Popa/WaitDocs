# cie_handler.py — prompt minimal Tkinter doar pentru PIN, apoi continuă rularea
import sys, json, urllib.parse, webbrowser
import tkinter as tk
from tkinter import ttk, messagebox
from cie_reader_core import read_all

# ---------- util: parsing & return ----------
def parse_cie_url(arg: str):
    """
    Ex: cie://read?ret=https%3A%2F%2Fsite.tau%2Fcie%2Freturn&nonce=abc123
    (opțional pt. test: &pin=1234 — NU folosi pin în URL în producție)
    """
    u = urllib.parse.urlparse(arg)
    qs = urllib.parse.parse_qs(u.query)
    return {
        "cmd":   (u.netloc or u.path.lstrip("/")).lower(),
        "nonce": (qs.get("nonce") or [""])[0],
        "ret":   (qs.get("ret")   or [""])[0],
        "pin":   (qs.get("pin")   or [""])[0],  # doar pt. test
    }

def b64url_encode_utf8(obj) -> str:
    if not isinstance(obj, str):
        s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    else:
        s = obj
    b = s.encode("utf-8")
    import base64
    out = base64.urlsafe_b64encode(b).decode("ascii")
    return out.rstrip("=")

def is_safe_return_url(ret: str) -> bool:
    try:
        u = urllib.parse.urlparse(ret)
        return u.scheme in ("http", "https")
    except Exception:
        return False

def open_return_url(ret: str, nonce: str, payload: dict | None, error: str | None):
    """
    Construiește URL: ret#nonce=...&payload=... sau ret#nonce=...&error=...
    și îl deschide în browser. Dacă nu e ret valid, afișează local + print.
    """
    if not ret or not is_safe_return_url(ret):
        blob = json.dumps({"nonce": nonce, "data": payload, "error": error}, ensure_ascii=False, indent=2)
        try:
            messagebox.showinfo("Rezultat CIE", blob)
        except Exception:
            pass
        print(blob)
        return

    frag = {"nonce": nonce}
    if error:
        frag["error"] = error
    else:
        frag["payload"] = b64url_encode_utf8(payload or {})

    fragment = urllib.parse.urlencode(frag, safe=".*-_/")
    if "#" in ret:
        ret = ret.split("#", 1)[0]
    url = f"{ret}#{fragment}"
    try:
        # NU mai ridica fereastra: new=0, autoraise=False
        webbrowser.open(url, new=0, autoraise=False)
    except Exception:
        print(url)

# ---------- UI minimală pentru PIN ----------
def prompt_pin(default_pin: str = "") -> str | None:
    """Afișează un dialog simplu pentru PIN. Returnează PIN sau None dacă se anulează."""
    root = tk.Tk()
    root.title("CIE PIN")
    root.resizable(False, False)
    root.geometry("320x140")

    try:
        root.call("tk", "scaling", 1.25)
    except Exception:
        pass

    frm = ttk.Frame(root, padding=16)
    frm.pack(fill="both", expand=True)

    ttk.Label(frm, text="Introduceți PIN-ul CIE", font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(0, 8))

    row = ttk.Frame(frm)
    row.pack(fill="x", pady=(0, 8))
    ttk.Label(row, text="PIN").pack(side="left", padx=(0, 10))

    pin_var = tk.StringVar(value=default_pin or "")
    entry = ttk.Entry(row, textvariable=pin_var, show="•", width=24)
    entry.pack(side="left", expand=True, fill="x")
    entry.focus_set()

    show_var = tk.BooleanVar(value=False)
    def toggle_show():
        entry.config(show="" if show_var.get() else "•")
    ttk.Checkbutton(row, text="Arată", variable=show_var, command=toggle_show).pack(side="left", padx=(8, 0))

    # butoane
    btns = ttk.Frame(frm)
    btns.pack(fill="x")
    result = {"pin": None}

    def on_ok():
        p = (pin_var.get() or "").strip()
        if not p:
            messagebox.showwarning("PIN lipsă", "Introduceți PIN-ul.")
            return
        result["pin"] = p
        root.destroy()

    def on_cancel():
        result["pin"] = None
        root.destroy()

    ttk.Button(btns, text="Continuă", command=on_ok).pack(side="left")
    ttk.Button(btns, text="Renunță", command=on_cancel).pack(side="right")

    root.bind("<Return>", lambda e: on_ok())
    root.bind("<Escape>", lambda e: on_cancel())

    # centrează pe ecran
    root.update_idletasks()
    w = root.winfo_width(); h = root.winfo_height()
    x = (root.winfo_screenwidth() - w) // 2
    y = (root.winfo_screenheight() - h) // 2
    root.geometry(f"{w}x{h}+{x}+{y}")

    root.mainloop()
    return result["pin"]

# ---------- entry point ----------
def main():
    if len(sys.argv) < 2 or not sys.argv[1].startswith("cie://"):
        print("Se așteaptă URL de forma: cie://read?ret=<url_encoded>&nonce=<id>")
        sys.exit(2)

    p = parse_cie_url(sys.argv[1])

    if p["cmd"] not in ("read", "read_all"):
        print(f"Comandă necunoscută: {p['cmd']}")
        sys.exit(2)
    if not p["nonce"]:
        print("Lipsește nonce.")
        sys.exit(2)

    # 1) cere PIN în UI (dacă nu a venit doar pentru test)
    pin = p["pin"].strip() if p["pin"] else prompt_pin("")
    if not pin:
        open_return_url(p["ret"], p["nonce"], None, "Operațiune anulată sau PIN gol.")
        sys.exit(1)

    # 2) citește și întoarce rezultatul
    try:
        data = read_all(pin)   # -> dict
    except Exception as e:
        open_return_url(p["ret"], p["nonce"], None, f"Eroare la citire: {e}")
        sys.exit(1)

    open_return_url(p["ret"], p["nonce"], data, None)
    print("OK: rezultat trimis către pagina de return.")

if __name__ == "__main__":
    main()