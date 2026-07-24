import os
import libsql
from dotenv import load_dotenv

load_dotenv()

TURSO_URL = os.getenv("TURSO_DATABASE_URL")
TURSO_TOKEN = os.getenv("TURSO_AUTH_TOKEN")


def get_connection():
    if not TURSO_URL or not TURSO_TOKEN:
        raise RuntimeError("TURSO_DATABASE_URL and TURSO_AUTH_TOKEN env vars must be set")
    return libsql.connect(database=TURSO_URL, auth_token=TURSO_TOKEN)


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
