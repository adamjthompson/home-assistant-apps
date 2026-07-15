#!/usr/bin/env python3
"""
AES Ohio Energy Usage
Logs into myprofile.aes-ohio.com, follows the SAML/OIDC handoff to AES's
Opower-hosted usage portal (aeso.opower.com), downloads the interval usage
export, and imports the latest hourly totals into Home Assistant's long-term
statistics as an external statistic, so the Energy Dashboard shows correctly
-dated history (a plain sensor state can't be backdated -- HA timestamps
state changes at message-arrival time, not by any embedded date/time).

Login itself is a three-domain federation (AES Ohio ASP.NET WebForms ->
Oracle Identity Cloud Service SAML/OAuth -> Opower), so a real browser
(Playwright) drives it rather than hand-replicating every redirect.
"""

import asyncio
import csv
import json
import logging
import os
import re
import sys
import zipfile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import aiohttp
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("ohio_aes")

# ─── Configuration ────────────────────────────────────────────────────────────

AES_USERNAME = os.environ["AES_USERNAME"]
AES_PASSWORD = os.environ["AES_PASSWORD"]
DAYS_BACK = int(os.environ.get("DAYS_BACK", 3))

CHROMIUM_PATH = os.environ.get("CHROMIUM_PATH", "/usr/bin/chromium-browser")
DOWNLOAD_PATH = "/tmp/ohio_aes_export.zip"

LOGIN_URL = "https://myprofile.aes-ohio.com/Profile/Login.aspx"

# `homeassistant_api: true` in config.yaml makes Supervisor inject this token
# and proxy these two hosts to HA Core -- no manual long-lived token needed.
SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN")
SUPERVISOR_API_BASE = "http://supervisor/core/api"
SUPERVISOR_WS_URL = "ws://supervisor/core/websocket"

# "external" statistic (colon-separated, not a real entity/integration domain)
# -- this is what gets added as an Energy Dashboard grid-consumption source.
STATISTIC_ID = "ohio_aes:hourly_usage"
STATE_PATH = "/share/ohio_aes_state.json"
STATS_RETENTION_DAYS = DAYS_BACK + 2


# ─── Browser automation ───────────────────────────────────────────────────────

async def download_usage_export():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            executable_path=CHROMIUM_PATH,
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(accept_downloads=True)
        page = await context.new_page()

        try:
            log.info("Logging into myprofile.aes-ohio.com")
            await page.goto(LOGIN_URL, wait_until="networkidle")
            await page.fill('input[name="ctl00$MainContent$LoginControl$UserName"]', AES_USERNAME)
            await page.fill('input[name="ctl00$MainContent$LoginControl$Password"]', AES_PASSWORD)
            await page.click('input[name="ctl00$MainContent$LoginControl$btnLogin"]')
            await page.wait_for_load_state("networkidle")

            # "PowerView" (the "My Usage" dropdown's link to Opower) opens in a new
            # tab via the SAML handoff (AES -> Oracle IDCS -> Opower). Its dropdown
            # is CSS hover-only and closes mid-click before Playwright could reach
            # it, so rather than fight that we open the known target URL directly
            # in a new tab in the same (already-authenticated) browser context.
            log.info("Navigating to Your Energy Use")
            page = await context.new_page()
            await page.goto("http://aeso.opower.com/ei/x/dashboard", wait_until="networkidle")
            await page.wait_for_url(re.compile(r"aeso\.opower\.com"), timeout=45_000)

            # "Download my data" lives on the energy-use-details page, not the
            # dashboard -- the dashboard goto above only establishes the
            # authenticated Opower session.
            log.info("Navigating to Energy Use Details")
            await page.goto("https://aeso.opower.com/ei/x/energy-use-details/", wait_until="networkidle")

            log.info("Opening 'Download my data'")
            await page.get_by_text("Download my data", exact=False).click()
            await page.get_by_text("Export usage for a range of days", exact=False).click()

            end_date = datetime.now().date() - timedelta(days=1)
            start_date = end_date - timedelta(days=DAYS_BACK - 1)
            # get_by_label("From"/"To") is ambiguous here: these inputs' aria-label
            # ("select-date-to - Enter end date...") overrides the wrapping "To"
            # label text for accessible-name purposes, and "To" also substring-
            # matches unrelated elements (e.g. "Scroll to next bill range").
            await page.locator("#date-selector--select-date-from").fill(start_date.strftime("%m/%d/%Y"))
            await page.locator("#date-selector--select-date-to").fill(end_date.strftime("%m/%d/%Y"))
            await page.get_by_text("CSV", exact=True).click()

            log.info("Requesting export for %s to %s", start_date, end_date)
            async with page.expect_download(timeout=120_000) as download_info:
                await page.get_by_role("button", name="Export").click()
            download = await download_info.value
            await download.save_as(DOWNLOAD_PATH)

        except Exception:
            log.exception("Automation failed")
            raise
        finally:
            await browser.close()

    return DOWNLOAD_PATH


# ─── CSV parsing ───────────────────────────────────────────────────────────────

# The export's DATE column format has been observed as both "7/11/26" and
# "2026-07-13" across runs -- likely a locale difference between the
# container's headless Chromium and a regular browser -- so parsing is
# defensive against either rather than assuming one.
_EXPORT_DATE_FORMATS = ("%m/%d/%y", "%m/%d/%Y", "%Y-%m-%d")


def _parse_export_date(date_str):
    for fmt in _EXPORT_DATE_FORMATS:
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unrecognized export DATE format: {date_str!r}")


def parse_hourly_totals(zip_path):
    with zipfile.ZipFile(zip_path) as z:
        csv_name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        raw = z.read(csv_name).decode("utf-8-sig")

    # The export has a few "Name,/Address,/Account Number," metadata lines
    # before the real interval table -- find the actual header row.
    lines = raw.splitlines()
    header_idx = next(i for i, line in enumerate(lines) if line.startswith("TYPE,DATE"))
    reader = csv.DictReader(lines[header_idx:])

    hourly_totals = {}
    for row in reader:
        if row.get("TYPE") != "Electric usage":
            continue
        start_time = row.get("START TIME")
        if not start_time:
            continue
        date = _parse_export_date(row["DATE"])
        hour = int(start_time.split(":")[0])
        bucket = f"{date.isoformat()}T{hour:02d}:00:00"
        usage = float(row["USAGE (kWh)"] or 0)
        hourly_totals[bucket] = hourly_totals.get(bucket, 0.0) + usage

    return hourly_totals


# ─── Statistics import ─────────────────────────────────────────────────────────
#
# HA's `recorder/import_statistics` is WebSocket-only (there is no REST
# service for it), and requires `sum` to be a monotonically increasing
# cumulative running total -- not the discrete per-hour kWh amount -- since
# the Energy Dashboard computes displayed consumption as the delta between
# consecutive `sum` values. A local ledger in /share tracks that running
# total across runs (the container has no in-memory state between runs, and
# is the only persistent storage this add-on has). Re-submitting an hour
# that already exists safely overwrites it, so recomputing and resubmitting
# this run's whole overlap window every time is safe and self-correcting if
# AES revises a recent hour's value.

def load_state():
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"schema_version": 1, "hours": {}, "high_water_hour": None, "high_water_sum": 0.0}


def save_state(state):
    tmp_path = f"{STATE_PATH}.tmp"
    with open(tmp_path, "w") as f:
        json.dump(state, f)
    os.replace(tmp_path, STATE_PATH)


def trim_state_hours(state, retention_days):
    cutoff = datetime.now() - timedelta(days=retention_days)
    state["hours"] = {
        hour: data for hour, data in state["hours"].items()
        if datetime.fromisoformat(hour) >= cutoff
    }
    return state


def resolve_anchor(state, earliest_hour):
    # If this run's window starts exactly at an hour we already have local
    # history for (the common case, since days_back windows overlap by
    # design), back out that hour's own contribution to get the baseline
    # immediately before it -- rather than searching for a *strictly earlier*
    # local hour, which usually won't exist for exactly the preceding hour.
    if earliest_hour in state["hours"]:
        prior = state["hours"][earliest_hour]
        return earliest_hour, round(prior["sum"] - prior["kwh"], 3)

    # Otherwise, most recent locally-known hour strictly before this run's
    # window -- its sum is the correct baseline to accumulate from.
    earlier_hours = [h for h in state["hours"] if h < earliest_hour]
    if earlier_hours:
        anchor_hour = max(earlier_hours)
        return anchor_hour, state["hours"][anchor_hour]["sum"]

    high_water_hour = state.get("high_water_hour")
    high_water_sum = state.get("high_water_sum", 0.0)
    if high_water_hour and high_water_hour < earliest_hour:
        # Local per-hour retention has aged out past this point, but we know a
        # later cumulative sum exists -- resume from it. Only valid when the
        # high-water hour genuinely precedes this window (a real gap, e.g. the
        # add-on was down a while); otherwise this window's own hours already
        # cover everything up to high-water and recomputing from 0 below
        # reconstructs the same sums it produced last time.
        log.warning(
            "No local hourly history before %s; resuming from high-water hour %s "
            "(any gap between them is treated as 0 kWh)",
            earliest_hour, high_water_hour,
        )
        return high_water_hour, high_water_sum

    if not high_water_hour:
        log.warning("No prior statistics state found -- starting cumulative total at 0.0 from %s", earliest_hour)
    return None, 0.0


def compute_statistics_entries(hourly_totals, state):
    if not hourly_totals:
        return [], state

    hours = dict(state.get("hours", {}))

    # Never let a widened export window (e.g. `days_back` increased, then the
    # app restarted) reach further into the past than what we've already
    # established a reliable cumulative baseline for. There's no valid
    # anchor for hours older than our local retention, and recomputing them
    # from scratch would corrupt the existing (much larger) cumulative sum
    # for every hour after them -- a documented limitation, not a bug:
    # increasing `days_back` doesn't backfill history it newly exposes.
    if hours:
        earliest_known = min(hours)
        skipped = sorted(h for h in hourly_totals if h < earliest_known)
        if skipped:
            log.warning(
                "Ignoring %d hour(s) before %s (earliest hour with a known cumulative "
                "baseline) -- likely 'days_back' was increased; these can't be safely "
                "backfilled without corrupting already-imported statistics",
                len(skipped), earliest_known,
            )
            hourly_totals = {h: v for h, v in hourly_totals.items() if h >= earliest_known}
            if not hourly_totals:
                return [], state

    earliest_hour = min(hourly_totals)
    _, running = resolve_anchor(state, earliest_hour)

    entries = []
    for hour in sorted(hourly_totals):
        running = round(running + hourly_totals[hour], 3)
        hours[hour] = {"kwh": hourly_totals[hour], "sum": running}
        entries.append({"start": hour, "state": hourly_totals[hour], "sum": running})

    latest_hour = max(hourly_totals)
    high_water_hour = state.get("high_water_hour")
    high_water_sum = state.get("high_water_sum", 0.0)
    if not high_water_hour or latest_hour > high_water_hour:
        high_water_hour = latest_hour
        high_water_sum = hours[latest_hour]["sum"]

    new_state = {
        "schema_version": 1,
        "hours": hours,
        "high_water_hour": high_water_hour,
        "high_water_sum": high_water_sum,
    }
    return entries, new_state


async def get_ha_time_zone():
    headers = {"Authorization": f"Bearer {SUPERVISOR_TOKEN}"}
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{SUPERVISOR_API_BASE}/config", headers=headers) as resp:
            resp.raise_for_status()
            data = await resp.json()
    return ZoneInfo(data["time_zone"])


async def import_hourly_statistics(entries, tz):
    if not entries:
        return

    stats = [
        {
            "start": datetime.fromisoformat(entry["start"]).replace(tzinfo=tz).isoformat(),
            "state": entry["state"],
            "sum": entry["sum"],
        }
        for entry in entries
    ]
    command = {
        "id": 1,
        "type": "recorder/import_statistics",
        "metadata": {
            "has_sum": True,
            "name": "AES Ohio Hourly Usage",
            "source": "ohio_aes",
            "statistic_id": STATISTIC_ID,
            "unit_of_measurement": "kWh",
            "mean_type": 0,
            "unit_class": "energy",
        },
        "stats": stats,
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
    zip_path = await download_usage_export()
    hourly_totals = parse_hourly_totals(zip_path)
    if not hourly_totals:
        log.warning("No hourly usage data parsed, nothing to import")
        return

    state = load_state()
    entries, new_state = compute_statistics_entries(hourly_totals, state)

    try:
        if not SUPERVISOR_TOKEN:
            raise RuntimeError("SUPERVISOR_TOKEN not set -- is 'homeassistant_api: true' configured?")
        tz = await get_ha_time_zone()
        await import_hourly_statistics(entries, tz)
        save_state(trim_state_hours(new_state, STATS_RETENTION_DAYS))
        log.info("Imported %d hourly statistics entries (%s .. %s)", len(entries), entries[0]["start"], entries[-1]["start"])
    except Exception:
        log.exception("Statistics import failed -- will retry next run")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        log.exception("Run failed")
        sys.exit(1)
