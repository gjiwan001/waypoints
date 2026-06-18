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

import csv, json, re, time, urllib.request, urllib.parse, os, sys

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
    """Return (lat, lng) from Nominatim, or None on failure."""
    query = f"{name}, {city}, South Korea" if city else f"{name}, South Korea"
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
        print(f"  ⚠ Nominatim error for '{name}': {e}", file=sys.stderr)
    return None

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
        f'lat:{lat:.4f},'
        f'lng:{lng:.4f},'
        f'area:"{js_str(clean_area(area))}",'
        f'd:"{js_str(desc)}",'
        f'tags:[{tags_js}],'
        f'day:"{js_str(day)}"}}'
    )


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

        coords = geocode(name, city) or geocode(name, '')
        if coords:
            cache[name] = {'lat': coords[0], 'lng': coords[1]}
            geocoded += 1
            print(f"→ {coords[0]:.4f}, {coords[1]:.4f}")
        else:
            default = city_default(city)
            cache[name] = {'lat': default[0], 'lng': default[1]}
            print(f"→ default ({city})")
        time.sleep(1.1)   # Nominatim: max 1 req/s

    if geocoded:
        save_cache(cache)
        print(f"Geocoded {geocoded} new places; cache updated")

    # 4. Build JS entries
    entries = []
    for name, info in places.items():
        c = cache.get(name, {'lat': SEOUL_DEFAULT[0], 'lng': SEOUL_DEFAULT[1]})
        desc = info['descs'][0] if info['descs'] else ''
        days = sorted(info['days'])
        day  = days[0] if len(days) == 1 else (', '.join(days) if days else 'Flexible')
        tags = make_tags(name, info['cat'], desc)
        entries.append(fmt_entry(name, info['cat'], c['lat'], c['lng'], info['city'], desc, tags, day))

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
