"""Headless Stellantis web login that returns the OAuth authorization code.

Speaks the same protocol as the integration's "remote" OAuth mode: the
integration POSTs {"url", "email", "password"} and expects {"code": ...}.
"""

import asyncio
import logging
from urllib.parse import parse_qs, urlparse

from playwright.async_api import Browser

_LOGGER = logging.getLogger(__name__)

# Login hosts of every brand in the integration's configs.json; anything else
# is refused so the worker can't be used to post credentials somewhere
# unexpected. A new brand upstream needs adding here.
ALLOWED_HOSTS = {
    "idpcvs.peugeot.com",
    "idpcvs.citroen.com",
    "idpcvs.driveds.com",
    "idpcvs.opel.com",
    "idpcvs.vauxhall.co.uk",
}

LOGIN_FORM = "#gigya-login-form"
AUTHORIZE_BUTTON = '#cvs_from input[type="submit"]'
# Form-level error (e.g. wrong password); field-level "required" errors can
# flash while Gigya initialises and are not fatal.
LOGIN_ERROR = '.gigya-error-msg-active[data-bound-to="gigya-login-form"]'


class LoginError(Exception):
    pass


async def fetch_oauth_code(browser: Browser, url: str, email: str, password: str, timeout: float = 120) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise LoginError(f"unexpected login URL host: {parsed.hostname}")
    redirect_uri = parse_qs(parsed.query).get("redirect_uri", [""])[0]
    app_scheme = urlparse(redirect_uri).scheme
    if not app_scheme:
        raise LoginError("login URL has no redirect_uri")

    loop = asyncio.get_running_loop()
    code_future = loop.create_future()

    # The final redirect goes to the app's custom scheme (e.g. mymap://), which
    # the browser can't open; the code is read from that request's URL.
    def on_request(request):
        if code_future.done() or not request.url.startswith(f"{app_scheme}://"):
            return
        code = parse_qs(urlparse(request.url).query).get("code", [None])[0]
        if code:
            code_future.set_result(code)

    context = await browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
        viewport={"width": 1280, "height": 720},
    )
    try:
        page = await context.new_page()
        page.on("request", on_request)
        page.on("requestfailed", on_request)

        _LOGGER.info("Opening Stellantis login page")
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)

        form = page.locator(LOGIN_FORM)
        await form.wait_for(timeout=60_000)
        # Gigya keeps wiring up the form after it appears; typing too early
        # gets lost.
        await page.wait_for_timeout(3_000)
        await form.locator('input[name="username"]').press_sequentially(email, delay=30)
        await form.locator('input[name="password"]').press_sequentially(password, delay=30)
        await form.locator('input[type="submit"]').click()
        _LOGGER.info("Credentials submitted, waiting for the authorization code")

        deadline = loop.time() + timeout
        authorized = False
        while not code_future.done():
            if loop.time() > deadline:
                raise LoginError("timed out waiting for the authorization code")
            error = page.locator(LOGIN_ERROR)
            if await error.count():
                message = (await error.first.inner_text()).strip()
                if message:
                    raise LoginError(f"login rejected: {message}")
            # The consent page only shows up on some logins.
            if not authorized:
                button = page.locator(AUTHORIZE_BUTTON)
                if await button.count() and await button.first.is_visible():
                    try:
                        await button.first.click(timeout=10_000)
                    except Exception:
                        # The click navigates to the app scheme and the
                        # navigation fails; the code is captured regardless.
                        pass
                    authorized = True
            await asyncio.wait({code_future}, timeout=0.5)

        _LOGGER.info("Authorization code captured")
        return code_future.result()
    finally:
        await context.close()
