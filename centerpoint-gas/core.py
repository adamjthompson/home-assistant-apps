#!/usr/bin/env python3
"""Run the CenterPoint collector against a standalone Home Assistant Core API."""

import asyncio
import importlib.util
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required for a standalone Core deployment")
    return value


def _load_collector():
    source = Path(__file__).with_name("centerpoint-gas.py")
    spec = importlib.util.spec_from_file_location("centerpoint_gas_collector", source)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load the CenterPoint collector")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


async def main() -> None:
    ha_url = _required("HA_URL").rstrip("/")
    token = _required("HA_TOKEN")
    parsed = urlsplit(ha_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise RuntimeError("HA_URL must be an absolute http:// or https:// URL")

    collector = _load_collector()
    collector.SUPERVISOR_TOKEN = token
    collector.SUPERVISOR_API_BASE = f"{ha_url}/api"
    collector.SUPERVISOR_WS_URL = urlunsplit((
        "wss" if parsed.scheme == "https" else "ws",
        parsed.netloc,
        f"{parsed.path.rstrip('/')}/api/websocket",
        "",
        "",
    ))
    await collector.main()


if __name__ == "__main__":
    asyncio.run(main())
