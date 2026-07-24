import smtplib
import imaplib
import email
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from dotenv import load_dotenv
from database import get_connection

load_dotenv()

GMAIL = os.getenv("GMAIL_ADDRESS")
APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")


def get_signature():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT key, value FROM settings WHERE key LIKE 'sig_%'")
    rows = dict(c.fetchall())
    conn.close()
    name = rows.get("sig_name", "Dona Maina")
    title = rows.get("sig_title", "Life & Income Insurance Specialist")
    phone = rows.get("sig_phone", "")
    return f"{name}\n{title}" + (f"\n{phone}" if phone else "")


def get_template_text(template_name, lead_name):
    from email_templates import TEMPLATES
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT key, value FROM settings WHERE key LIKE 'tpl_%'")
    rows = dict(c.fetchall())
    conn.close()
    base = TEMPLATES.get(template_name)
    if not base:
        return None
    subject = rows.get(f"tpl_subject_{template_name}", base["subject"])
    body = rows.get(f"tpl_body_{template_name}", base["body"])
    sig = get_signature()
    body = body.replace("{name}", lead_name).replace("{gmail}", GMAIL or "").replace("{signature}", sig)
    return {"subject": subject, "body": body}


def send_email(to_email: str, lead_id: int, template_name: str, lead_name: str, status_callback=None):
    try:
        template = get_template_text(template_name, lead_name)
        if not template:
            if status_callback:
                status_callback(f"Template '{template_name}' not found.")
            return False

        msg = MIMEMultipart()
        msg["From"] = GMAIL
        msg["To"] = to_email
        msg["Subject"] = template["subject"]
        msg.attach(MIMEText(template["body"], "plain"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL, APP_PASSWORD)
            server.sendmail(GMAIL, to_email, msg.as_string())

        conn = get_connection()
        c = conn.cursor()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c.execute("UPDATE leads SET email_sent=1, email_type=?, date_emailed=? WHERE id=?",
                  (template_name, now, lead_id))
        c.execute("INSERT INTO email_log (lead_id, email_type, date_sent, status) VALUES (?,?,?,?)",
                  (lead_id, template_name, now, "sent"))
        conn.commit()
        conn.close()

        if status_callback:
            status_callback(f"Sent to {to_email}")
        return True

    except Exception as e:
        if status_callback:
            status_callback(f"Failed {to_email}: {str(e)}")
        return False


def send_bulk_emails(leads: list, template_name: str, status_callback=None):
    sent = 0
    failed = 0
    for lead in leads:
        success = send_email(
            to_email=lead["email"],
            lead_id=lead["id"],
            template_name=template_name,
            lead_name=lead.get("full_name", "Valued Customer"),
            status_callback=status_callback
        )
        if success:
            sent += 1
        else:
            failed += 1
    if status_callback:
        status_callback(f"Done! Sent: {sent} | Failed: {failed}")
    return sent, failed


def check_replies(status_callback=None):
    if status_callback:
        status_callback("Checking Gmail for replies...")
    replies = []
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(GMAIL, APP_PASSWORD)
        mail.select("inbox")

        _, data = mail.search(None, "UNSEEN")
        email_ids = data[0].split()

        for eid in email_ids:
            _, msg_data = mail.fetch(eid, "(RFC822)")
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)

            sender = msg.get("From", "")
            subject = msg.get("Subject", "")
            date = msg.get("Date", "")

            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        body = part.get_payload(decode=True).decode(errors="ignore")
                        break
            else:
                body = msg.get_payload(decode=True).decode(errors="ignore")

            replies.append({"from": sender, "subject": subject, "date": date, "body": body[:500]})
            mail.store(eid, '+FLAGS', '\\Seen')

            conn = get_connection()
            c = conn.cursor()
            sender_email = sender.split("<")[-1].replace(">", "").strip()
            c.execute("SELECT id FROM leads WHERE email=?", (sender_email,))
            row = c.fetchone()
            if row:
                c.execute("UPDATE leads SET response_received=1, response_text=?, date_responded=? WHERE id=?",
                          (body[:500], date, row[0]))
                conn.commit()
            conn.close()

        mail.logout()
        if status_callback:
            status_callback(f"Found {len(replies)} new replies.")

    except Exception as e:
        if status_callback:
            status_callback(f"Error checking replies: {str(e)}")

    return replies
