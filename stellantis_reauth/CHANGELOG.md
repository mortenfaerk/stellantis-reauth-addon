# Changelog

## 0.2.3

- Every notification text can be replaced in the configuration (own wording or
  translation). Invalid texts fall back to the default with a log warning.

## 0.2.2

- Labels and help texts for every option on the configuration page.
- Notification texts are English by default.

## 0.2.1

- Country is a dropdown.
- An empty phone notification target notifies every phone with the companion app.

## 0.2.0

- All brands supported by the integration: MyPeugeot, MyCitroën, MyDS, MyOpel,
  MyVauxhall.
- Several accounts and integration entries, matched by brand and country.
- PIN and phone notifications are optional; without remote commands a full
  re-login is automatic.
- iOS reply support for the SMS code prompt.
- aarch64 build.

## 0.1.0

- First version: automatic renewal via reconfigure (no SMS) or reauth with the
  SMS code from an actionable phone notification.
