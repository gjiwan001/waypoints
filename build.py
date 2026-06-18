#!/usr/bin/env python3
"""
build.py — Rebuild the P_KR array in korea_map.html from korea_saved_places.csv.

Usage: python build.py

What it does:
  1. Seeds geocache.json with any hand-placed coords already in the HTML
  2. Reads korea_saved_places.csv, deduplicates by Place Name
  3. Geocodes new entries via Nominatim (free, no API key, ~1 req/s)
  4. Replaces the const P_KR=[...]; block in korea_map.html
  5. Saves updated geocache.json so re-runs skip geocoding
"""

import csv, json, re, time, urllib.request, urllib.parse, os, sys, hashlib, math

BASE = os.path.dirname(os.path.abspath(__file__))
CSV_FILE  = os.path.join(BASE, 'korea_saved_places.csv')
HTML_FILE = os.path.join(BASE, 'korea_map.html')
CACHE_FILE = os.path.join(BASE, 'geocache.json')

SEOUL_DEFAULT = (37.5665, 126.9780)
BUSAN_DEFAULT = (35.1796, 129.0756)
JEJU_DEFAULT  = (33.4890, 126.4983)

# CSV category → HTML CC object key
CAT_MAP = {
    'DayTrip': 'DayTrip',
    'Day Trip': 'DayTrip',
    'Nature / Day Trip': 'DayTrip',
    'Island': 'DayTrip',
    'City': 'DayTrip',
    'Clothing Store': 'Shopping',
    'Fashion Brand / Store': 'Shopping',
    'Fashion Brand/Store': 'Shopping',
    'Shopping': 'Shopping',
    'Shopping Tip': 'Shopping',
    'Market': 'Shopping',
    'Department Store': 'Shopping',
    'Food': 'Food',
    'Café / Bakery': 'Food',
    'Café/Bakery': 'Food',
    'Café': 'Food',
    'Bakery': 'Food',
    'Restaurant': 'Food',
    'Culture': 'Culture',
    'Landmark': 'Culture',
    'Cultural Site': 'Culture',
    'Museum': 'Culture',
    'Jjimjilbang': 'Culture',
    'Jjimjilbang / Spa': 'Culture',
    'Hidden Gem': 'Culture',
    'Event / Festival': 'Culture',
    'Neighbourhood': 'Neighbourhood',
    'Skincare': 'Skincare',
    'Skincare Clinic': 'Skincare',
    'Wellness/Clinic': 'Skincare',
    'Wellness / Clinic': 'Skincare',
    'Wellness': 'Skincare',
    'Beauty': 'Skincare',
    'Beauty / Hair': 'Skincare',
    'Pharmacy / Skincare': 'Skincare',
    'Transport': 'Transport',
    'Accommodation': 'Accommodation',
    'Accommodation Platform': 'Accommodation',
    'Tip': 'Culture',
    'Resource': 'Culture',
}


# ── helpers ──────────────────────────────────────────────────────────────────

def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_cache(cache):
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)

def seed_from_html(cache):
    """Pull existing hand-placed coords from the HTML into the cache."""
    with open(HTML_FILE, encoding='utf-8') as f:
        content = f.read()
    added = 0
    for m in re.finditer(r'\{n:"([^"]+)"[^}]*lat:([\d.]+),lng:([\d.]+)', content):
        name = m.group(1)
        lat, lng = float(m.group(2)), float(m.group(3))
        # Skip entries that were left at Seoul default (37.5665, 126.9780)
        if name not in cache and not (abs(lat - 37.5665) < 0.0001 and abs(lng - 126.9780) < 0.0001):
            cache[name] = {'lat': lat, 'lng': lng}
            added += 1
    return added

def geocode(name, city):
    """Return (lat, lng) from Nominatim, or None on failure.
    Tries: (1) full name + city, (2) Korean name in parentheses, (3) name only."""
    queries = []
    # Primary: full name with city
    queries.append(f"{name}, {city}, South Korea" if city else f"{name}, South Korea")
    # Extract Korean name from parentheses e.g. "Hongdae (홍대)" → try "홍대, Seoul"
    kr_match = re.search(r'\(([^)]+)\)', name)
    if kr_match:
        kr_name = kr_match.group(1)
        queries.append(f"{kr_name}, {city}, South Korea" if city else f"{kr_name}, South Korea")
    # Strip parenthetical and try clean English name
    clean = re.sub(r'\s*\([^)]*\)', '', name).strip()
    if clean != name:
        queries.append(f"{clean}, {city}, South Korea" if city else f"{clean}, South Korea")

    for query in queries:
        url = 'https://nominatim.openstreetmap.org/search?' + urllib.parse.urlencode({
            'q': query, 'format': 'json', 'limit': 1, 'countrycodes': 'kr',
        })
        req = urllib.request.Request(
            url, headers={'User-Agent': 'waypoints-map-builder/1.0 (github.com/gjiwan001/waypoints)'}
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                results = json.loads(resp.read())
                if results:
                    return float(results[0]['lat']), float(results[0]['lon'])
        except Exception as e:
            print(f"  ⚠ Nominatim error for '{query}': {e}", file=sys.stderr)
        time.sleep(1.1)
    return None

def jitter(name, base_lat, base_lng, radius=0.018):
    """Spread default-coord entries around the base point so the map doesn't collapse them.
    Uses the place name as a seed so the offset is deterministic across runs."""
    seed = int(hashlib.md5(name.encode()).hexdigest(), 16)
    angle = (seed % 3600) / 3600 * 2 * math.pi
    dist  = (((seed >> 12) % 1000) / 1000) * radius
    return round(base_lat + dist * math.sin(angle), 6), round(base_lng + dist * math.cos(angle), 6)

def city_default(city):
    c = city.lower()
    if 'busan' in c: return BUSAN_DEFAULT
    if 'jeju' in c:  return JEJU_DEFAULT
    return SEOUL_DEFAULT

def clean_area(city):
    """'Gangnam Seoul' → 'Gangnam', 'Incheon' → 'Incheon'"""
    city = city.split(',')[0].strip()
    if city.endswith(' Seoul') and city != 'Seoul':
        city = city[:-6].strip()
    return city or 'Seoul'

def map_cat(raw):
    return CAT_MAP.get(raw.strip(), 'Culture')

def make_tags(name, cat, desc):
    tags = []
    d = desc.lower(); n = name.lower(); c = cat.lower()
    if any(x in c for x in ('food', 'café', 'restaurant', 'bakery')): tags.append('food')
    if any(x in c for x in ('shopping', 'store', 'market', 'fashion', 'clothing')): tags.append('shopping')
    if any(x in c for x in ('skincare', 'clinic', 'wellness', 'beauty', 'pharmacy')): tags.append('wellness')
    if 'neighbourhood' in c: tags.append('stay')
    if any(x in c for x in ('daytrip', 'day trip', 'nature', 'island')): tags.append('daytrip')
    if '★' in name or 'top pick' in d or 'must' in d or 'best' in d[:60]: tags.append('★ top pick')
    if 'free' in d: tags.append('free')
    if 'english' in d: tags.append('english friendly')
    return list(dict.fromkeys(tags))

def js_str(s):
    """Escape for use inside JS double-quoted string."""
    return s.replace('\\', '\\\\').replace('"', '\\"').replace('\n', ' ').replace('\r', '')

def fmt_entry(name, cat, lat, lng, area, desc, tags, day):
    tags_js = ', '.join(f'"{js_str(t)}"' for t in tags)
    return (
        f'  {{n:"{js_str(name)}",'
        f'c:"{js_str(map_cat(cat))}",'
        f'lat:{lat:.6f},'
        f'lng:{lng:.6f},'
        f'area:"{js_str(clean_area(area))}",'
        f'd:"{js_str(desc)}",'
        f'tags:[{tags_js}],'
        f'day:"{js_str(day)}"}}'
    )

def resolve_collisions(entries_data):
    """Guarantee every entry gets a unique map coordinate key (Math.round(lat*10000)).
    Uses a golden-angle spiral to find the nearest free slot."""
    used = set()

    def key(lat, lng):
        return (round(lat * 10000), round(lng * 10000))

    result = []
    for item in entries_data:
        k = key(item['lat'], item['lng'])
        if k not in used:
            used.add(k)
            result.append(item)
        else:
            # Spiral outward until a free key is found
            seed = int(hashlib.md5(item['name'].encode()).hexdigest(), 16)
            angle0 = math.radians((seed % 360))
            step = 0.0002   # ~20 m per step
            placed = False
            for i in range(1, 200):
                angle = angle0 + i * 2.39996   # golden angle ≈ 137.5°
                dist  = step * i
                nlat = round(item['lat'] + dist * math.sin(angle), 6)
                nlng = round(item['lng'] + dist * math.cos(angle), 6)
                nk = key(nlat, nlng)
                if nk not in used:
                    item = dict(item)
                    item['lat'], item['lng'] = nlat, nlng
                    used.add(nk)
                    result.append(item)
                    placed = True
                    break
            if not placed:
                result.append(item)   # shouldn't happen

    return result


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    # 1. Load + seed geocache
    cache = load_cache()
    seeded = seed_from_html(cache)
    if seeded:
        print(f"Seeded {seeded} coords from existing HTML")
        save_cache(cache)

    # 2. Read + deduplicate CSV by Place Name
    places = {}   # name → {cat, city, descs[], days{}}
    with open(CSV_FILE, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            if row.get('Country', '').strip() != 'Korea':
                continue
            name = row['Place Name'].strip()
            if not name:
                continue
            cat  = row['Category'].strip()
            city = row['Neighbourhood / City'].strip()
            desc = row['Description / Notes'].strip()
            day  = row['Day'].strip()

            if name not in places:
                places[name] = {
                    'cat':  cat,
                    'city': city,
                    'descs': [desc] if desc else [],
                    'days':  {day} if day else set(),
                }
            else:
                p = places[name]
                if desc and desc not in p['descs']:
                    p['descs'].append(desc)
                if day:
                    p['days'].add(day)
                # Prefer more specific category
                if p['cat'] in ('Culture', 'Tip', 'Resource', '') and cat not in ('', 'Tip', 'Resource'):
                    p['cat'] = cat
                if not p['city'] and city:
                    p['city'] = city

    print(f"Loaded {len(places)} unique places from CSV")

    # 3. Geocode missing entries
    geocoded = 0
    for name, info in places.items():
        if name in cache:
            continue
        city = info['city']
        print(f"Geocoding: {name!r} ({city}) …", end=' ', flush=True)

        coords = geocode(name, city)
        if coords:
            cache[name] = {'lat': coords[0], 'lng': coords[1]}
            geocoded += 1
            print(f"→ {coords[0]:.4f}, {coords[1]:.4f}")
        else:
            base = city_default(city)
            jlat, jlng = jitter(name, base[0], base[1])
            cache[name] = {'lat': jlat, 'lng': jlng, 'approx': True}
            print(f"→ approx near {city or 'Seoul'}")

    if geocoded:
        save_cache(cache)
        print(f"Geocoded {geocoded} new places; cache updated")

    # 4. Build entries list (dicts), resolve coordinate collisions, then format JS
    entries_data = []
    for name, info in places.items():
        c = cache.get(name, {'lat': SEOUL_DEFAULT[0], 'lng': SEOUL_DEFAULT[1]})
        desc = info['descs'][0] if info['descs'] else ''
        days = sorted(info['days'])
        day  = days[0] if len(days) == 1 else (', '.join(days) if days else 'Flexible')
        tags = make_tags(name, info['cat'], desc)
        entries_data.append({
            'name': name, 'cat': info['cat'],
            'lat': c['lat'], 'lng': c['lng'],
            'city': info['city'], 'desc': desc, 'tags': tags, 'day': day,
        })

    entries_data = resolve_collisions(entries_data)
    collisions_fixed = sum(1 for e in entries_data if e.get('_jittered'))
    entries = [fmt_entry(e['name'], e['cat'], e['lat'], e['lng'], e['city'], e['desc'], e['tags'], e['day'])
               for e in entries_data]

    # 5. Replace P_KR block in HTML
    new_block = 'const P_KR=[\n' + ',\n'.join(entries) + '\n];'
    with open(HTML_FILE, encoding='utf-8') as f:
        html = f.read()

    html_new, n = re.subn(r'const P_KR=\[[\s\S]*?\n\];', new_block, html)
    if n == 0:
        print("ERROR: Could not find 'const P_KR=[...];' block in HTML", file=sys.stderr)
        sys.exit(1)

    with open(HTML_FILE, 'w', encoding='utf-8') as f:
        f.write(html_new)

    print(f"✓ Wrote {len(entries)} places to P_KR in {os.path.basename(HTML_FILE)}")


if __name__ == '__main__':
    main()
