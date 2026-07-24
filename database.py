import os
import libsql_experimental as libsql
from dotenv import load_dotenv

load_dotenv()

TURSO_URL = os.getenv("TURSO_DATABASE_URL")
TURSO_TOKEN = os.getenv("TURSO_AUTH_TOKEN")


class _Cursor:
    """Thin wrapper so existing sqlite3-style code works with libsql."""

    def __init__(self, conn):
        self._conn = conn
        self._rows = []
        self.description = []
        self.rowcount = 0

    def execute(self, sql, params=()):
        result = self._conn.execute(sql, params)
        self._rows = result.rows if result.rows else []
        self.description = [(col[0],) for col in result.columns] if result.columns else []
        self.rowcount = result.rows_affected if hasattr(result, "rows_affected") else 0
        return self

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _Connection:
    def __init__(self):
        self._conn = libsql.connect(TURSO_URL, auth_token=TURSO_TOKEN)

    def cursor(self):
        return _Cursor(self._conn)

    def execute(self, sql, params=()):
        return self._conn.execute(sql, params)

    def commit(self):
        self._conn.commit()

    def close(self):
        pass  # libsql manages its own connection lifecycle


def get_connection():
    return _Connection()


def init_db():
    conn = get_connection()
    conn.execute('''
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
    conn.execute('''
        CREATE TABLE IF NOT EXISTS email_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER,
            email_type TEXT,
            date_sent TEXT,
            status TEXT,
            FOREIGN KEY(lead_id) REFERENCES leads(id)
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    conn.commit()


if __name__ == "__main__":
    init_db()
    print("Turso database initialized successfully.")
