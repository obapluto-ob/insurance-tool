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
APP_USERNAME = os.getenv("APP_USERNAME", "dona")
APP_PASSWORD = os.getenv("APP_PASSWORD", "dona1234")

init_db()

task_status = {"message": "Ready.", "running": False}


# ── Auth ──────────────────────────────────────────────
def make_token():
    payload = {"user": APP_USERNAME, "exp": datetime.datetime.utcnow() + datetime.timedelta(days=7)}
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
        def cb(msg): task_status["message"] = msg
        fn(cb, *args)
        task_status["running"] = False
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
    if data.get("username") == APP_USERNAME and data.get("password") == APP_PASSWORD:
        return jsonify({"token": make_token()})
    return jsonify({"error": "Invalid credentials"}), 401


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


@app.route("/api/settings", methods=["GET", "POST"])
@protected
def settings():
    if request.method == "POST":
        data = request.get_json()
        if data.get("portal_password"):
            os.environ["PORTAL_PASSWORD"] = data["portal_password"]
        if data.get("gmail_app_password"):
            os.environ["GMAIL_APP_PASSWORD"] = data["gmail_app_password"]
        return jsonify({"ok": True})

    return jsonify({
        "PORTAL_USERNAME": os.getenv("PORTAL_USERNAME", ""),
        "GMAIL_ADDRESS": os.getenv("GMAIL_ADDRESS", ""),
    })


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
