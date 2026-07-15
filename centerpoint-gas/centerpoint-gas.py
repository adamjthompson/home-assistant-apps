#!/usr/bin/env python3
"""
CenterPoint Energy Gas Usage
Logs into CenterPoint Energy's customer portal (persisting the browser
session across runs so 2FA isn't required every time), reads the natural-gas
billing-history table, and imports each meter-read cycle's usage into Home
Assistant's long-term statistics as an external statistic, so the Energy
Dashboard shows a correctly-dated gas consumption source.

Unlike this repo's ohio-aes add-on, CenterPoint's billing-history table
already reports both the period usage (Therms) and the absolute cumulative
meter reading for each row, so there's no local ledger or cumulative-sum
reconstruction needed here -- each row is a fully self-contained statistic
entry. Therms are converted to kWh (THERM_TO_KWH below) since HA's Energy
Dashboard gas-source unit picker doesn't accept therms directly.

Login and table-scrape selectors were confirmed against the real login page
markup (CenterPoint uses Azure AD B2C's default self-asserted sign-in
template -- #signInName/#password/#rememberMe/#next are the platform's own
field IDs, not guesses) and the real billing-history table sample provided
during development. The 2FA-challenge step (which page/field it uses) is
still unconfirmed since it only appears after a real login.
"""

import asyncio
import email as email_module
import imaplib
import logging
import os
import re
import secrets
import sys
from datetime import datetime, timezone
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import aiohttp
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("centerpoint_gas")

# ─── Configuration ────────────────────────────────────────────────────────────

CENTERPOINT_USERNAME = os.environ["CENTERPOINT_USERNAME"]
CENTERPOINT_PASSWORD = os.environ["CENTERPOINT_PASSWORD"]
GMAIL_ADDRESS = os.environ["GMAIL_ADDRESS"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
CYCLES_BACK = int(os.environ.get("CYCLES_BACK", 3))

CHROMIUM_PATH = os.environ.get("CHROMIUM_PATH", "/usr/bin/chromium-browser")

# Confirmed via fetching the real login URL: CenterPoint's tenant/policy/
# client_id/redirect_uri are fixed, app-level identifiers, safe to hardcode.
# `state`/`nonce` are single-use per-request values though -- generated fresh
# per run (_build_login_url) rather than reusing any literal captured value.
_B2C_TENANT = "cnpcwecafprod.onmicrosoft.com"
_B2C_POLICY = "b2c_1a_signuporsignin"
_B2C_CLIENT_ID = "a6386527-7544-45d9-94e0-f0e22e480d15"
_B2C_REDIRECT_URI = "https://myaccount.centerpointenergy.com/myaccounts/Index"


def _build_login_url():
    params = {
        "client_id": _B2C_CLIENT_ID,
        "redirect_uri": _B2C_REDIRECT_URI,
        "response_type": "id_token",
        "scope": "openid",
        "response_mode": "form_post",
        "state": secrets.token_urlsafe(24),
        "nonce": secrets.token_urlsafe(24),
    }
    return f"https://login.centerpointenergy.com/{_B2C_TENANT}/{_B2C_POLICY}/oauth2/v2.0/authorize?{urlencode(params)}"


def _build_billing_history_url():
    # MeterNumber/Installation are account-specific but auto-populate once
    # logged in (confirmed: navigating without them still lands on the right
    # meter's history for a single-meter account) -- Lob/DefaultLobType/ST
    # are fixed view-selector params (which utility service to show), not
    # account-specific, so no per-account config is needed here at all.
    params = {"Lob": "20", "DefaultLobType": "20", "ST": "Gas"}
    return f"https://myaccount.centerpointenergy.com/UsageView/UsageHistory?{urlencode(params)}"

# `/data` is this add-on's own private persistent volume (unlike `/share`,
# provisioned automatically with no `map:` entry needed) -- appropriate here
# since this file holds live session credentials, not just app data.
SESSION_STATE_PATH = "/data/centerpoint_state.json"

# `homeassistant_api: true` in config.yaml makes Supervisor inject this token
# and proxy these two hosts to HA Core -- no manual long-lived token needed.
SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN")
SUPERVISOR_API_BASE = "http://supervisor/core/api"
SUPERVISOR_WS_URL = "ws://supervisor/core/websocket"

STATISTIC_ID = "centerpoint_gas:cycle_usage"

# HA's Energy Dashboard gas-source unit picker accepts volume units
# (CCF/ft³/L/MCF/m³) and energy units (cal/Gcal/GJ/GWh/J/kcal/kJ/kWh/Mcal/MJ/
# mWh/MWh/TWh/Wh) -- confirmed directly against a real HA instance -- but NOT
# therms. Therms are already an energy value (that's why utilities bill gas
# in therms rather than raw volume -- it accounts for the actual heating
# value of the gas delivered), so this is a fixed, exact linear conversion
# rather than something recoverable as a volume unit.
THERM_TO_KWH = 29.3001111


# ─── Gmail 2FA code retrieval ──────────────────────────────────────────────────
#
# CenterPoint's login sometimes (not always -- the device gets remembered
# between runs via SESSION_STATE_PATH) challenges with an emailed 2FA code.
# This polls Gmail via IMAP with an app password rather than the account
# password -- note this grants read access to the whole mailbox, not just
# CenterPoint's emails, since an app password can't be scoped narrower.
#
# TODO: the sender/subject search terms and the code-format regex below are
# unconfirmed placeholders -- update once a real 2FA email sample is
# available.

_CODE_RE = re.compile(r"\b(\d{6})\b")


def _extract_email_text(msg):
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                return part.get_payload(decode=True).decode(errors="ignore")
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                return part.get_payload(decode=True).decode(errors="ignore")
        return ""
    return msg.get_payload(decode=True).decode(errors="ignore")


def _search_gmail_for_code(after_time):
    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    try:
        imap.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        imap.select("INBOX", readonly=True)
        since_str = after_time.strftime("%d-%b-%Y")
        # TODO: narrow this once the real sender address is known, e.g.
        # imap.search(None, 'FROM', '"noreply@centerpointenergy.com"')
        status, data = imap.search(None, f'(SINCE "{since_str}" SUBJECT "verification")')
        if status != "OK" or not data or not data[0]:
            return None
        message_ids = data[0].split()
        for message_id in reversed(message_ids):
            status, msg_data = imap.fetch(message_id, "(RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue
            msg = email_module.message_from_bytes(msg_data[0][1])
            msg_time = email_module.utils.parsedate_to_datetime(msg["Date"])
            if msg_time < after_time:
                continue
            match = _CODE_RE.search(_extract_email_text(msg))
            if match:
                return match.group(1)
        return None
    finally:
        imap.logout()


async def fetch_2fa_code(after_time, timeout_seconds=60, poll_interval=5):
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_seconds
    while loop.time() < deadline:
        code = await asyncio.to_thread(_search_gmail_for_code, after_time)
        if code:
            return code
        await asyncio.sleep(poll_interval)
    raise RuntimeError("Timed out waiting for CenterPoint's 2FA code email")


# ─── Browser automation ───────────────────────────────────────────────────────

async def _needs_login(page):
    # Confirmed real domain/field for CenterPoint's Azure B2C sign-in page.
    if "login.centerpointenergy.com" in page.url:
        return True
    return await page.locator("#signInName").count() > 0


async def _login_with_2fa(page):
    login_start_time = datetime.now(timezone.utc)
    log.info("Logging into CenterPoint Energy")
    # #signInName / #password / #rememberMe / #next are confirmed real field
    # IDs from CenterPoint's actual Azure B2C sign-in template (Microsoft's
    # own self-asserted attribute IDs, not guesses). Checking #rememberMe is
    # very likely what controls whether future logins skip 2FA.
    await page.fill("#signInName", CENTERPOINT_USERNAME)
    await page.fill("#password", CENTERPOINT_PASSWORD)
    if await page.locator("#rememberMe").count() > 0:
        await page.check("#rememberMe")
    await page.click("#next")

    # The self-asserted form submits via an async JS handler (an AJAX POST,
    # not a plain form POST -- see the module docstring), so
    # wait_for_load_state("networkidle") alone can resolve before that
    # round-trip actually finishes and redirects away. Waiting explicitly for
    # the browser to leave the login domain is a much more reliable signal
    # that the attempt actually completed -- confirmed necessary after a real
    # run where "networkidle" resolved while still sitting on the unsubmitted
    # sign-in page, and the code below wrongly read that as "no 2FA needed".
    try:
        await page.wait_for_function(
            "() => !location.host.includes('login.centerpointenergy.com')",
            timeout=30_000,
        )
    except Exception:
        body_snippet = await page.evaluate("() => document.body.innerText.slice(0, 2000)")
        log.error(
            "Still on the login page 30s after submitting -- likely bad "
            "credentials or an on-page validation error. Page text: %r",
            body_snippet,
        )
        raise RuntimeError("Login did not proceed past the sign-in page -- see logged page text above")

    await page.wait_for_load_state("networkidle")

    # TODO: unconfirmed -- this step only appears after a real login, which
    # hasn't happened yet. Update the selector/detection once seen for real.
    if await page.locator('input[name="verificationCode"]').count() > 0:
        log.info("2FA challenge detected, fetching code from Gmail")
        code = await fetch_2fa_code(after_time=login_start_time)
        await page.fill('input[name="verificationCode"]', code)
        await page.click('button[type="submit"]')
        await page.wait_for_load_state("networkidle")
    else:
        log.info("No 2FA challenge -- device appears to be remembered")


async def scrape_billing_history(page):
    # TODO: confirmed structure is Reading Date / Meter Reading / Therms /
    # Charges columns, but the table's real selector/markup is unconfirmed --
    # this matches by visible header text rather than a guessed CSS
    # selector, which should be more resilient to markup we haven't seen.
    #
    # Wait explicitly for the header text rather than relying solely on
    # "networkidle" from the caller's page.goto -- a client-rendered table
    # can still finish rendering a moment after the network itself goes
    # quiet.
    try:
        await page.get_by_text("Reading Date", exact=False).first.wait_for(timeout=15_000)
    except Exception:
        pass  # fall through to the diagnostic dump below either way

    result = await page.evaluate(
        """
        () => {
            const tables = Array.from(document.querySelectorAll('table'));
            for (const table of tables) {
                const headerText = table.innerText.slice(0, 500);
                if (headerText.includes('Reading Date') && headerText.includes('Therms')) {
                    return {
                        rows: Array.from(table.querySelectorAll('tbody tr')).map(
                            tr => Array.from(tr.querySelectorAll('td')).map(td => td.innerText.trim())
                        ),
                    };
                }
            }
            return {
                rows: null,
                tableCount: tables.length,
                tableSnippets: tables.map(t => t.innerText.slice(0, 200)),
                bodySnippet: document.body.innerText.slice(0, 2000),
            };
        }
        """
    )
    if result["rows"] is None:
        # Logged rather than saved to a file (this add-on deliberately
        # doesn't write debug screenshots/HTML to disk) -- enough to diagnose
        # from the container log without needing a separate artifact.
        log.error("Page URL when the table wasn't found: %s", page.url)
        log.error("Found %d <table> element(s) on the page", result.get("tableCount", 0))
        for i, snippet in enumerate(result.get("tableSnippets", [])):
            log.error("  table[%d] text: %r", i, snippet)
        log.error("Page body text snippet: %r", result.get("bodySnippet", ""))
        raise RuntimeError(
            "Could not find the billing-history table on the page -- the real "
            "page structure doesn't match what scrape_billing_history expects, "
            "see the debug output logged above and update this function"
        )
    return result["rows"]


async def scrape_gas_usage():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            executable_path=CHROMIUM_PATH,
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        storage_state = SESSION_STATE_PATH if os.path.exists(SESSION_STATE_PATH) else None
        context = await browser.new_context(storage_state=storage_state)
        page = await context.new_page()
        billing_history_url = _build_billing_history_url()

        try:
            log.info("Navigating to billing history")
            await page.goto(billing_history_url, wait_until="networkidle")

            if await _needs_login(page):
                if "login.centerpointenergy.com" not in page.url:
                    # The app didn't auto-redirect us to the sign-in page --
                    # navigate there explicitly rather than assume it always
                    # will.
                    log.info("Not auto-redirected to login -- navigating there directly")
                    await page.goto(_build_login_url(), wait_until="networkidle")
                await _login_with_2fa(page)
                await page.goto(billing_history_url, wait_until="networkidle")

            raw_rows = await scrape_billing_history(page)
            # Re-save after every successful run (login or reused session)
            # so the trust window keeps extending.
            await context.storage_state(path=SESSION_STATE_PATH)
            return raw_rows
        except Exception:
            log.exception("Automation failed")
            raise
        finally:
            await browser.close()


# ─── Table parsing ─────────────────────────────────────────────────────────────

_ROW_DATE_FORMAT = "%b %d,%Y"  # e.g. "Jul 06,2026"


def parse_billing_rows(raw_rows):
    entries = []
    for row in raw_rows:
        if len(row) < 3:
            continue
        reading_date_str, meter_reading_str, therms_str = row[0], row[1], row[2]
        try:
            reading_date = datetime.strptime(reading_date_str.strip(), _ROW_DATE_FORMAT).date()
        except ValueError:
            log.warning("Skipping row with unrecognized date format: %r", reading_date_str)
            continue
        entries.append({
            "date": reading_date,
            "meter_reading": float(meter_reading_str.replace(",", "")),
            "therms": float(therms_str.replace(",", "")),
        })
    return entries


# ─── Statistics import ─────────────────────────────────────────────────────────

def compute_statistics_entries(billing_rows, tz):
    entries = []
    for row in sorted(billing_rows, key=lambda r: r["date"]):
        start = datetime.combine(row["date"], datetime.min.time()).replace(tzinfo=tz)
        entries.append({
            "start": start.isoformat(),
            "state": round(row["therms"] * THERM_TO_KWH, 3),
            "sum": round(row["meter_reading"] * THERM_TO_KWH, 3),
        })
    return entries


async def get_ha_time_zone():
    headers = {"Authorization": f"Bearer {SUPERVISOR_TOKEN}"}
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{SUPERVISOR_API_BASE}/config", headers=headers) as resp:
            resp.raise_for_status()
            data = await resp.json()
    return ZoneInfo(data["time_zone"])


async def import_cycle_statistics(entries):
    if not entries:
        return

    command = {
        "id": 1,
        "type": "recorder/import_statistics",
        "metadata": {
            "has_sum": True,
            "name": "CenterPoint Gas Usage",
            "source": "centerpoint_gas",
            "statistic_id": STATISTIC_ID,
            "unit_of_measurement": "kWh",
            "mean_type": 0,
            "unit_class": "energy",
        },
        "stats": entries,
    }

    headers = {"Authorization": f"Bearer {SUPERVISOR_TOKEN}"}
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(SUPERVISOR_WS_URL, headers=headers) as ws:
            auth_required = await ws.receive_json()
            if auth_required.get("type") != "auth_required":
                raise RuntimeError(f"Unexpected HA WebSocket handshake message: {auth_required}")

            await ws.send_json({"type": "auth", "access_token": SUPERVISOR_TOKEN})
            auth_result = await ws.receive_json()
            if auth_result.get("type") != "auth_ok":
                raise RuntimeError(f"HA WebSocket auth failed: {auth_result}")

            await ws.send_json(command)
            result = await ws.receive_json()
            if not result.get("success"):
                raise RuntimeError(f"recorder/import_statistics failed: {result}")


# ─── Entry point ───────────────────────────────────────────────────────────────

async def main():
    raw_rows = await scrape_gas_usage()
    billing_rows = parse_billing_rows(raw_rows)
    if not billing_rows:
        log.warning("No billing-history rows parsed, nothing to import")
        return

    # The table has no pagination and shows at most 24 rows already sorted
    # newest-first, so this just keeps the most recent CYCLES_BACK of
    # whatever was scraped.
    billing_rows = sorted(billing_rows, key=lambda r: r["date"], reverse=True)[:CYCLES_BACK]

    try:
        if not SUPERVISOR_TOKEN:
            raise RuntimeError("SUPERVISOR_TOKEN not set -- is 'homeassistant_api: true' configured?")
        tz = await get_ha_time_zone()
        entries = compute_statistics_entries(billing_rows, tz)
        await import_cycle_statistics(entries)
        log.info(
            "Imported %d billing-cycle statistics entries (%s .. %s)",
            len(entries), entries[0]["start"], entries[-1]["start"],
        )
    except Exception:
        log.exception("Statistics import failed -- will retry next run")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        log.exception("Run failed")
        sys.exit(1)
