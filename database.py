import os
import requests
from dotenv import load_dotenv

load_dotenv()

TURSO_URL = os.getenv("TURSO_DATABASE_URL", "")
TURSO_TOKEN = os.getenv("TURSO_AUTH_TOKEN", "")

# Convert libsql:// or libsqls:// URL to HTTPS endpoint
def _http_url():
    url = TURSO_URL.replace("libsql://", "https://").replace("libsqls://", "https://")
    return f"{url}/v2/pipeline"


class _Cursor:
    def __init__(self, conn):
        self._conn = conn
        self.description = None
        self._rows = []
        self._pos = 0

    def execute(self, sql, params=()):
        self._conn._queue.append({"type": "execute", "stmt": {"sql": sql, "args": [_arg(p) for p in params]}})
        self._conn._cursors.append(self)
        self._rows = []
        self._pos = 0
        self.description = None

    def executemany(self, sql, seq):
        for params in seq:
            self.execute(sql, params)

    def _load(self, result):
        cols = [c["name"] for c in (result.get("cols") or [])]
        self.description = [(c, None, None, None, None, None, None) for c in cols]
        self._rows = [tuple(v.get("value") for v in row) for row in (result.get("rows") or [])]
        self._pos = 0

    def fetchone(self):
        if self._pos < len(self._rows):
            row = self._rows[self._pos]
            self._pos += 1
            return row
        return None

    def fetchall(self):
        rows = self._rows[self._pos:]
        self._pos = len(self._rows)
        return rows


def _arg(v):
    if v is None:
        return {"type": "null"}
    if isinstance(v, int):
        return {"type": "integer", "value": str(v)}
    if isinstance(v, float):
        return {"type": "float", "value": v}
    return {"type": "text", "value": str(v)}


class _Connection:
    def __init__(self):
        self._queue = []
        self._cursors = []

    def cursor(self):
        return _Cursor(self)

    def _flush(self):
        if not self._queue:
            return
        if not TURSO_URL or not TURSO_TOKEN:
            raise RuntimeError("TURSO_DATABASE_URL and TURSO_AUTH_TOKEN env vars must be set")
        payload = {"requests": self._queue}
        resp = requests.post(
            _http_url(),
            json=payload,
            headers={"Authorization": f"Bearer {TURSO_TOKEN}", "Content-Type": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        for i, cur in enumerate(self._cursors):
            r = results[i] if i < len(results) else {}
            if r.get("type") == "error":
                raise Exception(r.get("error", {}).get("message", "Turso error"))
            cur._load(r.get("response", {}).get("result", {}))
        self._queue = []
        self._cursors = []

    def commit(self):
        self._flush()

    def close(self):
        self._flush()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def get_connection():
    return _Connection()


def init_db():
    conn = get_connection()
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT,
            email TEXT UNIQUE,
            phone TEXT,
            category TEXT,
            policy_status TEXT,
            source TEXT,
            date_scraped TEXT,
            email_sent INTEGER DEFAULT 0,
            email_type TEXT,
            date_emailed TEXT,
            response_received INTEGER DEFAULT 0,
            response_text TEXT,
            date_responded TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS email_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER,
            email_type TEXT,
            date_sent TEXT,
            status TEXT,
            FOREIGN KEY(lead_id) REFERENCES leads(id)
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print("Turso database initialized successfully.")
