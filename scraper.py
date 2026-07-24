import time
import os
from datetime import datetime
from dotenv import load_dotenv
from database import get_connection, init_db

load_dotenv()

PORTAL_URL = os.getenv("PORTAL_URL")
USERNAME = os.getenv("PORTAL_USERNAME")
PASSWORD = os.getenv("PORTAL_PASSWORD")

BROWSER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".playwright-browsers")
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", BROWSER_PATH)


def run_scraper(status_callback=None):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        if status_callback:
            status_callback("Playwright not installed.")
        return 0

    init_db()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--single-process"
        ])
        page = browser.new_page()

        try:
            if status_callback:
                status_callback("Navigating to portal...")
            page.goto(PORTAL_URL, timeout=30000)

            if status_callback:
                status_callback("Looking for login fields...")

            # Fill username
            for selector in ["input[name='username']", "input[name='email']", "input[type='email']", "input[type='text']"]:
                try:
                    page.fill(selector, USERNAME, timeout=5000)
                    break
                except:
                    continue

            # Fill password
            for selector in ["input[name='password']", "input[type='password']"]:
                try:
                    page.fill(selector, PASSWORD, timeout=5000)
                    break
                except:
                    continue

            # Submit
            for selector in ["button[type='submit']", "input[type='submit']", "button:has-text('Login')", "button:has-text('Sign In')"]:
                try:
                    page.click(selector, timeout=5000)
                    break
                except:
                    continue

            page.wait_for_timeout(3000)
            if status_callback:
                status_callback("Logged in! Looking for leads...")

            # Find leads page
            for keyword in ["lead", "leads", "contact", "contacts", "prospect", "clients"]:
                try:
                    page.click(f"a:has-text('{keyword}')", timeout=3000)
                    page.wait_for_timeout(2000)
                    if status_callback:
                        status_callback(f"Found leads section: {page.url}")
                    break
                except:
                    continue

            leads = []
            page_num = 1

            while True:
                if status_callback:
                    status_callback(f"Scraping page {page_num}...")

                # Scrape tables
                tables = page.query_selector_all("table")
                for table in tables:
                    headers = [th.inner_text().strip().lower() for th in table.query_selector_all("th")]
                    rows = table.query_selector_all("tr")
                    for row in rows:
                        cells = [td.inner_text().strip() for td in row.query_selector_all("td")]
                        if cells:
                            lead = dict(zip(headers, cells)) if headers else {f"col_{i}": v for i, v in enumerate(cells)}
                            leads.append(lead)

                # Try next page
                try:
                    next_btn = page.query_selector("a:has-text('Next'), button:has-text('Next')")
                    if next_btn and next_btn.is_visible():
                        next_btn.click()
                        page.wait_for_timeout(2000)
                        page_num += 1
                    else:
                        break
                except:
                    break

            browser.close()

            if status_callback:
                status_callback(f"Found {len(leads)} leads. Saving...")

            saved = save_leads(leads, status_callback)
            return saved

        except Exception as e:
            browser.close()
            if status_callback:
                status_callback(f"❌ Scraper error: {str(e)}")
            return 0


def save_leads(leads, status_callback=None):
    conn = get_connection()
    c = conn.cursor()
    saved = 0

    for lead in leads:
        email = lead.get("email", lead.get("e-mail", lead.get("email address", ""))).strip()
        name = lead.get("name", lead.get("full name", lead.get("client name", "Unknown"))).strip()
        phone = lead.get("phone", lead.get("phone number", lead.get("mobile", ""))).strip()
        policy_status = lead.get("policy status", lead.get("status", lead.get("policy", "Unknown"))).strip()

        if not email or "@" not in email:
            continue

        try:
            c.execute('''
                INSERT OR IGNORE INTO leads (full_name, email, phone, policy_status, source, date_scraped)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (name, email, phone, policy_status, "planetaltig.com", datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            saved += 1
        except:
            continue

    conn.commit()
    conn.close()

    if status_callback:
        status_callback(f"Saved {saved} new leads.")
    return saved


if __name__ == "__main__":
    run_scraper(print)
