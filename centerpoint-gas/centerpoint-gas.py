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
entry. Therms are converted to CCF (THERM_TO_CCF below) since HA's Energy
Dashboard gas-source unit picker doesn't accept therms directly. Unlike the
therm-to-kWh conversion this replaced, this one is NOT exact -- see
THERM_TO_CCF's comment.

Login and table-scrape selectors were confirmed against the real login page
markup (CenterPoint uses Azure AD B2C's default self-asserted sign-in
template -- #signInName/#password/#rememberMe/#next are the platform's own
field IDs, not guesses) and the real billing-history table sample provided
during development. The 2FA email itself (sender, subject, code format) is
also confirmed against a real captured message -- it's Microsoft's own
Azure B2C verification-email service, not a centerpointenergy.com address.
The 2FA flow itself has an MFA method-choice step confirmed via a real
screenshot of a manual login -- "Phone" is pre-selected by default, so
Email must be explicitly chosen since only Gmail retrieval is implemented.
Still unconfirmed: the actual verification-code *entry page/field*
(`input[name="verificationCode"]`) that follows the method choice, since a
real login has never gotten that far yet -- only the method-choice step has
real data to go on.
"""

import asyncio
import email as email_module
import imaplib
import logging
import os
import re
import secrets
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import aiohttp
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("centerpoint_gas")

# ─── Configuration ────────────────────────────────────────────────────────────

CENTERPOINT_USERNAME = os.environ["CENTERPOINT_USERNAME"]
CENTERPOINT_PASSWORD = os.environ["CENTERPOINT_PASSWORD"]
# Optional -- only ever needed if CenterPoint actually challenges a login
# with 2FA, which hasn't happened in any real run yet (repeated logins have
# all skipped it, cause unconfirmed). Not required for the add-on to work.
GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
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


# The billing-history URL needs account-specific MeterNumber/Installation
# query params that the SPA only resolves through its own client-side
# account-selection state -- confirmed via a live run that a fresh
# page.goto() straight to that URL (without those params, bypassing that
# state entirely) lands on a generic /Error/Index page instead of the real
# table. So rather than construct that URL ourselves, this replicates the
# real two-step click-through confirmed against the live site: from account
# home, click "View Usage" (-> /UsageView/Index?ShowMeterInfo=True&ST=Gas),
# then "View Historical Energy Usage" (-> the real, fully-populated
# /UsageView/UsageHistory?MeterNumber=...&Installation=...&Lob=20&... URL).
_USAGE_NAV_STEPS = ["View Usage", "View Historical Energy Usage"]


async def _safe_body_text(page, limit=2000):
    """document.body.innerText for diagnostics, tolerant of the page being
    mid-navigation.

    Confirmed necessary after a live run: a diagnostic snapshot taken right
    after a fast redirect (e.g. following the MFA method-choice page) can
    land in a transient window where <body> doesn't exist yet, or the whole
    execution context has been torn down -- either way raising a *new*
    error that masks whatever the original failure actually was. This must
    never itself crash, since it only exists to help explain a failure.
    """
    try:
        text = await page.evaluate(
            f"() => document.body ? document.body.innerText.slice(0, {limit}) : null"
        )
        return text if text is not None else "(page had no <body> yet -- likely mid-navigation)"
    except Exception as e:
        return f"(could not read page text, page was likely mid-navigation: {e})"


async def _navigate_to_billing_history(page):
    for step_text in _USAGE_NAV_STEPS:
        locator = page.get_by_text(step_text, exact=False)
        if await locator.count() == 0:
            body_snippet = await _safe_body_text(page)
            all_links = await page.evaluate(
                "() => Array.from(document.querySelectorAll('a')).map(a => a.innerText.trim()).filter(Boolean)"
            )
            log.error("Could not find the %r link on the current page.", step_text)
            log.error("Page URL: %s", page.url)
            log.error("Page body text: %r", body_snippet)
            log.error("All link texts found: %r", all_links)
            raise RuntimeError(f"Could not find the {step_text!r} link -- see debug output above")

        log.info("Clicking %r", step_text)
        await locator.first.click()
        await page.wait_for_load_state("load")

# `/data` is this add-on's own private persistent volume (unlike `/share`,
# provisioned automatically with no `map:` entry needed) -- appropriate here
# since this file holds live session credentials, not just app data.
SESSION_STATE_PATH = "/data/centerpoint_state.json"

# `homeassistant_api: true` in config.yaml makes Supervisor inject this token
# and proxy these two hosts to HA Core -- no manual long-lived token needed.
SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN")
SUPERVISOR_API_BASE = "http://supervisor/core/api"
SUPERVISOR_WS_URL = "ws://supervisor/core/websocket"

# New statistic_id (not the original `centerpoint_gas:cycle_usage`) --
# that one already has real kWh history imported. Changing its unit under
# the same ID risks HA either rejecting the metadata change or silently
# mixing kWh and CCF values together (CCF values are ~29x smaller than the
# equivalent kWh, which would look badly wrong on a chart). Migrating means
# removing the old kWh source from the Energy Dashboard and adding this one.
STATISTIC_ID = "centerpoint_gas:cycle_usage_ccf"

# HA's Energy Dashboard gas-source unit picker accepts volume units
# (CCF/ft³/L/MCF/m³) and energy units (cal/Gcal/GJ/GWh/J/kcal/kJ/kWh/Mcal/MJ/
# mWh/MWh/TWh/Wh) -- confirmed directly against a real HA instance -- but NOT
# therms directly.
#
# IMPORTANT: unlike the exact therm<->kWh conversion this replaced (a fixed
# physical definition, no ambiguity), therm<->CCF depends on the actual
# heating value (BTU/cubic-foot) of the specific gas delivered, which varies
# by region/season/supplier -- CenterPoint's own billing-history table never
# exposes the exact factor it used for a given cycle, only Therms and the
# cumulative Meter Reading (already in therm-equivalent units). 1.037 is a
# commonly-cited industry-average heating value (source:
# https://www.paenergyratings.com/resources/natural-gas-units), not this
# account's actual real factor for any given cycle -- expect the resulting
# CCF figures to be off by roughly 1-2% from what CenterPoint's own systems
# would show as the true metered volume.
THERM_TO_CCF = 1.037


# ─── Gmail 2FA code retrieval ──────────────────────────────────────────────────
#
# CenterPoint's login sometimes (not always -- the device gets remembered
# between runs via SESSION_STATE_PATH) challenges with an emailed 2FA code.
# This polls Gmail via IMAP with an app password rather than the account
# password -- note this grants read access to the whole mailbox, not just
# CenterPoint's emails, since an app password can't be scoped narrower.
#
# Sender/subject/code-format confirmed against a real captured 2FA email:
# it's sent by Microsoft's own Azure B2C "IdentityExperienceFramework"
# verification-email service on CenterPoint's behalf (not from any
# centerpointenergy.com address, matching CenterPoint's B2C-based login),
# single-part text/html, quoted-printable encoded, body reading "Your code
# is: <6 digits>".

_2FA_SENDER = "msonlineservicesteam@microsoftonline.com"
_2FA_SUBJECT = "verification code"
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
        status, data = imap.search(
            None, f'(SINCE "{since_str}" FROM "{_2FA_SENDER}" SUBJECT "{_2FA_SUBJECT}")'
        )
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
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        raise RuntimeError(
            "CenterPoint is asking for a 2FA code, but gmail_address/"
            "gmail_app_password aren't configured -- set both in this "
            "add-on's config to enable automatic code retrieval, or log "
            "into CenterPoint manually once from the same network to "
            "refresh the remembered-device session."
        )
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_seconds
    while loop.time() < deadline:
        code = await asyncio.to_thread(_search_gmail_for_code, after_time)
        if code:
            return code
        await asyncio.sleep(poll_interval)
    raise RuntimeError("Timed out waiting for CenterPoint's 2FA code email")


# ─── Browser automation ───────────────────────────────────────────────────────

async def _goto_checked(page, url, **kwargs):
    """page.goto() that logs (but does not fail on) a non-2xx/3xx response.

    Originally raised unconditionally on a bad status, added after a live
    run where CenterPoint's account-home URL returned a plain HTTP 404 with
    a genuinely broken/empty page. But a *second* live run showed this
    site's B2C login redirect can report HTTP 404 on a completely normal,
    fully-working sign-in page too (very likely a client-side-routed SPA
    quirk, where the server 404s the literal path while the SPA's own JS
    still renders the correct page) -- so the raw status code alone isn't a
    reliable signal on this site and raising on it produces false
    positives. Downstream content-based checks (_needs_login,
    _navigate_to_billing_history's own diagnostics) are the real source of
    truth for whether something is actually broken; this only logs the
    status code as a diagnostic breadcrumb.
    """
    response = await page.goto(url, **kwargs)
    if response is not None and response.status >= 400:
        log.warning(
            "Navigating to %s returned HTTP %d -- continuing, since this "
            "site is known to report misleading status codes on otherwise "
            "working pages; downstream checks will catch it if this page "
            "is genuinely broken.",
            url, response.status,
        )
    return response


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
    #
    # BUT: credentials being accepted can lead to two different outcomes,
    # both still served from login.centerpointenergy.com -- a direct
    # redirect away (no MFA), or an MFA method-choice page ("Multi-factor
    # Authentication", confirmed via a real screenshot of a manual login).
    # Waiting only for "left the login domain" would misdiagnose a real MFA
    # challenge as a failed/stuck login, since the method-choice page (and
    # whatever code-entry page follows it) never leaves that domain until
    # after MFA actually completes.
    try:
        await page.wait_for_function(
            "() => !location.host.includes('login.centerpointenergy.com') || "
            "(document.body && document.body.innerText.includes('Multi-factor Authentication'))",
            timeout=30_000,
        )
    except Exception:
        body_snippet = await _safe_body_text(page)
        log.error(
            "Still on the login page 30s after submitting -- likely bad "
            "credentials or an on-page validation error. Page text: %r",
            body_snippet,
        )
        raise RuntimeError("Login did not proceed past the sign-in page -- see logged page text above")

    # "networkidle" is unreliable on real-world sites (confirmed via a live
    # run: it hung the full 30s timeout here even though "load" had already
    # fired) -- many pages have persistent background network activity
    # (analytics, heartbeats, etc.) that never goes fully idle. "load" is a
    # much more robust signal that the page itself is actually usable.
    await page.wait_for_load_state("load")

    if "login.centerpointenergy.com" not in page.url:
        log.info("No 2FA challenge -- device appears to be remembered")
        return

    # MFA method-choice page confirmed via a real screenshot: "Phone" is
    # pre-selected by default, but we can only retrieve a code via Gmail
    # IMAP, not SMS/phone, so Email must be explicitly selected.
    log.info("MFA method-choice page detected, selecting Email")
    email_option = page.get_by_label("Email")
    if await email_option.count() == 0:
        email_option = page.get_by_text("Email", exact=True)
    await email_option.first.click()
    await page.get_by_role("button", name="Continue").click()

    # Confirmed via a live run: clicking Continue shows a "Please Wait...
    # do not close this window" processing overlay while the code is
    # actually being sent -- the same class of issue as the initial
    # credentials submission (an async JS handler, not a plain page load),
    # so wait_for_load_state("load") alone can resolve before that overlay
    # clears and the real next page replaces it. Wait for the overlay text
    # to disappear first; fall through to the diagnostic dump below either
    # way if it doesn't within the timeout, same pattern as
    # scrape_billing_history's wait for "Reading Date".
    try:
        await page.wait_for_function(
            "() => !document.body || !document.body.innerText.includes('Please Wait')",
            timeout=30_000,
        )
    except Exception:
        pass
    await page.wait_for_load_state("load")

    # TODO: unconfirmed -- the actual code-entry page has never been seen,
    # only the preceding method-choice step has. Update the selector once
    # seen for real; the diagnostic dump below should give what's needed.
    if await page.locator('input[name="verificationCode"]').count() > 0:
        log.info("2FA challenge detected, fetching code from Gmail")
        code = await fetch_2fa_code(after_time=login_start_time)
        await page.fill('input[name="verificationCode"]', code)
        await page.click('button[type="submit"]')
        await page.wait_for_load_state("load")
    elif "login.centerpointenergy.com" in page.url:
        body_snippet = await _safe_body_text(page)
        all_inputs = await page.evaluate(
            "() => Array.from(document.querySelectorAll('input')).map("
            "i => ({name: i.name, id: i.id, type: i.type}))"
        )
        log.error(
            "Still on the login domain after the MFA method choice, and no "
            "known verification-code field found. Page text: %r", body_snippet,
        )
        log.error("All input fields found: %r", all_inputs)
        raise RuntimeError("Could not find the verification-code field -- see debug output above")


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
                bodySnippet: document.body ? document.body.innerText.slice(0, 2000) : null,
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

        try:
            log.info("Navigating to account home")
            await _goto_checked(page, _B2C_REDIRECT_URI, wait_until="load")

            if await _needs_login(page):
                if "login.centerpointenergy.com" not in page.url:
                    # The app didn't auto-redirect us to the sign-in page --
                    # navigate there explicitly rather than assume it always
                    # will.
                    log.info("Not auto-redirected to login -- navigating there directly")
                    await _goto_checked(page, _build_login_url(), wait_until="load")
                await _login_with_2fa(page)
                await _goto_checked(page, _B2C_REDIRECT_URI, wait_until="load")

            await _navigate_to_billing_history(page)
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
    """Spread each cycle's usage evenly across the days between meter reads.

    CenterPoint only ever gives us one cumulative reading per ~30-day cycle,
    so there's no real information about how usage actually varied within a
    cycle -- this divides each cycle's total evenly across its days (an
    estimate, logged as such) so the Energy Dashboard's daily view shows
    something continuous instead of one spike every ~30 days followed by
    zeroes. Each day's cumulative `sum` still lands exactly on the real
    meter reading at the cycle's actual boundary, so weekly/monthly views
    (which just sum whatever's in range) are unaffected either way.

    The oldest row has no prior reading to anchor a real cycle length
    against, so it's imported as a single-day entry on its own reading date
    rather than guessing how long that first cycle ran.
    """
    ordered = sorted(billing_rows, key=lambda r: r["date"])
    entries = []

    for i, row in enumerate(ordered):
        cur_date = row["date"]
        cur_sum_ccf = round(row["meter_reading"] * THERM_TO_CCF, 3)

        if i == 0:
            start = datetime.combine(cur_date, datetime.min.time()).replace(tzinfo=tz)
            entries.append({
                "start": start.isoformat(),
                "state": round(row["therms"] * THERM_TO_CCF, 3),
                "sum": cur_sum_ccf,
            })
            continue

        prev_date = ordered[i - 1]["date"]
        prev_sum_ccf = round(ordered[i - 1]["meter_reading"] * THERM_TO_CCF, 3)
        cycle_days = (cur_date - prev_date).days
        if cycle_days <= 0:
            log.warning("Skipping row with non-increasing reading date: %s", cur_date)
            continue

        daily_ccf = round((cur_sum_ccf - prev_sum_ccf) / cycle_days, 3)
        log.info(
            "Spreading %.3f CCF (estimated, see THERM_TO_CCF) evenly across "
            "%d day(s) between %s and %s (%.3f CCF/day -- an averaged "
            "estimate, not measured daily usage)",
            cur_sum_ccf - prev_sum_ccf, cycle_days, prev_date, cur_date, daily_ccf,
        )

        for day_offset in range(1, cycle_days + 1):
            day = prev_date + timedelta(days=day_offset)
            start = datetime.combine(day, datetime.min.time()).replace(tzinfo=tz)
            # Land exactly on the real cumulative reading on the actual
            # meter-read date, rather than compounding rounding error
            # across the cycle.
            day_sum_ccf = (
                cur_sum_ccf if day_offset == cycle_days
                else round(prev_sum_ccf + daily_ccf * day_offset, 3)
            )
            entries.append({
                "start": start.isoformat(),
                "state": daily_ccf,
                "sum": day_sum_ccf,
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
            "name": "CenterPoint Gas Usage (CCF, estimated)",
            "source": "centerpoint_gas",
            "statistic_id": STATISTIC_ID,
            "unit_of_measurement": "CCF",
            "mean_type": 0,
            "unit_class": "volume",
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
    # newest-first. Keep one extra row before the CYCLES_BACK window so the
    # oldest cycle we care about still has a real prior reading to spread
    # its days against -- otherwise it would fall back to a single-day
    # spike for lack of an anchor, same as the very first row always does.
    billing_rows = sorted(billing_rows, key=lambda r: r["date"])
    windowed_rows = billing_rows[-(CYCLES_BACK + 1):]
    has_anchor_row = len(windowed_rows) > CYCLES_BACK

    try:
        if not SUPERVISOR_TOKEN:
            raise RuntimeError("SUPERVISOR_TOKEN not set -- is 'homeassistant_api: true' configured?")
        tz = await get_ha_time_zone()
        entries = compute_statistics_entries(windowed_rows, tz)
        if has_anchor_row:
            # Drop the leading anchor row's own single-day entry -- it only
            # exists to give the oldest cycle we care about a real prior
            # reading to spread against, not something we want to submit.
            entries = entries[1:]
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
