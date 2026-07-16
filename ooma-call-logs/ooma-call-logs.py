#!/usr/bin/env python3
"""
Ooma Call Logs
Logs into Ooma's customer portal (behind Cloudflare) via a separately-run
FlareSolverr instance (yours, or any other -- configured via
FLARESOLVERR_URL), reads the recent call log, and updates a Home Assistant
sensor directly via the Supervisor API.

Ported from a working Node-RED flow -- the login/scrape logic (FlareSolverr
session handling, CSRF-token extraction, native POST login, HTML table
parsing) mirrors that flow's function nodes directly, translated to Python.
Only the initial login-page GET goes through FlareSolverr (matching the
original flow's design, since spinning up a full browser session has real
cost); the POST login and GET call-logs are direct aiohttp calls reusing the
resulting cookies.

Bundling FlareSolverr in this same container was tried and reverted -- it
hit a real, unresolved upstream "chrome not reachable" bug (see CHANGELOG
0.2.0) with no confirmed fix, and even if it had worked, bundling would
have risked users ending up with two redundant FlareSolverr instances if
they already run one. This add-on is a standard ohio-aes/centerpoint-gas-
style bashio-based add-on again: config comes from env vars set by run.sh
via bashio::config, and this script runs once per invocation (the bash loop
in run.sh handles repetition on run_interval_minutes).
"""

import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

import aiohttp

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("ooma_call_logs")

# ─── Configuration ────────────────────────────────────────────────────────────

OOMA_USERNAME = os.environ["OOMA_USERNAME"]
OOMA_PASSWORD = os.environ["OOMA_PASSWORD"]

# Points at a separately-run FlareSolverr instance -- yours, or any other.
# Not bundled in this container (see module docstring).
FLARESOLVERR_URL = os.environ["FLARESOLVERR_URL"].rstrip("/") + "/v1"
SESSION_ID = "ooma_session"

LOGIN_URL = "https://my.ooma.com/login"
CALL_LOGS_URL = "https://my.ooma.com/phone/call_logs"

# `homeassistant_api: true` in config.yaml makes Supervisor inject this token
# regardless of base image -- no manual long-lived token needed.
SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN")
SUPERVISOR_API_BASE = "http://supervisor/core/api"

# Deliberately the same entity ID as the user's existing MQTT-triggered
# template sensor, so their current dashboard card keeps working unchanged.
SENSOR_ENTITY_ID = "sensor.ooma_call_feed"


# ─── FlareSolverr session handling ─────────────────────────────────────────────

async def flaresolverr_command(session, payload):
    async with session.post(
        FLARESOLVERR_URL, json=payload, timeout=aiohttp.ClientTimeout(total=70)
    ) as resp:
        text = await resp.text()
        if resp.status != 200:
            raise RuntimeError(f"FlareSolverr returned {resp.status}: {text}")
        return json.loads(text)


async def destroy_session(session):
    # The original Node-RED flow never checked this step's result either --
    # it unconditionally moved on to create a fresh session regardless.
    # FlareSolverr errors if asked to destroy a session that doesn't exist
    # (e.g. this add-on's very first run), which is an expected, harmless
    # condition, not a real failure -- don't let it block session creation.
    try:
        await flaresolverr_command(session, {"cmd": "sessions.destroy", "session": SESSION_ID})
    except Exception as e:
        log.info("Ignoring sessions.destroy failure (probably no prior session to destroy): %s", e)


async def create_session(session):
    await flaresolverr_command(session, {"cmd": "sessions.create", "session": SESSION_ID})


async def flaresolverr_get(session, url):
    result = await flaresolverr_command(session, {
        "cmd": "request.get",
        "url": url,
        "session": SESSION_ID,
        "maxTimeout": 60_000,
    })
    return result.get("solution", {})


# ─── Login ──────────────────────────────────────────────────────────────────────

_CSRF_META_RE = re.compile(r'name="csrf-token"\s+content="([^"]+)"', re.IGNORECASE)
_CSRF_FORM_RE = re.compile(r'name="authenticity_token"\s*value="([^"]+)"', re.IGNORECASE)
_SESSION_COOKIE_RE = re.compile(r'_myooma2_session=([^;]+)')

_DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


def extract_csrf_token(html):
    match = _CSRF_META_RE.search(html) or _CSRF_FORM_RE.search(html)
    return match.group(1) if match else None


async def native_post_login(session, csrf_token, cookie_str, user_agent, username, password):
    post_data = (
        f"authenticity_token={quote(csrf_token, safe='')}"
        f"&username={quote(username, safe='')}"
        f"&password={quote(password, safe='')}"
        "&remember_me=on"
        "&button="
    )
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Cookie": cookie_str,
        "Origin": "https://my.ooma.com",
        "Referer": LOGIN_URL,
        "User-Agent": user_agent,
        "Upgrade-Insecure-Requests": "1",
    }
    # allow_redirects=False so we can inspect the 30x response's own
    # Set-Cookie header directly, same as the original flow's statusCode
    # 301/302 branch (its "redirectList" fallback branch isn't needed here
    # since this always gets the raw redirect response).
    async with session.post(
        LOGIN_URL, data=post_data, headers=headers, allow_redirects=False
    ) as resp:
        if resp.status not in (301, 302):
            raise RuntimeError(f"Login did not redirect as expected (status {resp.status})")
        for header_value in resp.headers.getall("Set-Cookie", []):
            match = _SESSION_COOKIE_RE.search(header_value)
            if match:
                return f"_myooma2_session={match.group(1)}"
    raise RuntimeError("Login failed -- no _myooma2_session cookie in the response")


async def native_get_call_logs(session, cookie_str, user_agent):
    headers = {
        "Cookie": cookie_str,
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Referer": "https://my.ooma.com/dashboard",
        "Upgrade-Insecure-Requests": "1",
    }
    async with session.get(CALL_LOGS_URL, headers=headers) as resp:
        resp.raise_for_status()
        return await resp.text()


# ─── Call-log HTML parsing ──────────────────────────────────────────────────────

# Direct port of the Node-RED flow's regex-based extractor. Note these
# capture the FULL <td>...</td> string (tags included) per cell, not just
# its inner text -- the type-detection step below relies on inspecting a
# cell's raw markup (e.g. an icon-down/icon-up CSS class), same as the
# original flow.
_ROW_RE = re.compile(r'<tr[^>]*>[\s\S]*?</tr>', re.IGNORECASE)
_CELL_RE = re.compile(r'<td[^>]*>[\s\S]*?</td>', re.IGNORECASE)
_TAG_RE = re.compile(r'<[^>]*>')
_MISSED_RE = re.compile(r'missed|cancel', re.IGNORECASE)
_INCOMING_RE = re.compile(r'incoming|inbound|icon-down', re.IGNORECASE)
_OUTGOING_RE = re.compile(r'outgoing|outbound|icon-up', re.IGNORECASE)


def parse_call_logs_html(html):
    if not html or not isinstance(html, str):
        return {"calls": [], "count": 0, "status": "error"}

    calls = []
    for row_html in _ROW_RE.findall(html):
        cells = _CELL_RE.findall(row_html)
        if len(cells) < 6:
            continue

        raw_type_cell = cells[1].lower()
        if _MISSED_RE.search(raw_type_cell):
            call_type = "Missed"
        elif _INCOMING_RE.search(raw_type_cell):
            call_type = "Incoming"
        elif _OUTGOING_RE.search(raw_type_cell):
            call_type = "Outgoing"
        else:
            call_type = "Unknown"

        clean_cells = [_TAG_RE.sub("", c).replace("&nbsp;", " ").strip() for c in cells]

        extracted_name = clean_cells[3] if len(clean_cells) > 3 else ""
        if not extracted_name and call_type == "Outgoing":
            extracted_name = "OUTGOING"

        record = {
            "type": call_type,
            "number": clean_cells[2] if len(clean_cells) > 2 else "",
            "name": extracted_name,
            "date": clean_cells[4] if len(clean_cells) > 4 else "",
            "duration": clean_cells[5] if len(clean_cells) > 5 else "",
        }

        if len(record["number"]) >= 7:
            calls.append(record)

    return {
        "calls": calls,
        "count": len(calls),
        "status": "success" if calls else "empty_table",
    }


# ─── Home Assistant sensor update ───────────────────────────────────────────────

async def get_ha_time_zone(session):
    headers = {"Authorization": f"Bearer {SUPERVISOR_TOKEN}"}
    async with session.get(f"{SUPERVISOR_API_BASE}/config", headers=headers) as resp:
        resp.raise_for_status()
        data = await resp.json()
    return ZoneInfo(data["time_zone"])


async def update_ha_sensor(session, result, tz):
    # Matches the shape of the user's existing MQTT-triggered template
    # sensor exactly (state = last-updated timestamp, attributes carry the
    # real data), on the same entity ID, so the existing dashboard card
    # needs no changes.
    state = datetime.now(tz).strftime("%Y-%m-%d %H:%M")
    headers = {"Authorization": f"Bearer {SUPERVISOR_TOKEN}", "Content-Type": "application/json"}
    body = {
        "state": state,
        "attributes": {
            "calls": result["calls"],
            "count": result["count"],
            "status": result["status"],
            "friendly_name": "Ooma Call Feed",
            "icon": "mdi:phone-log",
        },
    }
    async with session.post(
        f"{SUPERVISOR_API_BASE}/states/{SENSOR_ENTITY_ID}", headers=headers, json=body
    ) as resp:
        resp.raise_for_status()


# ─── Entry point ───────────────────────────────────────────────────────────────

async def main():
    async with aiohttp.ClientSession() as session:
        log.info("Purging any existing FlareSolverr session")
        await destroy_session(session)
        await create_session(session)

        log.info("Fetching login page via FlareSolverr")
        solution = await flaresolverr_get(session, LOGIN_URL)
        html = solution.get("response", "")
        cookies = solution.get("cookies", [])
        user_agent = solution.get("userAgent") or _DEFAULT_USER_AGENT

        csrf_token = extract_csrf_token(html)
        if not csrf_token:
            raise RuntimeError(
                f"FlareSolverr failed to load the login page -- no CSRF token "
                f"found (HTML length: {len(html)})"
            )

        cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)

        log.info("Logging in natively")
        session_cookie = await native_post_login(
            session, csrf_token, cookie_str, user_agent, OOMA_USERNAME, OOMA_PASSWORD
        )

        if "_myooma2_session" in cookie_str:
            final_cookies = _SESSION_COOKIE_RE.sub(session_cookie, cookie_str)
        else:
            final_cookies = f"{cookie_str}; {session_cookie}"

        log.info("Fetching call logs")
        logs_html = await native_get_call_logs(session, final_cookies, user_agent)
        result = parse_call_logs_html(logs_html)

        if not SUPERVISOR_TOKEN:
            raise RuntimeError("SUPERVISOR_TOKEN not set -- is 'homeassistant_api: true' configured?")
        tz = await get_ha_time_zone(session)
        await update_ha_sensor(session, result, tz)
        log.info(
            "Updated %s with %d call(s), status=%s",
            SENSOR_ENTITY_ID, result["count"], result["status"],
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        log.exception("Run failed")
        sys.exit(1)
