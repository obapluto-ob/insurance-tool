import os
from datetime import datetime
from dotenv import load_dotenv
from database import get_connection, init_db

load_dotenv()

PORTAL_URL = os.getenv("PORTAL_URL", "https://www.planetaltig.com")
USERNAME = os.getenv("PORTAL_USERNAME")
PASSWORD = os.getenv("PORTAL_PASSWORD")
LEAD_INBOX = "https://www.planetaltig.com/Lead/Inbox"

BROWSER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".playwright-browsers")
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", BROWSER_PATH)


def cb(status_callback, msg):
    if status_callback:
        status_callback(msg)


def is_cancelled():
    try:
        from app import task_status
        return task_status.get("cancel", False)
    except:
        return False


def _parse_lead_tags(lead_tags):
    """Extract email and DOB from lead_tags field. Returns (email, dob, clean_tags)"""
    email = ""
    dob = ""
    clean_lines = []
    for line in lead_tags.replace("\r", "").split("\n"):
        line = line.strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith("email:"):
            val = line[6:].strip()
            if "@" in val:
                email = val
        elif low.startswith("dob :") or low.startswith("dob:"):
            dob = line.split(":", 1)[1].strip()
        else:
            clean_lines.append(line)
    return email, dob, " | ".join(clean_lines)


def login(page, context, session_cookie, status_callback):
    import json

    cookie_json = os.getenv("BROWSER_COOKIES")
    if not cookie_json:
        conn = get_connection()
        c = conn.cursor()
        c.execute("SELECT value FROM settings WHERE key='browser_cookies'")
        row = c.fetchone()
        conn.close()
        cookie_json = row[0] if row else None

    # Navigate first so domain is set, then inject cookies and reload
    page.goto(PORTAL_URL, timeout=30000)
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except:
        pass

    if cookie_json:
        try:
            saved_cookies = json.loads(cookie_json)
            context.add_cookies(saved_cookies)
            cb(status_callback, f"Loaded {len(saved_cookies)} saved cookies. Reloading...")
            page.reload(timeout=30000)
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except:
                pass
        except:
            pass

    if session_cookie:
        cb(status_callback, "Injecting manual session cookie...")
        for name in [".AspNet.ApplicationCookie", ".ASPXAUTH", "ASP.NET_SessionId", ".AspNetCore.Cookies"]:
            context.add_cookies([{"name": name, "value": session_cookie, "domain": "www.planetaltig.com", "path": "/", "httpOnly": True, "secure": True}])
        page.reload(timeout=30000)
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except:
            pass

    cb(status_callback, f"Cookie check - current URL: {page.url}")
    if "Login" not in page.url and "login" not in page.url:
        cb(status_callback, "Logged in via saved session.")
        return True

    if session_cookie:
        cb(status_callback, "Manual session cookie expired. Get a fresh one from Settings.")
        return False

    # Clear expired cookies and fall back to password
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute("DELETE FROM settings WHERE key='browser_cookies'")
        conn.commit()
        conn.close()
        os.environ.pop("BROWSER_COOKIES", None)
    except:
        pass

    cb(status_callback, "Saved session expired. Logging in with username/password...")
    for selector in ["input[name='Alias']", "input[type='text']"]:
        try:
            page.fill(selector, USERNAME, timeout=3000)
            break
        except:
            continue

    for selector in ["input[name='Password']", "input[type='password']"]:
        try:
            page.fill(selector, PASSWORD, timeout=3000)
            break
        except:
            continue

    for selector in ["button[type='submit']", "input[type='submit']", "button:has-text('Login')"]:
        try:
            page.click(selector, timeout=3000)
            break
        except:
            continue

    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except:
        pass
    page.wait_for_timeout(3000)
    cb(status_callback, f"After login - URL: {page.url}")

    if "Login" in page.url or "login" in page.url:
        page_error = ""
        for sel in [".validation-summary-errors", ".text-danger", ".alert"]:
            try:
                el = page.query_selector(sel)
                if el:
                    page_error = el.inner_text().strip()
                    break
            except:
                pass
        if "locked" in page_error.lower():
            cb(status_callback, "FAILED: Account locked out. Wait 15 minutes.")
        elif page_error:
            cb(status_callback, f"FAILED: {page_error}")
        else:
            cb(status_callback, "FAILED: Wrong username or password.")
        return False

    cb(status_callback, "Login successful.")
    _save_cookies(context, status_callback)
    return True


def _save_cookies(context, status_callback=None):
    import json
    try:
        cookies = context.cookies()
        cookie_json = json.dumps(cookies)

        conn = get_connection()
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", ("browser_cookies", cookie_json))
        conn.commit()
        conn.close()

        os.environ["BROWSER_COOKIES"] = cookie_json
        _push_cookies_to_render(cookie_json, status_callback)
        cb(status_callback, f"Session saved ({len(cookies)} cookies). Next sync will skip login.")
    except Exception as e:
        cb(status_callback, f"Could not save session: {e}")


def _push_cookies_to_render(cookie_json, status_callback=None):
    import urllib.request
    import urllib.error
    import json as _json
    service_id = os.getenv("RENDER_SERVICE_ID")
    api_key = os.getenv("RENDER_API_KEY")
    if not service_id or not api_key:
        return
    try:
        url = f"https://api.render.com/v1/services/{service_id}/env-vars"
        payload = _json.dumps([{"key": "BROWSER_COOKIES", "value": cookie_json}]).encode()
        req = urllib.request.Request(url, data=payload, method="PUT")
        req.add_header("Authorization", f"Bearer {api_key}")
        req.add_header("Content-Type", "application/json")
        resp = urllib.request.urlopen(req, timeout=10)
        cb(status_callback, f"Cookies pushed to Render env var (status {resp.status}).")
    except urllib.error.HTTPError as e:
        cb(status_callback, f"Render API error {e.code}: {e.read().decode()}")
    except Exception as e:
        cb(status_callback, f"Could not push cookies to Render: {e}")


def run_scraper(status_callback=None):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        cb(status_callback, "ERROR: Playwright not installed.")
        return 0

    init_db()

    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key='session_cookie'")
    row = c.fetchone()
    conn.close()
    session_cookie = row[0] if row else None

    has_saved = bool(os.getenv("BROWSER_COOKIES"))
    cb(status_callback, "Starting browser...")
    method = "Saved session" if has_saved else ("Session Cookie" if session_cookie else "Username/Password")
    cb(status_callback, f"Login method: {method}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=[
            "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--single-process"
        ])
        context = browser.new_context()
        page = context.new_page()

        try:
            if not login(page, context, session_cookie, status_callback):
                browser.close()
                return 0

            cb(status_callback, "Navigating to Lead Inbox...")
            page.goto(LEAD_INBOX, timeout=30000)
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except:
                pass
            cb(status_callback, f"Lead Inbox URL: {page.url}")

            leads_data = []
            tag_samples = set()

            try:
                page.wait_for_selector("table tbody tr", timeout=8000)
            except:
                cb(status_callback, "No table rows found.")
                browser.close()
                return 0

            rows = page.query_selector_all("table tbody tr")
            cb(status_callback, f"Found {len(rows)} leads in inbox.")

            for row in rows:
                if is_cancelled():
                    cb(status_callback, "Sync cancelled.")
                    browser.close()
                    return 0
                try:
                    cells = row.query_selector_all("td")
                    if len(cells) < 5:
                        continue
                    name = cells[3].inner_text().strip()
                    address = cells[4].inner_text().strip() if len(cells) > 4 else ""
                    lead_tags = cells[5].inner_text().strip() if len(cells) > 5 else ""
                    assign_date = cells[7].inner_text().strip() if len(cells) > 7 else ""
                    city = cells[9].inner_text().strip() if len(cells) > 9 else ""
                    state = cells[10].inner_text().strip() if len(cells) > 10 else ""
                    lead_type = cells[11].inner_text().strip() if len(cells) > 11 else ""
                    link = row.query_selector("a")
                    detail_url = link.get_attribute("href") if link else None
                    email, dob, clean_tags = _parse_lead_tags(lead_tags)
                    leads_data.append({
                        "name": name, "address": address, "lead_tags": clean_tags,
                        "assign_date": assign_date, "city": city, "state": state,
                        "lead_type": lead_type, "detail_url": detail_url,
                        "email": email, "phone": "", "dob": dob
                    })
                    tag_samples.add(clean_tags[:40] if clean_tags else "(empty)")
                except:
                    continue

            new_leads = sum(1 for l in leads_data if save_lead(l))
            with_email = sum(1 for l in leads_data if l.get("email"))
            cb(status_callback, f"Saved {new_leads} new leads. {with_email}/{len(leads_data)} have emails.")
            cb(status_callback, f"Sample lead_tags: {list(tag_samples)[:10]}")
            type_samples = set(l["lead_type"] for l in leads_data if l.get("lead_type"))
            cb(status_callback, f"Unique lead_type values: {list(type_samples)[:20]}")

            browser.close()
            return new_leads

        except Exception as e:
            browser.close()
            cb(status_callback, f"SCRAPER ERROR: {str(e)}")
            return 0


def save_lead(lead):
    conn = get_connection()
    c = conn.cursor()
    try:
        name = lead.get("name", "Unknown").strip() or "Unknown"
        email = lead.get("email", "").strip()
        phone = lead.get("phone", "").strip()
        policy_status = lead.get("lead_tags", lead.get("lead_type", "Unknown")).strip()

        c.execute("""
            INSERT OR IGNORE INTO leads (full_name, email, phone, policy_status, source, date_scraped)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (name, email, phone, policy_status, "planetaltig.com", datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        inserted = c.rowcount > 0
        conn.commit()
        return inserted
    except:
        return False
    finally:
        conn.close()


def save_leads(leads, status_callback=None):
    saved = sum(1 for l in leads if save_lead(l))
    cb(status_callback, f"Saved {saved} new leads.")
    return saved


if __name__ == "__main__":
    run_scraper(print)
