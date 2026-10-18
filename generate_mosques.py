#!/usr/bin/env python3
"""OpenStreetMap camilerini geohash-4 karolarına bölünmüş statik JSON'a çevirir.

Yalnız Python standart kitaplığı kullanılır.

    python3 generate_mosques.py --out site                      # tüm dünya
    python3 generate_mosques.py --out site --region turkey      # hızlı mod (4 sorgu)
    python3 generate_mosques.py --out site --previous-manifest \
        https://ambercatalbas.github.io/vaktinde-mosque-data/v1/manifest.json

Çıktı:
    <out>/v1/manifest.json   {version, generatedAt, count, countTR, attribution, license, ...}
    <out>/v1/t/<gh4>.json    karodaki kayıtlar (boş karo yazılmaz; istemci 404'ü boş sayar)

Güvenlik kapısı düşerse çıkış kodu 2'dir ve hiçbir şey yayınlanmamalıdır.
"""
import argparse
import json
import math
import os
import re
import shutil
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

SCHEMA_VERSION = 1
ATTRIBUTION = "© OpenStreetMap contributors"
LICENSE = "ODbL-1.0"
REPO_URL = "https://github.com/ambercatalbas/vaktinde-mosque-data"
USER_AGENT = f"vaktinde-mosque-data/1.0 (+{REPO_URL})"

SERVERS = [
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
PAUSE_BETWEEN_BOXES = 5          # sn
ATTEMPTS_PER_BOX = 6
QUERY_TIMEOUT = 300              # Overpass [timeout:]
HTTP_TIMEOUT = QUERY_TIMEOUT + 90
DEFAULT_QUERY_BUDGET = 100       # tüm çalıştırmada en çok bu kadar sorgu

MIN_WORLD = 250_000
MIN_TR = 45_000
MIN_RATIO_VS_PREVIOUS = 0.95

HERE = os.path.dirname(os.path.abspath(__file__))

# --------------------------------------------------------------------------- geohash

_BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"


def geohash_encode(lat, lon, precision=4):
    lat_lo, lat_hi = -90.0, 90.0
    lon_lo, lon_hi = -180.0, 180.0
    out = []
    bits = 0
    ch = 0
    even = True  # çift bit = boylam
    while len(out) < precision:
        if even:
            mid = (lon_lo + lon_hi) / 2
            if lon >= mid:
                ch = (ch << 1) | 1
                lon_lo = mid
            else:
                ch <<= 1
                lon_hi = mid
        else:
            mid = (lat_lo + lat_hi) / 2
            if lat >= mid:
                ch = (ch << 1) | 1
                lat_lo = mid
            else:
                ch <<= 1
                lat_hi = mid
        even = not even
        bits += 1
        if bits == 5:
            out.append(_BASE32[ch])
            bits = 0
            ch = 0
    return "".join(out)


def geohash_bounds(gh):
    """(güney, batı, kuzey, doğu)"""
    lat_lo, lat_hi = -90.0, 90.0
    lon_lo, lon_hi = -180.0, 180.0
    even = True
    for c in gh:
        v = _BASE32.index(c)
        for shift in range(4, -1, -1):
            bit = (v >> shift) & 1
            if even:
                mid = (lon_lo + lon_hi) / 2
                if bit:
                    lon_lo = mid
                else:
                    lon_hi = mid
            else:
                mid = (lat_lo + lat_hi) / 2
                if bit:
                    lat_lo = mid
                else:
                    lat_hi = mid
            even = not even
    return lat_lo, lon_lo, lat_hi, lon_hi


# --------------------------------------------------------------------------- ızgara

def _split(box):
    s, w, n, e = box
    ms, mw = (s + n) / 2, (w + e) / 2
    return [(s, w, ms, mw), (s, mw, ms, e), (ms, w, n, mw), (ms, mw, n, e)]


def world_boxes():
    """~69 kutu. Kutular (güney, batı, kuzey, doğu) ve birbirine yalnız kenarda değer."""
    boxes = []
    for w in range(-180, 180, 60):                       # -60..-30 ve 60..85: 60° geniş
        boxes.append((-60, w, -30, w + 60))
        boxes.append((60, w, 85, w + 60))
    boxes.append((-30, -180, 30, -120))                  # orta Pasifik: tek kutu
    boxes.append((30, -180, 60, -120))                   # Kuzey Pasifik / Alaska güneyi
    dense = {  # (güney, batı) → 4'e bölünür
        (30, 0), (30, 30), (30, 60),                      # Avrupa/Türkiye, İran/Kafkasya, Orta Asya
        (0, -30), (0, 0), (0, 30), (0, 60), (0, 90),      # Batı Afrika, Sahel, Mısır/Arabistan, Hindistan, Bengal
        (-30, 90),                                        # Java/Sumatra
    }
    for s in (-30, 0, 30):
        for w in range(-120, 180, 30):
            box = (s, w, s + 30, w + 30)
            boxes.extend(_split(box) if (s, w) in dense else [box])
    return boxes


TURKEY_BOXES = _split((35.6, 25.4, 42.3, 45.0))


# --------------------------------------------------------------------------- Overpass

def build_query(box):
    s, w, n, e = box
    return (
        f"[out:json][timeout:{QUERY_TIMEOUT}][bbox:{s},{w},{n},{e}];\n"
        "(\n"
        '  nwr["amenity"="place_of_worship"]["religion"="muslim"];\n'
        '  nwr["building"="mosque"][!"amenity"];\n'
        ");\n"
        "out tags center;\n"
    )


class QueryBudgetExceeded(RuntimeError):
    pass


class Overpass:
    def __init__(self, budget, cache_dir=None):
        self.budget = budget
        self.used = 0
        self.cache_dir = cache_dir
        self.server_index = 0
        self.log = []
        self.last_from_cache = False

    def _cache_path(self, box):
        if not self.cache_dir:
            return None
        name = "box_" + "_".join(f"{v:g}" for v in box) + ".json"
        return os.path.join(self.cache_dir, name)

    def fetch(self, box):
        path = self._cache_path(box)
        self.last_from_cache = bool(path and os.path.exists(path))
        if self.last_from_cache:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)["elements"]
        query = build_query(box)
        last_error = None
        for attempt in range(ATTEMPTS_PER_BOX):
            if self.used >= self.budget:
                raise QueryBudgetExceeded(f"sorgu bütçesi ({self.budget}) doldu")
            server = SERVERS[self.server_index % len(SERVERS)]
            self.used += 1
            started = time.monotonic()
            try:
                data = self._post(server, query)
                remark = data.get("remark") or ""
                if "runtime error" in remark or "runtime remark" in remark:
                    raise RuntimeError(f"Overpass uyarısı: {remark.strip()[:200]}")
                elements = data.get("elements", [])
                took = time.monotonic() - started
                self.log.append({"box": box, "server": server, "ok": True,
                                 "seconds": round(took, 1), "elements": len(elements)})
                if path:
                    os.makedirs(self.cache_dir, exist_ok=True)
                    with open(path, "w", encoding="utf-8") as fh:
                        json.dump({"elements": elements}, fh)
                return elements
            except Exception as exc:  # ağ, HTTP 429/5xx, JSON, Overpass zaman aşımı
                took = time.monotonic() - started
                last_error = exc
                self.log.append({"box": box, "server": server, "ok": False,
                                 "seconds": round(took, 1), "error": str(exc)[:200]})
                print(f"    ! {server.split('/')[2]}: {exc} ({took:.0f} sn)", flush=True)
                self.server_index += 1          # sıradaki sunucuya geç
                wait = min(30 * 2 ** attempt, 300)
                if isinstance(exc, urllib.error.HTTPError) and exc.code == 429:
                    wait = max(wait, 120)
                time.sleep(wait)
        raise RuntimeError(f"kutu {box} {ATTEMPTS_PER_BOX} denemede alınamadı: {last_error}")

    @staticmethod
    def _post(server, query):
        body = urllib.parse.urlencode({"data": query}).encode()
        req = urllib.request.Request(server, data=body, headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        })
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            raw = resp.read()
        return json.loads(raw)


# --------------------------------------------------------------------------- kayıtlar

LIFECYCLE_PREFIXES = ("disused:", "abandoned:", "was:", "demolished:", "razed:",
                      "removed:", "destroyed:")
LIFECYCLE_BASE_KEYS = {"amenity", "building", "religion"}


def is_excluded(tags):
    """Kullanılmayan/yıkılmış yapıları dışarıda bırakır.

    `disused:amenity`, `abandoned:building`, `was:religion` gibi yaşam döngüsü önekli
    temel anahtarlar ya da `disused=yes`/`abandoned=yes` varsa kayıt alınmaz.
    `was:name` gibi önekli ama yapının durumunu söylemeyen anahtarlar kaydı düşürmez.
    """
    for key in tags:
        for prefix in LIFECYCLE_PREFIXES:
            if key.startswith(prefix) and key[len(prefix):] in LIFECYCLE_BASE_KEYS:
                return True
    for flag in ("disused", "abandoned", "demolished"):
        if tags.get(flag) == "yes":
            return True
    religion = tags.get("religion")
    if religion and religion != "muslim":   # building=mosque ama başka din (dönüştürülmüş)
        return True
    return False


def _fold(text):
    text = text.replace("İ", "i").replace("I", "ı").lower()
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c))


_MOSQUE_RE = re.compile(
    r"\bcami|\bdzamija|\bdžamija|\bxhami|\bmosque|\bmosquee|\bmoschee|\bmezquita|\bmoskee|"
    r"\bmeczet|\bmesita|\bmešita|\bmasjid|\bmasjed|\bmesjid|\bmasged|\bmesdzhid|\bmechet|"
    r"мечеть|джами|مسجد|جامع|مسجید"
)
_PRAYER_ROOM_RE = re.compile(
    r"\bmescit|\bmescid|\bmusalla|\bmusallah|\bmusala|\bmusholla|\bmushola|\bmusolla|"
    r"\bsurau|\blanggar|\bnamazgah|\bnamaz odasi|\bnamaz odası|\bprayer room|\bprayer hall|"
    r"\bsalle de priere|\bgebetsraum|مصلى|مصلي"
)


def classify(tags, names):
    folded = " | ".join(_fold(n) for n in names if n)
    if folded:
        if _MOSQUE_RE.search(folded):
            return "m"
        if _PRAYER_ROOM_RE.search(folded):
            return "s"
    if tags.get("building") == "mosque":
        return "m"
    return "u"


def _clean(value, limit=120):
    if not value:
        return None
    value = " ".join(value.split())
    return value[:limit] or None


def to_record(el):
    tags = el.get("tags") or {}
    if is_excluded(tags):
        return None
    if el["type"] == "node":
        lat, lon = el.get("lat"), el.get("lon")
    else:
        center = el.get("center") or {}
        lat, lon = center.get("lat"), center.get("lon")
    if lat is None or lon is None:
        return None
    rec = {"i": el["type"][0] + str(el["id"]), "a": round(lat, 5), "o": round(lon, 5)}
    n = _clean(tags.get("name"))
    na = _clean(tags.get("name:ar"))
    ne = _clean(tags.get("name:en"))
    if n:
        rec["n"] = n
    if na and na != n:
        rec["na"] = na
    if ne and ne != n:
        rec["ne"] = ne
    rec["k"] = classify(tags, [n, na, ne, tags.get("name:tr")])
    w = _clean(tags.get("wheelchair"), 16)
    if w:
        rec["w"] = w
    street = _clean(tags.get("addr:street"), 80)
    if street:
        number = _clean(tags.get("addr:housenumber"), 16)
        rec["ad"] = f"{street} {number}" if number else street
    return rec


# --------------------------------------------------------------------------- Türkiye

class TurkeyRegion:
    """Natural Earth sınırlarıyla yaklaşık Türkiye testi (bkz. tools/build_tr_region.py).

    Nokta Türkiye çokgenindeyse TR; başka bir ülke çokgenindeyse değil; hiçbir karada
    değilse (kıyı/deniz, 1:10m kıyı çizgisinin hatası) en yakın kıyı Türkiye'ninse ve
    5 km'den yakınsa TR sayılır.
    """
    BAND = 0.02

    def __init__(self, path=os.path.join(HERE, "data", "tr_region.json")):
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        self.bbox = data["bbox"]  # batı, güney, doğu, kuzey
        self.tr = [self._index(r) for r in data["TR"]]
        self.others = [self._index(r) for r in data["others"]]

    def _index(self, ring):
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        lat0 = min(ys)
        bands = {}
        edges = list(zip(ring, ring[1:] + ring[:1]))
        for (x1, y1), (x2, y2) in edges:
            lo, hi = sorted((y1, y2))
            for b in range(int((lo - lat0) / self.BAND), int((hi - lat0) / self.BAND) + 1):
                bands.setdefault(b, []).append((x1, y1, x2, y2))
        return {"bbox": (min(xs), min(ys), max(xs), max(ys)), "lat0": lat0,
                "bands": bands, "edges": edges}

    def _inside(self, r, lat, lon):
        w, s, e, n = r["bbox"]
        if not (w <= lon <= e and s <= lat <= n):
            return False
        inside = False
        for x1, y1, x2, y2 in r["bands"].get(int((lat - r["lat0"]) / self.BAND), ()):
            if (y1 > lat) != (y2 > lat):
                x = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
                if x > lon:
                    inside = not inside
        return inside

    @staticmethod
    def _dist_km(r, lat, lon):
        k = math.cos(math.radians(lat))
        best = float("inf")
        for (x1, y1), (x2, y2) in r["edges"]:
            ax, ay = (x1 - lon) * k, y1 - lat
            bx, by = (x2 - lon) * k, y2 - lat
            dx, dy = bx - ax, by - ay
            L = dx * dx + dy * dy
            t = 0.0 if L == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / L))
            px, py = ax + t * dx, ay + t * dy
            best = min(best, px * px + py * py)
        return math.sqrt(best) * 111.2

    def contains(self, lat, lon):
        w, s, e, n = self.bbox
        if not (w <= lon <= e and s <= lat <= n):
            return False
        if any(self._inside(r, lat, lon) for r in self.tr):
            return True
        if any(self._inside(r, lat, lon) for r in self.others):
            return False
        pad = 0.1
        near = [r for r in self.tr + self.others
                if r["bbox"][0] - pad <= lon <= r["bbox"][2] + pad
                and r["bbox"][1] - pad <= lat <= r["bbox"][3] + pad]
        if not near:
            return False
        best = min(near, key=lambda r: self._dist_km(r, lat, lon))
        return any(best is r for r in self.tr) and self._dist_km(best, lat, lon) <= 5


# --------------------------------------------------------------------------- akış

def load_previous_manifest(ref):
    if not ref:
        return None
    try:
        if ref.startswith("http"):
            req = urllib.request.Request(ref, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read())
        with open(ref, encoding="utf-8") as fh:
            return json.load(fh)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    except FileNotFoundError:
        return None


def check_gate(region, count, count_tr, previous):
    errors = []
    if count_tr < MIN_TR:
        errors.append(f"Türkiye sayısı {count_tr} < {MIN_TR}")
    if region == "world":
        if count < MIN_WORLD:
            errors.append(f"dünya sayısı {count} < {MIN_WORLD}")
        if previous and previous.get("region", "world") == "world":
            prev = int(previous.get("count", 0))
            if count < prev * MIN_RATIO_VS_PREVIOUS:
                errors.append(f"dünya sayısı {count} < önceki {prev} × {MIN_RATIO_VS_PREVIOUS}")
    return errors


def run(args):
    started = time.monotonic()
    boxes = TURKEY_BOXES if args.region == "turkey" else world_boxes()
    client = Overpass(args.query_budget, args.cache_dir)
    pending = list(boxes)
    records = {}
    raw_count = 0
    excluded = 0
    done = 0
    while pending:
        box = pending.pop(0)
        print(f"[{done + 1}/{done + 1 + len(pending)}] kutu {box}", flush=True)
        try:
            elements = client.fetch(box)
        except QueryBudgetExceeded:
            raise
        except RuntimeError as exc:
            # Kalıcı hata: büyük kutuyu bir kez dörde böl (yoğunluk/zaman aşımı ihtimali).
            if (box[2] - box[0]) > 8 and client.used < client.budget - 4:
                print(f"    → bölünüyor: {exc}", flush=True)
                pending = _split(box) + pending
                continue
            raise
        done += 1
        raw_count += len(elements)
        for el in elements:
            rec = to_record(el)
            if rec is None:
                excluded += 1
                continue
            records[rec["i"]] = rec   # kutu kenarındaki tekrarlar id ile tekilleşir
        print(f"    {len(elements)} öğe, toplam tekil {len(records)}", flush=True)
        if pending and not client.last_from_cache:
            time.sleep(PAUSE_BETWEEN_BOXES)

    tr = TurkeyRegion()
    tiles = {}
    count_tr = named = named_tr = 0
    kinds = {"m": 0, "s": 0, "u": 0}
    for rec in records.values():
        tiles.setdefault(geohash_encode(rec["a"], rec["o"], 4), []).append(rec)
        is_tr = tr.contains(rec["a"], rec["o"])
        has_name = "n" in rec
        count_tr += is_tr
        named += has_name
        named_tr += is_tr and has_name
        kinds[rec["k"]] += 1
    count = len(records)

    previous = load_previous_manifest(args.previous_manifest)
    errors = check_gate(args.region, count, count_tr, previous)

    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    manifest = {
        "version": SCHEMA_VERSION,
        "generatedAt": generated_at,
        "count": count,
        "countTR": count_tr,
        "attribution": ATTRIBUTION,
        "license": LICENSE,
        "source": "OpenStreetMap (Overpass API)",
        "region": args.region,
        "tileGeohashPrecision": 4,
        "tiles": len(tiles),
        "repository": REPO_URL,
    }

    out_v1 = os.path.join(args.out, "v1")
    if os.path.exists(out_v1):
        shutil.rmtree(out_v1)
    os.makedirs(os.path.join(out_v1, "t"))
    sizes = {}
    for gh, recs in tiles.items():
        recs.sort(key=lambda r: r["i"])
        payload = json.dumps(recs, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        sizes[gh] = len(payload)
        with open(os.path.join(out_v1, "t", f"{gh}.json"), "wb") as fh:
            fh.write(payload)
    with open(os.path.join(out_v1, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    biggest = sorted(sizes.items(), key=lambda kv: -kv[1])[:5]
    duration = time.monotonic() - started
    stats = {
        "count": count, "countTR": count_tr,
        "named": named, "namedPct": round(100 * named / count, 1) if count else 0,
        "namedTR": named_tr, "namedTRPct": round(100 * named_tr / count_tr, 1) if count_tr else 0,
        "kinds": kinds, "rawElements": raw_count, "excluded": excluded,
        "tiles": len(tiles), "totalBytes": sum(sizes.values()),
        "biggestTiles": [{"tile": gh, "bytes": b, "records": len(tiles[gh])} for gh, b in biggest],
        "medianTileBytes": sorted(sizes.values())[len(sizes) // 2] if sizes else 0,
        "queries": client.used, "failedQueries": sum(1 for q in client.log if not q["ok"]),
        "durationSeconds": round(duration),
        "previousCount": previous.get("count") if previous else None,
        "gateErrors": errors,
    }
    with open(os.path.join(args.out, "stats.json"), "w", encoding="utf-8") as fh:
        json.dump({"stats": stats, "queries": client.log}, fh, ensure_ascii=False, indent=2)
    report(stats)
    if errors:
        print("GÜVENLİK KAPISI DÜŞTÜ — yayınlanmayacak:\n  " + "\n  ".join(errors), file=sys.stderr)
        return 2
    return 0


def report(stats):
    lines = [
        "## Cami veri seti",
        "",
        f"- Kayıt: **{stats['count']:,}** (Türkiye **{stats['countTR']:,}**)",
        f"- İsimli: %{stats['namedPct']} (Türkiye %{stats['namedTRPct']})",
        f"- Tür: {stats['kinds']}",
        f"- Ham öğe {stats['rawElements']:,}, dışlanan {stats['excluded']:,}",
        f"- Karo: {stats['tiles']:,}, toplam {stats['totalBytes'] / 1e6:.1f} MB, "
        f"medyan {stats['medianTileBytes']:,} B",
        "- En büyük karolar: " + ", ".join(
            f"`{t['tile']}` {t['bytes'] / 1e3:.0f} KB/{t['records']}" for t in stats["biggestTiles"]),
        f"- Sorgu: {stats['queries']} (başarısız {stats['failedQueries']}), "
        f"süre {stats['durationSeconds'] // 60} dk {stats['durationSeconds'] % 60} sn",
        f"- Önceki sayı: {stats['previousCount']}",
    ]
    if stats["gateErrors"]:
        lines.append("- **Güvenlik kapısı düştü:** " + "; ".join(stats["gateErrors"]))
    text = "\n".join(lines) + "\n"
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(text)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", default="site", help="çıktı klasörü (varsayılan: site)")
    p.add_argument("--region", choices=("world", "turkey"), default="world")
    p.add_argument("--previous-manifest", help="önceki manifest (URL ya da dosya); yoksa yalnız mutlak eşikler")
    p.add_argument("--query-budget", type=int, default=DEFAULT_QUERY_BUDGET)
    p.add_argument("--cache-dir", help="ham Overpass yanıtlarını burada sakla/yeniden kullan (yerel deneme için)")
    args = p.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
