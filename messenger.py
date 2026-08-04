import os
import logging
from datetime import datetime
from dotenv import load_dotenv
from twilio.rest import Client
from database import get_connection

load_dotenv()

log = logging.getLogger(__name__)


def _check_config():
    sid   = os.getenv("TWILIO_ACCOUNT_SID", "")
    token = os.getenv("TWILIO_AUTH_TOKEN", "")
    from_  = os.getenv("TWILIO_FROM_NUMBER", "")
    log.info(f"[Twilio] SID set: {bool(sid)} | Token set: {bool(token)} | From: {from_ or 'NOT SET'}")
    if not sid or not token or not from_:
        log.error("[Twilio] ❌ Missing credentials — SMS will not work. Check TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER in Render env vars.")
        return False
    return True

_check_config()


def _twilio_client():
    return Client(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))


def _clean_phone(phone: str) -> str:
    """Strip everything except digits and leading +, ensure E.164 format."""
    digits = "".join(c for c in phone if c.isdigit())
    if not digits:
        return ""
    # assume US number if no country code
    if len(digits) == 10:
        digits = "1" + digits
    return "+" + digits


def get_template_text(template_name: str, lead_name: str) -> str | None:
    from email_templates import TEMPLATES
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT key, value FROM settings WHERE key LIKE 'tpl_%'")
    rows = dict(c.fetchall())
    conn.close()

    base = TEMPLATES.get(template_name)
    if not base:
        return None

    body = rows.get(f"tpl_body_{template_name}", base["body"])

    # SMS/WhatsApp: strip email placeholder, keep it clean
    sig_name  = _get_sig("sig_name",  "Dona Maina")
    sig_title = _get_sig("sig_title", "Life & Income Insurance Specialist")
    sig_phone = _get_sig("sig_phone", "")
    sig = f"{sig_name}, {sig_title}" + (f" | {sig_phone}" if sig_phone else "")

    body = body.replace("{name}", lead_name)
    body = body.replace("{gmail}", sig_phone or "")
    body = body.replace("{signature}", sig)
    return body.strip()


def _get_sig(key: str, default: str) -> str:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else default


def send_message(lead: dict, template_name: str, status_callback=None) -> bool:
    phone_raw = lead.get("phone", "") or ""
    phone = _clean_phone(phone_raw)
    if not phone:
        if status_callback:
            status_callback(f"No phone for {lead.get('full_name')} — skipped")
        return False

    lead_name = lead.get("full_name", "Valued Customer")
    body = get_template_text(template_name, lead_name)
    if not body:
        if status_callback:
            status_callback(f"Template '{template_name}' not found.")
        return False

    try:
        client = _twilio_client()
        from_num = os.getenv("TWILIO_FROM_NUMBER", "")
        log.info(f"[SMS] Sending to {lead_name} ({phone}) via {from_num} | template={template_name}")
        msg = client.messages.create(body=body, from_=from_num, to=phone)
        log.info(f"[SMS] ✅ Delivered to {lead_name} ({phone}) | SID={msg.sid} | Status={msg.status}")
        _record_sent(lead["id"], template_name, msg.sid)
        if status_callback:
            status_callback(f"✅ SMS sent to {lead_name} ({phone}) — SID {msg.sid}")
        return True

    except Exception as e:
        log.error(f"[SMS] ❌ Failed {lead_name} ({phone}): {e}")
        if status_callback:
            status_callback(f"❌ Failed {lead_name} ({phone}): {e}")
        _record_sent(lead["id"], template_name, None, status="failed", error=str(e))
        return False


def _record_sent(lead_id: int, template_name: str, sid: str | None,
                 status: str = "sent", error: str = ""):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "INSERT INTO msg_log (lead_id, template, channel, sid, status, error, date_sent) VALUES (?,?,?,?,?,?,?)",
        (lead_id, template_name, "sms", sid or "", status, error, now)
    )
    if status == "sent" and lead_id:
        c.execute(
            "UPDATE leads SET msg_sent=1, msg_type=?, msg_channel='sms', date_messaged=?, "
            "times_messaged=COALESCE(times_messaged,0)+1 WHERE id=?",
            (template_name, now, lead_id)
        )
    conn.commit()
    conn.close()


def send_bulk_messages(leads: list, template_name: str, status_callback=None):
    sent = failed = 0
    for lead in leads:
        ok = send_message(lead, template_name, status_callback=status_callback)
        if ok:
            sent += 1
        else:
            failed += 1
    if status_callback:
        status_callback(f"✅ Done! Sent: {sent} | Failed: {failed}")
    return sent, failed


def check_replies(status_callback=None) -> list:
    """
    Fetch inbound SMS/WhatsApp messages from Twilio and store replies.
    Returns list of reply dicts.
    """
    if status_callback:
        status_callback("Checking Twilio for inbound messages...")
    replies = []
    try:
        client = _twilio_client()
        messages = client.messages.list(limit=50)
        conn = get_connection()
        c = conn.cursor()
        for msg in messages:
            if msg.direction not in ("inbound",):
                continue
            sender = msg.from_
            body   = (msg.body or "")[:500]
            date   = str(msg.date_sent)
            replies.append({"from": sender, "body": body, "date": date, "sid": msg.sid})

            # match to lead by phone
            clean = _clean_phone(sender)
            c.execute("SELECT id FROM leads WHERE phone LIKE ?", (f"%{clean[-10:]}%",))
            row = c.fetchone()
            if row:
                c.execute(
                    "UPDATE leads SET response_received=1, response_text=?, date_responded=? WHERE id=?",
                    (body, date, row[0])
                )
        conn.commit()
        conn.close()
        if status_callback:
            status_callback(f"Found {len(replies)} inbound messages.")
    except Exception as e:
        if status_callback:
            status_callback(f"Error checking replies: {e}")
    return replies
