from flask import Flask, request, jsonify
from flask_cors import CORS
import threading
import os
import jwt
import datetime
from dotenv import load_dotenv
from database import init_db, get_connection
from categorizer import categorize_all_leads, get_leads_by_category
from emailer import send_bulk_emails, check_replies
from scraper import run_scraper

load_dotenv()

app = Flask(__name__)
CORS(app)

SECRET_KEY = os.getenv("SECRET_KEY", "changeme123")
APP_PIN = os.getenv("APP_PIN", "0518")


init_db()

# Load persisted settings into env on startup
def _load_saved_settings():
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute("SELECT key, value FROM settings")
        for key, value in c.fetchall():
            os.environ[key.upper()] = value
    except:
        pass
    conn.close()

_load_saved_settings()

task_status = {"message": "Ready.", "running": False, "logs": [], "cancel": False}


# ── Auth ──────────────────────────────────────────────
def make_token():
    payload = {"user": "dona", "exp": datetime.datetime.utcnow() + datetime.timedelta(days=7)}
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")


def verify_token(req):
    auth = req.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return False
    token = auth.split(" ", 1)[1]
    try:
        jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        return True
    except:
        return False


def protected(f):
    from functools import wraps
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not verify_token(request):
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


# ── Background tasks ──────────────────────────────────
def run_task(fn, *args):
    def wrapper():
        task_status["running"] = True
        task_status["logs"] = []
        task_status["cancel"] = False
        def cb(msg):
            task_status["message"] = msg
            task_status["logs"].append(msg)
        fn(cb, *args)
        task_status["running"] = False
        task_status["cancel"] = False
    threading.Thread(target=wrapper, daemon=True).start()

def scraper_task(cb):
    count = run_scraper(cb)
    categorize_all_leads(cb)
    cb(f"✅ Sync complete! {count} new leads added.")

def replies_task(cb):
    replies = check_replies(cb)
    cb(f"✅ Found {len(replies)} new replies.")

def send_task(cb, leads, template):
    sent, failed = send_bulk_emails(leads, template, cb)
    cb(f"✅ Done! Sent: {sent} | Failed: {failed}")


# ── Routes ────────────────────────────────────────────
@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json()
    pin = data.get("pin", "")
    if pin == APP_PIN:
        return jsonify({"token": make_token()})
    return jsonify({"error": "Incorrect PIN"}), 401


@app.route("/api/dashboard")
@protected
def dashboard():
    conn = get_connection()
    c = conn.cursor()
    stats = {}
    for key, query in [
        ("total", "SELECT COUNT(*) FROM leads"),
        ("no_policy", "SELECT COUNT(*) FROM leads WHERE category='NO_POLICY'"),
        ("pos", "SELECT COUNT(*) FROM leads WHERE category='POS'"),
        ("sglw", "SELECT COUNT(*) FROM leads WHERE category='SGLW'"),
        ("emailed", "SELECT COUNT(*) FROM leads WHERE email_sent=1"),
        ("responses", "SELECT COUNT(*) FROM leads WHERE response_received=1"),
    ]:
        c.execute(query)
        stats[key] = c.fetchone()[0]
    conn.close()
    return jsonify(stats)


@app.route("/api/leads")
@protected
def leads():
    cat = request.args.get("category", "ALL")
    data = get_leads_by_category(None if cat == "ALL" else cat)
    return jsonify(data)


@app.route("/api/send", methods=["POST"])
@protected
def send():
    data = request.get_json()
    lead_ids = data.get("ids", [])
    template = data.get("template", "Will Kit")
    conn = get_connection()
    c = conn.cursor()
    placeholders = ",".join("?" * len(lead_ids))
    c.execute(f"SELECT * FROM leads WHERE id IN ({placeholders})", lead_ids)
    columns = [d[0] for d in c.description]
    leads_data = [dict(zip(columns, row)) for row in c.fetchall()]
    conn.close()
    run_task(send_task, leads_data, template)
    return jsonify({"ok": True})


@app.route("/api/sync")
@protected
def sync():
    run_task(scraper_task)
    return jsonify({"ok": True})


@app.route("/api/check_replies")
@protected
def check_replies_route():
    run_task(replies_task)
    return jsonify({"ok": True})


@app.route("/api/cancel", methods=["POST"])
@protected
def cancel():
    task_status["cancel"] = True
    task_status["logs"].append("Sync cancelled by user.")
    task_status["message"] = "Cancelled."
    return jsonify({"ok": True})


@app.route("/api/status")
@protected
def status():
    return jsonify(task_status)


@app.route("/api/responses")
@protected
def responses():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT full_name, email, response_text, date_responded FROM leads WHERE response_received=1")
    rows = [{"name": r[0], "email": r[1], "text": r[2], "date": r[3]} for r in c.fetchall()]
    conn.close()
    return jsonify(rows)


@app.route("/api/templates", methods=["GET", "POST"])
@protected
def templates():
    conn = get_connection()
    c = conn.cursor()
    if request.method == "POST":
        data = request.get_json()
        for name, tpl in data.items():
            c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                      (f"tpl_subject_{name}", tpl.get("subject", "")))
            c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                      (f"tpl_body_{name}", tpl.get("body", "")))
        conn.commit()
        conn.close()
        return jsonify({"ok": True})

    c.execute("SELECT key, value FROM settings WHERE key LIKE 'tpl_%'")
    rows = dict(c.fetchall())
    conn.close()

    from email_templates import TEMPLATES
    result = {}
    for name, tpl in TEMPLATES.items():
        result[name] = {
            "subject": rows.get(f"tpl_subject_{name}", tpl["subject"]),
            "body": rows.get(f"tpl_body_{name}", tpl["body"])
        }
    return jsonify(result)


@app.route("/api/signature", methods=["GET", "POST"])
@protected
def signature():
    conn = get_connection()
    c = conn.cursor()
    if request.method == "POST":
        data = request.get_json()
        for key in ["sig_name", "sig_title", "sig_phone"]:
            if data.get(key) is not None:
                c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, data[key]))
        conn.commit()
        conn.close()
        return jsonify({"ok": True})

    c.execute("SELECT key, value FROM settings WHERE key LIKE 'sig_%'")
    rows = dict(c.fetchall())
    conn.close()
    return jsonify({
        "sig_name": rows.get("sig_name", "Dona Maina"),
        "sig_title": rows.get("sig_title", "Life & Income Insurance Specialist"),
        "sig_phone": rows.get("sig_phone", ""),
    })


@protected
def settings():
    conn = get_connection()
    c = conn.cursor()
    if request.method == "POST":
        data = request.get_json()
        for key in ["portal_password", "gmail_app_password", "session_cookie"]:
            if data.get(key):
                c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, data[key]))
                os.environ[key.upper()] = data[key]
        conn.commit()
        conn.close()
        return jsonify({"ok": True})

    c.execute("SELECT key, value FROM settings")
    saved = dict(c.fetchall())
    conn.close()
    if saved.get("portal_password"):
        os.environ["PORTAL_PASSWORD"] = saved["portal_password"]
    if saved.get("gmail_app_password"):
        os.environ["GMAIL_APP_PASSWORD"] = saved["gmail_app_password"]
    return jsonify({
        "PORTAL_USERNAME": os.getenv("PORTAL_USERNAME", ""),
        "GMAIL_ADDRESS": os.getenv("GMAIL_ADDRESS", ""),
        "has_portal_password": bool(saved.get("portal_password")),
        "has_gmail_password": bool(saved.get("gmail_app_password")),
        "has_session_cookie": bool(saved.get("session_cookie")),
    })


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
