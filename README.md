# Terra Kaffe for Home Assistant (TK Cloud 2.0)

Home Assistant integration for the Terra Kaffe TK-02, rebuilt for the cloud
platform behind the **Terra Kaffe 2.0** app.

In 2026 Terra Kaffe moved the TK-02 off the Afero IoT platform onto its own
cloud. Machines that have migrated no longer answer on the Afero API, which is
what every earlier integration used (`bassrock/hass-terra-kaffe` and its
forks). This fork replaces the API layer and keeps the good parts of
`nickrunco/hass-terra-kaffe`: saved-drink buttons, cancel, and the
remote-brewing safety arm.

> **Status: verified against a real TK-02 (firmware 39) on 2026-11-20.**
> Sign-in, reading state, wake, sleep, settings writes and a full brew all
> worked end to end. Cancel brew follows the same route as brewing but has
> not been exercised on hardware yet. See [What is verified](#what-is-verified).

## What you get

| Platform | Entities |
|---|---|
| Sensor | Power status, brewing status and progress, bean and waste bin level, total cups and shots, maintenance remaining (brew unit, descale, water filter, waste bin), firmware versions, settings |
| Binary sensor | Cloud connection, brewing, brew error, screen lock, pre-ground, auto wake schedule |
| Button | Wake, Sleep, Cancel brew, and one **Brew _drink name_** button per saved drink on the machine |
| Switch | **Arm remote brewing** (safety, see below) |
| Service | `terra_kaffe.brew_recipe` for a raw recipe string |

### Remote brewing safety

A drink only starts when all of these hold:

1. The **Arm remote brewing** switch is on. It turns itself off after one
   drink, after 10 minutes, and on every restart.
2. The machine is online, awake, and its brewing status is Ready.

Cancel brew is never blocked. Put a cup under the spout before you arm it.

```yaml
# Example: one tap for a morning latte, cup already in place.
script:
  morning_latte:
    sequence:
      - action: switch.turn_on
        target: { entity_id: switch.kitchen_tk_arm_remote_brewing }
      - action: button.press
        target: { entity_id: button.kitchen_tk_brew_morning_latte }
```

## Install

1. HACS, Integrations, three-dot menu, **Custom repositories**. Add this
   repository as type **Integration**, then download it.
   (Manual: copy `custom_components/terra_kaffe` into your config folder.)
2. Restart Home Assistant.
3. Settings, Devices and services, **Add integration**, Terra Kaffe. Sign in
   with the same email and password as the Terra Kaffe 2.0 app.

Coming from an Afero-based version: remove the old entry first. Afero device
ids and tokens mean nothing to the new cloud, so entries cannot be converted.

## How it works

- **Sign-in:** Keycloak at `auth.terrakaffeservice.com`, realm `tkf`, public
  client `tk_android`, password grant with `offline_access`. The refresh
  token is stored in the config entry and rotated as Keycloak rotates it.
  Your password is **not** stored: it is used once to get the token. If the
  token is ever revoked, Home Assistant asks you to sign in again. See
  [SECURITY.md](SECURITY.md).
- **State:** `GET /api/devices/{id}/twin` on `mercury.terrakaffeservice.com`,
  polled every 30 seconds. The twin is an Azure IoT Hub device twin.
- **Brew and cancel:** `POST /api/devices/{id}/command` with
  `{"drink_order": "id^epoch^name^$53|e1|f115"}` or `{"cancel_brew": true}`.
- **Wake and sleep:** `PUT /api/devices/{id}/twin` with a desired-properties
  patch setting power status to 2 (wake) or 3 (sleep). The machine reports 2
  while it warms up and 1 once it is ready; brewing waits for 1.
- **Brew status:** 0 ready, 1 brewing, 3 done. The official app labels 3
  "paused", but the machine reports it after every finished drink, so it
  counts as idle and a new drink can start.

Full notes are in [docs/API.md](docs/API.md).

## What is verified

| Piece | Status | Source |
|---|---|---|
| Sign-in form and token refresh | Verified live | App source and a real account |
| Device list and twin read | Verified live | Real TK-02, firmware 39 |
| Twin key shape | Verified live | Flat keys (`dev_sts`, `brew_sts`, `drink_01`...) on firmware 39; the nested shape is still handled |
| Settings write (`PUT /twin`) | Verified live | Schedule re-sent unchanged |
| Wake and sleep | Verified live | Reported status follows within about a second |
| Brew (`POST /command` with `drink_order`) | Verified live | Saved Americano brewed start to finish |
| Cancel brew (`cancel_brew`) | Not yet on hardware | Same route and schema as brew |

If something is off, **download diagnostics** from the integration page. The
dump contains the twin exactly as the cloud returned it (serial, Wi-Fi, PIN
and account details redacted) and is all that is needed to fix a mapping.

## Development

```bash
uv venv --python 3.14 && uv pip install -r requirements_test.txt
uv run --no-project pytest --cov=custom_components/terra_kaffe
uvx ruff check custom_components tests
```

The suite (118 tests, about 98% line coverage) runs the API client against
a fake Keycloak and device API, runs the integration inside a real Home
Assistant test harness, and replays the state a real TK-02 reported, so a
change that would break brewing on hardware fails in CI first. CI also runs
hassfest, HACS validation and a secret scan.

## Credits and license

Fork of [nickrunco/hass-terra-kaffe](https://github.com/nickrunco/hass-terra-kaffe),
itself a fork of [bassrock/hass-terra-kaffe](https://github.com/bassrock/hass-terra-kaffe).
GPL-3.0, same as upstream.

Unofficial. Not affiliated with or endorsed by Terra Kaffe. It uses your own
account to control your own machine, and brewing is a real physical action.
