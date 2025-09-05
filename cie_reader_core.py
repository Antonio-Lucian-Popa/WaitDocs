# cie_reader_core.py
from cie_core_common import (
    connect_pcsc, select_aid_edata, verify_pin, read_ef,
    parse_ef0101, parse_ef0102_or_addr, parse_ef0104,
    read_identity_cert_via_pkcs11
)

def read_all(pin: str) -> dict:
    """Rulează citirea și întoarce dict-ul cu toate câmpurile."""
    # 1) certificat (opțional)
    try:
        identity_from_certificate = read_identity_cert_via_pkcs11(pin)
    except Exception:
        identity_from_certificate = None

    # 2) APDU
    conn = connect_pcsc()
    try:
        if not select_aid_edata(conn):
            raise RuntimeError("SELECT AID EDATA a eșuat")

        # e ok dacă verify pin eșuează (unele EF-uri se citesc și fără)
        verify_pin(conn, pin, ref=0x03)

        raw_0101 = read_ef(conn, 0x0101)
        raw_0104 = read_ef(conn, 0x0104)

        # adresă (depinde de generație)
        raw_addr, addr_fid = None, None
        for fid in (0x0106, 0x0103, 0x0102):
            raw_addr = read_ef(conn, fid)
            if raw_addr:
                addr_fid = fid
                break
    finally:
        try:
            conn.disconnect()
        except Exception:
            pass

    id1  = parse_ef0101(raw_0101 or b"")
    id4  = parse_ef0104(raw_0104 or b"")
    addr = parse_ef0102_or_addr(raw_addr or b"")

    return {
        "identity_from_certificate": identity_from_certificate,
        "personal_identification_number": id1.get("cnp"),
        "birthdate": id1.get("birthdate"),
        "sex": id1.get("sex"),
        "issuer": id4.get("issuer"),
        "document_number": id4.get("document_number"),
        "issuing_date": id4.get("issuing_date"),
        "expiration_date": id4.get("expiry_date"),
        "surname": id1.get("surname"),
        "givenName": id1.get("givenName"),
        "domicile": {
            "fid_used": f"0x{addr_fid:04X}" if addr_fid else None,
            "street": addr.get("street"),
            "locality": addr.get("locality"),
            "county": addr.get("county"),
            "postal_code": addr.get("postal_code"),
            "country": addr.get("country"),
            "text": addr.get("text"),
        },
        "domiciliu_struct": {
            "judet": addr.get("judet"),  # ex: "VN"
            "localitate": addr.get("localitate"),  # ex: "Adjud"
            "rest": addr.get("rest"),  # ex: "Str.Republicii, nr.261" sau "Mun.Adjud ..."
        },

        "debug_children_0101": id1.get("_raw_children"),
        "debug_children_addr": addr.get("_raw_children") if addr else None,
        "debug_children_0104": id4.get("_raw_children"),
    }