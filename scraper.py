import os
from datetime import datetime
from dotenv import load_dotenv
from database import get_connection, init_db

load_dotenv()

PORTAL_URL = os.getenv("PORTAL_URL")
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



def login(page, context, session_cookie, status_callback):
    import json

    # First try cookies from env var (survives Render restarts), then DB
    import json
    cookie_json = os.getenv("BROWSER_COOKIES")
    if not cookie_json:
        conn = get_connection()
        c = conn.cursor()
        c.execute("SELECT value FROM settings WHERE key='browser_cookies'")
        row = c.fetchone()
        conn.close()
        cookie_json = row[0] if row else None

    # Navigate first so the domain is set, then inject cookies and reload
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

    # Saved browser cookies expired — clear and fall back to password
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute("DELETE FROM settings WHERE key='browser_cookies'")
        conn.commit()
        conn.close()
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
    # Save all browser cookies to DB so next sync skips login
    _save_cookies(context, status_callback)
    return True


def _save_cookies(context, status_callback=None):
    import json
    try:
        cookies = context.cookies()
        cookie_json = json.dumps(cookies)

        # Save to DB
        conn = get_connection()
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                  ("browser_cookies", cookie_json))
        conn.commit()
        conn.close()

        # Save to memory so current process reuses it
        os.environ["BROWSER_COOKIES"] = cookie_json

        # Persist to Render env var so it survives restarts
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
        cb(status_callback, "Note: RENDER_SERVICE_ID or RENDER_API_KEY not set — cookies won't survive restart")
        return
    try:
        url = f"https://api.render.com/v1/services/{service_id}/env-vars"
        payload = _json.dumps([{"key": "BROWSER_COOKIES", "value": cookie_json}]).encode()
        req = urllib.request.Request(url, data=payload, method="PUT")
        req.add_header("Authorization", f"Bearer {api_key}")
        req.add_header("Content-Type", "application/json")
        resp = urllib.request.urlopen(req, timeout=10)
        cb(status_callback, f"Cookies pushed to Render env var (status {resp.status}). Will survive restarts.")
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        cb(status_callback, f"Render API error {e.code}: {body}")
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
    method = 'Saved session' if has_saved else ('Session Cookie' if session_cookie else 'Username/Password')
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

            # Go directly to Lead Inbox
            cb(status_callback, f"Navigating to Lead Inbox...")
            page.goto(LEAD_INBOX, timeout=30000)
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except:
                pass
            cb(status_callback, f"Lead Inbox URL: {page.url}")

            leads_data = []
            page_num = 1
            tag_samples = set()

            while True:
                if is_cancelled():
                    cb(status_callback, "Sync cancelled.")
                    browser.close()
                    return 0

                cb(status_callback, f"Scraping page {page_num}...")

                # Wait for table rows
                try:
                    page.wait_for_selector("table tbody tr", timeout=8000)
                except:
                    cb(status_callback, "No table rows found on this page.")
                    break

                rows = page.query_selector_all("table tbody tr")
                cb(status_callback, f"Found {len(rows)} lead rows on page {page_num}")

                for row in rows:
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

                        # Get detail page link
                        link = row.query_selector("a")
                        detail_url = link.get_attribute("href") if link else None

                        leads_data.append({
                            "name": name,
                            "address": address,
                            "lead_tags": lead_tags,
                            "assign_date": assign_date,
                            "city": city,
                            "state": state,
                            "lead_type": lead_type,
                            "detail_url": detail_url,
                        })
                        tag_samples.add(lead_tags[:40] if lead_tags else "(empty)")
                    except:
                        continue

                # Next page
                try:
                    next_btn = page.query_selector("a:has-text('Next')")
                    if next_btn and next_btn.is_visible():
                        next_btn.click()
                        try:
                            page.wait_for_load_state("networkidle", timeout=8000)
                        except:
                            pass
                        page_num += 1
                    else:
                        break
                except:
                    break

            cb(status_callback, f"Collected {len(leads_data)} leads from inbox.")
            cb(status_callback, f"Unique lead_tags found: {list(tag_samples)[:20]}")
            # Report missing fields
            missing_name = sum(1 for l in leads_data if not l.get("name") or l["name"] == "Unknown")
            missing_tags = sum(1 for l in leads_data if not l.get("lead_tags"))
            if missing_name:
                cb(status_callback, f"Warning: {missing_name} leads have no name")
            if missing_tags:
                cb(status_callback, f"Warning: {missing_tags} leads have no lead_tags — will be saved as NO_POLICY")
            cb(status_callback, "Fetching details for first 50 leads...")

            # Fetch email from each lead detail page (first 50 to avoid timeout)
            saved = 0
            fetch_limit = min(len(leads_data), 50)
            for i, lead in enumerate(leads_data[:fetch_limit]):
                if is_cancelled():
                    cb(status_callback, "Sync cancelled during detail fetch.")
                    browser.close()
                    return saved
                try:
                    if lead.get("detail_url"):
                        detail_url = "https://www.planetaltig.com" + lead["detail_url"] if lead["detail_url"].startswith("/") else lead["detail_url"]
                        page.goto(detail_url, timeout=15000)
                        try:
                            page.wait_for_load_state("networkidle", timeout=8000)
                        except:
                            pass

                        # Extract email
                        email = ""
                        for sel in ["a[href^='mailto:']", "[class*='email']", "td:has-text('@')"]:
                            try:
                                el = page.query_selector(sel)
                                if el:
                                    text = el.inner_text().strip()
                                    if "@" in text:
                                        email = text.replace("mailto:", "").strip()
                                        break
                            except:
                                pass

                        # Extract phone
                        phone = ""
                        for sel in ["a[href^='tel:']", "[class*='phone']"]:
                            try:
                                el = page.query_selector(sel)
                                if el:
                                    phone = el.inner_text().strip()
                                    break
                            except:
                                pass

                        lead["email"] = email
                        lead["phone"] = phone

                    if (i + 1) % 10 == 0:
                        cb(status_callback, f"Fetched details for {i+1}/{fetch_limit} leads...")

                    result = save_lead(lead)
                    if result:
                        saved += 1
                except:
                    continue

            # Save remaining leads without email
            for lead in leads_data[fetch_limit:]:
                lead["email"] = ""
                lead["phone"] = ""
                save_lead(lead)

            browser.close()
            cb(status_callback, f"Done! Saved {saved} new leads with details. {len(leads_data) - fetch_limit} saved without email (detail fetch limit).")
            return saved

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
        address = lead.get("address", "").strip()

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
