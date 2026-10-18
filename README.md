# vaktinde-mosque-data

**TR** · OpenStreetMap'teki camilerin ve mescitlerin, coğrafi karolara bölünmüş statik JSON
hâli. [Vaktinde](https://ambersf.com/vaktinde/) Android uygulamasının "Yakındaki Camiler"
ekranı bu veriyi kullanır. Uygulama yalnız bulunduğu ~39×20 km'lik karonun kimliğini ister;
kesin konum cihazdan çıkmaz.

**EN** · Mosques and prayer rooms from OpenStreetMap, as static JSON split into geographic
tiles. Used by the "Nearby Mosques" screen of the Vaktinde Android app. The app only requests
the ~39×20 km tile it is in, so the precise location never leaves the device.

- Temel URL / base URL: **https://ambercatalbas.github.io/vaktinde-mosque-data/v1/**
- Kaynak / source: [OpenStreetMap](https://www.openstreetmap.org/) (Overpass API)
- Atıf / attribution: **© OpenStreetMap contributors**
- Lisans / license: [ODbL 1.0](LICENSE) — veri tabanı / database. Kod (`*.py`, iş akışı) aynı
  depodadır ki türetme yöntemi de açık olsun / the code is here so the derivation method is open.
- Güncelleme / updates: her pazartesi 03:00 UTC (GitHub Actions) / every Monday 03:00 UTC.

## Biçim / format

`v1/manifest.json`

```json
{
  "version": 1,
  "generatedAt": "2026-10-19T03:41:12Z",
  "count": 300000,
  "countTR": 48000,
  "attribution": "© OpenStreetMap contributors",
  "license": "ODbL-1.0",
  "source": "OpenStreetMap (Overpass API)",
  "region": "world",
  "tileGeohashPrecision": 4,
  "tiles": 20000,
  "repository": "https://github.com/ambercatalbas/vaktinde-mosque-data"
}
```

`v1/t/{geohash4}.json` — karodaki kayıtların dizisi, `i`'ye göre sıralı. **Boş karo yazılmaz;
404 = bu karoda kayıt yok.** / Array of records in the tile, sorted by `i`. Empty tiles are not
written; a 404 means "no records".

```json
[{"i":"w24524316","a":41.01654,"o":28.94966,"n":"Fatih Camii","ne":"Fatih Mosque","k":"m","w":"yes"}]
```

| Alan / field | Anlam / meaning |
|---|---|
| `i` | OSM türü + kimliği / OSM type + id (`n` node, `w` way, `r` relation) |
| `a`, `o` | Enlem, boylam (5 ondalık; alanlar için merkez) / lat, lon (5 decimals; centre for areas) |
| `n` | `name` (yoksa alan yok / omitted if missing) |
| `na` | `name:ar` (`n` ile aynıysa yazılmaz / omitted when equal to `n`) |
| `ne` | `name:en` (`n` ile aynıysa yazılmaz) |
| `k` | `m` cami / mosque, `s` mescit / prayer room, `u` bilinmiyor / unknown |
| `w` | `wheelchair` |
| `ad` | `addr:street` + `addr:housenumber` |

İstemci karo kimliğini standart geohash ile hesaplar (4 karakter ≈ 0,35° × 0,18°). Yakın
çevre için bulunduğu karo ve gerekirse komşu karolar istenir.

## Yöntem / method

[`generate_mosques.py`](generate_mosques.py) (yalnız Python standart kitaplığı):

1. Dünya ~71 kutuya bölünür (yoğun bölgeler 15°×15°, seyrek bölgeler 30°–60°). Kutular **sırayla**
   sorgulanır, aralarında 5 sn beklenir. Hata (429/5xx/zaman aşımı/Overpass `runtime error`)
   olursa geri çekilerek (30 sn → 5 dk) `overpass-api.de` → `maps.mail.ru` → `overpass.kumi.systems`
   arasında dönülür. Bir kutu inatla başarısız olursa bir kez dörde bölünür. Tüm çalıştırmada en
   çok 100 sorgu.
2. Sorgu:
   ```
   nwr["amenity"="place_of_worship"]["religion"="muslim"];
   nwr["building"="mosque"][!"amenity"];
   out tags center;
   ```
3. Dışlananlar: `disused:`/`abandoned:`/`was:`/`demolished:`/`razed:`/`removed:`/`destroyed:`
   önekli `amenity`/`building`/`religion` anahtarı, `disused=yes`/`abandoned=yes`, ya da
   `building=mosque` olup `religion` başka bir din olanlar. (`was:name` gibi yalnız eski adı
   anlatan anahtarlar kaydı düşürmez.)
4. Kutu kenarındaki tekrarlar OSM kimliğiyle tekilleştirilir.
5. `k`: adda cami/mosque/masjid/مسجد/جامع… geçiyorsa `m`; mescit/mescid/musalla/musholla/
   prayer room/مصلى… geçiyorsa `s`; ad bir şey söylemiyor ama `building=mosque` ise `m`; değilse `u`.
6. **Güvenlik kapısı:** dünya < 250.000, Türkiye < 45.000 ya da dünya < geçen haftanın %95'i ise
   hiçbir şey yayınlanmaz; iş başarısız olur ve GitHub bildirimi gelir.
7. **Türkiye sayımı (`countTR`):** [Natural Earth](https://www.naturalearthdata.com/) 1:10m ülke
   sınırları (kamu malı) ile yaklaşık. Nokta Türkiye çokgenindeyse sayılır, komşu bir ülkenin
   (KKTC dahil) çokgenindeyse sayılmaz; hiçbir karada değilse (kıyı çizgisi hatası) en yakın kıyı
   Türkiye'ninse ve 5 km'den yakınsa sayılır. Sınır dosyası `data/tr_region.json`,
   [`tools/build_tr_region.py`](tools/build_tr_region.py) ile üretilir.
8. Çıktı `gh-pages` dalına her hafta **yetimsiz tek commit** olarak zorla yazılır; geçmiş büyümez.

Bu veri Apple, Google ya da başka bir özel kaynakla **birleştirilmez**.
This data is never merged with Apple, Google or other proprietary sources.

## Yerelde çalıştırma / running locally

```bash
python3 -m unittest discover -s tests
python3 generate_mosques.py --out site --region turkey --cache-dir .cache   # hızlı mod, 4 sorgu
python3 generate_mosques.py --out site --cache-dir .cache                   # tüm dünya, ~71 sorgu
```

`--cache-dir` ham Overpass yanıtlarını saklar; yarıda kalan bir çalıştırma kaldığı yerden sürer.
`--previous-manifest <url|dosya>` geçen haftayla karşılaştırma kapısını açar. Lütfen herkese açık
Overpass sunucularına kibar olun: aynı gün tekrar tekrar tüm dünyayı çekmeyin.
