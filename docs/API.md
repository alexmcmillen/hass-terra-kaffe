# Terra Kaffe cloud API notes (TK Cloud 2.0)

Derived from static analysis of the Android app `com.terrakaffeandroid.tkcontrol`
version 2.0.6 (code 220), the device API's published OpenAPI schema, and the
OIDC discovery document. No account was signed in to and no machine was
commanded while gathering this. Unofficial; it can change without notice.

## Hosts

| Role | URL |
|---|---|
| Sign-in (Keycloak) | `https://auth.terrakaffeservice.com/realms/tkf/protocol/openid-connect` |
| Device API ("mercury") | `https://mercury.terrakaffeservice.com/api/` |
| Account and recipe API ("gemini") | `https://gemini.terrakaffeservice.com/api/` (not used here) |

## Sign-in

```
POST .../openid-connect/token
Content-Type: application/x-www-form-urlencoded

grant_type=password&client_id=tk_android&username=<email>&password=<pw>&scope=openid profile offline_access
```

Refresh: `grant_type=refresh_token&client_id=tk_android&refresh_token=<token>`.
The client is public, so there is no secret. Calls to the device API carry
`Authorization: Bearer <access_token>`.

## Device API

| Purpose | Request | Body |
|---|---|---|
| List machines | `GET devices/` | returns `{devices: [{device_id, access_level}]}` |
| Read state | `GET devices/{id}/twin` | returns `{device_id, etag, connection_state, last_activity_time, desired, reported, tags}` |
| Change settings | `PUT devices/{id}/twin` | `{"desired": {...}}`, merge semantics |
| Brew | `POST devices/{id}/command` | `{"drink_order": "<order>"}`, max 90 characters |
| Cancel | `POST devices/{id}/command` | `{"cancel_brew": true}` |
| Live updates | `GET devices/{id}/websocket` | returns a short-lived Azure Web PubSub URL (not used here) |

## Drink orders

`id^epochSeconds^name^recipeString[^espressoProfileId]`

The recipe string is unchanged from the Afero era: `$<type>|<key><value>|...`,
for example `$53|e1|f115`. Saved drinks sit in twin slots `drinks.01` to
`drinks.10` in the same caret format, espresso profiles in `eps.01` to `eps.10`.

## Twin keys

The schema documents a nested shape; the app's native library also knows a
flat abbreviated set. `custom_components/terra_kaffe/twin.py` holds the full
mapping.

| Nested path | Flat key | Meaning |
|---|---|---|
| `device.status` | `dev_sts` | 0 sleep, 1 awake, 2 going to wake, 3 going to sleep. Write 2 or 3; the firmware ignores 0 and 1 |
| `brew.status` | `brew_sts` | 0 ready, 1 brewing, 2 prewarmup, 3 done (the app calls it paused; TK-02 firmware 39 reports it after every finished drink), 4 cleaning, 5 error |
| `brew.progress` | `brew_prog` | percent |
| `levels.bean` | `bean_lvl` | bean hopper |
| `levels.waste_bin` | `waste_bin_lvl` | waste bin |
| `stats.cups`, `stats.shots` | `cup_cnt`, `shot_cnt` | lifetime counters |
| `care.clean.*` | `cln_brew`, `cln_desc`, `cln_fltr`, `cln_wb` | `current/max` counters |
| `drinks.NN`, `eps.NN` | `drink_NN`, `ep_NN` | saved drinks and espresso profiles |
| `schedules.<day>.on/off` | `sched_<day>_on/off` | auto wake schedule |

## Capture note

The app's networking is a Rust library with its own root store, so a proxy CA
installed on the phone does not see device API traffic.
