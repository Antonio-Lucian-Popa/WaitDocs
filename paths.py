# paths.py (recomandat)
import os, sys

def resource_path(relpath: str) -> str:
    """
    Returnează calea corectă către o resursă (ex: assets/waitdocs.ico),
    atât în dezvoltare, cât și în exe-ul PyInstaller.
    """
    base = getattr(sys, "_MEIPASS", os.path.abspath("."))
    return os.path.join(base, relpath)
