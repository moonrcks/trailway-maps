#!/usr/bin/env python3
"""Builds Trailway's offline city packs: each Ohio metro's streets, sidewalks and trails from OpenStreetMap,
classified the way the app does it (OSMService.classify / oneway) and written in a compact binary format the
app loads instantly (OfflineMaps.swift). Run monthly by the moonrcks/trailway-maps GitHub workflow, which
publishes the packs and manifest.json as release assets.

  python3 build_city_packs.py OUT_DIR [city ...]       (default: every city)

Pack format (little-endian), compressed with raw DEFLATE:
  "TWPK", u16 version (1), u16 reserved
  f64 minLat, minLon, maxLat, maxLon
  u32 node count, then i32 lat[n], i32 lon[n] (1e-7 degrees)
  u32 name count, then for each: u16 byte length + UTF-8
  u32 way count, then for each: u8 kind, u8 lts, i8 oneway, u8 0, u32 name index, u32 node count, u32 node index[]
Kinds: 0 protected path, 1 bike lane, 2 shared road, 3 sidewalk.

Map data © OpenStreetMap contributors, available under the Open Database License (ODbL).
"""
import datetime, json, os, struct, subprocess, sys, time, zlib

CITIES = [
    ("columbus", "Columbus", 39.80, -83.25, 40.20, -82.75),
    ("cleveland", "Cleveland", 41.30, -81.95, 41.62, -81.40),
    ("cincinnati", "Cincinnati", 39.03, -84.75, 39.35, -84.25),
    ("dayton", "Dayton", 39.60, -84.35, 39.90, -83.95),
    ("toledo", "Toledo", 41.53, -83.80, 41.75, -83.45),
    ("akron", "Akron", 40.95, -81.65, 41.20, -81.40),
    ("canton", "Canton", 40.72, -81.50, 40.90, -81.30),
    ("youngstown", "Youngstown", 41.00, -80.80, 41.20, -80.55),
]
ENDPOINTS = ["https://overpass-api.de/api/interpreter", "https://overpass.private.coffee/api/interpreter",
             "https://overpass.kumi.systems/api/interpreter"]
TILE = 0.12   # degrees per tile side, so each Overpass request stays well within its limits
KINDS = {"protectedPath": 0, "bikeLane": 1, "sharedRoad": 2, "sidewalk": 3}

# The same streets the app downloads for a route (OSMService.buildQuery, full mode).
FILTER = ('way["highway"~"^(cycleway|path|footway|pedestrian|living_street|residential|unclassified|tertiary|'
          'tertiary_link|secondary|secondary_link|primary|primary_link|track)$"]["access"!~"^(private|no)$"]'
          '["bicycle"!="no"]["area"!="yes"]')


def classify(t):
    """Port of OSMService.classify: (kind, lts) or None. Keep the two in step."""
    hw = t.get("highway")
    if hw is None:
        return None
    bicycle = t.get("bicycle", "")
    unpaved = t.get("surface", "") in ("dirt", "grass", "ground", "mud", "sand", "earth")
    if hw == "cycleway":
        return ("protectedPath", 1)
    if hw in ("path", "bridleway"):
        if t.get("footway") == "sidewalk":
            return ("sidewalk", 1)
        if unpaved and bicycle != "designated":
            return ("sidewalk", 2)
        return ("protectedPath", 1)
    if hw in ("footway", "pedestrian"):
        if t.get("footway") in ("sidewalk", "crossing"):
            return ("sidewalk", 1)
        if bicycle in ("designated", "yes"):
            return ("protectedPath", 1)
        return ("sidewalk", 1)
    if hw == "track":
        return ("protectedPath", 1) if bicycle == "designated" else None
    if hw == "service":
        if t.get("service", "") in ("parking_aisle", "drive-through"):
            return None
        return ("sharedRoad", 2)
    road = {"living_street": 1, "residential": 1, "unclassified": 2, "tertiary": 3, "tertiary_link": 3,
            "secondary": 3, "secondary_link": 3, "primary": 4, "primary_link": 4}.get(hw)
    if road is None:
        return None
    lts = road
    digits = "".join(ch for ch in t.get("maxspeed", "") if ch.isdigit())
    if digits:
        speed = int(digits)
        if speed >= 45:
            lts = max(lts, 4)
        elif speed >= 35:
            lts = max(lts, 3)
        elif speed <= 25:
            lts = min(lts, 2)
    lanes = [t[k] for k in ("cycleway", "cycleway:both", "cycleway:right", "cycleway:left") if k in t]
    if "track" in lanes:
        return ("protectedPath", 1)
    if any(l in ("lane", "buffered_lane") for l in lanes):
        return ("bikeLane", max(1, lts - 1))
    return ("sharedRoad", lts)


def oneway(t, kind):
    """Port of OSMService.oneway."""
    if t.get("oneway:bicycle") == "no" or kind == "sidewalk":
        return 0
    o = t.get("oneway")
    if o in ("yes", "1", "true"):
        return 1
    if o == "-1":
        return -1
    return 1 if t.get("junction") == "roundabout" else 0


def fetch(bbox):
    query = f"[out:json][timeout:180];({FILTER}({bbox}););out body;>;out skel qt;"
    last = None
    for attempt in range(6):
        url = ENDPOINTS[attempt % len(ENDPOINTS)]
        # curl rather than urllib: some Python builds (macOS's) can't negotiate these servers' TLS.
        r = subprocess.run(["curl", "-sS", "--fail", "--max-time", "240", "-A", "Trailway city packs (github.com/moonrcks/trailway-maps)",
                            "--data-urlencode", "data@-", url], input=query.encode(), capture_output=True)
        try:
            if r.returncode != 0:
                raise RuntimeError(r.stderr.decode(errors="replace").strip() or f"curl exit {r.returncode}")
            return json.loads(r.stdout)["elements"]
        except Exception as e:   # busy servers: back off and try a mirror
            last = e
            print(f"    {url}: {str(e)[:160]}; retrying", flush=True)
            time.sleep(15 * (attempt + 1))
    raise RuntimeError(f"Overpass failed for {bbox}: {last}")


def build(city, out_dir):
    cid, name, s, w, n, e = city
    print(f"{name}:", flush=True)
    coords, ways = {}, {}
    lat = s
    while lat < n:
        lon = w
        while lon < e:
            bbox = f"{lat:.4f},{lon:.4f},{min(lat + TILE, n):.4f},{min(lon + TILE, e):.4f}"
            started = time.time()
            for el in fetch(bbox):
                if el["type"] == "node":
                    coords[el["id"]] = (el["lat"], el["lon"])
                elif el["type"] == "way" and el.get("nodes") and len(el["nodes"]) > 1:
                    ways[el["id"]] = el
            print(f"  tile {bbox}: {len(ways)} ways so far ({time.time() - started:.0f} s)", flush=True)
            time.sleep(2)
            lon += TILE
        lat += TILE

    index, lats, lons, names, name_ids, packed = {}, [], [], [], {}, []
    for way in ways.values():
        tags = way.get("tags", {})
        c = classify(tags)
        if c is None:
            continue
        kind, lts = c
        refs = []
        for ref in way["nodes"]:
            if ref not in coords:
                continue
            if ref not in index:
                index[ref] = len(lats)
                lats.append(round(coords[ref][0] * 1e7))
                lons.append(round(coords[ref][1] * 1e7))
            refs.append(index[ref])
        if len(refs) < 2:
            continue
        label = tags.get("name") or tags.get("ref") or ""
        if label not in name_ids:
            name_ids[label] = len(names)
            names.append(label)
        packed.append((KINDS[kind], lts, oneway(tags, kind), name_ids[label], refs))

    out = bytearray(b"TWPK" + struct.pack("<HH4d", 1, 0, s, w, n, e))
    out += struct.pack(f"<I{len(lats)}i", len(lats), *lats) + struct.pack(f"<{len(lons)}i", *lons)
    out += struct.pack("<I", len(names))
    for label in names:
        raw = label.encode("utf-8")[:65535]
        out += struct.pack("<H", len(raw)) + raw
    out += struct.pack("<I", len(packed))
    for kind, lts, ow, nm, refs in packed:
        out += struct.pack(f"<BBbBII{len(refs)}I", kind, lts, ow, 0, nm, len(refs), *refs)
    comp = zlib.compressobj(9, zlib.DEFLATED, -15)
    data = comp.compress(bytes(out)) + comp.flush()
    path = os.path.join(out_dir, f"{cid}.twpk")
    with open(path, "wb") as f:
        f.write(data)
    print(f"  {len(lats)} nodes, {len(packed)} ways: {len(out) / 1e6:.1f} MB, {len(data) / 1e6:.1f} MB compressed", flush=True)
    return {"id": cid, "name": name, "minLat": s, "minLon": w, "maxLat": n, "maxLon": e, "bytes": len(data),
            "ways": len(packed), "file": f"{cid}.twpk"}


def main():
    out_dir = sys.argv[1]
    wanted = set(sys.argv[2:])
    os.makedirs(out_dir, exist_ok=True)
    manifest_path = os.path.join(out_dir, "manifest.json")
    try:
        previous = {p["id"]: p for p in json.load(open(manifest_path))["packs"]}
    except (OSError, ValueError, KeyError):
        previous = {}
    today = datetime.date.today().isoformat()
    packs = []
    for city in CITIES:
        if wanted and city[0] not in wanted:
            if city[0] in previous:
                packs.append(previous[city[0]])
            continue
        pack = build(city, out_dir)
        pack["updated"] = today
        packs.append(pack)
    with open(manifest_path, "w") as f:
        json.dump({"version": 1, "updated": today, "attribution": "© OpenStreetMap contributors (ODbL)", "packs": packs}, f, indent=1)
    print("Wrote", manifest_path)


if __name__ == "__main__":
    main()
