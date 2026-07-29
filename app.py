from flask import Flask, request, jsonify
from flask_cors import CORS
import threading
import os
import time
import jwt
from datetime import datetime, timedelta, timezone
import logging
from dotenv import load_dotenv
from database import init_db, get_connection
from categorizer import categorize_all_leads, get_leads_by_category
from emailer import send_bulk_emails, check_replies
from scraper import run_scraper
from trader import trader

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}}, supports_credentials=False, allow_headers=["Content-Type", "Authorization"], methods=["GET", "POST", "OPTIONS"])

@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response

app.register_blueprint(trader)

SECRET_KEY = os.getenv("SECRET_KEY", "changeme123")
APP_PIN = os.getenv("APP_PIN", "0518")


try:
    init_db()
    log.info("Database initialized OK")
except Exception as e:
    log.error(f"Database init FAILED: {e}")

# Load persisted settings into env on startup
def _load_saved_settings():
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute("SELECT key, value FROM settings")
        for key, value in c.fetchall():
            os.environ[key.upper()] = value
        conn.close()
        log.info("Settings loaded from DB")
    except Exception as e:
        log.error(f"Failed to load settings: {e}")

_load_saved_settings()

task_status = {"message": "Ready.", "running": False, "logs": [], "cancel": False}


# ── Auth ──────────────────────────────────────────────
def make_token():
    payload = {"user": "dona", "exp": datetime.now(timezone.utc) + timedelta(days=7)}
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
    if task_status.get("running"):
        return
    def wrapper():
        task_status["running"] = True
        task_status["logs"] = []
        task_status["cancel"] = False
        def cb(msg):
            task_status["message"] = msg
            task_status["logs"].append(msg)
            log.info(f"[TASK] {msg}")
        try:
            fn(cb, *args)
        except Exception as e:
            log.error(f"[TASK] crashed: {e}")
            cb(f"ERROR: {e}")
        task_status["running"] = False
        task_status["cancel"] = False
    threading.Thread(target=wrapper, daemon=True).start()

def scraper_task(cb):
    cb("Starting sync...")
    count = run_scraper(cb)
    cb("Categorizing leads...")
    categorize_all_leads(cb)
    cb(f"✅ Done — {count} new leads added.")

def manual_sync_task(cb, portal_url, portal_username, portal_password):
    cb("Starting manual sync...")
    count = run_scraper(cb, override_url=portal_url, override_username=portal_username, override_password=portal_password)
    cb("Categorizing leads...")
    categorize_all_leads(cb)
    cb(f"✅ Done — {count} new leads added.")


def replies_task(cb):
    replies = check_replies(cb)
    cb(f"✅ Found {len(replies)} new replies.")

def send_task(cb, leads, template):
    sent, failed = send_bulk_emails(leads, template, cb)
    cb(f"✅ Done! Sent: {sent} | Failed: {failed}")


# ── Routes ────────────────────────────────────────────
@app.route("/api/health")
def health():
    return jsonify({"ok": True, "turso_url": bool(os.getenv("TURSO_DATABASE_URL")), "turso_token": bool(os.getenv("TURSO_AUTH_TOKEN"))})


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json()
    pin = data.get("pin", "")
    if pin == APP_PIN:
        log.info("[login] PIN accepted")
        return jsonify({"token": make_token()})
    log.warning("[login] Wrong PIN attempt")
    return jsonify({"error": "Incorrect PIN"}), 401


_dashboard_cache = {"data": None, "ts": 0}

@app.route("/api/dashboard")
@protected
def dashboard():
    if time.time() - _dashboard_cache["ts"] < 60 and _dashboard_cache["data"]:
        return jsonify(_dashboard_cache["data"])
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN category='NO_POLICY' THEN 1 ELSE 0 END) as no_policy,
                SUM(CASE WHEN category='POS' THEN 1 ELSE 0 END) as pos,
                SUM(CASE WHEN category='SGLW' THEN 1 ELSE 0 END) as sglw,
                SUM(CASE WHEN category='ACTIVE' THEN 1 ELSE 0 END) as active,
                SUM(CASE WHEN email_sent=1 THEN 1 ELSE 0 END) as emailed,
                SUM(CASE WHEN response_received=1 THEN 1 ELSE 0 END) as responses
            FROM leads
        """)
        row = c.fetchone()
        conn.close()
        stats = {
            "total":     row[0] or 0,
            "no_policy": row[1] or 0,
            "pos":       row[2] or 0,
            "sglw":      row[3] or 0,
            "active":    row[4] or 0,
            "emailed":   row[5] or 0,
            "responses": row[6] or 0,
        }
        log.info(f"[dashboard] stats: {stats}")
        _dashboard_cache["data"] = stats
        _dashboard_cache["ts"] = time.time()
        return jsonify(stats)
    except Exception as e:
        log.error(f"[dashboard] ERROR: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/leads")
@protected
def leads():
    try:
        cat = request.args.get("category", "ALL")
        data = get_leads_by_category(None if cat == "ALL" else cat)
        log.info(f"[leads] returned {len(data)} leads")
        return jsonify(data)
    except Exception as e:
        log.error(f"[leads] ERROR: {type(e).__name__}")
        return jsonify({"error": "Failed to fetch leads"}), 500


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


@app.route("/api/manual-sync", methods=["POST"])
@protected
def manual_sync():
    if task_status["running"]:
        return jsonify({"ok": False, "message": "A sync is already running"})
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT key, value FROM settings WHERE key IN ('manual_portal_url','manual_portal_username','manual_portal_password')")
    saved = dict(c.fetchall())
    conn.close()
    portal_url      = saved.get("manual_portal_url", "")
    portal_username = saved.get("manual_portal_username", "")
    portal_password = saved.get("manual_portal_password", "")
    if not portal_url or not portal_username or not portal_password:
        return jsonify({"ok": False, "message": "Manual portal credentials not set. Go to Settings → Manual Sync."})
    run_task(manual_sync_task, portal_url, portal_username, portal_password)
    return jsonify({"ok": True})


@app.route("/api/manual-settings", methods=["GET", "POST", "DELETE"])
@protected
def manual_settings():
    conn = get_connection()
    c = conn.cursor()
    if request.method == "DELETE":
        c.execute("DELETE FROM settings WHERE key IN ('manual_portal_url','manual_portal_username','manual_portal_password')")
        conn.commit()
        conn.close()
        return jsonify({"ok": True})
    if request.method == "POST":
        data = request.get_json()
        for key in ["manual_portal_url", "manual_portal_username", "manual_portal_password"]:
            if data.get(key):
                c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, data[key]))
        conn.commit()
        conn.close()
        return jsonify({"ok": True})
    c.execute("SELECT key, value FROM settings WHERE key IN ('manual_portal_url','manual_portal_username','manual_portal_password')")
    saved = dict(c.fetchall())
    conn.close()
    return jsonify({
        "manual_portal_url":      saved.get("manual_portal_url", ""),
        "manual_portal_username": saved.get("manual_portal_username", ""),
        "has_manual_password":    bool(saved.get("manual_portal_password")),
    })


@app.route("/api/sync/reset", methods=["POST"])
@protected
def sync_reset():
    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM settings WHERE key='last_sync_date'")
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "message": "Sync reset. Next sync will pull all leads from scratch."})


@app.route("/api/sync")
@protected
def sync():
    if task_status["running"]:
        log.warning("[sync] already running, ignoring duplicate request")
        return jsonify({"ok": False, "message": "Sync already running"})
    log.info("[sync] starting")
    run_task(scraper_task)
    return jsonify({"ok": True})


@app.route("/api/debug/schema")
@protected
def debug_schema():
    try:
        conn = get_connection()
        c = conn.cursor()
        schema = {}
        for table in ["leads", "settings", "email_log"]:
            c.execute(f"PRAGMA table_info({table})")
            schema[table] = [row[1] for row in c.fetchall()]
        conn.close()
        return jsonify(schema)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/debug")
@protected
def debug():
    """Returns system state — visible in browser and Render logs."""
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM leads")
        total = c.fetchone()[0]
        c.execute("SELECT value FROM settings WHERE key='last_scraped_page'")
        row = c.fetchone()
        last_page = row[0] if row else "not set (will start page 1)"
        c.execute("SELECT value FROM settings WHERE key='portal_explored'")
        row = c.fetchone()
        explored = "yes" if row else "no (will run on next sync)"
        c.execute("SELECT key FROM settings")
        setting_keys = [r[0] for r in c.fetchall()]
        conn.close()
        info = {
            "total_leads_in_db": total,
            "last_scraped_page": last_page,
            "portal_explored": explored,
            "settings_keys": setting_keys,
            "task_running": task_status["running"],
            "task_message": task_status["message"],
            "turso_url_set": bool(os.getenv("TURSO_DATABASE_URL")),
            "turso_token_set": bool(os.getenv("TURSO_AUTH_TOKEN")),
            "gmail_set": bool(os.getenv("GMAIL_APP_PASSWORD")),
            "portal_password_set": bool(os.getenv("PORTAL_PASSWORD")),
        }
        safe_info = {k: v for k, v in info.items() if k not in ("turso_url_set", "turso_token_set", "gmail_set", "portal_password_set")}
        log.info(f"[debug] {safe_info}")
        return jsonify(info)
    except Exception as e:
        log.error(f"[debug] ERROR: {e}")
        return jsonify({"error": str(e)}), 500


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


@app.route("/api/settings", methods=["GET", "POST"])
@protected
def settings():
    conn = get_connection()
    c = conn.cursor()
    if request.method == "POST":
        data = request.get_json()
        for key in ["portal_username", "portal_password", "gmail_address", "gmail_app_password", "session_cookie"]:
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
    if saved.get("gmail_address"):
        os.environ["GMAIL_ADDRESS"] = saved["gmail_address"]
    if saved.get("gmail_app_password"):
        os.environ["GMAIL_APP_PASSWORD"] = saved["gmail_app_password"]
    return jsonify({
        "PORTAL_USERNAME": saved.get("portal_username") or os.getenv("PORTAL_USERNAME", ""),
        "GMAIL_ADDRESS": saved.get("gmail_address") or os.getenv("GMAIL_ADDRESS", ""),
        "has_portal_password": bool(saved.get("portal_password")),
        "has_gmail_password": bool(saved.get("gmail_app_password")),
        "has_session_cookie": bool(saved.get("session_cookie")),
    })

