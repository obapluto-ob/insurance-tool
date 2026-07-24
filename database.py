import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "leads.db")

os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

def init_db():
    conn = sqlite3.connect(DB_PATH)
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

def get_connection():
    return sqlite3.connect(DB_PATH)

if __name__ == "__main__":
    init_db()
    print("Database initialized successfully.")
