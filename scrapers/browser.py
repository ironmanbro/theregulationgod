"""Headless-browser page rendering for JavaScript-driven regulator sites.

Uses the Chromium that ships with Playwright. Set CHROMIUM_PATH to point at a different
binary. Unset, Playwright uses the browser it installed.
"""
import os

from playwright.sync_api import sync_playwright

_pw = None
_browser = None


def _get_browser():
    global _pw, _browser
    if _browser is None:
        _pw = sync_playwright().start()
        opts = {"args": ["--no-sandbox"]}
        if os.environ.get("CHROMIUM_PATH"):
            opts["executable_path"] = os.environ["CHROMIUM_PATH"]
        _browser = _pw.chromium.launch(**opts)
    return _browser


def render_html(url: str, timeout_ms: int = 60000) -> str:
    page = _get_browser().new_page()
    try:
        page.goto(url, wait_until="networkidle", timeout=timeout_ms)
        return page.content()
    finally:
        page.close()
