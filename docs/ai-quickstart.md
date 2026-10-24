# AI Quick Start

Paste the block below into any terminal-capable agent (Claude Code, Codex, Cursor, …). It will run
environment checks, generate a candidate record, assert the invariants, upload, and verify.

---

## One-shot prompt

````text
Act as a local ops agent inside this repo (RunningMan). Complete one activity-record run end-to-end.

1. Verify Python >= 3.8 and run `pip install -r requirements.txt`.
2. Resolve auth: `--token`, `SR_TOKEN` env var, or `token.txt`. If none exists, stop and ask me for a
   valid JWT — never guess, scrape, or reuse a cached one.
3. Health check: the `RM_BASE` host must answer `GET {BASE}/mobile/api/h5/home/app_version`
   with HTTP 200.
4. Run `python track_run.py --dry`. Parse the output and assert (retry once, abort on second failure):
   - three checkpoints polled, in-order 3/3
   - zero early-approach violations
   - distance within 5.01–5.10 km and <= 5.5 km
   - average pace between 7'20" and 7'40" per km
   - adjacent per-km split deltas >= 5 s
5. Print the dry-run metrics as a 4-column table and wait for my confirmation.
6. Run `python track_run.py`, capture `data.id`.
7. Verify with `POST {BASE}/mobile/api/h5/sports/querySportsUserRecord/{id}`: distance, timeCount,
   avgSpeed, stepNumber present and mutually consistent, syncCheckFlag == true.
8. Verify with `POST {BASE}/mobile/api/h5/sports/querySportsUsers`
   (body: {"currentPage":1,"pageSize":10,"totalCount":0,"sortBy":"start_time","order":"desc","parameters":{"type":0}})
   that the record is the newest entry and the cumulative distance delta matches the record distance.
9. Report: id, distance, average pace, steps, kcal, cumulative distance.
10. If the run was a test, remove it with `POST {BASE}/mobile/api/h5/sports/delete`
    (form-encoded `sportId=<id>`) and re-query to confirm removal.

Hard constraints: 5.01–5.10 km per run (hard ceiling 5.5), pace 7–8 min/km, never mutate assets/,
never write credentials into the repo.
````

---

## Context blurb (append to the prompt if the agent has no repo knowledge)

```text
Repository facts:
- `track_run.py` polls three checkpoints via POST {BASE}/api/h5/random-way-points/fetch with
  {clientId, currentLng, currentLat}; the response carries `distance` (trigger radius) and three
  `[lng, lat]` points generated around the reported position.
- `assets/track_template.json` is a closed course polyline; the tool repeats it, truncates to the
  target distance and samples every 3 s.
- Record submission: POST {BASE}/mobile/api/h5/sports/saveSportsUserRecordV3 with headers
  X-Token=<JWT> and X-Secret=base64(AES-128-CBC/ZeroPadding("uI2sO8yO:{ms}:{token}")),
  key=lZ7pI6nZ3zT2qF8y, iv=sA7aC2nY4tG8lT6c; body is gzip-compressed JSON.
- All derived statistics (cadence, stride, kcal, altitude, split table) are computed from the same
  arrays that get submitted, so a successful upload is already internally consistent.
```
