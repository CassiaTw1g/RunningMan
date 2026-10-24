"""
Trajectory synthesis and record submission helper for the client under test.
----------------------------------------------------------------------------
- Pulls three random checkpoints, then composes a closed 5 km loop that hits W1/W2/W3 in order
- Generates 3 s-interval GPS samples at a 7~8 min/km pace
- Assembles the sportUserItem payload, gzips it and dispatches it with X-Secret
"""
import argparse, base64, gzip, json, math, os, random, sys, time
from datetime import datetime, timedelta, timezone
import requests
from Crypto.Cipher import AES

BASE = os.environ.get("RM_BASE", "").rstrip("/")   # service endpoint, e.g. https://host
KEY = b"lZ7pI6nZ3zT2qF8y"
IV = b"sA7aC2nY4tG8lT6c"
R_EARTH = 6371008.8

# UI labels emitted into the payload (kept as escaped code points: the client renders Chinese)
LABEL_START = "\u8d77\u70b9"
LABEL_END = "\u7ec8\u70b9"
LABEL_CHECKPOINT = "\u9014\u7ecf\u70b9"
LABEL_CLEAR = "\u6674"



# ---------------- crypto / http ----------------
def _pad(b):
    r = len(b) % 16
    return b if r == 0 else b + b"\x00" * (16 - r)


def create_secret(token):
    ts = int(time.time() * 1000)
    ct = AES.new(KEY, AES.MODE_CBC, IV).encrypt(_pad(f"uI2sO8yO:{ts}:{token}".encode()))
    return base64.b64encode(ct).decode()


def api(token, path, body=None, method="POST", raw=False):
    if not BASE:
        raise SystemExit("RM_BASE is not set (service endpoint, e.g. export RM_BASE=https://host)")
    h = {"X-Token": token, "X-Secret": create_secret(token),
         "terminal": "33", "content-type": "application/json"}
    url = BASE + "/mobile" + path
    last = None
    for attempt in range(4):
        try:
            if method == "GET":
                return requests.get(url, headers=h, timeout=25)
            if raw:
                return requests.post(url, headers=h, data=body, timeout=45)
            return requests.post(url, headers=h, json=body, timeout=25)
        except Exception as e:
            last = e
            time.sleep(2 + attempt * 2)
    raise last


# ---------------- geo ----------------
def dist(a, b):
    lon1, lat1, lon2, lat2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    dlon, dlat = lon2 - lon1, lat2 - lat1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * R_EARTH * math.asin(math.sqrt(h))


def bearing(a, b):
    lon1, lat1, lon2, lat2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    dlon = lon2 - lon1
    y = math.sin(dlon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def offset(p, dx_east, dy_north):
    lat = p[1] + dy_north / 111320.0
    lon = p[0] + dx_east / (111320.0 * math.cos(math.radians(p[1])))
    return (lon, lat)


def catmull_rom_closed(pts, samples_per_seg=40):
    n = len(pts)
    out = []
    for i in range(n):
        p0, p1, p2, p3 = pts[(i - 1) % n], pts[i], pts[(i + 1) % n], pts[(i + 2) % n]
        for s in range(samples_per_seg):
            t = s / samples_per_seg
            t2, t3 = t * t, t * t * t
            x = 0.5 * ((2 * p1[0]) + (-p0[0] + p2[0]) * t +
                       (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2 +
                       (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3)
            y = 0.5 * ((2 * p1[1]) + (-p0[1] + p2[1]) * t +
                       (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2 +
                       (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3)
            out.append((x, y))
    out.append(out[0])
    return out


def polyline_len(pts):
    return sum(dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


# ---------------- route ----------------
def build_route(waypoints, target_m, seed=0):
    """Closed loop anchored on the three checkpoints; the outward bulge height controls length.
    Anchors stay fixed => the checkpoints are always passed in order; bulges point outward =>
    the path never brushes a later checkpoint early."""
    C = (sum(p[0] for p in waypoints) / 3, sum(p[1] for p in waypoints) / 3)
    lat0 = math.radians(C[1])
    mx = 111320.0 * math.cos(lat0)
    my = 111320.0

    def to_local(p):
        return ((p[0] - C[0]) * mx, (p[1] - C[1]) * my)

    def to_geo(x, y):
        return (C[0] + x / mx, C[1] + y / my)

    loc = [to_local(w) for w in waypoints]

    def ctrl_for(h):
        out = []
        for i in range(3):
            a = loc[i]
            b = loc[(i + 1) % 3]
            out.append(a)
            seg = math.hypot(b[0] - a[0], b[1] - a[1]) or 1e-6
            dx, dy = (b[0] - a[0]) / seg, (b[1] - a[1]) / seg
            # outward normal
            nx, ny = -dy, dx
            mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            if nx * mid[0] + ny * mid[1] < 0:
                nx, ny = -nx, -ny
            nfill = max(8, int(round((seg + 2 * h) / 35.0)))
            if h < 1.0:
                for k in range(1, nfill):
                    f = k / nfill
                    out.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
                continue
            # circular arc bulge (sagitta h) - closer to a real looped course
            r = (seg * seg / 4.0 + h * h) / (2 * h)
            ox, oy = (a[0] + b[0]) / 2 - nx * (r - h), (a[1] + b[1]) / 2 - ny * (r - h)
            ang_a = math.atan2(a[1] - oy, a[0] - ox)
            ang_b = math.atan2(b[1] - oy, b[0] - ox)
            apex = ((a[0] + b[0]) / 2 + nx * h, (a[1] + b[1]) / 2 + ny * h)
            best_d, best_dang = None, 0.0
            for base in ((ang_b - ang_a) % (2 * math.pi),
                         -((ang_a - ang_b) % (2 * math.pi))):
                mx_ = ox + r * math.cos(ang_a + base * 0.5)
                my_ = oy + r * math.sin(ang_a + base * 0.5)
                dd = math.hypot(mx_ - apex[0], my_ - apex[1])
                if best_d is None or dd < best_d:
                    best_d, best_dang = dd, base
            for k in range(1, nfill):
                f = k / nfill
                ang = ang_a + best_dang * f
                out.append((ox + r * math.cos(ang), oy + r * math.sin(ang)))
        return [to_geo(x, y) for x, y in out]

    lo, hi, best = 0.0, 4000.0, (0.0, 0.0)
    for _ in range(36):
        mid = (lo + hi) / 2.0
        L = polyline_len(catmull_rom_closed(ctrl_for(mid), 20))
        best = (mid, L)
        if L < target_m:
            lo = mid
        else:
            hi = mid
        if abs(L - target_m) < 6:
            break
    print("   fit bulge=%.1f L=%.1f" % best)
    dense = catmull_rom_closed(ctrl_for(best[0]), 90)

    # start point: past W3, more than 60 m away from every checkpoint
    i3 = min(range(len(dense)), key=lambda i: dist(dense[i], waypoints[2]))
    dense = dense[i3 + 1:-1] + dense[:i3 + 1] + [dense[i3 + 1]]
    n = len(dense) - 1
    for k in range(n):
        if min(dist(dense[k], w) for w in waypoints) > 60:
            dense = dense[k:n] + dense[:k] + [dense[k]]
            break
    return dense


def _order_score(waypoints, theta):
    """Project the checkpoints onto direction theta; return (usable, direction)."""
    lat = math.radians(waypoints[0][1])
    mx, my = 111320.0 * math.cos(lat), 111320.0
    C = (sum(w[0] for w in waypoints) / 3, sum(w[1] for w in waypoints) / 3)
    tx, ty = -math.sin(theta), math.cos(theta)
    proj = [((w[0] - C[0]) * mx) * tx + ((w[1] - C[1]) * my) * ty for w in waypoints]
    asc = sorted(range(3), key=lambda i: proj[i])
    desc = asc[::-1]
    if asc == [0, 1, 2]:
        return True, 1
    if desc == [0, 1, 2]:
        return True, -1
    return False, 0


def build_circle_route(waypoints, target_m, theta, direction, seed=0):
    """Large ring outside the cluster, locally bent to pass through all three checkpoints."""
    lat = math.radians(waypoints[0][1])
    mx, my = 111320.0 * math.cos(lat), 111320.0
    C = (sum(w[0] for w in waypoints) / 3, sum(w[1] for w in waypoints) / 3)

    def to_local(p):
        return ((p[0] - C[0]) * mx, (p[1] - C[1]) * my)

    def to_geo(x, y):
        return (C[0] + x / mx, C[1] + y / my)

    loc_w = [to_local(w) for w in waypoints]
    ang_w = [math.atan2(y - math.sin(theta) * 0, x) for x, y in loc_w]  # placeholder

    def ctrl_for(R):
        cx, cy = math.cos(theta) * R, math.sin(theta) * R
        items = []
        for w in loc_w:
            a = math.atan2(w[1] - cy, w[0] - cx) % (2 * math.pi)
            items.append((a, w))
        n_fill = 30
        out = []
        for k in range(n_fill + 3):
            pass
        # ring points inserted in angular order
        items.sort(key=lambda t: t[0])
        angles = [t[0] for t in items]
        for i in range(3):
            a0 = angles[i]
            a1 = angles[(i + 1) % 3]
            gap = (a1 - a0) % (2 * math.pi)
            if gap < 1e-4:
                gap = 2 * math.pi / 3
            steps = max(4, int(round(math.degrees(gap) / 12.0)))
            for k in range(steps):
                f = k / steps
                a = a0 + gap * f
                rr = R * (1 + 0.04 * math.sin(3 * a + 1.1) + 0.02 * math.sin(5 * a + 2.2))
                out.append(to_geo(cx + rr * math.cos(a), cy + rr * math.sin(a)))
            out.append(to_geo(*items[(i + 1) % 3][1]))
        return out

    lo, hi, best = 300.0, 2000.0, (800.0, 0.0)
    for _ in range(30):
        mid = (lo + hi) / 2
        pts = ctrl_for(mid)
        L = polyline_len(catmull_rom_closed(pts, 25))
        best = (mid, L)
        if L < target_m:
            lo = mid
        else:
            hi = mid
        if abs(L - target_m) < 8:
            break
    dense = catmull_rom_closed(ctrl_for(best[0]), 90)
    # rotate so the run starts >60 m from every checkpoint (W1 is the first one met)
    n = len(dense) - 1
    for k in range(n):
        if min(dist(dense[k], w) for w in waypoints) > 60:
            dense = dense[k:n] + dense[:k] + [dense[k]]
            break
    if direction < 0:
        dense = [dense[0]] + dense[1:][::-1]
    return dense


def resample(path, step_m):
    """Resample the polyline at a fixed arc-length step."""
    out = [path[0]]
    acc = 0.0
    for i in range(len(path) - 1):
        a, b = path[i], path[i + 1]
        seg = dist(a, b)
        if seg <= 0:
            continue
        while acc + seg >= step_m:
            t = (step_m - acc) / seg
            a = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
            out.append(a)
            seg = dist(a, b)
            acc = 0.0
        acc += seg
    return out


def load_gpx(path):
    """Read a GPX track ([trkpt] or [rtept]) into [(lon, lat), ...]."""
    import xml.etree.ElementTree as ET
    tree = ET.parse(path)
    pts = []
    for node in tree.iter():
        tag = node.tag.split('}')[-1]
        if tag in ("trkpt", "rtept", "wpt"):
            lat, lon = node.get("lat"), node.get("lon")
            if lat and lon:
                pts.append((float(lon), float(lat)))
    if len(pts) < 2:
        raise SystemExit("GPX file does not contain enough track points")
    return pts


def splice_waypoints(route, waypoints, thr):
    """Splice the random checkpoints into a custom route (out-and-back detour when far)."""
    pts = list(route)
    out = [pts[0]]
    wi = 0
    for i in range(1, len(pts)):
        out.append(pts[i])
        while wi < len(waypoints) and dist(pts[i], waypoints[wi]) <= thr:
            wi += 1
        if wi < len(waypoints) and dist(pts[i], waypoints[wi]) > thr:
            # if the next point is closer to that checkpoint, detour now
            if dist(pts[i], waypoints[wi]) < 220 and (
                    i + 1 >= len(pts) or dist(pts[i + 1], waypoints[wi]) > dist(pts[i], waypoints[wi])):
                out.append((waypoints[wi][0], waypoints[wi][1]))
                out.append(pts[i])
                wi += 1
    return out


# ---------------- stats ----------------
def fmt_pace(sec_per_km):
    m, s = divmod(int(round(sec_per_km)), 60)
    return f"{m:02d}'{s:02d}''"


def build_payload(token_ctx, route_pts, waypoints, threshold, start_dt, pace_s, weight, client_id,
                  device_info, marker_imgs, km_spread=11.0):
    """km_spread: per-km pace jitter in seconds; adjacent splits differ by >=5 s."""
    dt = 3                                   # nominal sampling interval
    n = len(route_pts)
    ts0 = start_dt

    # 1) apply GPS jitter
    q_pts = []
    for i, p in enumerate(route_pts):
        q_pts.append(offset(p, random.gauss(0, 0.6), random.gauss(0, 0.6)))

    # 2) cumulative distance
    cum = [0.0]
    for i in range(1, n):
        cum.append(cum[-1] + dist(q_pts[i - 1], q_pts[i]))
    total_m = cum[-1]

    # 3) give every full kilometre its own duration, then interpolate timestamps
    n_full = int(total_m // 1000)
    km_secs = [pace_s + random.uniform(-km_spread, km_spread) for _ in range(n_full)]
    # enforce at least 5 s difference between adjacent splits
    for k in range(1, len(km_secs)):
        delta = km_secs[k] - km_secs[k - 1]
        if abs(delta) < 5.0:
            sign = 1 if delta >= 0 else -1
            km_secs[k] = km_secs[k - 1] + sign * random.uniform(5.0, 9.0)
    if km_secs:   # re-center so the average pace still matches the target
        shift = pace_s - sum(km_secs) / len(km_secs)
        km_secs = [x + shift for x in km_secs]
    # remaining sub-kilometre tail scaled by the average pace
    tail_m = total_m - n_full * 1000
    tail_secs = tail_m / 1000.0 * (sum(km_secs) / n_full if n_full else pace_s)

    marks_s = [0.0]                       # distance marks
    marks_t = [0.0]                       # cumulative time marks
    for k in range(n_full):
        marks_s.append((k + 1) * 1000.0)
        marks_t.append(marks_t[-1] + km_secs[k])
    marks_s.append(total_m)
    marks_t.append(marks_t[-1] + tail_secs)

    def time_at(s):
        for k in range(1, len(marks_s)):
            if s <= marks_s[k] or k == len(marks_s) - 1:
                span = marks_s[k] - marks_s[k - 1]
                f = 0.0 if span <= 0 else (s - marks_s[k - 1]) / span
                return marks_t[k - 1] + (marks_t[k] - marks_t[k - 1]) * f
        return marks_t[-1]

    duration = int(round(marks_t[-1]))
    steps_total = int(round(168 * duration / 60.0))  # cadence 168 spm
    alt0 = 26.0

    locs = []
    last_t = 0.0
    for i in range(n):
        t_i = time_at(cum[i])
        seg_t = max(0.5, t_i - last_t) if i else 1.0
        seg_d = (cum[i] - cum[i - 1]) if i else 0.0
        spd = seg_d / seg_t if i else 0.0
        last_t = t_i
        alt = alt0 + 3.4 * math.sin(i / 90.0) + random.gauss(0, 0.3)
        brg = bearing(route_pts[i - 1], route_pts[i]) if i else 0.0
        locs.append({
            "timestamp": int((ts0 + timedelta(seconds=t_i)).timestamp() * 1000),
            "longitude": round(q_pts[i][0], 7),
            "speed": round(max(spd, 0), 2),
            "altitude": round(alt, 1),
            "accuracy": round(random.uniform(2.0, 6.0), 1),
            "latitude": round(q_pts[i][1], 7),
            "bearing": round((brg + random.gauss(0, 2)) % 360, 2),
            "locationType": 1,
            "state": 1,
            "steps": int(steps_total * t_i / max(1.0, marks_t[-1])),
            "locNumber": i + 1,
        })
    # first sample fix-up
    locs[0].update({"speed": 0, "locationType": 5, "steps": 0})

    total = sum(dist((locs[i - 1]["longitude"], locs[i - 1]["latitude"]),
                     (locs[i]["longitude"], locs[i]["latitude"])) for i in range(1, len(locs)))
    dist_km = math.floor(total / 100.0) / 10.0        # client: floor(m/1000*100)/100
    dist_km = round(total / 1000.0, 2)

    # per-kilometre splits
    details, acc, cur, km_no = [], 0.0, 0, 0
    t_start = locs[0]["timestamp"]
    per_km = []
    for i in range(1, len(locs)):
        d = dist((locs[i - 1]["longitude"], locs[i - 1]["latitude"]),
                 (locs[i]["longitude"], locs[i]["latitude"]))
        acc += d
        if acc >= 1000:
            per_km.append(i)
            acc -= 1000
    details, cum = [], 0
    prev_i = 0
    for k, idx in enumerate(per_km[: int(dist_km)]):
        secs = int((locs[idx]["timestamp"] - locs[prev_i]["timestamp"]) / 1000)
        cum += secs
        details.append({"number": k + 1, "time": locs[idx]["timestamp"],
                        "sportSeconds": secs, "totalSportSeconds": cum})
        prev_i = idx
    if not details:
        details = [{"number": 1, "time": locs[-1]["timestamp"], "sportSeconds": duration,
                    "totalSportSeconds": duration}]
    sp_vals = [d["sportSeconds"] for d in details]
    speed_km = {"fast": min(sp_vals), "slow": max(sp_vals),
                "average": int(round(duration / dist_km)) if dist_km else pace_s,
                "details": details}

    # cadence
    sf_details, cum_s = [], 0
    for k, d in enumerate(details):
        sn = int(steps_total * (k + 1) / len(details))
        cum_s = sn
        sf_details.append({"number": k + 1, "time": d["time"], "stepNumber": sn,
                           "totalStepNumber": sn})
    step_freq = {"fast": 176, "slow": 160, "average": int(round(steps_total / duration * 60)),
                 "details": sf_details}

    # altitude (per km block: max - min = climb of that block)
    al_details, cum_a, total_down = [], 0.0, 0.0
    bounds = [0] + per_km + [len(locs) - 1]
    for k in range(len(bounds) - 1):
        seg = locs[bounds[k]:bounds[k + 1] + 1]
        alts = [x["altitude"] for x in seg] or [alt0]
        climb = round(max(alts) - min(alts), 2)
        cum_a += climb
        total_down += climb * 0.95
        al_details.append({"number": k + 1,
                           "time": seg[-1]["timestamp"],
                           "altitude": round(sum(alts) / len(alts), 2),
                           "min": round(min(alts), 1), "max": round(max(alts), 1)})
    alt_data = {"total": int(round(cum_a)), "down": -int(round(total_down)), "details": al_details}

    markers = []
    mk = 1
    seq = [(LABEL_START, locs[0], marker_imgs["start"]), (LABEL_END, locs[-1], marker_imgs["end"])]
    for n, w in enumerate(waypoints, start=1):
        seq.append((LABEL_CHECKPOINT, {"longitude": w[0], "latitude": w[1]},
                    marker_imgs["wp"][str(n)]))
    for title, pos, img in seq:
        markers.append({"id": mk, "latitude": pos["latitude"], "longitude": pos["longitude"],
                        "title": title, "subtitle": title, "image": img, "width": 28,
                        "height": 28, "offsetX": 0, "offsetY": 0, "tintColor": "#ff0000",
                        "timestamp": locs[0]["timestamp"]})
        mk += 1

    avg_sp_km = int(duration / dist_km) if dist_km else pace_s
    payload = {
        "state": 3, "type": 1,
        "startTime": ts0.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (ts0.microsecond // 1000),
        "endTime": (ts0 + timedelta(seconds=duration)).strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % 0,
        "distance": dist_km,
        "timeCount": duration,
        "stepNumber": steps_total,
        "avgSpeed": fmt_pace(avg_sp_km),
        "locations": json.dumps(locs, separators=(",", ":")),
        "smoothLocations": json.dumps(locs, separators=(",", ":")),
        "markers": json.dumps(markers, separators=(",", ":")),
        "kilocalorie": int(1.036 * dist_km * weight),
        "stepFreqData": json.dumps(step_freq, separators=(",", ":")),
        "avgStepFreq": float(step_freq["average"]),
        "fastStepFreq": float(step_freq["fast"]),
        "slowStepFreq": float(step_freq["slow"]),
        "avgStride": round(100 * (dist_km * 1000) / steps_total, 1),
        "speedKilometerData": json.dumps(speed_km, separators=(",", ":")),
        "fastSpeedKilometer": speed_km["fast"],
        "slowSpeedKilometer": speed_km["slow"],
        "avgSpeedKilometer": avg_sp_km,
        "totalAltitude": alt_data["total"],
        "downAltitude": alt_data["down"],
        "altitudeData": json.dumps(alt_data, separators=(",", ":")),
        "targetValue": 0.0, "targetType": 0,
        "deviceInfo": json.dumps(device_info, ensure_ascii=False, separators=(",", ":")),
        "weather": LABEL_CLEAR,
        "sameTimeFlag": 0, "sameTimeSportId": 0,
        "sportSourceType": "APP",
        "clientId": client_id,
        "sportMode": 2,
        "extraAttributes": json.dumps({"wayPoints": [list(w) for w in waypoints],
                                       "checkWayPoint": True}, separators=(",", ":")),
        "isPaused": False,
        "gpsLevel": 2,
    }
    return payload, locs, total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", default=os.environ.get("SR_TOKEN") or (open("token.txt").read().strip() if os.path.exists("token.txt") else None))
    ap.add_argument("--km", type=float, default=5.0)
    ap.add_argument("--pace", type=int, default=450, help="seconds per kilometre")
    ap.add_argument("--lat", type=float, default=39.7129)
    ap.add_argument("--lng", type=float, default=116.1726)
    ap.add_argument("--weight", type=float, default=55)
    ap.add_argument("--dry", action="store_true", help="generate only, do not upload")
    ap.add_argument("--delay", type=int, default=0, help="seconds between endTime and now")
    ap.add_argument("--gpx", default=None, help="GPX file for a custom route (checkpoints spliced in)")
    args = ap.parse_args()

    tok = args.token
    clients = api(tok, "/api/h5/system-oauth2-access-token/clients", method="GET").json()
    cid = clients["data"][0]["clientId"]
    print("clientId:", cid)

    wp = api(tok, "/api/h5/random-way-points/fetch",
             {"clientId": cid, "currentLng": args.lng, "currentLat": args.lat}).json()
    print("waypoints:", json.dumps(wp, ensure_ascii=False))
    wps = [tuple(x) for x in wp["data"]["wayPoints"]]
    thr = wp["data"]["distance"]

    device_info = json.load(open("assets/device.json", encoding="utf-8"))
    mimg = json.load(open("assets/marker_imgs.json", encoding="utf-8"))
    mimg["wp"] = json.load(open("wp_icons.json", encoding="utf-8"))

    end = datetime.now(timezone.utc) - timedelta(seconds=args.delay)

    # generate (GPS jitter makes the measured distance ~1% longer than the nominal length)
    target = args.km * 1000.0
    seed = random.randrange(99999)
    payload = locs = None
    for attempt in range(3):
        random.seed(seed)
        if args.gpx:
            base = load_gpx(args.gpx)
            base = splice_waypoints(base, wps, thr)
            if attempt == 0:
                print("   custom route:", len(base), "pts, base len:",
                      round(polyline_len(base), 1), "m")
            route = resample(base, 1000.0 / args.pace * 3)
        else:
            route = resample(build_route(wps, target, seed=seed), 1000.0 / args.pace * 3)
        dur = (len(route) - 1) * 3
        start = end - timedelta(seconds=dur)
        payload, locs, total = build_payload(tok, route, wps, thr, start, args.pace,
                                             args.weight, cid, device_info, mimg)
        if args.gpx or abs(total - args.km * 1000.0) < args.km * 1000.0 * 0.008:
            break
        target *= (args.km * 1000.0) / total
    print("route pts:", len(route), "base:", round(target), "measured:", round(total, 1))

    # sanity check: checkpoints passed in order
    passed = 0
    for p in [(l["longitude"], l["latitude"]) for l in locs]:
        if passed < 3 and dist(p, wps[passed]) <= thr:
            passed += 1
    print("waypoints passed in order:", passed)
    print("distance:", payload["distance"], "timeCount:", payload["timeCount"],
          "avgSpeed:", payload["avgSpeed"], "steps:", payload["stepNumber"])
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    gz = gzip.compress(body, 6)
    print("payload bytes:", len(body), "gzip:", len(gz))
    open("payload.json", "wb").write(body)

    if args.dry:
        print("dry run, skip upload")
        return

    r = api(tok, "/api/h5/sports/saveSportsUserRecordV3", gz, raw=True)
    print("upload:", r.status_code, r.text[:500])


if __name__ == "__main__":
    main()
