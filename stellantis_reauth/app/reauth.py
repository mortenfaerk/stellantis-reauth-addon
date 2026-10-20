"""Drives the Stellantis Vehicles config flows with the stored credentials.

Two ways to renew the login:
- reconfigure -> "oauth": new OAuth tokens, keeps the existing remote-command
  (OTP) setup, no SMS. Only works while the config entry is loaded.
- the reauth flow itself: needs a fresh SMS code when remote commands are on;
  the code is asked for through an actionable phone notification.
"""

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

from ha_client import HomeAssistant

_LOGGER = logging.getLogger(__name__)

DOMAIN = "stellantis_vehicles"
STATE_FILE = Path("/data/state.json")
NOTIFICATION_TAG = "stellantis_reauth"
# Every SMS reauth registers a new OTP device, and Stellantis caps those
# (NOK:MAXNBTOOLS), so automatic SMS attempts are rate limited.
SMS_MIN_INTERVAL = 6 * 3600
# A reauth shortly after a no-SMS renewal means new OAuth tokens didn't fix
# it (e.g. the OTP setup itself is broken), so go straight to the SMS reauth.
RECONFIGURE_MIN_INTERVAL = 3600
MAX_STEPS = 12


@dataclass
class Account:
    email: str
    password: str
    pin: str = ""
    brand: str = ""
    country: str = ""


@dataclass
class Settings:
    accounts: list[Account]
    notify_service: str
    sms_timeout_minutes: int
    notify_on_success: bool
    worker_url: str
    messages: dict


class FlowFailed(Exception):
    pass


class ManualNeeded(Exception):
    """Can't be done automatically; the reason is a strings key."""


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state))


def entry_state(entry_id: str) -> dict:
    return load_state().get("entries", {}).get(entry_id, {})


def update_entry_state(entry_id: str, **values) -> None:
    state = load_state()
    state.setdefault("entries", {}).setdefault(entry_id, {}).update(values)
    save_state(state)


def schema_defaults(result: dict) -> dict:
    return {f["name"]: f["default"] for f in result.get("data_schema") or [] if "default" in f}


def parse_unique_id(unique_id: str | None) -> tuple[str, str]:
    """The integration's unique_id is "<customer_id>_<brand>_<country>"."""
    parts = (unique_id or "").rsplit("_", 2)
    return (parts[1], parts[2].upper()) if len(parts) == 3 else ("", "")


def match_account(accounts: list[Account], entry_count: int, brand: str, country: str) -> Account | None:
    """Pick the account for an entry, or None when it isn't unambiguous.

    Logging in with the wrong account would put another customer's tokens on
    the entry, so with more than one entry or account each account must name
    its brand and country and exactly one may match.
    """
    if len(accounts) == 1 and entry_count == 1:
        return accounts[0]
    matches = [
        a for a in accounts
        if a.brand and a.country and a.brand == brand and a.country.upper() == country
    ]
    return matches[0] if len(matches) == 1 else None


class Reauthenticator:
    def __init__(self, ha: HomeAssistant, settings: Settings, notify_services: list[str]):
        self._ha = ha
        self._settings = settings
        self._t = settings.messages
        self._notify_services = notify_services
        self._handled: set[str] = set()
        self._lock = asyncio.Lock()

    async def check_flows(self) -> None:
        """Handle any reauth flow for the integration that we haven't seen yet."""
        for flow in await self._ha.flows_in_progress():
            if flow["handler"] != DOMAIN or flow["context"].get("source") != "reauth":
                continue
            if flow["flow_id"] in self._handled:
                continue
            self._handled.add(flow["flow_id"])
            async with self._lock:
                await self._handle_reauth(flow)

    async def renew_now(self, entry_id: str | None = None) -> None:
        """Renew the OAuth tokens on request (event stellantis_reauth_renew)."""
        async with self._lock:
            entries = await self._ha.config_entries(DOMAIN)
            for entry in entries:
                if entry_id and entry["entry_id"] != entry_id:
                    continue
                name = entry["title"]
                if entry["state"] != "loaded":
                    _LOGGER.warning("%s is not loaded; skipping manual renewal", name)
                    continue
                brand, country = await self._entry_brand_country(entry["entry_id"], len(entries))
                account = match_account(self._settings.accounts, len(entries), brand, country)
                if account is None:
                    await self._notify_failure(entry, self._t["manual"].format(name=name, reason=self._t["reason_no_account"]))
                    continue
                try:
                    await self._renew_via_reconfigure(entry, account)
                except Exception as err:
                    _LOGGER.error("Manual renewal of %s failed: %s", name, err)
                    await self._notify_failure(entry, self._t["failed"].format(name=name, error=err))
                    continue
                await self._notify_success(self._t["renewed_manual"].format(name=name))

    async def _entry_brand_country(self, entry_id: str, entry_count: int) -> tuple[str, str]:
        # Only needed to tell several entries apart; read from diagnostics,
        # since the config entry API doesn't expose the unique_id.
        if entry_count == 1:
            return "", ""
        try:
            diag = await self._ha.rest("GET", f"/diagnostics/config_entry/{entry_id}")
            config = diag["data"]["config"]
            return config["mobile_app"], config["country_code"].upper()
        except Exception as err:
            _LOGGER.warning("Could not read brand/country of entry %s: %s", entry_id, err)
            return "", ""

    async def _handle_reauth(self, flow: dict) -> None:
        flow_id = flow["flow_id"]
        entries = await self._ha.config_entries(DOMAIN)
        entry = next((e for e in entries if e["entry_id"] == flow["context"].get("entry_id")), None)
        if entry is None:
            _LOGGER.warning("Reauth flow %s has no matching config entry", flow_id)
            return
        name = entry["title"]
        brand, country = parse_unique_id(flow["context"].get("unique_id"))
        _LOGGER.info("Reauth flow %s for %s (%s %s, state %s)", flow_id, name, brand, country, entry["state"])

        account = match_account(self._settings.accounts, len(entries), brand, country)
        if account is None:
            await self._notify_failure(entry, self._t["manual"].format(name=name, reason=self._t["reason_no_account"]))
            return

        recently_renewed = time.time() - entry_state(entry["entry_id"]).get("last_reconfigure", 0) < RECONFIGURE_MIN_INTERVAL
        if entry["state"] == "loaded" and not recently_renewed:
            try:
                await self._renew_via_reconfigure(entry, account)
            except Exception as err:
                _LOGGER.warning("Renewal without SMS failed, falling back to the reauth flow: %s", err)
            else:
                # The new tokens are in place; the pending reauth flow is stale.
                try:
                    await self._ha.flow_abort(flow_id)
                except Exception as err:
                    _LOGGER.warning("Could not close the reauth flow: %s", err)
                await self._notify_success(self._t["renewed"].format(name=name))
                return

        sms_before = entry_state(entry["entry_id"]).get("last_sms_attempt")
        try:
            result = await self._drive(await self._ha.flow_current(flow_id), entry, account)
        except ManualNeeded as reason:
            await self._notify_failure(entry, self._t["manual"].format(name=name, reason=self._t[str(reason)]))
            return
        except Exception as err:
            _LOGGER.error("Reauth of %s failed: %s", name, err)
            await self._notify_failure(entry, self._t["failed"].format(name=name, error=err))
            return
        if result.get("reason") == "reauth_successful":
            used_sms = entry_state(entry["entry_id"]).get("last_sms_attempt") != sms_before
            await self._notify_success(self._t["renewed_sms" if used_sms else "renewed"].format(name=name))
        else:
            await self._notify_failure(entry, self._t["failed"].format(name=name, error=result.get("reason")))

    async def _renew_via_reconfigure(self, entry: dict, account: Account) -> None:
        result = await self._ha.start_reconfigure(DOMAIN, entry["entry_id"])
        try:
            result = await self._drive(result, entry, account, reconfigure=True)
        except Exception:
            if result.get("flow_id"):
                try:
                    await self._ha.flow_abort(result["flow_id"])
                except Exception:
                    pass
            raise
        if result.get("reason") != "reconfigure_successful":
            raise FlowFailed(f"reconfigure ended with {result.get('reason')}")
        update_entry_state(entry["entry_id"], last_reconfigure=time.time())
        _LOGGER.info("OAuth tokens of %s renewed via reconfigure", entry["title"])

    async def _drive(self, result: dict, entry: dict, account: Account, reconfigure: bool = False) -> dict:
        """Answer the flow's forms until it finishes; returns the final result."""
        login_sent = False
        for _ in range(MAX_STEPS):
            if result["type"] != "form":
                return result
            step = result["step_id"]
            flow_id = result["flow_id"]
            timeout = 60
            _LOGGER.info("Flow %s at step %s", flow_id, step)

            if step == "reconfigure":
                user_input = {"reconfigure": "oauth"}
            elif step == "reauth_confirm":
                user_input = {}
            elif step == "oauth_mode":
                if login_sent:
                    raise FlowFailed(f"login was not accepted: {result.get('errors')}")
                user_input = {"oauth_manual_mode": False}
            elif step == "oauth_remote":
                user_input = {
                    "email": account.email,
                    "password": account.password,
                    "oauth_code_url": self._settings.worker_url,
                }
                login_sent = True
                # The integration waits up to 300 s for the login worker.
                timeout = 360
            elif step == "get_access_token":
                # Default reflects the entry's current remote-commands setting.
                user_input = schema_defaults(result)
                if user_input.get("remote_commands") and not reconfigure:
                    # Submitting this sends the SMS, so check everything
                    # needed to finish is there first.
                    if not (account.pin and self._notify_services):
                        raise ManualNeeded("reason_no_sms")
                    if time.time() - entry_state(entry["entry_id"]).get("last_sms_attempt", 0) < SMS_MIN_INTERVAL:
                        raise ManualNeeded("reason_rate_limited")
            elif step == "otp" and not reconfigure:
                sms_code = await self._ask_sms_code(entry)
                user_input = {"sms_code": sms_code, "pin_code": account.pin}
            elif step == "options":
                user_input = schema_defaults(result)
            else:
                raise FlowFailed(f"unexpected step {step}")

            result = await self._ha.flow_step(flow_id, user_input, timeout=timeout)
        raise FlowFailed("too many flow steps")

    async def _ask_sms_code(self, entry: dict) -> str:
        # The otp form has just sent the SMS.
        update_entry_state(entry["entry_id"], last_sms_attempt=time.time())
        # REPLY is Android's inline reply; behavior textInput is the iOS one.
        actions = [{"action": "REPLY", "title": self._t["sms_action"], "behavior": "textInput"}]
        replies: asyncio.Queue = asyncio.Queue()

        def on_action(event: dict) -> None:
            data = event.get("data", {})
            if data.get("action") == "REPLY" and data.get("tag", NOTIFICATION_TAG) == NOTIFICATION_TAG:
                replies.put_nowait(data.get("reply_text", ""))

        unsubscribe = await self._ha.subscribe("mobile_app_notification_action", on_action)
        try:
            await self._notify(self._t["sms_prompt"].format(name=entry["title"]), actions=actions, sticky=True)
            deadline = time.monotonic() + self._settings.sms_timeout_minutes * 60
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise FlowFailed("no SMS code received in time")
                try:
                    reply = await asyncio.wait_for(replies.get(), remaining)
                except TimeoutError:
                    raise FlowFailed("no SMS code received in time") from None
                code = re.sub(r"\D", "", reply)
                if code:
                    return code
                await self._notify(self._t["sms_invalid"], actions=actions, sticky=True)
        finally:
            await unsubscribe()
            await self._notify("clear_notification")

    async def _notify(self, message: str, actions: list | None = None, sticky: bool = False) -> None:
        data = {"tag": NOTIFICATION_TAG}
        if actions:
            data["actions"] = actions
        if sticky:
            data.update({"sticky": "true", "priority": "high", "ttl": 0})
        for service in self._notify_services:
            try:
                await self._ha.call_service("notify", service, {
                    "title": self._t["title"], "message": message, "data": data,
                })
            except Exception as err:
                _LOGGER.error("Could not send phone notification via %s: %s", service, err)

    async def _notify_success(self, message: str) -> None:
        _LOGGER.info(message)
        if self._settings.notify_on_success:
            await self._notify(message)

    async def _notify_failure(self, entry: dict, message: str) -> None:
        _LOGGER.warning(message)
        await self._notify(message)
        try:
            await self._ha.call_service("persistent_notification", "create", {
                "title": "Stellantis auto-reauth",
                "message": message,
                "notification_id": f"{NOTIFICATION_TAG}_{entry['entry_id']}",
            })
        except Exception as err:
            _LOGGER.error("Could not create persistent notification: %s", err)
