import time
import os
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from dotenv import load_dotenv
from database import get_connection, init_db

load_dotenv()

PORTAL_URL = os.getenv("PORTAL_URL")
USERNAME = os.getenv("PORTAL_USERNAME")
PASSWORD = os.getenv("PORTAL_PASSWORD")


def get_driver(headless=False):
    options = webdriver.ChromeOptions()
    if headless:
        options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")

    # Try system chromium first, fall back to webdriver-manager
    for chromium_path in ["/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome"]:
        if os.path.exists(chromium_path):
            options.binary_location = chromium_path
            chromedriver_path = chromium_path.replace("chromium", "chromedriver").replace("google-chrome", "chromedriver")
            if os.path.exists(chromedriver_path):
                return webdriver.Chrome(service=Service(chromedriver_path), options=options)
            break

    # Fall back to webdriver-manager
    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options
    )
    return driver


def login(driver, status_callback=None):
    try:
        if status_callback:
            status_callback("Navigating to portal...")
        driver.get(PORTAL_URL)
        wait = WebDriverWait(driver, 15)

        if status_callback:
            status_callback("Looking for login fields...")

        # Try common username/email field selectors
        username_selectors = [
            (By.NAME, "username"), (By.NAME, "email"), (By.NAME, "user"),
            (By.ID, "username"), (By.ID, "email"), (By.ID, "user"),
            (By.CSS_SELECTOR, "input[type='email']"),
            (By.CSS_SELECTOR, "input[type='text']"),
        ]
        username_field = None
        for by, selector in username_selectors:
            try:
                username_field = wait.until(EC.presence_of_element_located((by, selector)))
                break
            except:
                continue

        password_selectors = [
            (By.NAME, "password"), (By.ID, "password"),
            (By.CSS_SELECTOR, "input[type='password']"),
        ]
        password_field = None
        for by, selector in password_selectors:
            try:
                password_field = driver.find_element(by, selector)
                break
            except:
                continue

        if not username_field or not password_field:
            if status_callback:
                status_callback("Could not find login fields. Please check portal URL.")
            return False

        username_field.clear()
        username_field.send_keys(USERNAME)
        password_field.clear()
        password_field.send_keys(PASSWORD)

        # Submit form
        submit_selectors = [
            (By.CSS_SELECTOR, "button[type='submit']"),
            (By.CSS_SELECTOR, "input[type='submit']"),
            (By.XPATH, "//button[contains(text(),'Login')]"),
            (By.XPATH, "//button[contains(text(),'Sign In')]"),
            (By.XPATH, "//button[contains(text(),'Log In')]"),
        ]
        for by, selector in submit_selectors:
            try:
                btn = driver.find_element(by, selector)
                btn.click()
                break
            except:
                continue

        time.sleep(3)
        if status_callback:
            status_callback("Login successful!")
        return True

    except Exception as e:
        if status_callback:
            status_callback(f"Login error: {str(e)}")
        return False


def find_leads_page(driver, status_callback=None):
    if status_callback:
        status_callback("Looking for leads section...")

    lead_keywords = ["lead", "leads", "contact", "contacts", "prospect", "clients"]
    links = driver.find_elements(By.TAG_NAME, "a")

    for link in links:
        text = link.text.lower().strip()
        href = link.get_attribute("href") or ""
        for keyword in lead_keywords:
            if keyword in text or keyword in href.lower():
                try:
                    link.click()
                    time.sleep(2)
                    if status_callback:
                        status_callback(f"Found leads page: {driver.current_url}")
                    return True
                except:
                    continue
    return False


def scrape_leads(driver, status_callback=None):
    if status_callback:
        status_callback("Scanning leads table...")

    leads = []
    page = 1

    while True:
        if status_callback:
            status_callback(f"Scraping page {page}...")
        page_leads = _scrape_current_page(driver, status_callback)
        leads.extend(page_leads)

        # Try to go to next page
        try:
            next_btn = driver.find_element(By.XPATH,
                "//a[contains(text(),'Next') or contains(text(),'›') or contains(text(),'»')] | "
                "//button[contains(text(),'Next') or contains(text(),'›')]")
            if next_btn and next_btn.is_enabled() and next_btn.is_displayed():
                next_btn.click()
                time.sleep(2)
                page += 1
            else:
                break
        except:
            break

    if status_callback:
        status_callback(f"Found {len(leads)} leads across {page} page(s).")
    return leads


def _scrape_current_page(driver, status_callback=None):
    leads = []
    try:
        tables = driver.find_elements(By.TAG_NAME, "table")
        if tables:
            for table in tables:
                rows = table.find_elements(By.TAG_NAME, "tr")
                headers = []
                for row in rows:
                    cells = row.find_elements(By.TAG_NAME, "th")
                    if cells:
                        headers = [c.text.strip().lower() for c in cells]
                        continue
                    cells = row.find_elements(By.TAG_NAME, "td")
                    if cells:
                        row_data = [c.text.strip() for c in cells]
                        lead = dict(zip(headers, row_data)) if headers else {f"col_{j}": v for j, v in enumerate(row_data)}
                        leads.append(lead)

        if not leads:
            cards = driver.find_elements(By.CSS_SELECTOR,
                ".lead, .contact, .card, .list-item, [class*='lead'], [class*='contact']")
            for card in cards:
                text = card.text.strip()
                if text:
                    leads.append({"raw_text": text})
    except Exception as e:
        if status_callback:
            status_callback(f"Scraping error: {str(e)}")
    return leads


def save_leads(leads, status_callback=None):
    init_db()
    conn = get_connection()
    c = conn.cursor()
    saved = 0

    for lead in leads:
        email = lead.get("email", lead.get("e-mail", lead.get("email address", ""))).strip()
        name = lead.get("name", lead.get("full name", lead.get("client name", lead.get("raw_text", "Unknown")))).strip()
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
        except Exception as e:
            continue

    conn.commit()
    conn.close()

    if status_callback:
        status_callback(f"Saved {saved} new leads to database.")
    return saved


def run_scraper(status_callback=None):
    driver = get_driver(headless=False)
    try:
        logged_in = login(driver, status_callback)
        if not logged_in:
            return 0

        found = find_leads_page(driver, status_callback)
        if not found:
            if status_callback:
                status_callback("Could not find leads page automatically. Scraping current page...")

        leads = scrape_leads(driver, status_callback)
        saved = save_leads(leads, status_callback)
        return saved
    finally:
        driver.quit()


if __name__ == "__main__":
    run_scraper(print)
