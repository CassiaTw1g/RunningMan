"""Oval-course mode: N laps of a 400 m track.
------------------------------------------------
Pipeline:
  1. poll three checkpoints from the service, generated around the reported position
  2. fit the track ellipse (adaptive a/b) so all checkpoints sit inside the 30 m trigger radius
  3. pick direction and start offset so the checkpoints fire in the order 1 -> 2 -> 3
  4. sample every 3 s at a 7~8 min/km pace, then gzip + X-Secret upload

Usage:
  python track_run.py                # 5.0 km, 7'30"/km, upload
  python track_run.py --km 5 --pace 450 --dry
  python track_run.py --gpx my.gpx   # custom route along real roads
"""
import argparse, gzip, json, math, os, sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, '.')
from protocol import api, dist, build_payload, load_gpx, splice_waypoints

HERE = os.path.dirname(os.path.abspath(__file__))


def asset(name):
    """Asset lookup: ./assets/ first, then the current directory."""
    for p in (os.path.join(HERE, "assets", name), os.path.join(HERE, name), name):
        if os.path.exists(p):
            return p
    return os.path.join(HERE, "assets", name)


def resolve_token(cli=None):
    if cli:
        return cli
    if os.environ.get("SR_TOKEN"):
        return os.environ["SR_TOKEN"].strip()
    for p in (os.path.join(HERE, "token.txt"), "token.txt"):
        if os.path.exists(p):
            return open(p).read().strip()
    raise SystemExit("missing token: pass --token, set SR_TOKEN, or create token.txt")


TRACK = (116.17304, 39.71102)        # track centre (derived from historical runs)
MAX_KM = 5.5                          # hard daily ceiling


def oval(center, a, b, n=240, reverse=False):
    pts = []
    for i in range(n):
        t = -2 * math.pi * i / n if reverse else 2 * math.pi * i / n
        pts.append((center[0] + a * math.cos(t) / (111320.0 * math.cos(math.radians(center[1]))),
                    center[1] + b * math.sin(t) / 111320.0))
    pts.append(pts[0])
    return pts


def perimeter(p):
    return sum(dist(p[i], p[i + 1]) for i in range(len(p) - 1))


def rotate(p, k):
    n = len(p) - 1
    k %= n
    return p[k:n] + p[:k] + [p[k]]


def check_order(path, wps, thr):
    cur, viol = 0, 0
    for q in path:
        if cur < 3 and dist(q, wps[cur]) <= thr:
            cur += 1
        for j in range(cur + 1, 3):
            if dist(q, wps[j]) <= thr:
                viol += 1
    return cur, viol


def fit_lane(wps, thr):
    """Enumerate a/b and pick the oval that covers all checkpoints with a ~400 m lap."""
    best = None
    for a in [x / 2 for x in range(80, 116)]:        # 40..57.5 m
        for b in [x / 2 for x in range(144, 184)]:   # 72..91.5 m
            p = oval(TRACK, a, b, 180)
            L = perimeter(p)
            if not (370 <= L <= 440):
                continue
            gaps = [min(dist(q, w) for q in p) for w in wps]
            if max(gaps) > thr - 3:
                continue
            score = abs(L - 400) + max(gaps) * 3
            if best is None or score < best[0]:
                best = (score, a, b, L, p, gaps)
    return best




def smooth_template(tpl):
    """Smoothly close the template seam: rotate to the smallest gap, then Hermite-blend."""
    n = len(tpl)
    k = min(range(n), key=lambda i: dist(tpl[i - 1], tpl[i]))
    t = tpl[k:] + tpl[:k]
    p0, p1 = t[-1], t[0]
    gap = dist(p0, p1)
    if gap < 1.5:
        return t
    # tangents from finite differences, scaled to the gap length
    m0 = ((p0[0] - t[-2][0]), (p0[1] - t[-2][1]))
    m1 = ((t[1][0] - p1[0]), (t[1][1] - p1[1]))
    for s in (1, 2, 3, 4):
        u = s / 5.0
        h00 = 2 * u ** 3 - 3 * u ** 2 + 1
        h10 = u ** 3 - 2 * u ** 2 + u
        h01 = -2 * u ** 3 + 3 * u ** 2
        h11 = u ** 3 - u ** 2
        t.append((h00 * p0[0] + h10 * m0[0] + h01 * p1[0] + h11 * m1[0],
                  h00 * p0[1] + h10 * m0[1] + h01 * p1[1] + h11 * m1[1]))
    t.append(t[0])
    return t

def build_from_template(tpl, target_m, jitter=0.7):
    """Repeat the real track shape up to the target distance.
    Each lap only gets a small jitter; laps overlap so the map shows a single course line."""
    import random
    lat0 = math.radians(tpl[0][1])
    kx = 111320.0 * math.cos(lat0)
    tpl = list(tpl)
    if dist(tpl[-1], tpl[0]) > 0.5:
        tpl.append(tpl[0])          # close the loop, avoids a straight seam on every lap
    path = [tpl[0]]
    total = 0.0
    while total < target_m:
        jx, jy = random.gauss(0, jitter), random.gauss(0, jitter)
        for (lon, lat) in tpl[1:]:
            pt = (lon + jx / kx, lat + jy / 111320.0)
            total += dist(path[-1], pt)
            path.append(pt)
            if total >= target_m:
                break
    if dist(path[-1], path[0]) > 1:
        path.append(path[0])
    return path



def trim_to(path, target_m):
    """Truncate the polyline after target_m (exact distance control)."""
    out = [path[0]]
    acc = 0.0
    for i in range(len(path) - 1):
        a, b = path[i], path[i + 1]
        seg = dist(a, b)
        if acc + seg >= target_m:
            t = (target_m - acc) / seg if seg else 0
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
            return out
        out.append(b)
        acc += seg
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", default=None, help="auth token; also SR_TOKEN or token.txt")
    ap.add_argument("--km", type=float, default=None, help="random 5.01~5.10 km when omitted")
    ap.add_argument("--pace", type=int, default=450, help="seconds per km (default 7'30\")")
    ap.add_argument("--weight", type=float, default=55)
    ap.add_argument("--gpx", default=None, help="custom route GPX")
    ap.add_argument("--spread", type=float, default=11.0, help="per-km pace jitter in seconds")
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    import random
    if args.km is None:
        args.km = round(random.uniform(5.01, 5.10), 2)
        print("target distance %.2f km (random 5.01~5.10)" % args.km)
    if args.km > MAX_KM:
        print(f"--km {args.km} exceeds the {MAX_KM} km ceiling, clamped to {MAX_KM}")
        args.km = MAX_KM

    tok = resolve_token(args.token)
    cid = api(tok, "/api/h5/system-oauth2-access-token/clients", method="GET").json()["data"][0]["clientId"]
    wp = api(tok, "/api/h5/random-way-points/fetch",
             {"clientId": cid, "currentLng": TRACK[0], "currentLat": TRACK[1]}).json()
    wps = [tuple(x) for x in wp["data"]["wayPoints"]]
    thr = wp["data"]["distance"]
    print("checkpoints:", [f"({w[0]:.6f},{w[1]:.6f})" for w in wps], "trigger radius", thr, "m")

    import os
    src = asset("track_template.json")
    tpl0 = None if args.gpx else smooth_template(json.load(open(src)))

    def make_route(target_m):
        if args.gpx:
            return splice_waypoints(load_gpx(args.gpx), wps, thr)
        best_t = None
        for rev in (False, True):
            t = tpl0[::-1] if rev else tpl0
            i1 = min(range(len(t)), key=lambda i: dist(t[i], wps[0]))
            for back in (4, 8, 14, 20):
                k = (i1 - back) % len(t)
                cand = build_from_template(t[k:] + t[:k], target_m)
                c0, v0 = check_order(cand, wps, thr)
                if c0 == 3 and (best_t is None or v0 < best_t[0]):
                    best_t = (v0, cand, rev, back)
        if best_t:
            v0, cand, rev, back = best_t
            print("track template: %.0f m, in-order 3/3, early-approach %d (reversed=%s)" % (perimeter(cand), v0, rev))
            return trim_to(cand, target_m)
        fit = fit_lane(wps, thr)
        if not fit:
            return None
        score, a, b, L, lap, gaps = fit
        for rev in (True, False):
            full = oval(TRACK, a, b, 240, reverse=rev)
            i1 = min(range(len(full) - 1), key=lambda i: dist(full[i], wps[0]))
            for back in (3, 6, 10, 16):
                pth = rotate(full, (i1 - back) % (len(full) - 1))
                c2, _ = check_order(pth, wps, thr)
                if c2 == 3:
                    n = max(1, int(math.ceil(target_m / L)))
                    if n * L > MAX_KM * 1000:
                        n = max(1, int(target_m / L))
                    route = []
                    for i in range(n):
                        route += pth if i == 0 else pth[1:]
                    route.append(route[0])
                    return route
        return None

    target_m = args.km * 1000
    payload = None
    for attempt in range(4):
        route = make_route(target_m)
        if not route:
            print("no usable route")
            return
        # 3 s sampling
        step = 1000.0 / args.pace * 3
        out = [route[0]]
        acc = 0.0
        for i in range(len(route) - 1):
            a1, b1 = route[i], route[i + 1]
            seg = dist(a1, b1)
            while acc + seg >= step:
                t = (step - acc) / seg
                a1 = (a1[0] + (b1[0] - a1[0]) * t, a1[1] + (b1[1] - a1[1]) * t)
                out.append(a1)
                seg = dist(a1, b1)
                acc = 0.0
            acc += seg

        device_info = json.load(open(asset("device.json"), encoding="utf-8"))
        mimg = json.load(open(asset("marker_imgs.json"), encoding="utf-8"))
        mimg["wp"] = json.load(open(asset("wp_icons.json"), encoding="utf-8"))

        dur = (len(out) - 1) * 3
        end = datetime.now(timezone.utc)
        start = end - timedelta(seconds=dur)
        payload, locs, total = build_payload(tok, out, wps, thr, start, args.pace,
                                             args.weight, cid, device_info, mimg,
                                             km_spread=args.spread)
        d = payload["distance"]
        print("attempt %d: %.2f km, %d s, pace %s, steps %d"
              % (attempt + 1, d, payload["timeCount"], payload["avgSpeed"], payload["stepNumber"]))
        if args.gpx or 5.01 <= d <= 5.10:
            break
        target_m += (args.km - d) * 1000
        target_m = min(target_m, MAX_KM * 1000 - 30)

    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    open("payload_track.json", "wb").write(body)

    if args.dry:
        print("dry run, nothing uploaded")
        return
    r = api(tok, "/api/h5/sports/saveSportsUserRecordV3", gzip.compress(body, 6), raw=True)
    print("upload:", " ".join(r.text.split())[:160])


if __name__ == "__main__":
    main()
