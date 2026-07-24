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


def cb(status_callback, msg):
    if status_callback:
        status_callback(msg)


def run_scraper(status_callback=None):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        cb(status_callback, "ERROR: Playwright not installed.")
        return 0

    init_db()

    cb(status_callback, f"Starting browser...")
    cb(status_callback, f"Portal URL: {PORTAL_URL}")
    cb(status_callback, f"Username: {USERNAME}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=[
            "--no-sandbox", "--disable-dev-shm-usage",
            "--disable-gpu", "--single-process"
        ])
        page = browser.new_page()

        try:
            cb(status_callback, f"Navigating to {PORTAL_URL}...")
            page.goto(PORTAL_URL, timeout=30000)
            page.wait_for_timeout(2000)
            cb(status_callback, f"Page title: {page.title()}")
            cb(status_callback, f"Current URL: {page.url}")

            # Log all inputs found
            inputs = page.query_selector_all("input")
            cb(status_callback, f"Found {len(inputs)} input fields on page:")
            for inp in inputs:
                t = inp.get_attribute("type") or "text"
                n = inp.get_attribute("name") or inp.get_attribute("id") or inp.get_attribute("placeholder") or "?"
                cb(status_callback, f"  - input type={t} name/id={n}")

            # Fill username
            filled_user = False
            for selector in ["input[name='Alias']", "input[name='username']", "input[name='email']", "input[type='email']", "input[type='text']"]:
                try:
                    page.fill(selector, USERNAME, timeout=3000)
                    cb(status_callback, f"Filled username using: {selector}")
                    filled_user = True
                    break
                except:
                    continue
            if not filled_user:
                cb(status_callback, "WARNING: Could not fill username field")

            # Fill password
            filled_pass = False
            for selector in ["input[name='Password']", "input[name='password']", "input[type='password']"]:
                try:
                    page.fill(selector, PASSWORD, timeout=3000)
                    cb(status_callback, f"Filled password using: {selector}")
                    filled_pass = True
                    break
                except:
                    continue
            if not filled_pass:
                cb(status_callback, "WARNING: Could not fill password field")

            # Submit
            submitted = False
            for selector in ["button[type='submit']", "input[type='submit']", "button:has-text('Login')", "button:has-text('Sign In')", "button:has-text('Log In')"]:
                try:
                    page.click(selector, timeout=3000)
                    cb(status_callback, f"Clicked submit using: {selector}")
                    submitted = True
                    break
                except:
                    continue
            if not submitted:
                cb(status_callback, "WARNING: Could not find submit button")

            page.wait_for_timeout(5000)
            cb(status_callback, f"After login - URL: {page.url}")
            cb(status_callback, f"After login - Title: {page.title()}")

            if "Login" in page.url or "login" in page.url:
                cb(status_callback, "ERROR: Still on login page — check PORTAL_USERNAME and PORTAL_PASSWORD env vars on Render")
                browser.close()
                return 0

            # Log all links so we can find the leads section
            links = page.query_selector_all("a")
            cb(status_callback, f"Logged in! Found {len(links)} links:")
            for link in links[:30]:
                href = link.get_attribute("href") or ""
                text = link.inner_text().strip()[:50]
                if text:
                    cb(status_callback, f"  [{text}] -> {href}")

            # Try to find leads section
            found_leads = False
            for keyword in ["lead", "leads", "contact", "contacts", "prospect", "client", "member", "members"]:
                try:
                    page.click(f"a:has-text('{keyword}')", timeout=3000)
                    page.wait_for_timeout(2000)
                    cb(status_callback, f"Clicked '{keyword}' link -> URL: {page.url}")
                    found_leads = True
                    break
                except:
                    continue
            if not found_leads:
                cb(status_callback, "Could not find leads nav link, scraping current page...")

            leads = []
            page_num = 1

            while True:
                cb(status_callback, f"--- Scraping page {page_num} | URL: {page.url} ---")

                # Log tables found
                tables = page.query_selector_all("table")
                cb(status_callback, f"Found {len(tables)} table(s) on page")

                for t_idx, table in enumerate(tables):
                    headers = [th.inner_text().strip().lower() for th in table.query_selector_all("th")]
                    rows = table.query_selector_all("tr")
                    cb(status_callback, f"Table {t_idx+1}: headers={headers}, rows={len(rows)}")
                    for row in rows:
                        cells = [td.inner_text().strip() for td in row.query_selector_all("td")]
                        if cells:
                            lead = dict(zip(headers, cells)) if headers else {f"col_{i}": v for i, v in enumerate(cells)}
                            leads.append(lead)

                # If no tables, log page text snippet
                if not tables:
                    body_text = page.inner_text("body")[:500]
                    cb(status_callback, f"No tables found. Page content preview: {body_text}")

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
            cb(status_callback, f"Scrape complete. Raw rows collected: {len(leads)}")
            if leads:
                cb(status_callback, f"Sample row keys: {list(leads[0].keys())}")
                cb(status_callback, f"Sample row data: {leads[0]}")

            saved = save_leads(leads, status_callback)
            return saved

        except Exception as e:
            browser.close()
            cb(status_callback, f"SCRAPER ERROR: {str(e)}")
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
    cb(status_callback, f"Saved {saved} new leads to database.")
    return saved


if __name__ == "__main__":
    run_scraper(print)
