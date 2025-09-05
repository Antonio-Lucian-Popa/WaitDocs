# waitdocs_window.py
import math
import json
import os
import re
import tempfile
import urllib.parse
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Optional, Callable

from PIL import Image, ImageTk

from api import ApiClient, ApiError
from config import API_BASE, MEDIA_URL
from paths import resource_path

# pentru PIN + citire CIE
from cie_integration import prompt_pin_modal, CIEReadError  # noqa: F401
from cie_reader_core import read_all

PAGE_SIZE = 10


# ---------------- helpers ----------------

def _dmy_to_iso(dmy: str) -> str | None:
    """DD.MM.YYYY -> YYYY-MM-DD (or return original if format not matched)."""
    if not dmy:
        return None
    m = re.match(r"^\s*(\d{2})[.\-/](\d{2})[.\-/](\d{4})\s*$", dmy)
    if not m:
        return dmy
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"


def _iso_to_dmy(iso: str | None) -> str:
    """YYYY-MM-DD -> DD.MM.YYYY (safe)."""
    if not iso:
        return ""
    m = re.match(r"^\s*(\d{4})-(\d{2})-(\d{2})", str(iso))
    return f"{m.group(3)}.{m.group(2)}.{m.group(1)}" if m else str(iso or "")


def _only_digits(s: str) -> str:
    return "".join(ch for ch in (s or "") if ch.isdigit())


# ---------------- edit dialog ----------------

class EditDialog(tk.Toplevel):
    """
    Modala de editare pentru un rând CI.
    - Buton 'Scanează CIE' pornește citirea în background și afișează un loader.
    - La Save: PUT /waitdocument/ cu emis (top-level), expira (ISO), data (ISO),
      nr (doar cifre), observatii și 'date' (JSON string) care include și ci.eliberat.
    - La deschidere: preumple din GET /waitdocument/?id=<id> (dacă există).
    """
    def __init__(self, master, row_data: dict, on_saved_reload=None, api: ApiClient | None = None):
        super().__init__(master)
        self.title("Editează document")
        self.resizable(False, False)
        self.configure(bg="#f5f6f8")
        try:
            self.iconbitmap(resource_path("assets/waitdocs.ico"))
        except Exception:
            pass

        self.row_data = row_data            # din tabel
        self.on_saved_reload = on_saved_reload
        self.api = api                      # pentru apelurile GET/PUT
        self._date_payload = None           # vine din scanare (date complet structurat)
        self._loader = None
        self._loader_bar = None

        self.grab_set()                     # modală
        self.transient(master)

        # ==== UI: font mai mare DOAR pentru inputuri ====
        s = ttk.Style(self)
        try:
            s.theme_use("clam")
        except Exception:
            pass

        # lăsăm fontul implicit pe etichete și butoane
        INPUT_FONT = ("Segoe UI", 12)
        s.configure("Input.TEntry", font=INPUT_FONT, padding=(8, 6))
        s.configure("Input.TCombobox", font=INPUT_FONT, padding=(8, 6))

        PADX = 14
        ROW_PADY = (4, 8)
        LABEL_COL_WIDTH = 120

        frm = ttk.Frame(self, padding=(PADX, 12, PADX, 10))
        frm.grid(row=0, column=0, sticky="nsew")
        frm.columnconfigure(0, minsize=LABEL_COL_WIDTH)  # etichete
        frm.columnconfigure(1, weight=1)                 # câmpuri

        def row(label_text, widget, r):
            ttk.Label(frm, text=label_text).grid(row=r, column=0, sticky="e", padx=(0, 10), pady=ROW_PADY)
            widget.grid(row=r, column=1, sticky="ew", pady=ROW_PADY)

        # câmpuri
        self.tip_var = tk.StringVar(value=row_data.get("tip", "").lower())
        tip = ttk.Combobox(frm, textvariable=self.tip_var,
                           values=["CI", "GDPR", "CONTRACT"],
                           state="readonly", style="Input.TCombobox", font=INPUT_FONT)
        tip.configure(state="disabled")
        row("Tip", tip, 0)

        self.subtip_var = tk.StringVar(value=row_data.get("subtip", ""))
        subtip = ttk.Entry(frm, textvariable=self.subtip_var, state="disabled", style="Input.TEntry", font=INPUT_FONT)
        row("Subtip", subtip, 1)

        self.angajat_var = tk.StringVar(value=row_data.get("angajat", ""))
        ang = ttk.Entry(frm, textvariable=self.angajat_var, state="disabled", style="Input.TEntry", font=INPUT_FONT)
        row("Angajat", ang, 2)

        row("Fișier existent", ttk.Label(frm, text="(nedisponibil din API)", foreground="#666"), 3)

        self.emis_var = tk.StringVar()
        emis = ttk.Entry(frm, textvariable=self.emis_var, style="Input.TEntry", font=INPUT_FONT)
        row("Emitent", emis, 4)

        self.expira_var = tk.StringVar()
        expira = ttk.Entry(frm, textvariable=self.expira_var, style="Input.TEntry", font=INPUT_FONT)
        row("Expiră", expira, 5)

        # rând Serie + Număr
        serie_row = ttk.Frame(frm)
        self.serie_var = tk.StringVar()
        e_serie = ttk.Entry(serie_row, textvariable=self.serie_var, width=8, style="Input.TEntry", font=INPUT_FONT)
        e_serie.pack(side="left")
        ttk.Label(serie_row, text="  Număr").pack(side="left")
        self.numar_var = tk.StringVar()
        e_numar = ttk.Entry(serie_row, textvariable=self.numar_var, style="Input.TEntry", font=INPUT_FONT)
        e_numar.pack(side="left", fill="x", expand=True, padx=(6, 0))
        row("Serie", serie_row, 6)

        self.data_var = tk.StringVar()
        data = ttk.Entry(frm, textvariable=self.data_var, style="Input.TEntry", font=INPUT_FONT)
        row("Data eliberării", data, 7)

        # Observații – mărim DOAR fontul din Text
        ttk.Label(frm, text="Observații").grid(row=8, column=0, sticky="ne", padx=(0, 10), pady=ROW_PADY)
        self.obs_txt = tk.Text(frm, height=5, width=44)
        self.obs_txt.configure(font=INPUT_FONT)
        self.obs_txt.grid(row=8, column=1, sticky="ew", pady=ROW_PADY)

        ttk.Separator(frm).grid(row=9, column=0, columnspan=2, sticky="ew", pady=(6, 10))

        btns = ttk.Frame(frm)
        btns.grid(row=10, column=0, columnspan=2, sticky="e")
        self.scan_btn = ttk.Button(btns, text="Scanează CIE", command=self.run_scan)
        self.scan_btn.pack(side="left")
        ttk.Button(btns, text="Salvează", style="Accent.TButton", command=self.save_and_close)\
            .pack(side="left", padx=(8, 0))
        ttk.Button(btns, text="Închide", command=self.destroy).pack(side="left", padx=(8, 0))

        # Preumple din API, dacă există date
        self._prefill_existing()

    # ---------- LOADER ----------
    def _show_loader(self, text="Se scanează CIE..."):
        self._loader = tk.Toplevel(self)
        self._loader.transient(self)
        self._loader.grab_set()
        self._loader.resizable(False, False)
        self._loader.title("")
        try:
            self._loader.iconbitmap(resource_path("assets/waitdocs.ico"))
        except Exception:
            pass
        frm = ttk.Frame(self._loader, padding=16)
        frm.pack()
        ttk.Label(frm, text=text).pack(anchor="w", pady=(0, 8))
        self._loader_bar = ttk.Progressbar(frm, mode="indeterminate", length=240)
        self._loader_bar.pack(fill="x")
        self._loader_bar.start(10)

        self._loader.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - self._loader.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - self._loader.winfo_height()) // 2
        self._loader.geometry(f"+{max(0, x)}+{max(0, y)}")

    def _hide_loader(self):
        try:
            if self._loader_bar:
                self._loader_bar.stop()
        except Exception:
            pass
        try:
            if self._loader:
                self._loader.destroy()
        except Exception:
            pass
        self._loader = None
        self._loader_bar = None

    # ---------- prefill din server ----------
    def _prefill_existing(self):
        """GET /waitdocument/?id=<id> și completează câmpurile dacă există."""
        if not self.api:
            return
        doc_id = self.row_data.get("id")
        if not doc_id:
            return
        try:
            try:
                resp = self.api.request("GET", "/waitdocument/", params={"id": doc_id})
            except TypeError:
                resp = self.api.request("GET", f"/waitdocument/?id={doc_id}")
        except Exception:
            return  # nu stricăm UI-ul dacă nu vine răspuns

        # expira în DD.MM.YYYY
        exp_dmy = _iso_to_dmy(resp.get("expira"))
        # fallback din date.ci
        ci = {}
        try:
            date_obj = resp.get("date")
            if isinstance(date_obj, str):
                date_obj = json.loads(date_obj or "{}")
            ci = (date_obj or {}).get("ci") or {}
            if not exp_dmy and ci:
                y, m, d = (ci.get("an_ex"), ci.get("luna_ex"), ci.get("zi_ex"))
                if y and m and d:
                    exp_dmy = _iso_to_dmy(f"{y}-{str(m).zfill(2)}-{str(d).zfill(2)}")
        except Exception:
            pass
        self.expira_var.set(exp_dmy or "")

        # emis (top-level)
        self.emis_var.set(resp.get("emis") or (ci.get("eliberat") if ci else "").strip())

        # serie / număr
        serie_val = ((ci.get("seria") if ci else "") or "").upper()
        numar_val = _only_digits((ci.get("numarul") if ci else "") or "")
        if not numar_val:
            # derivă din nr top-level (ex: "VN175774")
            nr_top = resp.get("nr") or ""
            m = re.match(r"^\s*([A-Za-z]{1,3})?(\d{3,})\s*$", str(nr_top))
            if m:
                if not serie_val:
                    serie_val = (m.group(1) or "").upper()
                numar_val = m.group(2)
        self.serie_var.set(serie_val)
        self.numar_var.set(numar_val)

        # data eliberării
        data_iso = resp.get("data") or (ci.get("data_emiterii") if ci else "")
        self.data_var.set(_iso_to_dmy(data_iso))

        # observatii
        obs = resp.get("observatii") or ""
        if obs:
            self.obs_txt.delete("1.0", "end")
            self.obs_txt.insert("1.0", str(obs))

        # păstrează și 'date' pentru eventuale alte câmpuri (nume, adresă etc.)
        try:
            self._date_payload = json.loads(resp.get("date")) if isinstance(resp.get("date"), str) else (resp.get("date") or None)
        except Exception:
            self._date_payload = None

    # ---------- map raw -> fields ----------
    def _map_raw_to_fields(self, raw: dict) -> dict:
        # --- helpers ---
        def _split_series_number(doc_number: str):
            if not doc_number:
                return "", ""
            m = re.match(r"^\s*([A-Za-z]{1,3})?\s*([0-9]{3,})\s*$", str(doc_number))
            if m:
                return (m.group(1) or "").upper(), m.group(2)
            letters = "".join(ch for ch in str(doc_number) if ch.isalpha()).upper()
            digits  = "".join(ch for ch in str(doc_number) if ch.isdigit())
            return letters, digits

        def _birth_from_cnp(cnp: str):
            cnp = "".join(ch for ch in (cnp or "") if ch.isdigit())
            if len(cnp) != 13:
                return "", "", "", ""
            s = int(cnp[0])
            yy = int(cnp[1:3]); mm = cnp[3:5]; dd = cnp[5:7]
            if s in (1,2): century = 1900
            elif s in (3,4): century = 1800
            else: century = 2000
            sex = "H" if s in (1,3,5,7) else "F" if s in (2,4,6,8) else ""
            return sex, f"{century+yy}", mm, dd

        # --- nume/cnp/sex/naștere (cheile din read_all) ---
        surname = (raw.get("surname") or "").strip()
        given   = (raw.get("givenName") or "").strip()

        cnp = (raw.get("personal_identification_number") or "").strip()

        sex_raw  = (raw.get("sex") or "").strip().upper()  # 'M'/'F'
        sex_conv = {"M": "H", "F": "F"}.get(sex_raw, "")

        birth_dmy = (raw.get("birthdate") or "").strip()   # DD.MM.YYYY
        an = luna = zi = ""
        if birth_dmy:
            birth_iso = _dmy_to_iso(birth_dmy) or ""
            if birth_iso and len(birth_iso.split("-")) == 3:
                an, luna, zi = birth_iso.split("-")
        if not an:
            sex_cnp, an, luna, zi = _birth_from_cnp(cnp)
            if not sex_conv and sex_cnp:
                sex_conv = sex_cnp

        # --- document ---
        issuer  = (raw.get("issuer") or "").strip()
        doc_no  = (raw.get("document_number") or "").strip()
        serie, numar = _split_series_number(doc_no)

        issuing_dmy = (raw.get("issuing_date") or "").strip()      # DD.MM.YYYY
        expiry_dmy  = (raw.get("expiration_date") or "").strip()   # DD.MM.YYYY
        issuing_iso = _dmy_to_iso(issuing_dmy) or ""
        expiry_iso  = _dmy_to_iso(expiry_dmy) or ""

        an_ex = luna_ex = zi_ex = ""
        if expiry_iso and len(expiry_iso.split("-")) == 3:
            an_ex, luna_ex, zi_ex = expiry_iso.split("-")

        # --- adresă ---
        doms = raw.get("domiciliu_struct") or {}
        dom  = raw.get("domicile") or {}

        judet      = (doms.get("judet") or dom.get("county") or "").strip().upper()
        localitate = (doms.get("localitate") or dom.get("locality") or "").strip().upper()
        adresa2    = (doms.get("rest") or dom.get("street") or dom.get("text") or "").strip()

        # --- observatii ---
        observatii = f"Nume:  {surname}  {given}".strip()

        # --- payload 'date' ---
        date_payload = {
            "nume": surname,
            "prenume": given,
            "cnp": cnp,
            "sex": sex_conv,       # 'H' / 'F'
            "an": an,
            "luna": luna,
            "zi": zi,
            "ci": {
                "seria": serie,
                "numarul": numar,
                "id": f"{serie}{numar}".strip(),
                "an_ex": an_ex,
                "luna_ex": luna_ex,
                "zi_ex": zi_ex,
                "data_emiterii": issuing_iso or "",
                "eliberat": issuer,
            },
            "judet": judet,
            "localitatea": localitate,
            "adresa2": adresa2,
        }

        # --- valori pentru UI ---
        return {
            "emis": issuer,
            "expira": expiry_dmy or (expiry_iso and _iso_to_dmy(expiry_iso)) or "",
            "serie": serie,
            "numar": numar,
            "data": issuing_dmy or (issuing_iso and _iso_to_dmy(issuing_iso)) or "",
            "observatii": observatii,
            "date_payload": date_payload,
        }

    # ---------- scan (with loader + thread) ----------
    def run_scan(self):
        """Dialog PIN + citire card în background + populare câmpuri."""
        pin = prompt_pin_modal(self, "")
        if not pin:
            return

        self.scan_btn.configure(state="disabled")
        self._show_loader("Se scanează CIE...")

        def worker():
            try:
                raw = read_all(pin)                 # citire CIE
                mapped = self._map_raw_to_fields(raw)
                self.after(0, lambda: self._on_scan_done(mapped))
            except Exception as e:
                self.after(0, lambda: self._on_scan_fail(e))

        threading.Thread(target=worker, daemon=True).start()

    def _on_scan_done(self, mapped: dict):
        self._hide_loader()
        self.scan_btn.configure(state="normal")

        self._date_payload = mapped.get("date_payload")
        self.emis_var.set(mapped.get("emis", ""))
        self.expira_var.set(mapped.get("expira", ""))
        self.serie_var.set(mapped.get("serie", ""))
        self.numar_var.set(mapped.get("numar", ""))
        self.data_var.set(mapped.get("data", ""))
        self.obs_txt.delete("1.0", "end")
        if mapped.get("observatii"):
            self.obs_txt.insert("1.0", mapped["observatii"])

        messagebox.showinfo("Scanare reușită",
                            "Câmpurile au fost completate automat din CIE.",
                            parent=self)

    def _on_scan_fail(self, err: Exception):
        self._hide_loader()
        self.scan_btn.configure(state="normal")
        messagebox.showerror("Citire CIE", f"Eroare la citire: {err}", parent=self)

    def _build_date_payload_for_save(self) -> dict:
        """
        Construiește payload-ul 'date' EXACT ca exemplul tău.
        Dacă avem unul din scan, îl folosim și suprascriem doar ci.eliberat/seria/numarul și datele.
        """
        base = json.loads(json.dumps(self._date_payload, ensure_ascii=False)) if self._date_payload else {
            "nume": "",
            "prenume": "",
            "cnp": "",
            "sex": "",
            "an": "",
            "luna": "",
            "zi": "",
            "ci": {
                "seria": "",
                "numarul": "",
                "id": "",
                "an_ex": "",
                "luna_ex": "",
                "zi_ex": "",
                "data_emiterii": "",
                "eliberat": ""
            },
            "judet": "",
            "localitatea": "",
            "adresa2": ""
        }

        emis_txt = (self.emis_var.get() or "").strip()
        serie = (self.serie_var.get() or "").strip().upper()
        numar = _only_digits(self.numar_var.get())
        expira_dmy = (self.expira_var.get() or "").strip()
        data_dmy = (self.data_var.get() or "").strip()

        # părți din expira
        def _parts(dmy: str):
            m = re.match(r"^\s*(\d{2})[.\-/](\d{2})[.\-/](\d{4})\s*$", dmy or "")
            if not m:
                return ("", "", "")
            return (m.group(3), m.group(2), m.group(1))  # (YYYY, MM, DD)

        ex_y, ex_m, ex_d = _parts(expira_dmy)

        base_ci = base.setdefault("ci", {})
        base_ci["seria"] = serie
        base_ci["numarul"] = numar
        base_ci["id"] = f"{serie}{numar}".strip()
        base_ci["an_ex"] = ex_y
        base_ci["luna_ex"] = ex_m
        base_ci["zi_ex"] = ex_d
        base_ci["data_emiterii"] = (_dmy_to_iso(data_dmy) or "")
        base_ci["eliberat"] = emis_txt  # IMPORTANT: emis și în 'date'

        return base

    def save_and_close(self):
        """
        PUT /waitdocument/ cu:
          id, emis(top-level), expira(ISO), data(ISO), nr(doar cifre), observatii, date(JSON-string).
        """
        if not self.api:
            # fallback: doar previzualizare payload
            payload_preview = {
                "id": self.row_data.get("id"),
                "emis": self.emis_var.get(),
                "expira": _dmy_to_iso(self.expira_var.get().strip()),
                "data": _dmy_to_iso(self.data_var.get().strip()),
                "nr": _only_digits(self.numar_var.get()),
                "observatii": self.obs_txt.get("1.0", "end").strip(),
                "date": json.dumps(self._build_date_payload_for_save(), ensure_ascii=False),
            }
            messagebox.showinfo("Preview salvare", json.dumps(payload_preview, ensure_ascii=False, indent=2), parent=self)
            self.destroy()
            return

        try:
            payload_api = {
                "id": int(self.row_data.get("id")),
                "emis": (self.emis_var.get() or "").strip(),                 # top-level
                "expira": _dmy_to_iso(self.expira_var.get().strip()) or None,
                "data": _dmy_to_iso(self.data_var.get().strip()) or None,
                "nr": _only_digits(self.numar_var.get()),
                "observatii": self.obs_txt.get("1.0", "end").strip(),
                "date": json.dumps(self._build_date_payload_for_save(), ensure_ascii=False),
            }

            print("PUT payload:", payload_api)  # debug
            try:
                self.api.request("PUT", "/waitdocument/", json=payload_api)
            except TypeError:
                self.api.request("PUT", "/waitdocument/", data=payload_api)

            messagebox.showinfo("Salvat", "Document actualizat cu succes.", parent=self)
            self.destroy()
            if callable(self.on_saved_reload):
                self.on_saved_reload()
        except ApiError as e:
            messagebox.showerror("Eroare API", str(e), parent=self)
        except Exception as e:
            messagebox.showerror("Eroare", str(e), parent=self)


# ---------------- data client ----------------

class WaitDocsClient:
    """Client simplu pentru endpointul documentescanate/ (stil DataTables)."""
    def __init__(self, api: ApiClient):
        self.api = api
        # cerem și 'file' + 'denumire' pentru preview
        self.cols = ["id", "tip", "subtip", "user_username", "angajat_username", "file", "denumire"]

    def fetch_page(self, page_index=0, page_size=10, search_text=""):
        start = page_index * page_size
        data = {
            "draw": page_index + 1,
            "start": start,
            "length": page_size,
            "search[value]": search_text,
            "order[0][column]": "0",
            "order[0][dir]": "asc",
        }
        for i, c in enumerate(self.cols):
            data[f"columns[{i}][data]"] = c
            data[f"columns[{i}][searchable]"] = "true"
            data[f"columns[{i}][orderable]"] = "true"
            data[f"columns[{i}][search][value]"] = ""

        payload = self.api.request("POST", "/documentescanate/cie/", data=data)
        rows = []
        for row in payload.get("data", []):
            rows.append({
                "id": row.get("id"),
                "tip": row.get("tip") or "",
                "subtip": row.get("subtip") or "",
                "user": row.get("user_username") or row.get("user") or "",
                "angajat": row.get("angajat_username") or row.get("angajat") or "",
                "file": row.get("file") or "",
                "denumire": row.get("denumire") or "",
            })
        return rows, payload.get("recordsFiltered", 0)


# ---------------- main window ----------------

class WaitDocsWindow(ttk.Frame):
    """Fereastra principală: tabel + preview + buton Edit + Logout."""
    def __init__(self, master, api_client: ApiClient, on_logged_out: Optional[Callable[[], None]] = None):
        super().__init__(master, padding=16, style="Main.TFrame")
        self.pack(fill="both", expand=True)
        self.api = api_client
        self.client = WaitDocsClient(self.api)
        self.cur_page = 0
        self.total = 0
        self.search_text = tk.StringVar()
        self._on_logged_out = on_logged_out

        # titlu + căutare + logout
        hdr = ttk.Frame(self, padding=(0, 0, 0, 8), style="Main.TFrame")
        hdr.pack(fill="x")
        ttk.Label(hdr, text="Lista documente în așteptare",
                  style="Heading.TLabel", font=("Segoe UI Semibold", 14)).pack(side="left")

        right_hdr = ttk.Frame(hdr, style="Main.TFrame")
        right_hdr.pack(side="right")
        ttk.Button(right_hdr, text="Logout", command=self.do_logout).pack(side="right", padx=(8, 0))
        ttk.Label(right_hdr, text="Caută", style="Subheading.TLabel").pack(side="right", padx=(0, 6))
        ent = ttk.Entry(right_hdr, textvariable=self.search_text, width=32)
        ent.pack(side="right")
        ent.bind("<Return>", lambda e: self.do_search())

        # layout principal
        content = ttk.Frame(self, style="Main.TFrame")
        content.pack(fill="both", expand=True)
        content.columnconfigure(0, weight=1)
        content.columnconfigure(1, weight=0)
        content.rowconfigure(0, weight=1)

        # stânga: tabel
        left = ttk.Frame(content, style="Main.TFrame")
        left.grid(row=0, column=0, sticky="nsew")
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)

        cols = ("id", "tip", "subtip", "user", "angajat", "fisier")
        self.tree = ttk.Treeview(left, columns=cols, show="headings", selectmode="browse")
        for c, label, w in [
            ("id", "ID", 60), ("tip", "Tip fișier", 110), ("subtip", "Subtip", 110),
            ("user", "User", 220), ("angajat", "Angajat", 320), ("fisier", "Fișier", 240)
        ]:
            self.tree.heading(c, text=label)
            self.tree.column(c, width=w, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscroll=vsb.set)
        vsb.grid(row=0, column=1, sticky="ns")

        # dreapta: preview (card)
        right = ttk.Frame(content, width=360, style="Main.TFrame")
        right.grid(row=0, column=1, sticky="ns", padx=(10, 0))
        right.grid_propagate(False)
        card = ttk.Frame(right, padding=14, style="Card.TFrame")
        card.pack(fill="y", expand=False)

        ttk.Label(card, text="Preview fișier", font=("Segoe UI Semibold", 11)).grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.preview_label = ttk.Label(card)
        self.preview_label.grid(row=1, column=0, sticky="nw")
        self.preview_info = ttk.Label(card, text="", justify="left")
        self.preview_info.grid(row=2, column=0, sticky="w", pady=(6, 8))

        btns_prev = ttk.Frame(card, style="Card.TFrame")
        btns_prev.grid(row=3, column=0, sticky="w")
        ttk.Button(btns_prev, text="Deschide", style="Accent.TButton", command=self.open_selected_file).pack(side="left")
        ttk.Button(btns_prev, text="Descarcă", command=self.save_selected_file_as).pack(side="left", padx=(8, 0))

        # acțiuni
        act = ttk.Frame(self, padding=(0, 10, 0, 0), style="Main.TFrame")
        act.pack(fill="x")
        ttk.Button(act, text="Editare rând selectat", style="Accent.TButton", command=self.edit_selected).pack(side="left")
        ttk.Button(act, text="Reîncarcă", command=lambda: self.load_page(self.cur_page)).pack(side="left", padx=(8, 0))

        # paginație (înapoi stânga, înainte dreapta)
        pag = ttk.Frame(self, style="Main.TFrame")
        pag.pack(fill="x")
        self.page_lbl = ttk.Label(pag, text="", style="Subheading.TLabel")
        self.page_lbl.pack(side="left")
        ttk.Button(pag, text="⟵ Înapoi", command=self.prev_page).pack(side="left")
        ttk.Button(pag, text="Înainte ⟶", style="Accent.TButton", command=self.next_page).pack(side="right", padx=(6, 10))

        # stil minim local (fallback)
        s = ttk.Style(self)
        try:
            s.theme_use("clam")
        except Exception:
            pass
        s.configure(".", font=("Segoe UI", 10))
        s.configure("Treeview.Heading", font=("Segoe UI Semibold", 10))
        s.configure("Treeview", rowheight=26)
        s.configure("TButton", padding=8)

        # zebra rows
        self.tree.tag_configure("odd", background="#ffffff")
        self.tree.tag_configure("even", background="#fafafa")

        # --- preview async: debounce + cache ---
        self._preview_img_ref = None
        self._thumb_cache: dict[str, ImageTk.PhotoImage] = {}
        self._preview_after_id: Optional[str] = None
        self._preview_seq = 0
        self.tree.bind("<<TreeviewSelect>>", self._schedule_preview_for_selected)

        self.load_page(0)

    # ------- logout -------
    def do_logout(self):
        if not messagebox.askyesno("Logout", "Sigur vrei să te delogezi?"):
            return
        try:
            self.api.logout()
        except Exception:
            pass

        # dacă avem un callback configurat din main, îl folosim
        if callable(self._on_logged_out):
            try:
                self._on_logged_out()
                return
            except Exception:
                pass

        # altfel încercăm să încărcăm fereastra de login în același root
        top = self.winfo_toplevel()
        try:
            from login_window import LoginWindow  # tipic: LoginWindow(top, ApiClient())
            for w in top.winfo_children():
                w.destroy()
            api2 = ApiClient()  # client curat, fără tokenuri
            LoginWindow(top, api_client=api2)
        except Exception:
            messagebox.showinfo("Logout", "Te-ai delogat. Redeschide aplicația pentru a te autentifica din nou.")
            top.destroy()

    # ------- internal -------
    def _absolute_url(self, path: str) -> str:
        """Construiește URL absolut pentru fișierul din API."""
        if not path:
            return ""
        p = str(path).replace("\\", "/")
        if p.startswith("http://") or p.startswith("https://"):
            return p
        if p.startswith("/media/") or p.startswith(MEDIA_URL):
            return API_BASE.rstrip("/") + p
        if p.startswith("/"):
            return API_BASE.rstrip("/") + MEDIA_URL.rstrip("/") + p
        return API_BASE.rstrip("/") + MEDIA_URL + p

    def _download_to_temp(self, url: str, show_message: bool = True) -> str | None:
        """Descarcă la un fișier temporar. Dacă show_message=False, nu afișează MessageBox (pt. thread)."""
        if not url:
            return None
        try:
            headers = {}
            if getattr(self.api, "tokens", None):
                headers["Authorization"] = f"Bearer {self.api.tokens.access}"
            r = self.api.session.get(url, headers=headers, timeout=30, stream=True)
            r.raise_for_status()
            suffix = os.path.splitext(urllib.parse.urlparse(url).path)[1] or ""
            fd, tmp_path = tempfile.mkstemp(prefix="wdl_", suffix=suffix)
            with os.fdopen(fd, "wb") as f:
                for chunk in r.iter_content(8192):
                    f.write(chunk)
            return tmp_path
        except Exception as e:
            if show_message:
                messagebox.showerror("Descărcare eșuată", str(e))
            return None

    # ------- actions -------
    def do_search(self):
        self.cur_page = 0
        self.load_page(0)

    def load_page(self, page_index):
        try:
            rows, total = self.client.fetch_page(page_index, PAGE_SIZE, self.search_text.get().strip())
        except ApiError as e:
            messagebox.showerror("Eroare API", str(e))
            return

        self.tree.delete(*self.tree.get_children())
        self._rows_cache = rows

        for i, r in enumerate(rows):
            fname = r.get("denumire") or (r.get("file").split("/")[-1] if r.get("file") else "")
            tag = "even" if i % 2 else "odd"
            self.tree.insert(
                "",
                "end",
                values=(r["id"], r["tip"], r["subtip"], r["user"], r["angajat"], fname),
                tags=(tag,)
            )

        self.cur_page = page_index
        self.total = total
        pages = max(1, math.ceil(self.total / PAGE_SIZE))
        self.page_lbl.config(text=f"Pagina {self.cur_page + 1}/{pages} — {self.total} rezultate")

        # curăță preview-ul la reload
        self.preview_label.configure(image="", text="")
        self.preview_label.image = None
        self.preview_info.configure(text="")
        self._preview_img_ref = None

    def next_page(self):
        pages = max(1, math.ceil(self.total / PAGE_SIZE))
        if self.cur_page + 1 < pages:
            self.load_page(self.cur_page + 1)

    def prev_page(self):
        if self.cur_page > 0:
            self.load_page(self.cur_page - 1)

    def get_selected_row(self, full: bool = False):
        sel = self.tree.selection()
        if not sel:
            return None
        idx = self.tree.index(sel[0])
        if full and hasattr(self, "_rows_cache") and 0 <= idx < len(self._rows_cache):
            return self._rows_cache[idx]
        v = self.tree.item(sel[0], "values")
        return {
            "id": v[0],
            "tip": v[1],
            "subtip": v[2],
            "user": v[3],
            "angajat": v[4],
            "fisier": v[5] if len(v) > 5 else ""
        }

    def edit_selected(self):
        row = self.get_selected_row(full=True)
        if not row:
            messagebox.showwarning("Selectează un rând", "Te rog selectează un rând din tabel.")
            return

        def after_save_reload():
            self.load_page(self.cur_page)

        EditDialog(self, row, on_saved_reload=after_save_reload, api=self.api)

    # ------- preview / open / save -------
    # (ASYNC) debounce + background thread + cache
    def _schedule_preview_for_selected(self, *_):
        if self._preview_after_id:
            try:
                self.after_cancel(self._preview_after_id)
            except Exception:
                pass
        self._preview_seq += 1
        seq = self._preview_seq
        self._preview_after_id = self.after(150, lambda: self._load_preview_async(seq))

    def _load_preview_async(self, seq):
        # dacă între timp s-a schimbat selecția, renunțăm
        if seq != self._preview_seq:
            return

        row = self.get_selected_row(full=True)
        self._preview_img_ref = None
        self.preview_label.configure(image="", text="")
        self.preview_label.image = None
        self.preview_info.configure(text="")

        if not row:
            return

        url = self._absolute_url(row.get("file", ""))
        fname = row.get("denumire") or (row.get("file", "").split("/")[-1] if row.get("file") else "")
        if not url:
            self.preview_info.configure(text="Nu există fișier atașat.")
            return

        ext = (os.path.splitext(url)[1] or "").lower()
        self.preview_info.configure(text=f"{fname}\n{url}")

        if ext in [".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"]:
            # cache: dacă avem deja thumbnail, afișăm instant
            if url in self._thumb_cache:
                tkimg = self._thumb_cache[url]
                self.preview_label.configure(image=tkimg, text="")
                self.preview_label.image = tkimg
                self._preview_img_ref = tkimg
                return

            self.preview_label.configure(text="Se încarcă imaginea…")

            def worker(u=url, seq_local=seq):
                tmp = self._download_to_temp(u, show_message=False)
                if not tmp:
                    self.after(0, lambda: self._apply_preview_error(seq_local, "Descărcare eșuată"))
                    return
                try:
                    img = Image.open(tmp)
                    img.thumbnail((300, 300))
                except Exception as e:
                    self.after(0, lambda: self._apply_preview_error(seq_local, f"Nu pot încărca imaginea.\n{e}"))
                    return

                def apply():
                    if seq_local != self._preview_seq:
                        return
                    tkimg = ImageTk.PhotoImage(img)
                    self._thumb_cache[u] = tkimg
                    self.preview_label.configure(image=tkimg, text="")
                    self.preview_label.image = tkimg
                    self._preview_img_ref = tkimg

                self.after(0, apply)

            threading.Thread(target=worker, daemon=True).start()

        elif ext == ".pdf":
            self.preview_label.configure(text="PDF atașat (apasă „Deschide”).")
        else:
            self.preview_label.configure(text=f"Fișier atașat: {ext or 'necunoscut'}")

    def _apply_preview_error(self, seq, msg: str):
        if seq != self._preview_seq:
            return
        self.preview_label.configure(text=msg)

    def open_selected_file(self):
        row = self.get_selected_row(full=True)
        if not row or not row.get("file"):
            messagebox.showinfo("Deschidere fișier", "Nu există fișier.")
            return
        url = self._absolute_url(row["file"])
        local = self._download_to_temp(url)
        if not local:
            return
        try:
            if os.name == "nt":
                os.startfile(local)  # Windows
            else:
                import subprocess
                subprocess.Popen(["xdg-open", local])
        except Exception as e:
            messagebox.showerror("Deschidere eșuată", str(e))

    def save_selected_file_as(self):
        from tkinter import filedialog
        row = self.get_selected_row(full=True)
        if not row or not row.get("file"):
            messagebox.showinfo("Descarcă fișier", "Nu există fișier.")
            return
        url = self._absolute_url(row["file"])
        local = self._download_to_temp(url)
        if not local:
            return
        fname = row.get("denumire") or (row["file"].split("/")[-1])
        dest = filedialog.asksaveasfilename(initialfile=fname)
        if not dest:
            return
        try:
            with open(local, "rb") as src, open(dest, "wb") as dst:
                dst.write(src.read())
            messagebox.showinfo("Salvat", f"Fișierul a fost salvat:\n{dest}")
        except Exception as e:
            messagebox.showerror("Salvare eșuată", str(e))
