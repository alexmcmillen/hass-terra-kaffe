# Security

## Reporting

Please report security problems privately through GitHub's
"Report a vulnerability" button on this repository, not in a public issue.

## What this integration stores

- Your Terra Kaffe **email** and an **offline refresh token** in Home
  Assistant's config entry (`.storage/core.config_entries`).
- It does **not** store your password. The password is used once, at
  sign-in, to obtain the refresh token. If Terra Kaffe revokes the token
  (for example after a password change), Home Assistant asks you to sign in
  again.

Treat the refresh token like a password: anyone holding it can control your
machine until you change your Terra Kaffe password.

## Safeguards in the code

- Requests only go to `auth.terrakaffeservice.com` and
  `mercury.terrakaffeservice.com` over HTTPS. Redirects are not followed, so
  the bearer token can never be forwarded to another host.
- Device ids from the cloud are validated before they are used in a URL.
- Recipe strings and drink names are validated and sanitised before they are
  sent to the machine.
- Remote brewing is off by default and must be armed for each drink (see the
  README). Cancel is always allowed.
- Diagnostics downloads redact the email, token, device id, serial number,
  machine name, Wi-Fi details, PIN, drink history and analytics.

## Dependencies

There are no runtime dependencies beyond what Home Assistant ships. Test
dependencies are pinned in `requirements_test.txt`, checked weekly by
Dependabot, and CI runs a secret scan over the full git history.
