# cie_core_common.py
import re
import os, struct, sys
from pathlib import Path

# --- PKCS#11 (certificat) ---
from PyKCS11 import PyKCS11Lib
from PyKCS11.LowLevel import (
    CKF_SERIAL_SESSION, CKF_RW_SESSION,
    CKA_CLASS, CKA_VALUE, CKO_CERTIFICATE
)
from cryptography import x509
from cryptography.x509.oid import NameOID, ObjectIdentifier

# --- PC/SC (APDU) ---
from smartcard.System import readers

# =================== SETĂRI ===================
DLL_DIR = r"C:\Program Files\IDEMIA\IDPlugClassic\DLLs"
MODULE = os.path.join(DLL_DIR, "idplug-pkcs11.dll")

AID_EDATA = [0xE8,0x28,0xBD,0x08,0x0F,0xA0,0x00,0x00,0x01,0x67,0x45,0x44,0x41,0x54,0x41]  # "EDATA"
P2_SELECT = 0x0C  # by DF name/current

# OID subiect
OID_STREET   = ObjectIdentifier("2.5.4.9")
OID_POSTAL   = ObjectIdentifier("2.5.4.17")
OID_LOCALITY = NameOID.LOCALITY_NAME
OID_STATE    = NameOID.STATE_OR_PROVINCE_NAME
OID_COUNTRY  = NameOID.COUNTRY_NAME

# =================== UTILE ===================
def add_dll_dir(path: str):
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(path)
    else:
        os.environ["PATH"] = path + os.pathsep + os.environ.get("PATH", "")

def subject_get(subject, oid):
    try:
        vals = subject.get_attributes_for_oid(oid)
        return vals[0].value if vals else None
    except Exception:
        return None

def parse_identity_from_cert(cert: x509.Certificate):
    s = cert.subject
    return {
        "surname":       subject_get(s, NameOID.SURNAME),
        "givenName":     subject_get(s, NameOID.GIVEN_NAME),
        "commonName":    subject_get(s, NameOID.COMMON_NAME),
        "serialNumber":  subject_get(s, NameOID.SERIAL_NUMBER),
        "streetAddress": subject_get(s, OID_STREET),
        "postalCode":    subject_get(s, OID_POSTAL),
        "locality":      subject_get(s, OID_LOCALITY),
        "state":         subject_get(s, OID_STATE),
        "country":       subject_get(s, OID_COUNTRY),
    }

def dump_hex(b: bytes) -> str:
    return " ".join(f"{x:02X}" for x in b)

def format_date_dmy(s: str | None):
    """
    Primește un string cu 8 cifre în format DDMMYYYY și întoarce DD.MM.YYYY.
    Dacă nu se potrivește, întoarce None sau valoarea brută când nu e numerică.
    """
    if not s:
        return None
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) != 8:
        return s if s.strip() else None
    d = int(digits[0:2])
    m = int(digits[2:4])
    y = int(digits[4:8])
    if 1 <= d <= 31 and 1 <= m <= 12 and 1900 <= y <= 2100:
        return f"{d:02d}.{m:02d}.{y:04d}"
    return s

# --- TLV helpers ---
def _read_len(buf: bytes, i: int):
    """ întoarce (lungime, bytes_consumed) pornind de la index i pentru câmpul Length """
    L = buf[i]
    if L & 0x80:
        n = L & 0x7F
        if n == 0:
            raise ValueError("Long form length indefinit nu este suportat")
        val = int.from_bytes(buf[i+1:i+1+n], "big")
        return val, 1 + n
    else:
        return L, 1

def parse_children_tlv(seq: bytes):
    """Primește conținutul interior (copiii) și returnează dict {tag: value} pentru tag-uri primare 0x80.."""
    out = {}
    i = 0
    while i < len(seq):
        tag = seq[i]
        i += 1
        if i >= len(seq):
            break
        L, used = _read_len(seq, i)
        i += used
        val = seq[i:i+L]
        i += L
        out[tag] = val
    return out

def parse_ef0101(raw: bytes):
    """
    EF 0101 = date persoană:
      0x80=SURN, 0x81=GIVEN, 0x82=SEX, 0x83=BIRTH(DDMMYYYY), 0x84=CNP, 0x85=CETĂȚENIE
    """
    if not raw:
        return {}
    b = memoryview(raw)
    if b[0] != 0x30:
        return {}
    _, len_used = _read_len(b.tobytes(), 1)
    start_children = 1 + len_used
    children = parse_children_tlv(b[start_children:].tobytes())

    def get_txt(tag):
        return children.get(tag, b"").decode("utf-8", errors="ignore").strip() or None

    sex = get_txt(0x82)
    if sex not in ("M", "F"):
        sex = None

    birth = format_date_dmy(get_txt(0x83))

    cnp = get_txt(0x84)
    if not (cnp and cnp.isdigit() and len(cnp) == 13):
        cnp = None

    return {
        "surname":      get_txt(0x80),
        "givenName":    get_txt(0x81),
        "sex":          sex,
        "birthdate":    birth,
        "cnp":          cnp,
        "citizenship":  get_txt(0x85),
        "_raw_children": {f"{k:02X}": children[k].decode("utf-8", "ignore") for k in children},
    }

def parse_ef0102_or_addr(raw: bytes):
    """
    EF 0102/0103/0106 – Domiciliu.
      - Suportă TLV și text brut (ex: "Jud.BC Sat.Sascut (Com.Sascut), Str.X, nr.Y ...").
      - Returnează:
          judet  -> cod județ (dacă există sau ghicit din text)
          localitate -> UPPERCASE: "<TIP_PRINCIPAL> <NUME>[, COM. <NUME_COM>]"
          rest  -> "Str.X, nr.Y, bl..., sc..., et..., ap..."
    Tag-uri TLV (unde există): 0x80=Stradă (uneori text „plin”), 0x81=Localitate, 0x82=Județ, 0x83=Cod poștal, 0x84=Țara
    """
    import re

    def empty():
        return {
            "street": None, "locality": None, "county": None,
            "postal_code": None, "country": None,
            "text": None, "raw_text": None, "_raw_children": None,
            "judet": None, "localitate": None, "rest": None,
        }

    if not raw:
        return empty()

    raw_text = raw.decode("utf-8", errors="ignore") if raw else ""

    def clean(s: str) -> str:
        s = re.sub(r'[\x00-\x1F]+', ' ', s or '')
        s = re.sub(r'\s+', ' ', s).strip()
        return s

    RAW = clean(raw_text)

    # --- regex uzuale ---
    RX_JUDET = re.compile(r'\bJud\.?\s*([A-Z]{1,3})\b', re.I)

    # tip principal: Mun / Ors / Oras / Oraș / Com / Sat
    RX_LOCAL_MAIN = re.compile(
        r'\b(?:(Mun|Ors|Or[aă]s|Ora[sș]|Com|Sat)\.?)\s*([A-ZĂÂÎȘȚ][\w\.\- ]+?)\b',
        re.I
    )
    # conținut în paranteză: de ex. (Com.Sascut) sau (Com. Sascut)
    RX_PAREN = re.compile(r'\(\s*(?:Com)\.?\s*([^)]+?)\s*\)', re.I)

    RX_STREET_START = re.compile(
        r'(Str(?:\.|ada)?|Bd(?:\.|ul)?|Bulevardul|Aleea|Calea|Sos(?:\.|eaua)?|Șos(?:\.|eaua)?)\b',
        re.I
    )
    RX_REST_FULL = re.compile(
        r'\b(?:Str(?:\.|ada)?|Bd(?:\.|ul)?|Bulevardul|Aleea|Calea|Sos(?:\.|eaua)?|Șos(?:\.|eaua)?)\s*'
        r'([^\d,][^,]*)'                                  # nume stradă
        r'(?:,\s*nr\.?\s*([A-Za-z0-9\-\/]+))?'            # nr
        r'(?:,\s*(?:bl(?:oc)?\.?\s*([A-Za-z0-9\-\/]+)))?' # bl
        r'(?:,\s*(?:sc(?:\.|ara)?\.?\s*([A-Za-z0-9\-\/]+)))?' # sc
        r'(?:,\s*(?:et(?:\.|aj)?\.?\s*([A-Za-z0-9\-\/]+)))?'  # et
        r'(?:,\s*(?:ap(?:\.|art)?\.?\s*([A-Za-z0-9\-\/]+)))?',# ap
        re.I
    )
    RX_STREET_CHUNK = re.compile(
        r'\b(?:Str(?:\.|ada)?|Bd(?:\.|ul)?|Bulevardul|Aleea|Calea|Sos(?:\.|eaua)?|Șos(?:\.|eaua)?)\b[^,\n\r]*',
        re.I
    )
    RX_NR = re.compile(r'\bnr\.?\s*\w+', re.I)

    # --- mapări tip -> textul cerut în UPPERCASE ---
    # Notă: după exemplul tău, „SAT” fără punct; „COM.” cu punct.
    def kind_to_label(kind_raw: str) -> str:
        if not kind_raw:
            return ""
        kl = kind_raw.lower()
        if kl.startswith("mun"):
            return "MUN."
        if kl.startswith("ors"):
            return "ORS."
        if kl.startswith("ora"):  # Oras / Oraș
            return "ORAS."
        if kl.startswith("com"):
            return "COM."
        if kl.startswith("sat"):
            return "SAT"
        return kind_raw.upper()

    # --- extrageri din „textul plin” ---
    def build_rest(txt: str) -> str | None:
        txt = clean(txt)
        mstart = RX_STREET_START.search(txt)
        if mstart:
            txt = txt[mstart.start():]
        m = RX_REST_FULL.search(txt)
        if m:
            st, nr, bl, sc, et, ap = m.groups()
            st_clean = clean(st)
            part0 = st_clean if re.search(r'\bStr', st_clean, re.I) else f"Str.{st_clean}"
            parts = [part0]
            if nr: parts.append(f"nr.{nr}")
            if bl: parts.append(f"bl.{bl}")
            if sc: parts.append(f"sc.{sc}")
            if et: parts.append(f"et.{et}")
            if ap: parts.append(f"ap.{ap}")
            return ", ".join(parts)
        m2 = RX_STREET_CHUNK.search(txt)
        if m2:
            chunk = clean(m2.group(0))
            after = txt[m2.end():]
            mnr = RX_NR.search(after)
            parts = [chunk]
            if mnr and mnr.start() < 20:
                parts.append(clean(mnr.group(0)))
            return ", ".join(parts) if parts else None
        return None

    def extract_structured_from_text(txt: str):
        txt = clean(txt)

        # JUD
        jud = None
        mj = RX_JUDET.search(txt)
        if mj:
            jud = (mj.group(1) or "").upper()

        # tip + nume principal
        local_kind, local_name = None, None
        ml = RX_LOCAL_MAIN.search(txt)
        if ml:
            local_kind = ml.group(1)
            local_name = clean(ml.group(2))

        # comună din paranteză (opțional)
        com_name = None
        mp = RX_PAREN.search(txt)
        if mp:
            com_name = clean(mp.group(1))

        # Formatează localitatea în UPPERCASE:
        #   "<TIP_PRINCIPAL> <NUME>[, COM. <NUME_COM>]"
        local_parts = []
        if local_kind and local_name:
            local_parts.append(f"{kind_to_label(local_kind)} {local_name.upper()}")
        elif local_name:
            local_parts.append(local_name.upper())

        if com_name:
            # pref. fix: „COM.” + numele comunei
            local_parts.append(f"COM. {com_name.upper()}")

        local_full = ", ".join(local_parts) if local_parts else None

        # rest (strada etc.)
        rest = build_rest(txt)
        return jud, local_full, rest

    # --- ramura: non-TLV (text brut) ---
    b = memoryview(raw)
    if b[0] != 0x30:
        jud, local_full, rest = extract_structured_from_text(RAW)
        return {
            "street": None, "locality": local_full, "county": jud,
            "postal_code": None, "country": None,
            "text": RAW or None, "raw_text": raw_text, "_raw_children": None,
            "judet": jud, "localitate": local_full, "rest": rest
        }

    # --- ramura: TLV ---
    _, len_used = _read_len(b.tobytes(), 1)
    start_children = 1 + len_used
    children = parse_children_tlv(b[start_children:].tobytes())

    def get_txt(tag):
        try:
            return children.get(tag, b"").decode("utf-8", "ignore").strip() or None
        except Exception:
            return None

    street   = get_txt(0x80)      # uneori „plin”: Jud./Sat./(Com.) Str...
    locality_tlv = get_txt(0x81)  # de regulă doar numele (fără tip)
    county   = get_txt(0x82)      # adesea cod județ (ex: "VN")
    postal   = get_txt(0x83)
    country  = get_txt(0x84)

    parts = [p for p in [street, locality_tlv, county, postal, country] if p]
    text_comp = ", ".join(parts) if parts else (RAW or None)

    # „supă de text” pentru extrageri (prinde și parantezele dacă 0x80 conține tot)
    soup = " ".join(p for p in [RAW, text_comp or "", street or ""] if p)
    jud_guess, local_full_guess, rest_guess = extract_structured_from_text(soup)

    # preferințe: judet din TLV sau ghicit; localitate (cu prefix + (Com. ...)) din supă, altfel fallback la 0x81 UPPER
    jud_final = (county or jud_guess or None)
    local_final = (local_full_guess or (locality_tlv.upper() if locality_tlv else None))

    # rest: din 0x80 dacă e „plin”, altfel din ghicit
    rest_from_street = None
    if street:
        rest_from_street = build_rest(street)
    rest_final = rest_from_street or rest_guess

    return {
        "street": street,
        "locality": local_final,         # <<< UPPERCASE, ex: "SAT SASCUT, COM. SASCUT"
        "county": jud_final,             # cod județ
        "postal_code": postal,
        "country": country,
        "text": text_comp,
        "raw_text": raw_text,
        "_raw_children": {f"{k:02X}": children[k].decode("utf-8", "ignore") for k in children},

        # câmpuri pentru UI:
        "judet": jud_final.upper() if jud_final else None,
        "localitate": local_final.upper() if local_final else None,       # idem cu 'locality'
        "rest": rest_final.upper() if rest_final else None,              # ex: "Str.Doctor Lucica Vultur, nr.1, bl.1, sc.A, et.3, ap.5"
    }





def parse_ef0104(raw: bytes):
    """
    EF 0104 = date document:
      0x80=DOC NR, 0x81=ISSUING(DDMMYYYY), 0x82=EXPIRY(DDMMYYYY), 0x83=ISSUER (text)
    """
    if not raw:
        return {}
    b = memoryview(raw)
    if b[0] != 0x30:
        return {}
    _, len_used = _read_len(b.tobytes(), 1)
    start_children = 1 + len_used
    children = parse_children_tlv(b[start_children:].tobytes())

    def get_txt(tag):
        return children.get(tag, b"").decode("utf-8", errors="ignore").strip() or None

    return {
        "document_number": get_txt(0x80),
        "issuing_date":    format_date_dmy(get_txt(0x81)),
        "expiry_date":     format_date_dmy(get_txt(0x82)),
        "issuer":          get_txt(0x83),
        "_raw_children":   {f"{k:02X}": children[k].decode("utf-8", "ignore") for k in children},
    }

# =================== PC/SC APDU ===================
def connect_pcsc():
    rlist = readers()
    if not rlist:
        raise RuntimeError("Nu am găsit niciun cititor PC/SC.")
    print(f"Cititor: {rlist[0]}")
    conn = rlist[0].createConnection()
    conn.connect()
    return conn

def tx(conn, apdu, label):
    data, sw1, sw2 = conn.transmit(apdu)
    sw = (sw1 << 8) | sw2
    print(f"[APDU] {label}: {dump_hex(bytes(apdu))} -> SW={sw:04X} len={len(data)}")
    return bytes(data), sw

def select_aid_edata(conn):
    apdu = [0x00,0xA4,0x04,0x0C, len(AID_EDATA)] + AID_EDATA
    _, sw = tx(conn, apdu, "SELECT AID EDATA")
    return sw == 0x9000

def verify_pin(conn, pin: str, ref: int = 0x03):
    # pin padding la 12 bytes cu 0xFF
    pb = pin.encode("ascii")[:12]
    pb = pb + b"\xFF"*(12-len(pb))
    apdu = [0x00,0x20,0x00,ref,0x0C] + list(pb)
    _, sw = tx(conn, apdu, f"VERIFY PIN ref=0x{ref:02X}")
    return sw == 0x9000

def select_ef(conn, fid: int):
    p1, p2 = 0x02, P2_SELECT
    fid_hi, fid_lo = (fid >> 8) & 0xFF, fid & 0xFF
    apdu = [0x00,0xA4,p1,p2,0x02,fid_hi,fid_lo]
    _, sw = tx(conn, apdu, f"SELECT EF {fid:04X}")
    return sw

def read_binary_full(conn):
    # READ BINARY cu Le=00 (cere tot fișierul)
    apdu = [0x00,0xB0,0x00,0x00,0x00]
    data, sw = tx(conn, apdu, "READ BINARY")
    return data if sw == 0x9000 else None

def read_ef(conn, fid: int):
    sw = select_ef(conn, fid)
    if sw == 0x9000 or (sw & 0xFF00) == 0x6200:  # 62xx = warning, dar EF e selectat
        return read_binary_full(conn)
    return None

# =================== PKCS#11 (certificat) ===================
def read_identity_cert_via_pkcs11(pin: str):
    if not Path(MODULE).exists():
        raise FileNotFoundError(f"Nu găsesc DLL PKCS#11 la: {MODULE}")
    add_dll_dir(DLL_DIR)

    lib = PyKCS11Lib()
    lib.load(MODULE)
    slots = lib.getSlotList(tokenPresent=True)
    if not slots:
        raise RuntimeError("Nu s-a detectat cardul.")
    slot = slots[0]
    sess = lib.openSession(slot, CKF_SERIAL_SESSION | CKF_RW_SESSION)
    identitate = None
    try:
        sess.login(pin)
        objs = sess.findObjects([(CKA_CLASS, CKO_CERTIFICATE)])
        for o in objs:
            der = sess.getAttributeValue(o, [CKA_VALUE], allAsBinary=True)[0]
            cert = x509.load_der_x509_certificate(bytes(der))
            info = parse_identity_from_cert(cert)
            if info.get("serialNumber") and str(info["serialNumber"]).isdigit():
                identitate = info
                break
        if identitate is None and objs:
            der = sess.getAttributeValue(objs[0], [CKA_VALUE], allAsBinary=True)[0]
            cert = x509.load_der_x509_certificate(bytes(der))
            identitate = parse_identity_from_cert(cert)
    finally:
        try:
            sess.logout()
        except Exception:
            pass
        sess.closeSession()
    return identitate