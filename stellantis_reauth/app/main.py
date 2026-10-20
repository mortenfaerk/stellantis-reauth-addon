"""Stellantis auto-reauth add-on: login worker + reauth flow driver."""

import asyncio
import json
import logging
import os
import secrets
import socket

import aiohttp
from aiohttp import web
from playwright.async_api import async_playwright

from ha_client import HomeAssistant
from login import LoginError, fetch_oauth_code
from reauth import Account, Reauthenticator, Settings, load_state, save_state
from strings import build_messages

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
_LOGGER = logging.getLogger("stellantis_reauth")

WORKER_PORT = 3000
POLL_INTERVAL = 60
RENEW_EVENT = "stellantis_reauth_renew"


def worker_token() -> str:
    """Secret path for the worker URL, so only the integration can use it."""
    state = load_state()
    if "worker_token" not in state:
        state["worker_token"] = secrets.token_urlsafe(24)
        save_state(state)
    return state["worker_token"]


async def addon_hostname(session: aiohttp.ClientSession, token: str) -> str:
    try:
        async with session.get(
            "http://supervisor/addons/self/info", headers={"Authorization": f"Bearer {token}"}
        ) as resp:
            return (await resp.json())["data"]["hostname"]
    except Exception as err:
        _LOGGER.warning("Could not read add-on hostname from Supervisor (%s); using container hostname", err)
        return socket.gethostname()


def make_worker(path_token: str, playwright) -> web.Application:
    lock = asyncio.Lock()

    async def handle_login(request: web.Request) -> web.Response:
        if not secrets.compare_digest(request.match_info["token"], path_token):
            raise web.HTTPNotFound()
        try:
            payload = await request.json()
            url, email, password = payload["url"], payload["email"], payload["password"]
        except (ValueError, KeyError):
            return web.json_response({"message": "Missing required params"}, status=400)

        async with lock:
            browser = await playwright.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            try:
                code = await fetch_oauth_code(browser, url, email, password)
            except LoginError as err:
                _LOGGER.error("Login failed: %s", err)
                return web.json_response({"message": str(err)}, status=400)
            except Exception as err:
                _LOGGER.exception("Login crashed")
                return web.json_response({"message": f"login error: {err}"}, status=400)
            finally:
                await browser.close()
        return web.json_response({"code": code})

    app = web.Application()
    app.router.add_post("/{token}", handle_login)
    return app


async def run_driver(session: aiohttp.ClientSession, token: str, settings: Settings) -> None:
    while True:
        ha = HomeAssistant(session, token)
        try:
            await ha.connect()
            if settings.notify_service:
                notify_services = [settings.notify_service]
            else:
                # Empty means every phone with the companion app; a reply
                # from any of them is accepted.
                notify_services = await ha.mobile_app_notify_services()
            if notify_services:
                _LOGGER.info("Phone notifications via: %s", ", ".join(notify_services))
            else:
                _LOGGER.warning("No mobile app notify service found: no phone notifications, and SMS reauths are left for manual handling")
            reauth = Reauthenticator(ha, settings, notify_services)
            renew = asyncio.Event()
            renew_entry: list[str | None] = []

            def on_renew(event: dict) -> None:
                renew_entry.append(event.get("data", {}).get("entry_id"))
                renew.set()

            await ha.subscribe(RENEW_EVENT, on_renew)
            _LOGGER.info("Connected to Home Assistant; watching for Stellantis reauth flows")
            while not ha.closed.done():
                await reauth.check_flows()
                if renew.is_set():
                    renew.clear()
                    while renew_entry:
                        await reauth.renew_now(renew_entry.pop(0))
                try:
                    await asyncio.wait_for(renew.wait(), POLL_INTERVAL)
                except TimeoutError:
                    pass
        except Exception as err:
            _LOGGER.error("Driver error, reconnecting in 30 s: %s", err)
        finally:
            await ha.close()
        await asyncio.sleep(30)


async def main() -> None:
    with open("/data/options.json") as f:
        options = json.load(f)
    token = os.environ["SUPERVISOR_TOKEN"]
    path_token = worker_token()

    async with aiohttp.ClientSession() as session, async_playwright() as playwright:
        hostname = await addon_hostname(session, token)
        settings = Settings(
            accounts=[
                Account(
                    email=a["email"],
                    password=a["password"],
                    pin=a.get("pin", ""),
                    brand=a.get("brand", ""),
                    country=a.get("country", ""),
                )
                for a in options.get("accounts", [])
            ],
            notify_service=options.get("notify_service", "").removeprefix("notify."),
            sms_timeout_minutes=options["sms_timeout_minutes"],
            notify_on_success=options["notify_on_success"],
            worker_url=f"http://{hostname}:{WORKER_PORT}/{path_token}",
            messages=build_messages(options.get("messages")),
        )

        runner = web.AppRunner(make_worker(path_token, playwright), access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", WORKER_PORT).start()
        _LOGGER.info("Login worker listening on http://%s:%s/<token>", hostname, WORKER_PORT)

        if not settings.accounts:
            _LOGGER.error("No accounts configured; add at least one under the add-on's Configuration tab")
            await asyncio.Event().wait()
        await run_driver(session, token, settings)


if __name__ == "__main__":
    asyncio.run(main())
