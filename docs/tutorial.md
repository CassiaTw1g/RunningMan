# Tutorial

## 0. Prerequisites

- Python 3.8+
- A valid account on the target service (already signed in on a device)
- Network access to the service endpoint (set `RM_BASE`)

```bash
pip install -r requirements.txt     # requests + pycryptodome
```

## 1. Obtain a token

The token is a JWT issued after login, valid for 60 days. Pick one of three routes.

**A. Lift it from a signed-in client (fastest)**

```bash
adb shell "run-as <package> cat /data/data/<package>/databases/DCStorage" > DCStorage.db
```

Open the SQLite file, find the table named `DC_<digits>_storage`, read the row where `key='token'`
and take the `data` field (a JSON string). Rooted devices / emulators can just `adb pull`.

**B. Use the built-in SMS flow**

```bash
export RM_BASE="https://<host>"
export SR_MOBILE=13800000000
python login.py captcha          # saves captcha.png, read the arithmetic answer yourself
python login.py send 10          # trigger the SMS, stores sms.key
python login.py login 590630 1   # <sms code> <captcha answer> → token.txt
```

The helper walks: image captcha → `/api/verCode/get` → AES-ECB form → `/api/h5/auth/login` →
`/api/h5/auth/getAppPlusUserInfo` (mints the real JWT).

**C. Provide it directly**

```bash
export SR_TOKEN="eyJhbGciOiJIUzI1NiIs..."
```

The CLI resolves the token as: `--token` → `SR_TOKEN` → `token.txt`.

## 2. Dry run first

```bash
python track_run.py --dry
```

Read the output and check:

- three checkpoints polled, `in-order 3/3`
- `early-approach 0`
- distance inside 5.01–5.10 km, never above 5.5 km
- average pace 7'20"–7'40" /km
- adjacent per-km splits differ by at least 5 s

Only proceed when all of the above hold.

## 3. Dispatch

```bash
python track_run.py
```

A successful call returns `{"errno":0,"data":{"id":<record id>}}`.

## 4. Parameters

| Flag | Meaning |
|---|---|
| `--km 5.05` | fixed distance (omit for a random 5.01–5.10 km) |
| `--pace 450` | average pace in seconds per km (450 = 7'30") |
| `--spread 11` | per-km pace jitter in seconds |
| `--gpx route.gpx` | run along a custom GPX course |
| `--dry` | generate only, no upload |

## 5. Swapping the course

Two things to change:

1. `TRACK` in `track_run.py` — centre of the oval (lat/lon).
2. `assets/track_template.json` — a closed polyline of that course, taken from one lap of a real
   recording: `[lon, lat]` pairs, 2–7 m point spacing, seam gap < 2 m, same direction as the run.

Everything downstream (lap repetition, truncation, sampling) is template-agnostic.

## 6. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `errno 50008` (empty credential) | `X-Token` missing — check token resolution order |
| `errno 50014` (credential expired) | token expired or session revoked — sign in again |
| `{code:500,msg:"system error"}` | `X-Secret` wrong — timestamp must be **milliseconds** |
| Upload OK but nothing in the app | pull-to-refresh the record list; confirm the right activity type |
| Course line drifts off the track | template does not match that oval — re-record one lap |
| Distance lands outside 5.01–5.10 | the tool re-calibrates up to 4 attempts; otherwise pass `--km` |

## 7. Verification snippet

```bash
python - <<'PY'
import json, sys; sys.path.insert(0, '.')
from starrun import api
tok = open('token.txt').read().strip()
body = {"currentPage": 1, "pageSize": 10, "totalCount": 0,
        "sortBy": "start_time", "order": "desc", "parameters": {"type": 0}}
d = api(tok, "/api/h5/sports/querySportsUsers", body).json()["data"]
print("total:", d["totalAdd"], "count:", len(d["sportsList"]))
print(d["sportsList"][0])
PY
```

## 8. Housekeeping

- One generated record per day is plenty; keep it inside the normal distribution of your history.
- Throwaway records can be removed with `POST /api/h5/sports/delete` (form-encoded `sportId=<id>`).
- Never commit tokens, device identifiers or account data.
