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


def login(page, context, session_cookie, status_callback):
    if session_cookie:
        cb(status_callback, "Injecting session cookie...")
        for name in [".AspNet.ApplicationCookie", ".ASPXAUTH", "ASP.NET_SessionId", ".AspNetCore.Cookies"]:
            context.add_cookies([{"name": name, "value": session_cookie, "domain": "www.planetaltig.com", "path": "/", "httpOnly": True, "secure": True}])

    page.goto(PORTAL_URL, timeout=30000)
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except:
        pass

    if "Login" not in page.url and "login" not in page.url:
        cb(status_callback, f"Logged in via cookie. URL: {page.url}")
        return True

    if session_cookie:
        all_cookies = context.cookies()
        cb(status_callback, f"Cookie failed. Site cookies: {[c['name'] for c in all_cookies]}")
        cb(status_callback, "Cookie expired. Get a fresh one from your browser and update Settings.")
        return False

    cb(status_callback, "Trying username/password login...")
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
    return True


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

    cb(status_callback, "Starting browser...")
    cb(status_callback, f"Login method: {'Session Cookie' if session_cookie else 'Username/Password'}")

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

            while True:
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

            cb(status_callback, f"Collected {len(leads_data)} leads from inbox. Fetching details...")

            # Fetch email from each lead detail page (first 50 to avoid timeout)
            saved = 0
            fetch_limit = min(len(leads_data), 50)
            for i, lead in enumerate(leads_data[:fetch_limit]):
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
