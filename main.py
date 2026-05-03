"""
Oppside v3 — Eiendom & Bil
"""
import os, re, time, smtplib, logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from typing import Optional, List

import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("oppside")

app = FastAPI(title="Oppside")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory="static"), name="static")

# ─── CONFIG ───────────────────────────────────────────────────────────────────
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept-Language": "nb-NO,nb;q=0.9,no;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
GMAIL_USER     = os.environ.get("GMAIL_USER", "")
GMAIL_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
ALERT_EMAIL    = os.environ.get("ALERT_EMAIL", "Ostensenanders@gmail.com")
CAR_ALERT_PCT  = float(os.environ.get("CAR_ALERT_PCT", "30"))
CAR_ALERT_KR   = float(os.environ.get("CAR_ALERT_KR", "50000"))
BASE_URL_CAR   = "https://www.finn.no/mobility/search/car"

# ─── FINN.NO MAKE → URL-NAME MAPPING ─────────────────────────────────────────
MAKE_MAP = {
    "tesla": "TESLA", "volkswagen": "VOLKSWAGEN", "vw": "VOLKSWAGEN",
    "hyundai": "HYUNDAI", "kia": "KIA", "bmw": "BMW", "audi": "AUDI",
    "nissan": "NISSAN", "polestar": "POLESTAR", "volvo": "VOLVO",
    "mercedes": "MERCEDES-BENZ", "ford": "FORD", "skoda": "SKODA",
    "renault": "RENAULT", "peugeot": "PEUGEOT", "opel": "OPEL",
    "mg": "MG", "byd": "BYD", "cupra": "CUPRA", "mini": "MINI",
}

# ─── FUEL TYPE MAPPING ────────────────────────────────────────────────────────
FUEL_LABEL_MAP = {
    "ELECTRIC": "El",
    "PETROL": "Bensin",
    "DIESEL": "Diesel",
    "HYBRID_PETROL": "Hybrid",
    "PLUG_IN_HYBRID_PETROL": "Ladbar hybrid",
}

# ─── OFV TOPP 20 ELBILER ─────────────────────────────────────────────────────
OFV_TOP = {
    "tesla model 3": 1, "tesla model y": 2, "volkswagen id.4": 3,
    "volkswagen id.3": 4, "nissan leaf": 5, "hyundai ioniq 5": 6,
    "hyundai ioniq 6": 7, "kia ev6": 8, "kia niro": 9, "bmw i4": 10,
    "bmw ix3": 11, "audi q4": 12, "mercedes eqc": 13, "skoda enyaq": 14,
    "peugeot e-208": 15, "renault zoe": 16, "volvo xc40 recharge": 17,
    "polestar 2": 18, "ford mustang mach-e": 19, "mg zs": 20,
}

def ofv_rank(title: str) -> Optional[int]:
    t = title.lower()
    for key, rank in OFV_TOP.items():
        if key in t:
            return rank
    return None

# ─── HJEMSTED → FINN LOCATION CODES ──────────────────────────────────────────
# Maps (hjemsted, radius_hours) → list of finn location codes to scrape
HJEMSTED_LOC = {
    "bergen":      ["1.22046.20220"],
    "oslo":        ["0.20061"],
    "trondheim":   ["1.20016.20318"],
    "stavanger":   ["1.20012.20196"],
    "tromso":      ["1.20019.20413"],
    "bodo":        ["1.20018.20346"],
    "kristiansand":["1.22042.20179"],
    "alesund":     ["1.20015.20282"],
    "sortland":    ["1.20018.20370"],
    "svolvaer":    ["1.20018.20363"],
    "narvik":      ["1.20018.20361"],
}

# When radius increases, we include adjacent regions
RADIUS_EXPAND = {
    "bergen": {
        1: ["1.22046.20220"],
        2: ["1.22046.20220", "1.22046.20216", "1.22046.20232"],  # + Askøy, Os
        3: ["1.22046.20220", "1.22046.20216", "1.20012.20196"],  # + Stavanger
        5: ["1.22046.20220", "1.20012.20196", "1.20015.20282"],  # + Stavanger, Ålesund
        99: [],  # Hele Norge = ingen location-filter
    },
    "oslo": {
        1: ["0.20061"],
        2: ["0.20061", "1.20003.20045", "1.20003.20046"],  # + Bærum, Asker
        3: ["0.20061", "0.20003", "1.20007.20110"],
        5: ["0.20061", "0.20003", "1.20007.20110", "1.20005.20038"],
        99: [],
    },
    "trondheim": {
        1: ["1.20016.20318"],
        2: ["1.20016.20318", "1.20016.20311"],
        3: ["1.20016.20318", "1.20016.20311", "1.20017.20337"],
        5: ["1.20016.20318", "1.20016.20311", "1.20015.20282"],
        99: [],
    },
    "sortland": {
        1: ["1.20018.20370"],
        2: ["1.20018.20370", "1.20018.20363"],  # + Svolvær
        3: ["1.20018.20370", "1.20018.20363", "1.20018.20346"],  # + Bodø
        5: ["1.20018.20370", "1.20018.20363", "1.20018.20346", "1.20018.20361"],
        99: [],
    },
    "svolvaer": {
        1: ["1.20018.20363"],
        2: ["1.20018.20363", "1.20018.20360", "1.20018.20370"],
        3: ["1.20018.20363", "1.20018.20360", "1.20018.20370", "1.20018.20346"],
        5: ["1.20018.20363", "1.20018.20360", "1.20018.20370", "1.20018.20346", "1.20018.20361"],
        99: [],
    },
}

def get_locations(hjemsted: str, radius_h: int) -> List[str]:
    """Return finn.no location codes based on home city and driving radius."""
    key = hjemsted.lower().strip()
    if not key or radius_h >= 99:
        return []  # Hele Norge
    if key in RADIUS_EXPAND:
        expand = RADIUS_EXPAND[key]
        # Find best match
        for h in sorted(expand.keys()):
            if radius_h <= h:
                return expand[h]
        return []
    # Fallback: just use default location
    return HJEMSTED_LOC.get(key, [])

# ─── CAR SCRAPING ─────────────────────────────────────────────────────────────
def parse_car_article(art, required_fuel: str = "", required_make: str = "") -> Optional[dict]:
    """Parse a finn.no car article. Returns None if filtered out."""
    txt = art.get_text(" ", strip=True)

    # Skip paid placements
    if "Betalt plassering" in txt:
        return None

    # Extract title (before first bullet)
    parts = txt.split("∙")
    raw_title = parts[0].strip()

    # Make filter — post-filter since finn API doesn't always apply it
    if required_make:
        make_label = MAKE_MAP.get(required_make.lower(), required_make).lower()
        if make_label.lower() not in raw_title.lower() and required_make.lower() not in raw_title.lower():
            return None

    # Fuel filter — post-filter by checking text
    fuel_in_text = ""
    if "∙ El" in txt or " El ∙" in txt or "km rekkevidde" in txt or "· El" in txt:
        fuel_in_text = "ELECTRIC"
    elif "Diesel" in txt:
        fuel_in_text = "DIESEL"
    elif "Hybrid bensin" in txt or "Ladbar hybrid" in txt:
        fuel_in_text = "HYBRID_PETROL"
    elif "Bensin" in txt:
        fuel_in_text = "PETROL"

    if required_fuel and required_fuel != "ALL":
        if fuel_in_text != required_fuel:
            return None

    result = {
        "title": raw_title,
        "fuel_detected": fuel_in_text,
        "fuel_label": FUEL_LABEL_MAP.get(fuel_in_text, fuel_in_text),
    }

    # Link
    link = art.find("a", href=True)
    if link:
        href = link.get("href", "")
        result["url"] = href if href.startswith("http") else f"https://www.finn.no{href}"
        m = re.search(r'/item/(\d+)', href)
        result["finnkode"] = m.group(1) if m else None

    # Image
    img = art.find("img")
    if img:
        result["image"] = img.get("src", "")

    # Year
    ym = re.search(r'\b(20\d{2}|19[89]\d)\b', txt)
    result["year"] = int(ym.group(1)) if ym else None

    # KM
    km_parts = [p.strip() for p in parts]
    for p in km_parts:
        km_m = re.search(r'^([\d\s]+)\s*km$', p.strip())
        if km_m:
            km = int(re.sub(r'\s', '', km_m.group(1)))
            if 100 < km < 1000000:
                result["km"] = km
                result["km_str"] = f"{km:,} km".replace(",", " ")
                break

    # Range (rekkevidde)
    range_m = re.search(r'(\d+)\s*km\s*rekkevidde', txt)
    result["range_km"] = int(range_m.group(1)) if range_m else None

    # Price
    price_m = re.search(r'([\d\s]{4,})\s*kr', txt)
    if price_m:
        p = int(re.sub(r'\s', '', price_m.group(1)))
        if 10000 < p < 5000000:
            result["price_int"] = p
            result["price"] = f"{p:,} kr".replace(",", " ")

    # Location — text after price usually
    loc_m = re.search(r'kr\s+([A-ZÆØÅ][a-zæøå]+(?:\s+[A-ZÆØÅ][a-zæøå]+)?)', txt)
    result["location"] = loc_m.group(1) if loc_m else ""

    # Model key for comparison URL
    result["model_key"] = extract_model_key(raw_title)
    result["comparison_url"] = build_comparison_url(raw_title, result.get("year"), fuel_in_text)

    return result

def extract_model_key(title: str) -> str:
    """Extract brand + base model (e.g. 'Tesla Model 3' from full title)."""
    # Remove common trim descriptors
    cleaned = re.sub(r'\b(Long Range|Standard Range|AWD|RWD|Performance|Plus|Pro|Sport|Elite|Premium|Executive|Comfort|Style|Edition|Automat|Manuell|4WD|xDrive|quattro|e-tron)\b.*', '', title, flags=re.I)
    words = cleaned.strip().split()
    return " ".join(words[:3]) if len(words) >= 3 else cleaned.strip()

def build_comparison_url(title: str, year: Optional[int], fuel: str) -> str:
    """Build finn.no URL that shows same model sorted cheapest."""
    model_key = extract_model_key(title)
    params = [f"q={requests.utils.quote(model_key)}", "sort=PRICE_ASC"]
    if year:
        params.append(f"year_from={year-1}&year_to={year+1}")
    if fuel == "ELECTRIC":
        params.append("fuel=ELECTRIC")
    elif fuel == "DIESEL":
        params.append("fuel=DIESEL")
    elif fuel == "PETROL":
        params.append("fuel=PETROL")
    return f"https://www.finn.no/mobility/search/car?" + "&".join(params)

def fetch_cars_from_finn(lokasjon: str = "", merke: str = "", fuel: str = "ELECTRIC",
                          maks_pris: int = 0, min_pris: int = 0,
                          year_from: int = 0, max_km: int = 0, page: int = 1) -> list:
    params = {"sort": "PRICE_ASC", "page": str(page)}
    if lokasjon:
        params["location"] = lokasjon
    if merke and merke.upper() in MAKE_MAP.values():
        params["make"] = merke.upper()
    elif merke:
        params["make"] = MAKE_MAP.get(merke.lower(), merke.upper())
    if fuel and fuel != "ALL":
        params["fuel"] = fuel
    if maks_pris:
        params["price_to"] = str(maks_pris)
    if min_pris:
        params["price_from"] = str(min_pris)
    if year_from:
        params["year_from"] = str(year_from)
    if max_km:
        params["mileage_to"] = str(max_km)

    try:
        r = requests.get(BASE_URL_CAR, headers=HEADERS, params=params, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        arts = soup.find_all("article", class_=lambda x: x and "sf-search-ad" in x)
        results = []
        for art in arts:
            parsed = parse_car_article(art, required_fuel=fuel, required_make=merke)
            if parsed and parsed.get("title") and parsed.get("price_int"):
                results.append(parsed)
        return results
    except Exception as e:
        log.error(f"Car fetch error: {e}")
        return []

def get_market_avg(model_key: str, year: Optional[int], fuel: str) -> Optional[dict]:
    """Fetch market average for same model on finn.no."""
    params = {
        "q": model_key,
        "sort": "PRICE_ASC",
    }
    if year:
        params["year_from"] = str(max(year - 1, 2010))
        params["year_to"] = str(year + 1)
    if fuel and fuel != "ALL":
        params["fuel"] = fuel
    try:
        r = requests.get(BASE_URL_CAR, headers=HEADERS, params=params, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        arts = soup.find_all("article", class_=lambda x: x and "sf-search-ad" in x)
        prices = []
        for art in arts[:25]:
            txt = art.get_text(" ", strip=True)
            if "Betalt plassering" in txt:
                continue
            pm = re.search(r'([\d\s]{4,})\s*kr', txt)
            if pm:
                p = int(re.sub(r'\s', '', pm.group(1)))
                if 20000 < p < 3000000:
                    prices.append(p)
        if len(prices) >= 3:
            prices.sort()
            # Remove extreme outliers (top 10%)
            cutoff = int(len(prices) * 0.9)
            prices = prices[:cutoff]
            return {"avg": round(sum(prices) / len(prices)), "count": len(prices),
                    "min": min(prices), "max": max(prices)}
        return None
    except Exception as e:
        log.error(f"Market avg error: {e}")
        return None

def analyze_car(car: dict) -> dict:
    """Enrich car with market comparison and signals."""
    price = car.get("price_int")
    if not price:
        car["signals"] = ["❓ Ingen pris oppgitt"]
        car["alert_worthy"] = False
        return car

    model_key = car.get("model_key", "")
    fuel = car.get("fuel_detected", "")
    year = car.get("year")
    km = car.get("km")
    title_lower = (car.get("title") or "").lower()
    signals = []

    # OFV rank
    rank = ofv_rank(car.get("title", ""))
    if rank:
        signals.append(f"🏆 OFV topp {rank} — høy etterspørsel, rask omsetning")

    # Market comparison
    market = get_market_avg(model_key, year, fuel)
    time.sleep(0.3)

    if market and market["count"] >= 3:
        avg = market["avg"]
        diff_kr = avg - price
        diff_pct = (diff_kr / avg) * 100 if avg > 0 else 0
        car["market_avg"] = avg
        car["market_count"] = market["count"]
        car["market_min"] = market["min"]
        car["underpriset_kr"] = round(diff_kr)
        car["underpriset_pct"] = round(diff_pct, 1)
        car["alert_worthy"] = diff_pct >= CAR_ALERT_PCT and diff_kr >= CAR_ALERT_KR
        if diff_pct >= 30:
            signals.append(f"🔥 {diff_pct:.0f}% under markedssnitt — {abs(int(diff_kr)):,} kr billigere enn snitt".replace(",", " "))
        elif diff_pct >= 15:
            signals.append(f"💰 {diff_pct:.0f}% under markedssnitt — {abs(int(diff_kr)):,} kr billigere".replace(",", " "))
        elif diff_pct >= 5:
            signals.append(f"📉 {diff_pct:.0f}% under markedssnitt ({avg:,} kr snitt)".replace(",", " "))
        elif diff_pct < -15:
            signals.append(f"⚠️ {abs(diff_pct):.0f}% OVER markedssnitt — vanskelig å flipe")
        else:
            signals.append(f"➡️ Rundt markedssnitt ({avg:,} kr for {market['count']} tilsv.)".replace(",", " "))
    else:
        car["market_avg"] = None
        car["underpriset_kr"] = None
        car["underpriset_pct"] = None
        car["alert_worthy"] = False
        signals.append("📊 For få sammenlignbare annonser — kontroller pris manuelt")

    # ─── RED FLAGS (årsaker til lav pris) ───
    red_flags = []
    title_and_text = title_lower
    # Damage keywords
    damage_kw = ["skadet", "skade", "hagl", "kollisjon", "totalskade", "påkjørt",
                 "vannskade", "brannsk", "rustsk", "karosseri", "kræsj", "krasjet",
                 "ulykkesb", "forsikringssk"]
    for kw in damage_kw:
        if kw in title_and_text:
            red_flags.append(f"⛔ Mulig skade: «{kw}»")

    # Wrong model name hints
    if re.search(r'\b(gt|gts|gti|amg|rs|m\d|svr)\b', title_lower):
        signals.append("⚡ Sport/performance-variant — sjekk ekte modellbetegnelse")

    # Battery/range check
    range_km = car.get("range_km")
    if fuel == "ELECTRIC":
        if range_km:
            if range_km >= 450:
                signals.append(f"⚡ Utmerket rekkevidde: {range_km} km WLTP")
            elif range_km >= 300:
                signals.append(f"⚡ God rekkevidde: {range_km} km WLTP")
            elif range_km < 200:
                red_flags.append(f"🔋 Kort rekkevidde ({range_km} km) — mulig slitt batteri")
        if km and km > 100000:
            red_flags.append(f"🔋 Høy km ({km:,} km) på elbil — sjekk batteristatus".replace(",", " "))

    # KM-stand
    if km:
        if km < 30000:
            signals.append(f"✅ Svært lav km-stand: {km:,} km".replace(",", " "))
        elif km < 60000:
            signals.append(f"✅ Lav km-stand: {km:,} km".replace(",", " "))
        elif km > 200000:
            red_flags.append(f"⚠️ Meget høy km-stand: {km:,} km".replace(",", " "))

    # Year
    if year:
        age = datetime.now().year - year
        if age <= 2:
            signals.append(f"🆕 Nesten ny bil ({year})")
        elif age <= 4:
            signals.append(f"📅 Relativt ny ({year})")

    # Price suspiciously low vs min
    if market and car.get("market_min") and price < market["market_min"] * 0.7:
        red_flags.append("🚨 Pris langt under billigste sammenlignbar — undersøk nøye")

    # Combine
    car["signals"] = signals + red_flags
    car["red_flags"] = red_flags
    return car

# ─── EIENDOM ─────────────────────────────────────────────────────────────────
OPPUSSING_STRONG = ["oppussingsobjekt","oppussing","renovering","renoveringsbehov","rehabilitering","totalrenovering","selges as is","as is","selges uten garanti","uten garanti","oppussingsprosjekt","rivningsobjekt","settes i stand","modernisering","rehabiliteringsprosjekt","selges uoppusset","uoppusset","i original stand","totaloppussing"]
OPPUSSING_WEAK = ["potensial","oppussingsbehov","noe oppussing","litt slitt","enkel standard","moderniseringsbehov","slitt","trenger","behov for","kan oppgraderes","godt potensiale","stort potensial"]
POSITIVE = ["hjørne","toppetasje","penthouse","utsikt","sjøutsikt","sørvest","sørvendt","vestvendt","balkong","terrasse","takterrasse","heis","garasje","parkering","sentralt","strand","innglasset"]

def parse_price(text):
    if not text: return None
    c = re.sub(r"[^\d]", "", str(text))
    return int(c) if c else None

def parse_size(text):
    if not text: return None
    m = re.search(r"(\d+)\s*m²", str(text))
    return int(m.group(1)) if m else None

def parse_property(art):
    r = {}
    h2 = art.find("h2")
    if h2:
        a = h2.find("a")
        r["title"] = (a or h2).get_text(strip=True)
    link = art.find("a", class_=lambda x: x and "sf-search-ad-link" in x)
    if link:
        href = link.get("href","")
        r["url"] = href if href.startswith("http") else f"https://www.finn.no{href}"
    img = art.find("img", alt="Bilde 1 av annonsen")
    r["image"] = img.get("src","").replace("480w","640w") if img else ""
    pb = art.find("div", class_=lambda x: x and "justify-between" in x if x else False)
    if pb:
        for span in pb.find_all("span"):
            txt = span.get_text(strip=True)
            if "m²" in txt: r["size"] = txt; r["size_int"] = parse_size(txt)
            elif "kr" in txt: r["price"] = txt; r["price_int"] = parse_price(txt)
    loc = art.find("div", class_=lambda x: x and "sf-realestate-location" in x)
    r["location"] = loc.get_text(strip=True) if loc else ""
    for d in art.find_all("div", class_=lambda x: x and "s-text-subtle" in x if x else False):
        txt = d.get_text(strip=True)
        if "Totalpris" in txt:
            for ef in ["Selveier","Andel","Aksje"]:
                if ef in txt: r["eierform"] = ef; r["property_info"] = txt; break
    return r

def score_property(l, avg=None):
    title = (l.get("title") or "").lower()
    desc = (l.get("property_info") or "").lower()
    score = 0; reasons = []
    for kw in OPPUSSING_STRONG:
        if kw in title: score += 30; reasons.append(f"🔨 Sterkt signal: «{kw}»"); break
    for kw in OPPUSSING_WEAK:
        if kw in title: score += 15; reasons.append(f"⚠️ Potensial: «{kw}»"); break
    p, s = l.get("price_int"), l.get("size_int")
    if p and s and s > 10:
        pps = p/s; l["pris_per_kvm"] = round(pps)
        if avg and pps < avg:
            rab = (1 - pps/avg)*100
            if rab > 30: score += 25; reasons.append(f"💰 {rab:.0f}% under snittet ({pps:,.0f} kr/m²)")
            elif rab > 15: score += 15; reasons.append(f"💰 {rab:.0f}% under snittet")
            elif rab > 5: score += 8; reasons.append(f"💰 Noe under snittet ({pps:,.0f} kr/m²)")
        if pps < 20000: score += 20; reasons.append(f"💎 Svært lav kvm-pris: {pps:,.0f} kr/m²")
        elif pps < 30000: score += 10; reasons.append(f"💡 Lav kvm-pris: {pps:,.0f} kr/m²")
    pos = [kw for kw in POSITIVE if kw in title]
    if pos: score += len(pos)*3; reasons.append(f"✨ {', '.join(pos)}")
    if "selveier" in desc: score += 5; reasons.append("🏠 Selveier")
    l["score"] = score; l["reasons"] = reasons
    return l

def fetch_properties(lokasjon=None, maks_pris=None, min_storrelse=None, boligtype=None, page=1):
    params = {"sort":"PRICE_SQM_ASC","is_new_property":"false","page":str(page)}
    if lokasjon: params["location"] = lokasjon
    if maks_pris: params["price_to"] = str(maks_pris)
    if min_storrelse: params["area_from"] = str(min_storrelse)
    if boligtype: params["property_type"] = boligtype
    try:
        r = requests.get("https://www.finn.no/realestate/homes/search.html", headers=HEADERS, params=params, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        arts = soup.find_all("article", class_=lambda x: x and "sf-search-ad" in x)
        return [parse_property(a) for a in arts if parse_property(a).get("title")]
    except Exception as e:
        log.error(f"Property fetch: {e}"); return []

def get_prop_avg(lokasjon=None):
    params = {"sort":"RELEVANCE","is_new_property":"false"}
    if lokasjon: params["location"] = lokasjon
    try:
        r = requests.get("https://www.finn.no/realestate/homes/search.html", headers=HEADERS, params=params, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        arts = soup.find_all("article", class_=lambda x: x and "sf-search-ad" in x)
        prices = []
        for art in arts[:30]:
            l = parse_property(art)
            p, s = l.get("price_int"), l.get("size_int")
            if p and s and s > 10: prices.append(p/s)
        return round(sum(prices)/len(prices)) if len(prices) >= 5 else None
    except: return None

def fetch_sold(lokasjon=None, limit=20):
    params = {"sort":"PUBLISHED_DESC"}
    if lokasjon: params["location"] = lokasjon
    try:
        r = requests.get("https://www.finn.no/realestate/sold/search.html", headers=HEADERS, params=params, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        results = []
        for art in soup.find_all("article")[:limit]:
            txt = art.get_text(" ", strip=True)
            item = {}
            h2 = art.find("h2"); item["title"] = h2.get_text(strip=True) if h2 else ""
            img = art.find("img"); item["image"] = img.get("src","") if img else ""
            pm = re.search(r"([\d\s\xa0]{6,})\s*kr", txt)
            if pm: item["sold_price"] = parse_price(pm.group(1))
            sm = re.search(r"(\d+)\s*m²", txt)
            if sm: item["size_int"] = int(sm.group(1)); item["size"] = f"{sm.group(1)} m²"
            loc = art.find("div", class_=lambda x: x and "location" in x.lower() if x else False)
            item["location"] = loc.get_text(strip=True) if loc else ""
            if item.get("title") and item.get("sold_price"):
                if item.get("size_int"): item["pris_per_kvm"] = round(item["sold_price"]/item["size_int"])
                results.append(item)
        return results
    except Exception as e:
        log.error(f"Sold fetch: {e}"); return []

# ─── EMAIL ────────────────────────────────────────────────────────────────────
_alerted: set = set()

def send_alert(cars: list):
    if not GMAIL_USER or not GMAIL_PASSWORD or not cars: return
    html = f"""<html><body style="font-family:Georgia,serif;background:#F7F4EF;padding:28px">
    <div style="max-width:580px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,.08)">
      <div style="background:#1C1C1E;padding:24px 28px">
        <h1 style="color:#fff;font-size:22px;margin:0">🚗 Oppside — Bilvarsel</h1>
        <p style="color:rgba(255,255,255,.45);margin:5px 0 0;font-size:13px">{len(cars)} eksepsjonelle kjøp funnet — {datetime.now().strftime('%d.%m.%Y kl. %H:%M')}</p>
      </div><div style="padding:24px 28px">"""
    for c in cars[:5]:
        pct = c.get("underpriset_pct",""); kr = c.get("underpriset_kr",""); avg = c.get("market_avg","")
        html += f"""<div style="border:1px solid #E5E0D8;border-radius:10px;padding:18px;margin-bottom:14px">
          <h2 style="font-size:16px;margin:0 0 6px;color:#1C1C1E">{c.get('title','')}</h2>
          <p style="color:#8A8A8E;font-size:11px;margin:0 0 10px">{c.get('location','')} • {c.get('year','')} • {c.get('km_str','')}</p>
          <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px">
            <span style="background:#EEF3E8;color:#2D5016;padding:4px 10px;border-radius:16px;font-size:12px;font-weight:700">{pct:.0f}% under snitt</span>
            <span style="background:#EEF3E8;color:#2D5016;padding:4px 10px;border-radius:16px;font-size:12px;font-weight:700">{int(kr):,} kr billigere".replace(',', ' ')</span>
            <span style="background:#F7F4EF;color:#3A3A3C;padding:4px 10px;border-radius:16px;font-size:12px">Pris: {c.get('price','?')}</span>
          </div>
          <a href="{c.get('url','#')}" style="display:inline-block;background:#2D5016;color:#fff;padding:9px 18px;border-radius:7px;text-decoration:none;font-size:13px;font-weight:600">Se på Finn.no →</a>
        </div>"""
    html += "</div></div></body></html>"
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"🚗 Oppside — {len(cars)} eksepsjonell{'e' if len(cars)>1 else ''} bil funnet"
        msg["From"] = GMAIL_USER; msg["To"] = ALERT_EMAIL
        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(GMAIL_USER, GMAIL_PASSWORD)
            s.sendmail(GMAIL_USER, ALERT_EMAIL, msg.as_string())
        log.info(f"✅ Alert sent with {len(cars)} cars")
    except Exception as e:
        log.error(f"Email error: {e}")

# ─── ENDPOINTS ────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def root():
    with open("static/index.html", encoding="utf-8") as f: return f.read()

@app.get("/search")
def search_prop(lokasjon: Optional[str]=None, maks_pris: Optional[int]=None,
                min_storrelse: Optional[int]=None, boligtype: Optional[str]=None,
                sider: int=Query(3,ge=1,le=10), min_score: int=10):
    avg = get_prop_avg(lokasjon)
    all_l = []
    for page in range(1, sider+1):
        listings = fetch_properties(lokasjon, maks_pris, min_storrelse, boligtype, page)
        if not listings: break
        all_l.extend(listings); time.sleep(0.4)
    scored = [score_property(l, avg) for l in all_l]
    filtered = sorted([l for l in scored if l.get("score",0) >= min_score], key=lambda x: x.get("score",0), reverse=True)
    return {"count": len(filtered), "total_scraped": len(all_l), "area_avg_sqm": avg, "listings": filtered[:60]}

@app.get("/sold")
def sold_prop(lokasjon: Optional[str]=None, limit: int=20):
    results = fetch_sold(lokasjon, limit)
    prices = [r["pris_per_kvm"] for r in results if r.get("pris_per_kvm")]
    avg = round(sum(prices)/len(prices)) if prices else None
    return {"count": len(results), "avg_sqm": avg, "sales": results}

@app.get("/cars")
def search_cars(
    hjemsted: Optional[str] = None,
    radius_h: int = Query(99, ge=1),
    merke: Optional[str] = None,
    fuel: Optional[str] = "ELECTRIC",
    maks_pris: Optional[int] = None,
    min_pris: Optional[int] = None,
    year_from: Optional[int] = None,
    max_km: Optional[int] = None,
    background_tasks: BackgroundTasks = None,
):
    locs = get_locations(hjemsted or "", radius_h)
    if not locs:
        locs = [""]  # Hele Norge

    all_cars = []
    for loc in locs:
        cars = fetch_cars_from_finn(
            lokasjon=loc, merke=merke or "",
            fuel=fuel or "ELECTRIC",
            maks_pris=maks_pris or 0, min_pris=min_pris or 0,
            year_from=year_from or 0, max_km=max_km or 0,
        )
        all_cars.extend(cars)
        time.sleep(0.4)

    # Deduplicate by finnkode
    seen = set(); unique = []
    for c in all_cars:
        key = c.get("finnkode") or c.get("url","")
        if key and key not in seen:
            seen.add(key); unique.append(c)

    analyzed = []
    for car in unique[:45]:
        analyzed.append(analyze_car(car))
        time.sleep(0.3)

    alert_cars = [c for c in analyzed if c.get("alert_worthy") and c.get("url","") not in _alerted]
    if alert_cars and background_tasks:
        for c in alert_cars: _alerted.add(c.get("url",""))
        background_tasks.add_task(send_alert, alert_cars)

    sorted_cars = sorted(analyzed, key=lambda x: x.get("underpriset_pct") or -999, reverse=True)
    return {"count": len(sorted_cars), "total_scraped": len(all_cars), "alert_sent": len(alert_cars), "cars": sorted_cars[:50]}

@app.post("/trigger-alert-scan")
def trigger_scan(bt: BackgroundTasks, fuel: str="ELECTRIC", hjemsted: Optional[str]=None, radius_h: int=99):
    locs = get_locations(hjemsted or "", radius_h) or [""]
    def scan():
        all_cars = []
        for loc in locs:
            cars = fetch_cars_from_finn(lokasjon=loc, fuel=fuel)
            all_cars.extend(cars)
            time.sleep(0.5)
        alert_cars = []
        for c in all_cars[:30]:
            a = analyze_car(c)
            if a.get("alert_worthy") and a.get("url","") not in _alerted:
                alert_cars.append(a); _alerted.add(a.get("url",""))
            time.sleep(0.3)
        if alert_cars: send_alert(alert_cars)
    bt.add_task(scan)
    return {"message": "Scanning startet"}

@app.get("/alert-config")
def alert_config():
    return {"configured": bool(GMAIL_USER and GMAIL_PASSWORD), "alert_email": ALERT_EMAIL,
            "threshold_pct": CAR_ALERT_PCT, "threshold_kr": CAR_ALERT_KR}

@app.get("/health")
def health(): return {"status": "ok", "app": "Oppside v3"}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
