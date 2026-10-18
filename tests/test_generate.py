import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import generate_mosques as g  # noqa: E402


class GeohashTests(unittest.TestCase):
    def test_known_vectors(self):
        # Wikipedia / geohash.org örnekleri
        self.assertEqual(g.geohash_encode(57.64911, 10.40744, 11), "u4pruydqqvj")
        self.assertEqual(g.geohash_encode(42.6, -5.6, 5), "ezs42")
        self.assertEqual(g.geohash_encode(-25.382708, -49.265506, 7), "6gkzwgj")
        self.assertEqual(g.geohash_encode(0.0, 0.0, 4), "s000")
        self.assertEqual(g.geohash_encode(-90.0, -180.0, 4), "0000")

    def test_fatih_tile(self):
        self.assertEqual(g.geohash_encode(41.0165, 28.9497, 4), "sxk9")
        s, w, n, e = g.geohash_bounds("sxk9")
        self.assertTrue(s <= 41.0165 < n and w <= 28.9497 < e)

    def test_bounds_roundtrip(self):
        for lat, lon in [(41.0165, 28.9497), (-6.2, 106.8), (51.5, -0.12), (21.42, 39.83)]:
            gh = g.geohash_encode(lat, lon, 4)
            s, w, n, e = g.geohash_bounds(gh)
            self.assertTrue(s <= lat < n and w <= lon < e, gh)
            self.assertAlmostEqual(e - w, 0.3515625)
            self.assertAlmostEqual(n - s, 0.17578125)


class GridTests(unittest.TestCase):
    def test_world_covered_without_overlap(self):
        boxes = g.world_boxes()
        self.assertLessEqual(len(boxes), 75)
        area = sum((n - s) * (e - w) for s, w, n, e in boxes)
        self.assertEqual(area, 360 * 145)  # -60..85
        for lat in range(-59, 85, 3):
            for lon in range(-179, 180, 7):
                hits = [b for b in boxes if b[0] < lat + 0.5 < b[2] and b[1] < lon + 0.5 < b[3]]
                self.assertEqual(len(hits), 1, (lat, lon))


class RecordTests(unittest.TestCase):
    def test_node_record(self):
        rec = g.to_record({"type": "node", "id": 123, "lat": 41.0123456, "lon": 28.9497149,
                           "tags": {"amenity": "place_of_worship", "religion": "muslim",
                                    "name": "Fatih Camii", "name:ar": "جامع الفاتح",
                                    "name:en": "Fatih Mosque", "wheelchair": "yes",
                                    "addr:street": "Fevzipaşa Cd.", "addr:housenumber": "1"}})
        self.assertEqual(rec, {"i": "n123", "a": 41.01235, "o": 28.94971, "n": "Fatih Camii",
                               "na": "جامع الفاتح", "ne": "Fatih Mosque", "k": "m", "w": "yes",
                               "ad": "Fevzipaşa Cd. 1"})

    def test_way_uses_center(self):
        rec = g.to_record({"type": "way", "id": 9, "center": {"lat": 1.0, "lon": 2.0},
                           "tags": {"building": "mosque"}})
        self.assertEqual(rec, {"i": "w9", "a": 1.0, "o": 2.0, "k": "m"})

    def test_exclusions(self):
        base = {"amenity": "place_of_worship", "religion": "muslim"}
        self.assertTrue(g.is_excluded({**base, "disused:amenity": "place_of_worship"}))
        self.assertTrue(g.is_excluded({"building": "mosque", "abandoned:religion": "muslim"}))
        self.assertTrue(g.is_excluded({"building": "mosque", "religion": "christian"}))
        self.assertTrue(g.is_excluded({**base, "disused": "yes"}))
        self.assertFalse(g.is_excluded({**base, "was:name": "Eski Ad"}))
        self.assertFalse(g.is_excluded(base))

    def test_classify(self):
        c = lambda name, **t: g.classify(t, [name])
        self.assertEqual(c("SÜLEYMANİYE CAMİİ"), "m")
        self.assertEqual(c("Hastane Mescidi"), "s")
        self.assertEqual(c("Mescid-i Aksa Camii"), "m")
        self.assertEqual(c("Masjid Istiqlal"), "m")
        self.assertEqual(c("Musholla Al-Ikhlas"), "s")
        self.assertEqual(c("مصلى النساء"), "s")
        self.assertEqual(c("East London Mosque"), "m")
        self.assertEqual(c("Islamic Centre"), "u")
        self.assertEqual(c("Islamic Centre", building="mosque"), "m")
        self.assertEqual(c(None), "u")


class TurkeyRegionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tr = g.TurkeyRegion()

    def test_inside(self):
        for lat, lon in [(41.0165, 28.9497), (39.92, 32.85), (37.87, 32.49), (39.90, 41.27),
                         (37.91, 40.24), (41.0, 39.72), (38.42, 27.13), (36.20, 36.16),
                         (40.99, 29.03), (41.025, 40.52)]:
            self.assertTrue(self.tr.contains(lat, lon), (lat, lon))

    def test_outside(self):
        for lat, lon in [(36.20, 37.15),   # Halep
                         (35.18, 33.36),   # Lefkoşa (KKTC ayrı sayılır)
                         (39.10, 26.55),   # Midilli
                         (41.64, 41.63),   # Batum
                         (39.21, 45.41),   # Nahçıvan
                         (42.70, 23.32),   # Sofya
                         (36.43, 28.22)]:  # Rodos
            self.assertFalse(self.tr.contains(lat, lon), (lat, lon))


if __name__ == "__main__":
    unittest.main()
