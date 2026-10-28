# RunningMan (CosmosUCM ver.)

> Offline trajectory composer for a mobile fitness SUT — route templates, sampling, and payload assembly.

Pure Python. No root, no hooking framework, no mock-location provider — the whole pipeline runs
on the wire format alone: pull checkpoints, compose a closed course, synthesise 3 s GPS samples,
assemble the record payload, dispatch it.

> **License: non-commercial only.** Personal study, research and testing are permitted; any
> commercial use, resale, or paid service built on this code is not. See [LICENSE](LICENSE)
> (PolyForm Noncommercial 1.0.0).

---

## 1. Scope

`RunningMan` is a **test harness** for a `uni-app` 5+ hybrid fitness client. It reproduces the
client's activity-record pipeline end to end so that route/telemetry behaviour can be exercised
deterministically, without a device and without a field run.

The client is treated as a black-box **SUT**: the only assumptions are the HTTPS contract and the
JSON schema of the record payload, both recovered from the shipped JS bundle.

## 2. Recovered contract

| Stage | Detail |
|---|---|
| Auth | `X-Token: <JWT>`, 60-day lifetime |
| Signature | `X-Secret = base64(AES-128-CBC/ZeroPadding("uI2sO8yO:{ms}:{token}"))`, static key + IV |
| Transport | `Content-Type: application/json` + `Content-Encoding: gzip` (gzip-compressed JSON body) |
| Record submission | `POST {BASE}/mobile/api/h5/sports/saveSportsUserRecordV3` |
| Checkpoint polling | `POST {BASE}/api/h5/random-way-points/fetch` → `{distance, wayPoints[[lng,lat] x3]}` |
| Client-side gates | distance 0.1–300 km, pace 2'00"–30'00"/km, in-order checkpoint flag (front-end only) |

`BASE` is supplied through the `RM_BASE` environment variable; no host is hard-coded in the repo.

## 3. Architecture

```
track_run.py            CLI orchestration layer
├── checkpoint polling   random-way-points/fetch
├── route synthesis      assets/track_template.json → Catmull-Rom smoothing
│                        → lap repetition → arc-length truncation
├── trajectory synthesis 3 s sampling / per-km pace jitter / GPS jitter
│                        → derived cadence, stride, calories, altitude, splits
└── dispatch             gzip + X-Token/X-Secret → saveSportsUserRecordV3

protocol.py              protocol layer: AES signer, request wrapper, payload schema, stat derivation
route_osrm.py           OSRM foot-profile routing (used for --gpx custom courses)
login.py                SMS login helper (image captcha → SMS code → AES form → JWT)
assets/                 course template, device fingerprint, checkpoint + marker icons
```

## 4. Quick start

```bash
pip install -r requirements.txt
export RM_BASE="https://<host>"
export SR_TOKEN="<jwt>"                 # Windows: set SR_TOKEN=...

python track_run.py --dry               # generate only, print the metrics
python track_run.py                     # generate and dispatch
python track_run.py --km 5.05 --pace 450 --spread 30
python track_run.py --gpx my_route.gpx  # custom course along real roads
```

Sample output:

```
target distance 5.04 km (random 5.01~5.10)
checkpoints: [...] trigger radius 30 m
track template: 5120 m, in-order 3/3, early-approach 0 (reversed=True)
attempt 1: 5.04 km, 2266 s, pace 07'29'', steps 6345
upload: { "errno":0, "data":{ "id":7269450 } }
```

## 5. Run profile

| Knob | Default | Notes |
|---|---|---|
| Distance | 5.01–5.10 km | randomised per run, hard ceiling 5.5 km, closed-loop regression calibration |
| Average pace | 7'30"/km | `--pace` |
| Per-km jitter | ±30 s | `--spread`; adjacent splits forced ≥5 s apart, then re-centred on the mean |
| Sampling | 3 s | matches the client's location-interval configuration |
| GPS noise | σ = 0.6 m | on top of the template's own noise |

Derived fields (cadence, stride, calories, altitude gain, split table, marker set) are computed
from the same arrays that are submitted, so the record is internally consistent.

## 6. Layout

```
.
├── track_run.py          main CLI (oval course / GPX)
├── protocol.py            protocol layer and payload builder
├── route_osrm.py         OSRM foot routing
├── login.py              SMS login helper
├── assets/
│   ├── track_template.json   closed course template (3 laps, 1215 m)
│   ├── device.json           device fingerprint fixture (scrubbed)
│   ├── wp_icons.json         checkpoint number icons (base64)
│   └── marker_imgs.json      start/finish marker icons (base64)
├── docs/
│   ├── tutorial.md           setup and operation guide
│   └── ai-quickstart.md      one-shot prompt for AI agents
└── requirements.txt
```

## 7. Limits

- The harness reproduces the *record* path only; live UI, sensors and map rendering are out of scope.
- The course template is a fixture: swap it for any closed polyline of your own and the rest of the
  pipeline is unchanged.
- For personal research and interface testing only. You are responsible for how the generated
  records are used against any service.
- Commercial use is not granted. If you need a commercial license, contact the author.

## 8. Disclaimer

Provided **"as is"**, without warranty of any kind regarding completeness, security, or fitness for
purpose. Users assume **sole and complete responsibility** for downloading, deploying, running,
modifying and redistributing this software, and for every input, output and consequence arising from
it, including any civil, administrative or criminal liability. The author and contributors accept no
liability — direct, indirect, incidental or consequential — for any damage resulting from misuse.
Full text: [DISCLAIMER.md](DISCLAIMER.md).

## 9. License

[PolyForm Noncommercial 1.0.0](LICENSE) — non-commercial use only. No commercial use, resale, or
incorporation into a paid product or service is permitted without a separate written license.
