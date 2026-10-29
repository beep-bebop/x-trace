"""Playwright login-session management for X.

The session is stored as a `storage_state.json` file produced by Playwright
after a successful interactive login. Subsequent crawls load this file to
recreate the authenticated browser context. If X invalidates the session,
``SessionExpiredError`` is raised and the user must run ``x-trace login``
again.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright, BrowserContext

from ..config import get_config

logger = logging.getLogger(__name__)


class SessionExpiredError(RuntimeError):
    """Raised when the saved storage_state no longer authenticates us to X."""


def _state_path() -> Path:
    return Path(get_config()["crawler"]["storage_state_path"])


def _proxy_arg() -> Optional[dict]:
    """Build Playwright's `proxy=` arg from config, or None when no proxy set."""
    url = get_config()["crawler"].get("proxy", "")
    if not url:
        return None
    return {"server": url}


def has_session() -> bool:
    p = _state_path()
    return p.exists() and p.stat().st_size > 0


async def login_interactive(headless: bool = False) -> Path:
    """Open a visible browser, let the user log into X, save the session.

    Returns the path to the saved storage_state file.
    """
    state_path = _state_path()
    state_path.parent.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless, proxy=_proxy_arg())
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto("https://x.com/login", wait_until="domcontentloaded")

        print("\n>>> Please log into X in the browser window.")
        print(">>> After you see your home timeline, return here and press Enter.")
        input()

        # Verify we are logged in by checking for a logged-in-only element
        try:
            await page.goto("https://x.com/home", wait_until="networkidle", timeout=15_000)
            await page.wait_for_selector('a[href*="/compose/post"]', timeout=10_000)
        except Exception as e:
            logger.warning("Login verification failed: %s", e)
            print(">>> WARNING: could not verify login. Saving state anyway.")

        await context.storage_state(path=str(state_path))
        await browser.close()

    print(f">>> Session saved to {state_path}")
    return state_path


def login_with_cookies(auth_token: str, ct0: str) -> Path:
    """Build a Playwright storage_state.json from X auth cookies.

    The two cookies `auth_token` and `ct0` are sufficient to authenticate
    a Playwright context as the logged-in user. The user can copy these
    from their regular browser's DevTools (Application → Cookies → x.com).
    """
    state_path = _state_path()
    state_path.parent.mkdir(parents=True, exist_ok=True)

    if not auth_token or not ct0:
        raise ValueError("Both auth_token and ct0 are required.")

    # Playwright storage_state schema. `expires: -1` = session cookie.
    state = {
        "cookies": [
            {
                "name": "auth_token",
                "value": auth_token,
                "domain": ".x.com",
                "path": "/",
                "expires": -1,
                "httpOnly": True,
                "secure": True,
                "sameSite": "None",
            },
            {
                "name": "ct0",
                "value": ct0,
                "domain": ".x.com",
                "path": "/",
                "expires": -1,
                "httpOnly": False,
                "secure": True,
                "sameSite": "Lax",
            },
        ],
        "origins": [],
    }
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(f">>> Cookies written to {state_path}")
    return state_path


async def get_logged_in_context(playwright, headless: bool = True) -> BrowserContext:
    """Create a browser context using the saved storage_state.

    Raises SessionExpiredError if the state file is missing or X rejects it.
    """
    state_path = _state_path()
    if not has_session():
        raise SessionExpiredError(
            f"No session at {state_path}. Run `x-trace login` first."
        )

    browser = await playwright.chromium.launch(headless=headless, proxy=_proxy_arg())
    context = await browser.new_context(storage_state=str(state_path))
    context.set_default_timeout(20_000)
    return context


async def is_session_valid(context: BrowserContext) -> bool:
    """Check whether the saved session still authenticates us."""
    page = await context.new_page()
    try:
        await page.goto("https://x.com/home", wait_until="domcontentloaded")
        # Logged-out users get redirected to /login or see a sign-in prompt
        if "/login" in page.url:
            return False
        await page.wait_for_selector('a[href*="/compose/post"]', timeout=8_000)
        return True
    except Exception as e:
        logger.debug("Session validity check failed: %s", e)
        return False
    finally:
        await page.close()
