# Stellantis auto-reauth

Renews the login of the **Stellantis Vehicles** integration (MyPeugeot,
MyCitroën, MyDS, MyOpel, MyVauxhall) automatically when it asks to
re-authenticate.

## How it works

The add-on checks every minute for a pending Stellantis re-authentication.

1. **Without SMS:** if the integration is still loaded (the usual case when its
   token expires while HA is running), the add-on runs the integration's
   *Reconfigure → OAuth* flow. That gets new tokens and keeps the existing
   remote-command (OTP) setup.
2. **Reauth:** otherwise (e.g. the token died across an HA restart) it runs the
   normal reauth.
   - Without remote commands, that's fully automatic.
   - With remote commands, Stellantis sends an SMS code and your phone gets a
     notification. Reply to it with the code; the add-on submits it with the
     account's PIN. This needs `pin` and the companion app. At most one SMS
     attempt per entry per 6 hours, because Stellantis limits registered
     devices (`NOK:MAXNBTOOLS`).

Anything the add-on can't finish creates a notification in HA (and on your
phone, if configured). The reauth is left in place for you to finish by hand.

## Configuration

```yaml
accounts:
  - email: me@example.com
    password: "..."
    pin: "1234"          # optional, only needed with remote commands
notify_service: ""       # empty: every phone with the companion app
sms_timeout_minutes: 15
notify_on_success: true
```

| Option | |
|---|---|
| `accounts` | One entry per Stellantis account: `email`, `password`, optional `pin` (from the brand app, used for remote commands), and `brand` + `country` (see below) |
| `notify_service` | Leave empty to notify every phone with the HA companion app (a reply from any of them works). To use one phone only, enter its notify service without `notify.`, e.g. `mobile_app_my_phone` |
| `sms_timeout_minutes` | How long to wait for the SMS code reply |
| `notify_on_success` | Also notify when a renewal succeeded |

### Several accounts or integration entries

With one account and one Stellantis integration entry, `brand` and `country`
can be left out. With more of either, pick a `brand` and `country` for every
account, matching what was chosen when the integration entry was set up. Exactly one account must match an entry,
otherwise the add-on leaves that entry for you. Logging in with the wrong
account would put another account's tokens on the entry.

```yaml
accounts:
  - email: me@example.com
    password: "..."
    pin: "1234"
    brand: MyPeugeot
    country: DK
  - email: partner@example.com
    password: "..."
    brand: MyOpel
    country: DK
```

## Phone notifications

The SMS prompt uses an actionable notification with a reply field, supported by
the Home Assistant companion app on both Android and iOS.

### Your own texts or translations

Every notification text can be replaced under **Notification texts** in the
configuration. Leave a field empty to keep the default (shown in the field's
help text). Some texts take placeholders, e.g. `{name}` (the integration entry's
name), `{reason}` or `{error}`. A text with an unknown placeholder or a stray
`{` falls back to the default, with a warning in the add-on log.

```yaml
messages:
  title: Stellantis-login
  renewed: "{name}: login fornyet automatisk."
  sms_prompt: "{name}: svar med SMS-koden fra Stellantis."
  sms_action: Indtast SMS-kode
```

## Renew on demand

Fire the event `stellantis_reauth_renew` (Developer tools → Events) to run the
no-SMS renewal right away, e.g. to test the setup or after a password change.
Add `entry_id: <config entry id>` to the event data to renew just one entry.

## Security

The web login runs in a headless Chromium inside this add-on, and credentials
only go to the brand's own login page. The integration reaches the login worker
on the internal Supervisor network through a secret URL.

Add-on options are masked in the UI but stored unencrypted, and are readable by
anything with admin API access to Home Assistant. They're included in backups,
so keep backup encryption on, and use a Stellantis password you don't use
anywhere else.
