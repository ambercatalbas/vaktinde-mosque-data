#!/usr/bin/env python3
"""Türkiye sayımında kullanılan sınır dosyasını (data/tr_region.json) üretir.

Kaynak: Natural Earth 1:10m Admin 0 – Countries (kamu malı, v5.1.2).
Türkiye ile çevresindeki ülkelerin çokgenleri tutulur; denizdeki kıyı noktaları en
yakın ülkeye atanır (bkz. generate_mosques.py → TurkeyRegion).

    curl -L -o /tmp/ne.geojson \
      https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/ne_10m_admin_0_countries.geojson
    python3 tools/build_tr_region.py /tmp/ne.geojson data/tr_region.json
"""
import json
import sys

# Türkiye'nin kabaca kapsamı (+ pay). Bu kutunun dışı hiçbir zaman TR sayılmaz.
BBOX = (25.4, 35.6, 45.0, 42.3)  # batı, güney, doğu, kuzey
NEIGHBORS = {
    "Greece", "Bulgaria", "Georgia", "Armenia", "Azerbaijan", "Iran", "Iraq", "Syria",
    "Cyprus", "Northern Cyprus", "Cyprus No Mans Area", "Lebanon",
    "Akrotiri Sovereign Base Area", "Dhekelia Sovereign Base Area",
}


def intersects(ring, pad=0.3):
    w, s, e, n = BBOX
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return not (max(xs) < w - pad or min(xs) > e + pad or max(ys) < s - pad or min(ys) > n + pad)


def main(src, dst):
    data = json.load(open(src, encoding="utf-8"))
    out = {"source": "Natural Earth 1:10m Admin 0 Countries v5.1.2 (public domain)",
           "bbox": BBOX, "TR": [], "others": []}
    for f in data["features"]:
        name = f["properties"]["ADMIN"]
        if name != "Turkey" and name not in NEIGHBORS:
            continue
        g = f["geometry"]
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        for poly in polys:
            outer = poly[0]  # delikler yok sayılır
            if not intersects(outer):
                continue
            ring = [[round(x, 4), round(y, 4)] for x, y in outer]
            if name == "Turkey":
                out["TR"].append(ring)
            else:
                out["others"].append(ring)
    with open(dst, "w", encoding="utf-8") as fh:
        json.dump(out, fh, separators=(",", ":"))
    print(f"TR halka: {len(out['TR'])}, komşu halka: {len(out['others'])}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
