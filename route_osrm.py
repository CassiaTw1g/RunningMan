"""Real-road routing through the OSRM foot profile; keeps checkpoint order."""
import json, math, os, time
import requests

CACHE = "osrm_cache.json"
_C = {}
if os.path.exists(CACHE):
    try:
        _C = json.load(open(CACHE, encoding="utf-8"))
    except Exception:
        _C = {}

R_EARTH = 6371008.8


def dist(a, b):
    lon1, lat1, lon2, lat2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    dlon, dlat = lon2 - lon1, lat2 - lat1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * R_EARTH * math.asin(math.sqrt(h))


def save_cache():
    json.dump(_C, open(CACHE, "w", encoding="utf-8"))


def osrm(p1, p2, profile="foot"):
    key = f"{profile}:{p1[0]:.6f},{p1[1]:.6f}->{p2[0]:.6f},{p2[1]:.6f}"
    if key in _C:
        return [tuple(x) for x in _C[key]]
    url = (f"https://routing.openstreetmap.de/routed-{profile}/route/v1/driving/"
           f"{p1[0]},{p1[1]};{p2[0]},{p2[1]}?overview=full&geometries=geojson")
    for attempt in range(4):
        try:
            r = requests.get(url, headers={"User-Agent": "RunningMan/1.0"}, timeout=30)
            j = r.json()
            if j.get("code") != "Ok":
                raise RuntimeError(j.get("code"))
            coords = j["routes"][0]["geometry"]["coordinates"]
            _C[key] = coords
            save_cache()
            return [tuple(x) for x in coords]
        except Exception:
            time.sleep(2 + attempt * 2)
    raise RuntimeError("OSRM routing failed")


def chain(points, profile="foot"):
    """Chain several points into a single path."""
    path = []
    for i in range(len(points) - 1):
        seg = osrm(points[i], points[i + 1], profile)
        path.extend(seg if not path else seg[1:])
    return path


def len_of(path):
    return sum(dist(path[i], path[i + 1]) for i in range(len(path) - 1))


def build_lap_route(wps, target_m, anchor, max_km=5.5):
    """One lap anchored at `anchor`, visiting W1->W2->W3, repeated to the target distance."""
    circuit = chain([anchor, wps[0], wps[1], wps[2], anchor])
    L = len_of(circuit)
    n = max(1, int(target_m // L))
    total_planned = L * n
    if total_planned < target_m * 0.9:
        n += 1
    full = []
    for k in range(n):
        full.extend(circuit if k == 0 else circuit[1:])
    # start from the point farthest from the checkpoints (avoid starting on top of one)
    npts = len(full) - 1
    best_i, best_d = 0, -1
    for i in range(npts):
        d = min(dist(full[i], w) for w in wps)
        if d > best_d:
            best_d, best_i = d, i
    full = full[best_i:npts] + full[:best_i] + [full[best_i]]
    return full, L, n
