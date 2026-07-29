import os
import json
import logging
import threading
import requests
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import NamedTuple
from dotenv import load_dotenv
from database import get_connection, init_db


class LeadDetail(NamedTuple):
    phone: str
    email: str
    dob: str
    address: str
    city: str
    state: str


class LeadTags(NamedTuple):
    email: str
    phone: str
    dob: str
    clean_tags: str

log = logging.getLogger(__name__)

load_dotenv()


BROWSER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".playwright-browsers")
# Use a fixed local path — never derive browser path from user-supplied env input
if "PLAYWRIGHT_BROWSERS_PATH" not in os.environ:
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = BROWSER_PATH


def cb(status_callback, msg):
    if status_callback:
        status_callback(msg)


def _get_portal_url():
    return os.getenv("PORTAL_URL", "https://www.planetaltig.com").rstrip("/")


def is_cancelled():
    try:
        from app import task_status
        return task_status.get("cancel", False)
    except ImportError:
        return False


def _parse_lead_tags(lead_tags):
    """Extract email, phone and DOB from lead_tags field. Returns (email, phone, dob, clean_tags)"""
    email = ""
    phone = ""
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
        elif low.startswith("phone:") or low.startswith("cell:") or low.startswith("mobile:") or low.startswith("tel:"):
            phone = line.split(":", 1)[1].strip()
        else:
            clean_lines.append(line)
    return LeadTags(email=email, phone=phone, dob=dob, clean_tags=" | ".join(clean_lines))


def _load_cookies_from_db(cookie_key="browser_cookies"):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key=?", (cookie_key,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None


def _try_inject_saved_cookies(page, context, cookie_json, status_callback):
    try:
        saved_cookies = json.loads(cookie_json)
        context.add_cookies(saved_cookies)
        cb(status_callback, f"Loaded {len(saved_cookies)} saved cookies. Reloading...")
        page.reload(timeout=30000)
        try:
            page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass
    except json.JSONDecodeError as e:
        cb(status_callback, f"Failed to parse saved cookies: {e}")
    except Exception as e:
        log.warning(f"Cookie injection error: {type(e).__name__}: {e}")


def _try_inject_session_cookie(page, context, session_cookie, status_callback, portal_url=None):
    cb(status_callback, "Injecting manual session cookie...")
    from urllib.parse import urlparse
    domain = urlparse(portal_url or _get_portal_url()).netloc
    for name in [".AspNet.ApplicationCookie", ".ASPXAUTH", "ASP.NET_SessionId", ".AspNetCore.Cookies"]:
        context.add_cookies([{"name": name, "value": session_cookie, "domain": domain, "path": "/", "httpOnly": True, "secure": True}])
    page.reload(timeout=30000)
    try:
        page.wait_for_load_state("domcontentloaded", timeout=15000)
    except Exception:
        pass


def _load_credentials_from_db(username, password):
    try:
        conn = get_connection()
        c = conn.cursor()
        if not username:
            c.execute("SELECT value FROM settings WHERE key='portal_username'")
            row = c.fetchone()
            username = row[0] if row else username
        if not password:
            c.execute("SELECT value FROM settings WHERE key='portal_password'")
            row = c.fetchone()
            password = row[0] if row else ""
        conn.close()
    except Exception as e:
        log.warning(f"Could not load credentials from DB: {e}")
    return username, password


def _fill_login_form(page, username, password, status_callback):
    for selector in ["input[name='Alias']", "input[type='text']"]:
        try:
            page.fill(selector, username, timeout=3000)
            break
        except Exception as e:
            log.warning(f"Could not fill username with selector {selector!r}: {type(e).__name__}")
    for selector in ["input[name='Password']", "input[type='password']"]:
        try:
            page.fill(selector, password, timeout=3000)
            break
        except Exception as e:
            log.warning(f"Could not fill password with selector {selector!r}: {type(e).__name__}")
    for selector in ["button[type='submit']", "input[type='submit']", "button:has-text('Login')"]:
        try:
            page.click(selector, timeout=3000)
            break
        except Exception as e:
            log.warning(f"Could not click submit with selector {selector!r}: {type(e).__name__}")
    try:
        page.wait_for_load_state("domcontentloaded", timeout=15000)
    except Exception:
        pass


def _attempt_session_login(page, context, session_cookie, status_callback, portal_url=None, cookie_key="browser_cookies"):
    """Try cookies/session. Returns True if already logged in, False if login page still showing."""
    cookie_json = (_load_cookies_from_db(cookie_key) if cookie_key != "browser_cookies"
                   else os.getenv("BROWSER_COOKIES") or _load_cookies_from_db())
    portal_url = portal_url or _get_portal_url()

    page.goto(portal_url, timeout=30000)
    try:
        page.wait_for_load_state("domcontentloaded", timeout=15000)
    except Exception:
        pass

    if cookie_json:
        _try_inject_saved_cookies(page, context, cookie_json, status_callback)
    if session_cookie:
        _try_inject_session_cookie(page, context, session_cookie, status_callback, portal_url=portal_url)

    cb(status_callback, f"Cookie check - current URL: {page.url}")
    return "Login" not in page.url and "login" not in page.url


def _attempt_password_login(page, context, status_callback, cookie_key="browser_cookies"):
    """Fill and submit login form, check result. Returns True on success."""
    username = os.getenv("_MANUAL_PORTAL_USERNAME") or os.getenv("PORTAL_USERNAME", "")
    password = os.getenv("_MANUAL_PORTAL_PASSWORD") or os.getenv("PORTAL_PASSWORD", "")
    os.environ.pop("_MANUAL_PORTAL_USERNAME", None)
    os.environ.pop("_MANUAL_PORTAL_PASSWORD", None)
    if not username or not password:
        username, password = _load_credentials_from_db(username, password)
    cb(status_callback, f"Using username: '{username}' | password set: {bool(password)}")
    _fill_login_form(page, username, password, status_callback)
    page.wait_for_timeout(3000)
    cb(status_callback, f"After login - URL: {page.url}")

    if "Login" not in page.url and "login" not in page.url:
        cb(status_callback, "Login successful.")
        _save_cookies(context, status_callback, cookie_key=cookie_key)
        return True

    page_error = ""
    for sel in [".validation-summary-errors", ".text-danger", ".alert"]:
        try:
            el = page.query_selector(sel)
            if el:
                page_error = el.inner_text().strip()
                break
        except Exception as e:
            log.warning(f"Could not read login error element: {type(e).__name__}")
    if "locked" in page_error.lower():
        cb(status_callback, "FAILED: Account locked out. Wait 15 minutes.")
    elif page_error:
        cb(status_callback, f"FAILED: {page_error}")
    else:
        cb(status_callback, "FAILED: Wrong username or password.")
    return False


def _clear_expired_cookies(status_callback):
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute("DELETE FROM settings WHERE key IN ('browser_cookies', 'manual_browser_cookies')")
        conn.commit()
        conn.close()
        os.environ.pop("BROWSER_COOKIES", None)
    except Exception as e:
        cb(status_callback, f"Could not clear expired cookies: {e}")


def login(page, context, session_cookie, status_callback, portal_url=None, cookie_key="browser_cookies"):
    if _attempt_session_login(page, context, session_cookie, status_callback, portal_url=portal_url, cookie_key=cookie_key):
        cb(status_callback, "Logged in via saved session.")
        return True

    if session_cookie:
        cb(status_callback, "Manual session cookie expired. Get a fresh one from Settings.")
        return False

    cb(status_callback, "Saved session expired. Logging in with username/password...")
    _clear_expired_cookies(status_callback)
    return _attempt_password_login(page, context, status_callback, cookie_key=cookie_key)


def _save_cookies(context, status_callback=None, cookie_key="browser_cookies"):
    try:
        cookies = context.cookies()
        cookie_json = json.dumps(cookies)
        conn = get_connection()
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (cookie_key, cookie_json))
        conn.commit()
        conn.close()
        if cookie_key == "browser_cookies":
            os.environ["BROWSER_COOKIES"] = cookie_json
            _push_cookies_to_render(cookie_json, status_callback)
        cb(status_callback, f"Session saved ({len(cookies)} cookies). Next sync will skip login.")
    except Exception as e:
        cb(status_callback, f"Could not save session: {e}")


def _push_cookies_to_render(cookie_json, status_callback=None):
    service_id = os.getenv("RENDER_SERVICE_ID")
    api_key = os.getenv("RENDER_API_KEY")
    if not service_id or not api_key:
        return
    try:
        url = f"https://api.render.com/v1/services/{service_id}/env-vars"
        resp = requests.put(
            url,
            json=[{"key": "BROWSER_COOKIES", "value": cookie_json}],
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        resp.raise_for_status()
        cb(status_callback, f"Cookies pushed to Render env var (status {resp.status_code}).")
    except requests.HTTPError as e:
        cb(status_callback, f"Render API error {e.response.status_code}: {e.response.text}")
    except requests.RequestException as e:
        cb(status_callback, f"Could not push cookies to Render: {e}")


def _load_sync_settings():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key='session_cookie'")
    row = c.fetchone()
    session_cookie = row[0] if row else None
    c.execute("SELECT value FROM settings WHERE key='last_sync_date'")
    row = c.fetchone()
    last_sync_date = row[0] if row else None
    conn.close()
    return session_cookie, last_sync_date


def _find_table_page(page, portal_url, status_callback):
    """After login, scan all nav links to find a page that has a scrapeable table."""
    cb(status_callback, "Scanning portal for a page with lead data...")
    try:
        links = page.query_selector_all("a[href]")
        hrefs = []
        for link in links:
            href = (link.get_attribute("href") or "").strip()
            if not href or href.startswith("#") or href.startswith("javascript") or href.startswith("mailto"):
                continue
            full = href if href.startswith("http") else portal_url.rstrip("/") + "/" + href.lstrip("/")
            if portal_url.split("//")[1].split("/")[0] in full:  # same domain only
                hrefs.append(full)
        hrefs = list(dict.fromkeys(hrefs))  # dedupe, preserve order
        priority_kw = ["lead", "inbox", "prospect", "contact", "pipeline", "member", "client"]
        priority = [u for u in hrefs if any(k in u.lower() for k in priority_kw)]
        rest = [u for u in hrefs if u not in priority]
        ordered = priority + rest
        cb(status_callback, f"Found {len(ordered)} links ({len(priority)} priority) to check...")
        for url in ordered:
            if is_cancelled():
                return None
            try:
                is_priority = url in priority
                page.goto(url, timeout=15000, wait_until="domcontentloaded")
                if is_priority:
                    try:
                        page.wait_for_selector("table tbody tr", timeout=8000)
                    except Exception:
                        pass
                else:
                    page.wait_for_timeout(1000)
                rows = page.query_selector_all("table tbody tr")
                cb(status_callback, f"Checked: {url} → {len(rows)} rows")
                if len(rows) >= 5:
                    cb(status_callback, f"Found scrapeable table at: {url} ({len(rows)} rows)")
                    return url
            except Exception as e:
                log.warning(f"Could not check {url}: {e}")
                continue
        # nothing found — try saving the inbox URL directly as last resort
        inbox_url = portal_url.rstrip("/") + "/Lead/Inbox"
        cb(status_callback, f"No table found via scan. Trying direct path: {inbox_url}")
        try:
            page.goto(inbox_url, timeout=20000, wait_until="domcontentloaded")
            page.wait_for_timeout(4000)
            rows = page.query_selector_all("table tbody tr")
            if len(rows) >= 1:
                cb(status_callback, f"Found scrapeable table at: {inbox_url} ({len(rows)} rows)")
                return inbox_url
        except Exception as e:
            log.warning(f"Direct inbox fallback failed: {e}")
    except Exception as e:
        log.warning(f"Link scan error: {e}")
    return None


def _get_saved_inbox_url(url_key):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key=?", (url_key,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None


def _save_inbox_url(url_key, url):
    conn = get_connection()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (url_key, url))
    conn.commit()
    conn.close()


def _navigate_to_inbox(page, status_callback, portal_url=None, inbox_url_key="inbox_url"):
    """Go to saved inbox URL if known, otherwise scan portal to find it. Returns row count."""
    base = portal_url or _get_portal_url()
    saved_url = _get_saved_inbox_url(inbox_url_key)

    if saved_url:
        cb(status_callback, f"Using saved inbox URL: {saved_url}")
        try:
            page.goto(saved_url, timeout=60000, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
            row_count = len(page.query_selector_all("table tbody tr"))
            if row_count >= 5:
                cb(status_callback, f"Table has {row_count} rows visible.")
                return row_count
            cb(status_callback, "Saved inbox URL no longer has data — re-scanning portal...")
        except Exception as e:
            log.warning(f"Saved inbox URL failed: {e} — re-scanning portal...")

    # Scan portal to find scrapeable page
    found_url = _find_table_page(page, base, status_callback)
    if not found_url:
        cb(status_callback, "Could not find a scrapeable page on this portal.")
        return 0

    _save_inbox_url(inbox_url_key, found_url)
    cb(status_callback, f"Inbox URL saved for future syncs: {found_url}")

    page.goto(found_url, timeout=60000, wait_until="domcontentloaded")
    cb(status_callback, "DOM ready. Waiting for table rows to render...")
    try:
        page.wait_for_selector("table tbody tr", timeout=30000)
    except Exception:
        log.warning("Timed out waiting for table rows selector")
    page.wait_for_timeout(2000)
    row_count = len(page.query_selector_all("table tbody tr"))
    cb(status_callback, f"Table has {row_count} rows visible.")
    return row_count


def _get_sync_page(is_manual=False):
    key = "manual_sync_page" if is_manual else "sync_current_page"
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = c.fetchone()
    conn.close()
    return int(row[0]) if row else 1


def _save_sync_page(page_num, is_manual=False):
    key = "manual_sync_page" if is_manual else "sync_current_page"
    conn = get_connection()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(page_num)))
    conn.commit()
    conn.close()


def _clear_sync_page(is_manual=False):
    key = "manual_sync_page" if is_manual else "sync_current_page"
    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM settings WHERE key=?", (key,))
    conn.commit()
    conn.close()


def _find_next_btn(page):
    """Find the Next pagination button using 4 strategies. Returns element or None."""
    # 1. known CSS/aria selectors
    for sel in [
        "a[aria-label='Next']", "a[aria-label='next']", "a[aria-label='Next Page']",
        "a:has-text('Next')", "button:has-text('Next')",
        "li.next:not(.disabled) a", "li.PagedList-skipToNext a",
        "a.next", "button.next", ".next-page a",
        "[data-page='next']", "[data-action='next']",
        ".pagination li:last-child:not(.disabled) a",
        "a[rel='next']", "input[value='Next']", "input[value='next']",
    ]:
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible() and btn.is_enabled():
                return btn
        except Exception:
            continue
    # 2. URL param
    import re as _re
    for param in ["page", "PageNumber", "pageNumber", "pg", "p"]:
        m = _re.search(rf"[?&]{param}=(\d+)", page.url)
        if m:
            next_val = int(m.group(1)) + 1
            for el in page.query_selector_all(f"a[href*='{param}={next_val}']"):
                try:
                    if el.is_visible():
                        return el
                except Exception:
                    continue
    # 3. text/symbol scan
    for el in page.query_selector_all("a, button, input[type='button'], [role='button']"):
        try:
            combined = " ".join([
                (el.inner_text() or "").lower(),
                (el.get_attribute("href") or "").lower(),
                (el.get_attribute("aria-label") or "").lower(),
                (el.get_attribute("title") or "").lower(),
                (el.get_attribute("value") or "").lower(),
            ])
            if any(kw in combined for kw in ["next", "›", "»", ">>", "forward"]) and el.is_visible() and el.is_enabled():
                return el
        except Exception:
            continue
    # 4. active page number + 1
    try:
        active = page.query_selector(".pagination .active a, .pagination .active, [class*='current-page']")
        if active:
            cur = int((active.inner_text() or "0").strip())
            if cur > 0:
                for el in page.query_selector_all(".pagination a, [class*='page'] a"):
                    try:
                        if (el.inner_text() or "").strip() == str(cur + 1) and el.is_visible():
                            return el
                    except Exception:
                        continue
    except Exception:
        pass
    return None


def _navigate_to_page(page, target_page, status_callback):
    """Click Next until we reach target_page. Returns True if reached, False if last page hit before target."""
    for current in range(1, target_page):
        next_btn = _find_next_btn(page)
        if not next_btn:
            cb(status_callback, f"Portal only has {current} page(s) — already at last page.")
            return False
        try:
            next_btn.click()
            page.wait_for_selector("table tbody tr", timeout=15000)
            page.wait_for_timeout(1000)
        except Exception as e:
            log.warning(f"Pagination click failed navigating to page {current+1}: {e}")
            return False
    return True


def _scrape_rows(page, last_sync_date, status_callback, target_page=1):
    """Scrape target_page only. Returns (leads_data, skipped, skipped_short, skipped_noname, is_last_page)."""
    if target_page > 1:
        cb(status_callback, f"Navigating to page {target_page}...")
        if not _navigate_to_page(page, target_page, status_callback):
            return [], 0, 0, 0, True  # already at last page

    rows = page.query_selector_all("table tbody tr")
    mode = f"since {last_sync_date}" if last_sync_date else "full sync (first time)"
    cb(status_callback, f"Page {target_page}: Found {len(rows)} rows — {mode}")

    leads_data = []
    skipped = skipped_short = skipped_noname = 0
    pending_lead = None
    stop_early = False

    for row in rows:
        if is_cancelled():
            return None, skipped, skipped_short, skipped_noname, False
        try:
            cells = row.query_selector_all("td")
            if len(cells) < 4:
                if pending_lead and len(cells) >= 2:
                    for cell in cells:
                        txt = cell.inner_text().strip()
                        low = txt.lower()
                        if low.startswith("phone no:") or low.startswith("phone:") or low.startswith("cell:") or low.startswith("mobile:"):
                            val = txt.split(":", 1)[1].strip()
                            if val and not pending_lead["phone"]:
                                pending_lead["phone"] = val
                        elif low.startswith("email:") and "@" in txt:
                            val = txt.split(":", 1)[1].strip()
                            if val and not pending_lead["email"]:
                                pending_lead["email"] = val
                skipped_short += 1
                continue
            name = cells[3].inner_text().strip()
            if not name:
                if pending_lead:
                    for cell in cells:
                        txt = cell.inner_text().strip()
                        low = txt.lower()
                        if low.startswith("email:") and "@" in txt:
                            val = txt.split(":", 1)[1].strip()
                            if val and not pending_lead["email"]:
                                pending_lead["email"] = val
                        elif low.startswith("dob :") or low.startswith("dob:"):
                            val = txt.split(":", 1)[1].strip()
                            if val and not pending_lead["dob"]:
                                pending_lead["dob"] = val
                skipped_noname += 1
                continue
            assign_date = cells[7].inner_text().strip() if len(cells) > 7 else ""
            if last_sync_date and assign_date:
                try:
                    if datetime.strptime(assign_date, "%m/%d/%Y") <= datetime.strptime(last_sync_date, "%m/%d/%Y"):
                        skipped += 1
                        stop_early = True
                        continue
                except ValueError:
                    pass
            address   = cells[4].inner_text().strip() if len(cells) > 4 else ""
            lead_tags = cells[5].inner_text().strip() if len(cells) > 5 else ""
            city      = cells[9].inner_text().strip() if len(cells) > 9 else ""
            state     = cells[10].inner_text().strip() if len(cells) > 10 else ""
            lead_type = cells[11].inner_text().strip() if len(cells) > 11 else ""
            link = row.query_selector("a")
            detail_url = None
            # check the <tr> row onclick first — most portals put navigation there
            row_onclick = (row.get_attribute("onclick") or "").strip()
            # also check the name cell (cells[3]) for onclick or data attrs
            name_cell_onclick = (cells[3].get_attribute("onclick") or "").strip() if len(cells) > 3 else ""
            name_cell_link = cells[3].query_selector("a") if len(cells) > 3 else None
            name_href = (name_cell_link.get_attribute("href") or "").strip() if name_cell_link else ""
            name_onclick = (name_cell_link.get_attribute("onclick") or "").strip() if name_cell_link else ""

            import re as _re
            for src in [name_href, name_onclick, name_cell_onclick, row_onclick]:
                if not src or src == "#":
                    continue
                if src.startswith("/") or src.startswith("http"):
                    if not src.startswith("javascript"):
                        detail_url = src
                        break
                m = _re.search(r"['\"]([/][^'\"]+)['\"]", src)
                if m:
                    detail_url = m.group(1)
                    break

            # log first 3 rows so we can see what the portal actually provides
            if len(leads_data) < 3:
                cb(status_callback, f"Row debug — tr_onclick={row_onclick!r} | name_href={name_href!r} | name_onclick={name_onclick!r} → detail_url={detail_url!r}")
            email, phone, dob, clean_tags = _parse_lead_tags(lead_tags)
            lead = {
                "name": name, "address": address, "lead_tags": clean_tags,
                "assign_date": assign_date, "city": city, "state": state,
                "lead_type": lead_type, "detail_url": detail_url,
                "email": email, "phone": phone, "dob": dob
            }
            leads_data.append(lead)
            pending_lead = lead
        except Exception as e:
            log.warning(f"Row parse error: {e}")
            continue

    # check if there's a next page
    is_last = stop_early or (_find_next_btn(page) is None)
    return leads_data, skipped, skipped_short, skipped_noname, is_last


def _save_sync_date(leads_data):
    dates = [l["assign_date"] for l in leads_data if l.get("assign_date")]
    try:
        newest = max(dates, key=lambda d: datetime.strptime(d, "%m/%d/%Y")) if dates else datetime.now().strftime("%m/%d/%Y")
    except ValueError:
        newest = datetime.now().strftime("%m/%d/%Y")
    conn = get_connection()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('last_sync_date', ?)", (newest,))
    conn.commit()
    conn.close()
    return newest


def run_scraper(status_callback=None, override_url=None, override_username=None, override_password=None):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        cb(status_callback, "ERROR: Playwright not installed.")
        return 0

    init_db()
    session_cookie, last_sync_date = _load_sync_settings()

    portal_url = override_url.rstrip("/") if override_url else _get_portal_url()
    is_manual = bool(override_url)
    cookie_key = "manual_browser_cookies" if is_manual else "browser_cookies"
    inbox_url_key = "manual_inbox_url" if is_manual else "inbox_url"
    if override_username:
        os.environ["_MANUAL_PORTAL_USERNAME"] = override_username
    if override_password:
        os.environ["_MANUAL_PORTAL_PASSWORD"] = override_password

    target_page = _get_sync_page(is_manual)
    cb(status_callback, f"Syncing page {target_page}..." if target_page > 1 else "Starting sync...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=[
            "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--single-process"
        ])
        context = browser.new_context()
        page = context.new_page()

        try:
            cb(status_callback, "Connecting to portal...")
            if not login(page, context, session_cookie, status_callback, portal_url=portal_url, cookie_key=cookie_key):
                browser.close()
                return 0
            cb(status_callback, "Logged in ✓")

            cb(status_callback, "Loading Lead Inbox...")
            row_count = _navigate_to_inbox(page, status_callback, portal_url=portal_url, inbox_url_key=inbox_url_key)
            if row_count < 1:
                cb(status_callback, f"HTML snippet: {page.content()[:2000]}")
                browser.close()
                return 0
            cb(status_callback, "Table ready. Scraping rows...")

            leads_data, skipped, skipped_short, skipped_noname, is_last_page = _scrape_rows(
                page, last_sync_date, status_callback, target_page=target_page
            )
            if leads_data is None:
                cb(status_callback, "Sync cancelled.")
                browser.close()
                return 0

            cb(status_callback, f"Page {target_page}: Scraped {len(leads_data)} rows (skipped {skipped} old, {skipped_short} short-row, {skipped_noname} no-name). Saving...")

            new_leads, new_lead_names = save_leads_bulk(leads_data, status_callback)
            with_email = sum(1 for l in leads_data if l.get("email"))
            with_phone = sum(1 for l in leads_data if l.get("phone"))
            cb(status_callback, f"Checked {len(leads_data)} | New: {new_leads} | With email: {with_email} | With phone: {with_phone}")

            needs_enrich = [
                l for l in leads_data
                if l.get("detail_url")
                and not (l.get("detail_url", "").startswith("javascript") or l.get("detail_url") == "#")
                and l.get("name") in new_lead_names
                and not (l.get("email") and l.get("phone"))
            ]
            if needs_enrich:
                cb(status_callback, f"Enriching {len(needs_enrich)} new leads missing contact info...")
                enrich_leads(needs_enrich, context, status_callback, workers=2)

            browser.close()

            if is_last_page:
                _clear_sync_page(is_manual)
                newest = _save_sync_date(leads_data)
                cb(status_callback, f"All pages done. Last sync date saved as {newest}.")
            else:
                next_page = target_page + 1
                _save_sync_page(next_page, is_manual)
                cb(status_callback, f"✅ Page {target_page} done — {new_leads} new leads. Sync page {next_page} when ready.")

            return new_leads

        except Exception as e:
            log.exception(f"SCRAPER ERROR: {e}")
            browser.close()
            cb(status_callback, f"SCRAPER ERROR: {e}")
            return 0


def _scrape_detail(page, url):
    """Scrape a single detail page and return field dict."""
    if not url.startswith("http"):
        url = _get_portal_url() + url
    page.goto(url, timeout=15000, wait_until="domcontentloaded")
    body = page.inner_text("body")
    phone = email = dob = address = city = state = ""
    for line in body.split("\n"):
        line = line.strip()
        if not line:
            continue
        low = line.lower()
        # Only match lines that start with known labels followed by a colon
        if not phone and any(low.startswith(p) for p in ["phone:", "cell:", "mobile:", "tel:", "phone no:"]):
            val = line.split(":", 1)[1].strip()
            # Must look like a phone number
            digits = ''.join(c for c in val if c.isdigit())
            if len(digits) >= 7:
                phone = val
        elif not email and low.startswith("email:") and "@" in line:
            email = line.split(":", 1)[1].strip()
        elif not dob and (low.startswith("dob:") or low.startswith("date of birth:")):
            dob = line.split(":", 1)[1].strip()
        elif not address and low.startswith("address:"):
            val = line.split(":", 1)[1].strip()
            # Must look like a street address (contains a digit)
            if any(c.isdigit() for c in val):
                address = val
        elif not city and low.startswith("city:"):
            city = line.split(":", 1)[1].strip()
        elif not state and low.startswith("state:"):
            state = line.split(":", 1)[1].strip()
    return LeadDetail(phone=phone, email=email, dob=dob, address=address, city=city, state=state)


def _save_enriched(lead, detail: LeadDetail):
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        UPDATE leads SET
            phone   = COALESCE(NULLIF(phone,''),   NULLIF(?, '')),
            email   = COALESCE(email,              NULLIF(?, '')),
            dob     = COALESCE(NULLIF(dob,''),     NULLIF(?, '')),
            address = COALESCE(NULLIF(address,''), NULLIF(?, '')),
            city    = COALESCE(NULLIF(city,''),    NULLIF(?, '')),
            state   = COALESCE(NULLIF(state,''),   NULLIF(?, '')),
            enriched = 1
        WHERE full_name=?
    """, (detail.phone, detail.email, detail.dob, detail.address, detail.city, detail.state, lead["name"]))
    conn.commit()
    conn.close()


def enrich_leads(leads, context, status_callback=None, workers=5):
    """Enrich leads in parallel using multiple browser pages."""
    total = len(leads)
    enriched = 0

    pages = [context.new_page() for _ in range(workers)]
    lock = threading.Lock()

    def enrich_one(idx, lead):
        slot = idx % workers
        p = pages[slot]
        try:
            detail = _scrape_detail(p, lead["detail_url"])
            _save_enriched(lead, detail)
            return True
        except Exception as e:
            log.warning(f"Enrichment failed for {lead.get('name')}: {e}")
            return False

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(enrich_one, i, lead): lead for i, lead in enumerate(leads)}
        for future in as_completed(futures):
            if is_cancelled():
                break
            with lock:
                enriched += 1
                if enriched % 10 == 0 or enriched == total:
                    cb(status_callback, f"Enriched {enriched}/{total} leads...")

    for p in pages:
        try:
            p.close()
        except Exception as e:
            log.warning(f"Could not close enrichment page: {e}")

    cb(status_callback, f"Enrichment done — {enriched}/{total} leads updated with full data.")


def _build_existing_sets(c):
    c.execute("SELECT email, full_name, address FROM leads")
    existing = c.fetchall()
    existing_emails = {row[0] for row in existing if row[0]}
    existing_no_email = {(row[1], row[2] or "") for row in existing if not row[0]}
    return existing_emails, existing_no_email


def _insert_lead(c, lead, now, existing_emails, existing_no_email):
    name = lead.get("name", "Unknown").strip() or "Unknown"
    if any(name.lower().startswith(p) for p in ["phone no:", "phone:", "group:", "name:", "email:", "dob:", "cell:", "mobile:"]):
        return False, True  # (inserted, rejected)
    email         = lead.get("email", "").strip() or None
    phone         = lead.get("phone", "").strip() or None
    policy_status = lead.get("lead_type", lead.get("lead_tags", "Unknown")).strip() or "Unknown"
    detail_url    = lead.get("detail_url") or None
    address       = lead.get("address", "").strip() or None
    city          = lead.get("city", "").strip() or None
    state         = lead.get("state", "").strip() or None
    dob           = lead.get("dob", "").strip() or None
    if email:
        if email in existing_emails:
            return False, False
        existing_emails.add(email)
    else:
        key = (name, address or "")
        if key in existing_no_email:
            return False, False
        existing_no_email.add(key)
    c.execute("""
        INSERT OR IGNORE INTO leads
            (full_name, email, phone, policy_status, source, date_scraped, detail_url, address, city, state, dob)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (name, email, phone, policy_status, "planetaltig.com", now, detail_url, address, city, state, dob))
    return True, False


def save_leads_bulk(leads, status_callback=None):
    if not leads:
        return 0, set()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    c = conn.cursor()
    existing_emails, existing_no_email = _build_existing_sets(c)
    new_count = 0
    rejected = 0
    new_names = set()
    for lead in leads:
        inserted, is_rejected = _insert_lead(c, lead, now, existing_emails, existing_no_email)
        if is_rejected:
            if rejected < 3:
                status_callback and status_callback(f"Rejected name: {lead.get('name')!r}")
            rejected += 1
        elif inserted:
            new_count += 1
            new_names.add(lead.get("name", ""))
    conn.commit()
    conn.close()
    if status_callback:
        status_callback(f"Saved {new_count} new leads to database. Rejected {rejected} sub-rows.")
    return new_count, new_names


def save_lead(lead):
    conn = get_connection()
    c = conn.cursor()
    try:
        name = lead.get("name", "Unknown").strip() or "Unknown"
        email = lead.get("email", "").strip() or None
        phone = lead.get("phone", "").strip() or None
        policy_status = lead.get("lead_type", lead.get("lead_tags", "Unknown")).strip() or "Unknown"

        if email:
            c.execute("""
                INSERT OR IGNORE INTO leads (full_name, email, phone, policy_status, source, date_scraped)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (name, email, phone, policy_status, "planetaltig.com", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")))
        else:
            c.execute("SELECT id FROM leads WHERE full_name=? AND email IS NULL AND policy_status=?", (name, policy_status))
            if c.fetchone():
                return False
            c.execute("""
                INSERT INTO leads (full_name, email, phone, policy_status, source, date_scraped)
                VALUES (?, NULL, ?, ?, ?, ?)
            """, (name, phone, policy_status, "planetaltig.com", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")))

        inserted = c.rowcount > 0
        conn.commit()
        return inserted
    except Exception as e:
        log.warning(f"save_lead error for {lead.get('name')!r}: {e}")
        return False
    finally:
        conn.close()


def save_leads(leads, status_callback=None):
    saved = sum(1 for l in leads if save_lead(l))
    cb(status_callback, f"Saved {saved} new leads.")
    return saved


if __name__ == "__main__":
    run_scraper(print)
