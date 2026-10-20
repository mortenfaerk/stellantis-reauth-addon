# Stellantis auto-reauth

[![Open your Home Assistant instance and show the add add-on repository dialog with this repository pre-filled.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fmortenfaerk%2Fstellantis-reauth-addon)

A Home Assistant add-on that keeps the
[Stellantis Vehicles](https://github.com/andreadegiovine/homeassistant-stellantis-vehicles)
integration (MyPeugeot, MyCitroën, MyDS, MyOpel, MyVauxhall) logged in.

The integration's login drops now and then (`invalid_grant`), and re-authenticating
by hand means a browser login and often an SMS code. This add-on does it for you.
It drives the integration's own config flows, so the integration itself stays
unmodified.

## How it works

The add-on checks every minute for a pending Stellantis re-authentication.

1. **Without SMS:** if the integration is still loaded, the usual case when its
   token expires while HA is running, the add-on runs *Reconfigure → OAuth*.
   That gets new tokens and keeps the existing remote-command (OTP) setup.
2. **Reauth:** otherwise, e.g. when the token died across an HA restart, it runs
   the normal reauth. Without remote commands that's fully automatic. With
   them, Stellantis sends an SMS code and you get an actionable phone
   notification (Android or iOS). Reply with the code; the add-on submits it
   with the stored PIN. At most one SMS attempt per entry per 6 hours, because
   Stellantis limits registered devices (`NOK:MAXNBTOOLS`).

Several accounts and integration entries are supported; each account is
matched to its entry by brand and country.

You get a phone notification for each renewal. Failures also create a
persistent notification in HA, and the reauth is left in place to finish by hand.

The browser login runs in a headless Chromium (Playwright) inside the add-on. The
integration's built-in "remote" OAuth mode calls it over the internal
Supervisor network through a secret URL, so credentials never go to a
third-party login service.

```
stellantis_reauth/
├── config.yaml        add-on manifest and options schema
├── Dockerfile         Playwright Python base image
├── DOCS.md            shown in the add-on's Documentation tab
└── app/
    ├── main.py        entry point: login worker + driver loop
    ├── login.py       headless Stellantis login, returns the OAuth code
    ├── reauth.py      reconfigure / reauth flow driver, SMS prompt
    ├── strings.py     default notification texts, custom text validation
    └── ha_client.py   minimal HA REST + websocket client
```

## Install

1. Click the button above, or go to Settings → Add-ons → Add-on store → ⋮ →
   **Repositories** and add `https://github.com/mortenfaerk/stellantis-reauth-addon`.
2. Install **Stellantis auto-reauth**. The first install builds the image, which
   takes a few minutes.
3. Fill in the configuration and start it. Check the add-on log, then fire the
   event `stellantis_reauth_renew` once to test the login (see below).

Requires Home Assistant OS or Supervised (add-ons), and the Stellantis Vehicles
integration set up through HACS.

### Local development

Copy the `stellantis_reauth` folder to the HA `/addons` share and install it
from *Local add-ons*. After code changes, bump `version` in `config.yaml` and
update, or use **Rebuild**.

## Configuration

```yaml
accounts:
  - email: me@example.com
    password: "..."
    pin: "1234"          # optional, only needed with remote commands
notify_service: ""       # empty: every phone with the companion app
```

With several accounts or integration entries, each account also needs `brand`
and `country`. See [DOCS.md](stellantis_reauth/DOCS.md) for all options.

Credentials are stored as standard add-on options: masked in the UI, but not
encrypted on disk, readable with admin API access, and included in HA backups.
Keep backup encryption on and use a unique Stellantis password.

## Renew on demand

Fire the event `stellantis_reauth_renew` (Developer tools → Events) to run the
no-SMS renewal right away, e.g. to test the setup or after a password change.

## Compatibility

- Brands: MyPeugeot, MyCitroën, MyDS, MyOpel, MyVauxhall. The login form was
  checked on all 204 brand/country combinations in the integration's
  `configs.json`; a full login has been tested with MyPeugeot.
- Architectures: amd64 and aarch64. Playwright doesn't support armv7. The
  image is large (~1–2 GB).
- Notifications: HA companion app on Android and iOS. All texts can be
  replaced in the configuration, e.g. to translate them.
- If Stellantis changes its login page, the selectors in `app/login.py` are the
  place to look.

## Disclaimer

Not affiliated with Stellantis or the Stellantis Vehicles integration. It
automates the same login you would otherwise do by hand; use it at your own
risk.

## License

[MIT](LICENSE)
