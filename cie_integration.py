# cie_integration.py
from __future__ import annotations
import re
from typing import Optional, Dict, Any, Tuple
import tkinter as tk
from tkinter import ttk, messagebox
from cie_reader_core import read_all


class CIEReadError(Exception):
    pass


# ---------- UI: dialog PIN ----------
def prompt_pin_modal(parent: tk.Misc, default: str = "") -> Optional[str]:
    dlg = tk.Toplevel(parent)
    dlg.title("CIE PIN")
    dlg.resizable(False, False)
    dlg.transient(parent)
    dlg.grab_set()

    frm = ttk.Frame(dlg, padding=16)
    frm.grid(sticky="nsew")
    dlg.columnconfigure(0, weight=1)
    dlg.rowconfigure(0, weight=1)

    ttk.Label(frm, text="Introduceți PIN-ul CIE", font=("Segoe UI", 11, "bold")).grid(
        row=0, column=0, columnspan=2, sticky="w", pady=(0, 10)
    )

    ttk.Label(frm, text="PIN").grid(row=1, column=0, sticky="w", padx=(0, 10))
    pin_var = tk.StringVar(value=default)
    ent = ttk.Entry(frm, textvariable=pin_var, show="•", width=24)
    ent.grid(row=1, column=1, sticky="ew")
    frm.columnconfigure(1, weight=1)
    ent.focus_set()

    show_var = tk.BooleanVar(value=False)
    ttk.Checkbutton(frm, text="Arată", variable=show_var,
                    command=lambda: ent.config(show="" if show_var.get() else "•")
                    ).grid(row=2, column=1, sticky="w", pady=(6, 10))

    result = {"pin": None}
    def ok():
        p = (pin_var.get() or "").strip()
        if not p:
            messagebox.showwarning("PIN lipsă", "Introduceți PIN-ul.", parent=dlg)
            return
        result["pin"] = p
        dlg.destroy()

    def cancel():
        result["pin"] = None
        dlg.destroy()

    btns = ttk.Frame(frm); btns.grid(row=3, column=0, columnspan=2, sticky="e")
    ttk.Button(btns, text="Continuă", command=ok).pack(side="left")
    ttk.Button(btns, text="Renunță", command=cancel).pack(side="left", padx=(8, 0))

    dlg.bind("<Return>", lambda e: ok())
    dlg.bind("<Escape>", lambda e: cancel())

    dlg.update_idletasks()
    x = parent.winfo_rootx() + (parent.winfo_width() - dlg.winfo_width()) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - dlg.winfo_height()) // 2
    dlg.geometry(f"+{max(x,0)}+{max(y,0)}")

    dlg.wait_window()
    return result["pin"]


# ---------- helpers ----------
def _dmy_to_iso(dmy: str | None) -> str | None:
    if not dmy:
        return None
    m = re.match(r"^\s*(\d{2})[.\-\/](\d{2})[.\-\/](\d{4})\s*$", str(dmy))
    if not m:
        return dmy
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"

def _iso_to_dmy(iso: str | None) -> str:
    if not iso:
        return ""
    m = re.match(r"^\s*(\d{4})-(\d{2})-(\d{2})", str(iso))
    return f"{m.group(3)}.{m.group(2)}.{m.group(1)}" if m else str(iso or "")

def _digits(s: str | None) -> str:
    return "".join(ch for ch in (s or "") if ch.isdigit())

def _birth_from_cnp(cnp: str) -> Tuple[str, str, str, str]:
    """
    din CNP -> (sex(H/F), YYYY, MM, DD) sau gol.
    Sex: 1/3/5/7 -> H; 2/4/6/8 -> F.
    """
    cnp = _digits(cnp)
    if len(cnp) != 13:
        return "", "", "", ""
    s = int(cnp[0])
    yy = int(cnp[1:3]); mm = cnp[3:5]; dd = cnp[5:7]
    if s in (1, 2): century = 1900
    elif s in (3, 4): century = 1800
    else: century = 2000
    year_full = f"{century + yy}"
    sex = "H" if s in (1,3,5,7) else "F" if s in (2,4,6,8) else ""
    return sex, year_full, mm, dd

def _split_series_number(doc_number: str) -> Tuple[str, str]:
    """VN1007098 -> ('VN','1007098'); 'VN 1007098' -> idem; '123456' -> ('','123456')."""
    if not doc_number:
        return "", ""
    m = re.match(r"^\s*([A-Za-z]{1,3})?\s*([0-9]{3,})\s*$", str(doc_number))
    if m:
        return (m.group(1) or "").upper(), m.group(2)
    letters = "".join(ch for ch in doc_number if ch.isalpha()).upper()
    digits = "".join(ch for ch in doc_number if ch.isdigit())
    return letters, digits


# ---------- main ----------
def scan_cie_and_map(parent: tk.Misc) -> Optional[Dict[str, Any]]:
    """
    Citește CIE și întoarce:
      {
        emis, expira(DMY), serie, numar, data(DMY), observatii,
        date_payload: {... exact schema ta ...}
      }
    """
    pin = prompt_pin_modal(parent, "")
    if not pin:
        return None

    try:
        raw = read_all(pin)  # folosește exact structura din cie_reader_core.py
    except Exception as e:
        raise CIEReadError(f"Eroare la citire CIE: {e}")

    # --- nume/prenume ---
    surname = (raw.get("surname") or "").strip()
    given   = (raw.get("givenName") or "").strip()

    # --- CNP / sex / naștere ---
    cnp = (raw.get("personal_identification_number") or "").strip()
    sex_raw = (raw.get("sex") or "").strip().upper()  # 'M' / 'F'
    sex_conv = {"M": "H", "F": "F"}.get(sex_raw, "")

    birth_dmy = (raw.get("birthdate") or "").strip()  # DD.MM.YYYY din parser
    birth_iso = _dmy_to_iso(birth_dmy) or ""
    if birth_iso:
        y_b, m_b, d_b = birth_iso.split("-")
    else:
        # fallback din CNP
        sex_cnp, y_b, m_b, d_b = _birth_from_cnp(cnp)
        if not sex_conv:
            sex_conv = sex_cnp

    # --- document ---
    issuer   = (raw.get("issuer") or "").strip()
    doc_no   = (raw.get("document_number") or "").strip()
    serie, numar = _split_series_number(doc_no)

    issuing_dmy  = (raw.get("issuing_date") or "").strip()      # DD.MM.YYYY
    expiry_dmy   = (raw.get("expiration_date") or "").strip()   # DD.MM.YYYY
    issuing_iso  = _dmy_to_iso(issuing_dmy) or ""
    expiry_iso   = _dmy_to_iso(expiry_dmy) or ""

    ex_y = ex_m = ex_d = ""
    if expiry_iso:
        ex_y, ex_m, ex_d = expiry_iso.split("-")

    # --- domiciliu / adresă ---
    doms = raw.get("domiciliu_struct") or {}
    dom  = raw.get("domicile") or {}

    judet       = (doms.get("judet") or dom.get("county") or "").strip().upper()
    localitate  = (doms.get("localitate") or dom.get("locality") or "").strip().upper()
    # preferă 'rest' (structurat) ; dacă lipsește, încearcă 'street' sau 'text'
    adresa2     = (doms.get("rest") or dom.get("street") or dom.get("text") or "").strip()

    # --- observații simple ---
    name_line = " ".join(p for p in [surname, given] if p).strip()
    observatii = f"Nume: {name_line}" if name_line else ""

    # --- payload 'date' exact pe schema ta ---
    date_payload = {
        "nume": surname,
        "prenume": given,
        "cnp": cnp,
        "sex": sex_conv,        # 'H' / 'F'
        "an": y_b or "",
        "luna": m_b or "",
        "zi": d_b or "",
        "ci": {
            "seria": serie,
            "numarul": numar,
            "id": f"{serie}{numar}",
            "an_ex": ex_y,
            "luna_ex": ex_m,
            "zi_ex": ex_d,
            "data_emiterii": issuing_iso or "",   # ISO
            "eliberat": issuer
        },
        "judet": judet,
        "localitatea": localitate,
        "adresa2": adresa2
    }

    # --- mapare pentru UI ---
    mapped = {
        "emis": issuer,
        "expira": expiry_dmy if expiry_dmy else _iso_to_dmy(expiry_iso),
        "serie": serie,
        "numar": numar,
        "data": issuing_dmy if issuing_dmy else _iso_to_dmy(issuing_iso),
        "observatii": observatii,
        "date_payload": date_payload,
    }
    return mapped
