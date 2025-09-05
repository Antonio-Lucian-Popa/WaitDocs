# api.py
from __future__ import annotations
import os, json, time, base64
from dataclasses import dataclass
from typing import Any, Optional
import requests

from config import API_BASE

class ApiError(Exception):
    def __init__(self, status: int, message: str, payload: Any | None = None):
        super().__init__(f"{status}: {message}")
        self.status = status
        self.payload = payload

@dataclass
class _TokenBox:
    access: Optional[str] = None
    refresh: Optional[str] = None
    access_exp: Optional[int] = None   # epoch seconds
    refresh_exp: Optional[int] = None

def _user_data_dir(app_name: str = "WaitDocs") -> str:
    if os.name == "nt":
        base = os.getenv("APPDATA") or os.path.expanduser("~")
        path = os.path.join(base, app_name)
    else:
        base = os.getenv("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
        path = os.path.join(base, app_name)
    os.makedirs(path, exist_ok=True)
    return path

def _tokens_path() -> str:
    return os.path.join(_user_data_dir(), "tokens.json")

def _jwt_payload(token: str) -> dict:
    try:
        if not token or "." not in token:
            return {}
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * ((4 - len(payload_b64) % 4) % 4)
        raw = base64.urlsafe_b64decode(payload_b64.encode("ascii"))
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}

def _jwt_exp(token: str) -> Optional[int]:
    return _jwt_payload(token).get("exp")

class ApiClient:
    """
    Client API cu token persistence + refresh automat.
    Folosește aceleași semnături ca înainte pentru request().
    """

    def __init__(self, base_url: str | None = None, timeout: int = 30):
        self.base_url = (base_url or API_BASE).rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.tokens = _TokenBox()
        self._load_tokens()

    # --------------- public ---------------

    def login(self, username: str, password: str) -> dict:
        """Face POST /rest_api/token/ și salvează token-urile."""
        url = self._abs("/rest_api/token/")
        resp = self.session.post(url, json={"username": username, "password": password}, timeout=self.timeout)
        data = self._json_or_raise(resp)
        self._set_tokens(data.get("access"), data.get("refresh"))
        return data

    def try_auto_login(self) -> bool:
        """Încercă să folosească tokenurile salvate; face refresh dacă e nevoie."""
        if not self.tokens.access and not self.tokens.refresh:
            return False
        # dacă access e valid pentru încă 60s, suntem ok
        if self._access_is_valid(skew=60):
            return True
        # altfel, încearcă refresh
        try:
            self._refresh_tokens()
            return True
        except Exception:
            self.logout()  # invalidează local
            return False

    def logout(self):
        """Șterge token-urile locale și header-ele de auth."""
        self.tokens = _TokenBox()
        try:
            os.remove(_tokens_path())
        except Exception:
            pass

    def request(self, method: str, path: str, *,
                params: dict | None = None,
                data: dict | None = None,
                json: dict | None = None,
                headers: dict | None = None,
                **kwargs) -> Any:
        """
        Trimite un request. Adaugă Authorization automat.
        Dacă răspunsul e 401 sau `access` e expirat, încearcă refresh o singură dată și reia cererea.
        Întoarce JSON dacă se poate, altfel text.
        """
        url = self._abs(path)
        headers = dict(headers or {})

        # refresh proactiv dacă access e expirat
        if not self._access_is_valid(skew=10) and self.tokens.refresh:
            try:
                self._refresh_tokens()
            except Exception:
                pass  # vom încerca oricum; serverul va spune 401 dacă e cazul

        if self.tokens.access:
            headers["Authorization"] = f"Bearer {self.tokens.access}"

        resp = self.session.request(method.upper(), url, params=params, data=data, json=json,
                                    headers=headers, timeout=self.timeout, **kwargs)

        # dacă primim 401 și avem refresh, încercăm o dată refresh și reluăm
        if resp.status_code == 401 and self.tokens.refresh:
            try:
                self._refresh_tokens()
                headers["Authorization"] = f"Bearer {self.tokens.access}" if self.tokens.access else ""
                resp = self.session.request(method.upper(), url, params=params, data=data, json=json,
                                            headers=headers, timeout=self.timeout, **kwargs)
            except Exception:
                pass

        return self._json_or_raise(resp)

    # --------------- intern ---------------

    def _abs(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        if not path.startswith("/"):
            path = "/" + path
        return self.base_url + path

    def _json_or_raise(self, resp: requests.Response) -> Any:
        if 200 <= resp.status_code < 300:
            try:
                return resp.json()
            except Exception:
                return resp.text
        # încearcă să extragi mesaj din json/text
        msg = ""
        try:
            j = resp.json()
            msg = j if isinstance(j, str) else json.dumps(j, ensure_ascii=False)
        except Exception:
            msg = resp.text
        raise ApiError(resp.status_code, msg or resp.reason)

    def _access_is_valid(self, *, skew: int = 0) -> bool:
        if not self.tokens.access:
            return False
        exp = self.tokens.access_exp or _jwt_exp(self.tokens.access)
        if not exp:
            # dacă nu putem citi exp, îl considerăm valid până la 401
            return True
        return (time.time() + skew) < int(exp)

    def _refresh_tokens(self):
        """POST /rest_api/token/refresh/ -> set tokens (poate întoarce și refresh nou)."""
        if not self.tokens.refresh:
            raise RuntimeError("Nu există refresh token.")
        url = self._abs("/rest_api/token/refresh/")
        resp = self.session.post(url, json={"refresh": self.tokens.refresh}, timeout=self.timeout)
        data = self._json_or_raise(resp)
        # backend-ul tău întoarce ambele câmpuri
        self._set_tokens(data.get("access") or self.tokens.access,
                         data.get("refresh") or self.tokens.refresh)

    def _set_tokens(self, access: Optional[str], refresh: Optional[str]):
        self.tokens.access = access
        self.tokens.refresh = refresh
        self.tokens.access_exp = _jwt_exp(access) if access else None
        self.tokens.refresh_exp = _jwt_exp(refresh) if refresh else None
        self._save_tokens()

    def _load_tokens(self):
        try:
            with open(_tokens_path(), "r", encoding="utf-8") as f:
                obj = json.load(f)
            self.tokens = _TokenBox(
                access=obj.get("access"),
                refresh=obj.get("refresh"),
                access_exp=obj.get("access_exp") or _jwt_exp(obj.get("access") or ""),
                refresh_exp=obj.get("refresh_exp") or _jwt_exp(obj.get("refresh") or "")
            )
        except Exception:
            self.tokens = _TokenBox()

    def _save_tokens(self):
        try:
            with open(_tokens_path(), "w", encoding="utf-8") as f:
                json.dump({
                    "access": self.tokens.access,
                    "refresh": self.tokens.refresh,
                    "access_exp": self.tokens.access_exp,
                    "refresh_exp": self.tokens.refresh_exp,
                }, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
