#!/usr/bin/env python3
"""
AES Ohio Energy Usage
Logs into myprofile.aes-ohio.com, follows the SAML/OIDC handoff to AES's
Opower-hosted usage portal (aeso.opower.com), downloads the interval usage
export, and publishes the latest day's total to Home Assistant via MQTT.

Login itself is a three-domain federation (AES Ohio ASP.NET WebForms ->
Oracle Identity Cloud Service SAML/OAuth -> Opower), so a real browser
(Playwright) drives it rather than hand-replicating every redirect.
"""

import asyncio
import csv
import io
import json
import logging
import os
import re
import sys
import zipfile
from datetime import datetime, timedelta

import paho.mqtt.publish as mqtt_publish
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("ohio_aes")

# ─── Configuration ────────────────────────────────────────────────────────────

AES_USERNAME = os.environ["AES_USERNAME"]
AES_PASSWORD = os.environ["AES_PASSWORD"]
MQTT_HOST = os.environ.get("MQTT_HOST", "core-mosquitto")
MQTT_PORT = int(os.environ.get("MQTT_PORT", 1883))
MQTT_USER = os.environ.get("MQTT_USER", "") or None
MQTT_PASS = os.environ.get("MQTT_PASS", "") or None
MQTT_TOPIC = os.environ.get("MQTT_TOPIC", "homeassistant/aes/usage")
DAYS_BACK = int(os.environ.get("DAYS_BACK", 3))

CHROMIUM_PATH = os.environ.get("CHROMIUM_PATH", "/usr/bin/chromium-browser")
DEBUG_SCREENSHOT = "/share/ohio_aes_debug.png"
DEBUG_HTML = "/share/ohio_aes_debug.html"
DOWNLOAD_PATH = "/tmp/ohio_aes_export.zip"

LOGIN_URL = "https://myprofile.aes-ohio.com/Profile/Login.aspx"


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
            # is CSS hover-only and closes mid-click before Playwright can reach it,
            # so rather than fight that we open the known target URL directly in a
            # new tab in the same (already-authenticated) browser context.
            log.info("Navigating to Your Energy Use")
            page = await context.new_page()
            await page.goto("http://aeso.opower.com/ei/x/dashboard", wait_until="networkidle")
            await page.wait_for_url(re.compile(r"aeso\.opower\.com"), timeout=45_000)

            # NOTE: also unconfirmed against the live "Download my data" modal --
            # check /share/ohio_aes_debug.png if this section fails.
            log.info("Opening 'Download my data'")
            await page.get_by_text("Download my data", exact=False).click()
            await page.get_by_text("Export usage for a range of days", exact=False).click()

            end_date = datetime.now().date() - timedelta(days=1)
            start_date = end_date - timedelta(days=DAYS_BACK - 1)
            await page.get_by_label("From").fill(start_date.strftime("%m/%d/%Y"))
            await page.get_by_label("To").fill(end_date.strftime("%m/%d/%Y"))
            await page.get_by_text("CSV", exact=True).click()

            log.info("Requesting export for %s to %s", start_date, end_date)
            async with page.expect_download(timeout=120_000) as download_info:
                await page.get_by_role("button", name="Export").click()
            download = await download_info.value
            await download.save_as(DOWNLOAD_PATH)

        except Exception:
            log.exception("Automation failed -- saving debug screenshot and HTML to /share")
            try:
                await page.screenshot(path=DEBUG_SCREENSHOT, full_page=True)
                with open(DEBUG_HTML, "w") as f:
                    f.write(await page.content())
            except Exception:
                log.exception("Could not save debug artifacts")
            raise
        finally:
            await browser.close()

    return DOWNLOAD_PATH


# ─── CSV parsing ───────────────────────────────────────────────────────────────

def parse_daily_totals(zip_path):
    with zipfile.ZipFile(zip_path) as z:
        csv_name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        raw = z.read(csv_name).decode("utf-8-sig")

    # The export has a few "Name,/Address,/Account Number," metadata lines
    # before the real interval table -- find the actual header row.
    lines = raw.splitlines()
    header_idx = next(i for i, line in enumerate(lines) if line.startswith("TYPE,DATE"))
    reader = csv.DictReader(lines[header_idx:])

    daily_totals = {}
    for row in reader:
        if row.get("TYPE") != "Electric usage":
            continue
        date = row["DATE"]
        usage = float(row["USAGE (kWh)"] or 0)
        daily_totals[date] = daily_totals.get(date, 0.0) + usage

    return daily_totals


# ─── MQTT publish ──────────────────────────────────────────────────────────────

def publish_to_mqtt(daily_totals):
    if not daily_totals:
        log.warning("No usage data parsed, nothing to publish")
        return

    latest_date = max(daily_totals)
    latest_kwh = round(daily_totals[latest_date], 3)
    log.info("Publishing %s kWh for %s to %s", latest_kwh, latest_date, MQTT_TOPIC)

    payload = json.dumps({"date": latest_date, "kwh": latest_kwh})
    auth = {"username": MQTT_USER, "password": MQTT_PASS} if MQTT_USER else None

    mqtt_publish.single(
        MQTT_TOPIC,
        payload=payload,
        retain=True,
        hostname=MQTT_HOST,
        port=MQTT_PORT,
        auth=auth,
    )


# ─── Entry point ───────────────────────────────────────────────────────────────

async def main():
    zip_path = await download_usage_export()
    daily_totals = parse_daily_totals(zip_path)
    publish_to_mqtt(daily_totals)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        log.exception("Run failed")
        sys.exit(1)
