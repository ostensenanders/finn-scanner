"""
Oppside — Eiendom & Bil
Backend: FastAPI + scraping + email varsler
"""

import os, re, time, smtplib, json, logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("oppside")

app = FastAPI(title="Oppside")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

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
CAR_ALERT_PCT  = float(os.environ.get("CAR_ALERT_PCT", "30"))   # varsle hvis 30%+ under snitt
CAR_ALERT_KR   = float(os.environ.get("CAR_ALERT_KR", "50000")) # varsle hvis 50k+ under snitt

# ─── OFV TOPP 20 ELBILER (mest omsatte, kilde: ofv.no) ─────────────────────
OFV_TOP_ELBIL = {
    "tesla model 3": {"rank": 1, "label": "Tesla Model 3"},
    "tesla model y": {"rank": 2, "label": "Tesla Model Y"},
    "volkswagen id.4": {"rank": 3, "label": "VW ID.4"},
    "volkswagen id.3": {"rank": 4, "label": "VW ID.3"},
    "nissan leaf": {"rank": 5, "label": "Nissan Leaf"},
    "hyundai ioniq 5": {"rank": 6, "label": "Hyundai IONIQ 5"},
    "hyundai ioniq 6": {"rank": 7, "label": "Hyundai IONIQ 6"},
    "kia ev6": {"rank": 8, "label": "Kia EV6"},
    "kia niro": {"rank": 9, "label": "Kia Niro EV"},
    "bmw i4": {"rank": 10, "label": "BMW i4"},
    "bmw ix3": {"rank": 11, "label": "BMW iX3"},
    "audi q4 e-tron": {"rank": 12, "label": "Audi Q4 e-tron"},
    "mercedes eqc": {"rank": 13, "label": "Mercedes EQC"},
    "skoda enyaq": {"rank": 14, "label": "Škoda Enyaq"},
    "peugeot e-208": {"rank": 15, "label": "Peugeot e-208"},
    "renault zoe": {"rank": 16, "label": "Renault ZOE"},
    "volvo xc40 recharge": {"rank": 17, "label": "Volvo XC40 Recharge"},
    "polestar 2": {"rank": 18, "label": "Polestar 2"},
    "ford mustang mach-e": {"rank": 19, "label": "Ford Mustang Mach-E"},
    "mg zs ev": {"rank": 20, "label": "MG ZS EV"},
}

def is_top_seller(title: str) -> Optional[dict]:
    t = title.lower()
    for key, val in OFV_TOP_ELBIL.items():
        if key in t:
            return val
    return None

# ─── EIENDOM ─────────────────────────────────────────────────────────────────
OPPUSSING_STRONG = [
    "oppussingsobjekt","oppussing","renovering","renoveringsbehov",
    "rehabilitering","totalrenovering","selges as is","as is",
    "selges uten garanti","uten garanti","oppussingsprosjekt",
    "rivningsobjekt","settes i stand","modernisering",
    "rehabiliteringsprosjekt","selges uoppusset","uoppusset",
    "i original stand","totaloppussing",
]
OPPUSSING_WEAK = [
    "potensial","oppussingsbehov","noe oppussing","litt slitt",
    "enkel standard","moderniseringsbehov","slitt","trenger",
    "behov for","kan oppgraderes","godt potensiale","stort potensial",
]
POSITIVE = [
    "hjørne","toppetasje","penthouse","utsikt","sjøutsikt","sørvest",
    "sørvendt","vestvendt","balkong","terrasse","takterrasse","heis",
    "garasje","parkering","sentralt","strand","innglasset",
]

def parse_price(text):
    if not text: return None
    cleaned = re.sub(r"[^\d]", "", str(text))
    return int(cleaned) if cleaned else None

def parse_size(text):
    if not text: return None
    m = re.search(r"(\d+)\s*m²", str(text))
    return int(m.group(1)) if m else None

def parse_property_article(art):
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
    price_block = art.find("div", class_=lambda x: x and "justify-between" in x if x else False)
    if price_block:
        for span in price_block.find_all("span"):
            txt = span.get_text(strip=True)
            if "m²" in txt:
                r["size"] = txt
                r["size_int"] = parse_size(txt)
            elif "kr" in txt:
                r["price"] = txt
                r["price_int"] = parse_price(txt)
    loc_div = art.find("div", class_=lambda x: x and "sf-realestate-location" in x)
    r["location"] = loc_div.get_text(strip=True) if loc_div else ""
    for d in art.find_all("div", class_=lambda x: x and "s-text-subtle" in x if x else False):
        txt = d.get_text(strip=True)
        if "Totalpris" in txt:
            for ef in ["Selveier","Andel","Aksje"]:
                if ef in txt:
                    r["eierform"] = ef
                    r["property_info"] = txt
                    break
    return r

def score_property(listing, area_avg=None):
    title = (listing.get("title") or "").lower()
    desc = (listing.get("property_info") or "").lower()
    score = 0; reasons = []
    for kw in OPPUSSING_STRONG:
        if kw in title:
            score += 30; reasons.append(f"🔨 Sterkt signal: «{kw}»"); break
    for kw in OPPUSSING_WEAK:
        if kw in title:
            score += 15; reasons.append(f"⚠️ Potensial: «{kw}»"); break
    price = listing.get("price_int")
    size = listing.get("size_int")
    if price and size and size > 10:
        pps = price / size
        listing["pris_per_kvm"] = round(pps)
        if area_avg and pps < area_avg:
            rabatt = (1 - pps/area_avg)*100
            if rabatt > 30: score += 25; reasons.append(f"💰 {rabatt:.0f}% under snittet ({pps:,.0f} kr/m²)")
            elif rabatt > 15: score += 15; reasons.append(f"💰 {rabatt:.0f}% under snittet")
            elif rabatt > 5: score += 8; reasons.append(f"💰 Noe under snittet ({pps:,.0f} kr/m²)")
        if pps < 20000: score += 20; reasons.append(f"💎 Svært lav kvm-pris: {pps:,.0f} kr/m²")
        elif pps < 30000: score += 10; reasons.append(f"💡 Lav kvm-pris: {pps:,.0f} kr/m²")
    pos = [kw for kw in POSITIVE if kw in title]
    if pos: score += len(pos)*3; reasons.append(f"✨ {', '.join(pos)}")
    if "selveier" in desc: score += 5; reasons.append("🏠 Selveier")
    listing["score"] = score; listing["reasons"] = reasons
    return listing

def fetch_properties(lokasjon=None, maks_pris=None, min_storrelse=None, boligtype=None, page=1):
    params = {"sort":"PRICE_SQM_ASC","is_new_property":"false","page":str(page)}
    if lokasjon: params["location"] = lokasjon
    if maks_pris: params["price_to"] = str(maks_pris)
    if min_storrelse: params["area_from"] = str(min_storrelse)
    if boligtype: params["property_type"] = boligtype
    try:
        r = requests.get("https://www.finn.no/realestate/homes/search.html",
                         headers=HEADERS, params=params, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        arts = soup.find_all("article", class_=lambda x: x and "sf-search-ad" in x)
        return [parse_property_article(a) for a in arts if parse_property_article(a).get("title")]
    except Exception as e:
        log.error(f"Property fetch error: {e}"); return []

def get_property_avg(lokasjon=None):
    params = {"sort":"RELEVANCE","is_new_property":"false"}
    if lokasjon: params["location"] = lokasjon
    try:
        r = requests.get("https://www.finn.no/realestate/homes/search.html",
                         headers=HEADERS, params=params, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        arts = soup.find_all("article", class_=lambda x: x and "sf-search-ad" in x)
        prices = []
        for art in arts[:30]:
            l = parse_property_article(art)
            p, s = l.get("price_int"), l.get("size_int")
            if p and s and s > 10: prices.append(p/s)
        return round(sum(prices)/len(prices)) if len(prices) >= 5 else None
    except: return None

def fetch_sold_properties(lokasjon=None, limit=20):
    params = {"sort":"PUBLISHED_DESC"}
    if lokasjon: params["location"] = lokasjon
    try:
        r = requests.get("https://www.finn.no/realestate/sold/search.html",
                         headers=HEADERS, params=params, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        results = []
        for art in soup.find_all("article")[:limit]:
            all_text = art.get_text(" ", strip=True)
            item = {}
            h2 = art.find("h2")
            if h2: item["title"] = h2.get_text(strip=True)
            img = art.find("img")
            item["image"] = img.get("src","") if img else ""
            pm = re.search(r"([\d\s\xa0]{6,})\s*kr", all_text)
            if pm: item["sold_price"] = parse_price(pm.group(1))
            sm = re.search(r"(\d+)\s*m²", all_text)
            if sm: item["size_int"] = int(sm.group(1)); item["size"] = f"{sm.group(1)} m²"
            loc = art.find("div", class_=lambda x: x and "location" in x.lower() if x else False)
            item["location"] = loc.get_text(strip=True) if loc else ""
            if item.get("title") and item.get("sold_price"):
                if item.get("size_int"): item["pris_per_kvm"] = round(item["sold_price"]/item["size_int"])
                results.append(item)
        return results
    except Exception as e:
        log.error(f"Sold fetch error: {e}"); return []

# ─── BIL ─────────────────────────────────────────────────────────────────────
def parse_car_article(art):
    r = {}
    h2 = art.find("h2")
    if h2:
        a = h2.find("a")
        r["title"] = (a or h2).get_text(strip=True)
    link = art.find("a", class_=lambda x: x and "sf-search-ad-link" in x)
    if not link: link = art.find("a", href=True)
    if link:
        href = link.get("href","")
        r["url"] = href if href.startswith("http") else f"https://www.finn.no{href}"
    img = art.find("img")
    r["image"] = img.get("src","") if img else ""
    all_text = art.get_text(" ", strip=True)
    # Price
    pm = re.search(r"([\d\s\xa0]{4,})\s*kr", all_text)
    if pm:
        p = parse_price(pm.group(1))
        if p and p > 10000: r["price_int"] = p; r["price"] = f"{p:,} kr".replace(",",".")
    # Year
    ym = re.search(r"\b(20\d{2}|19[89]\d)\b", all_text)
    if ym: r["year"] = int(ym.group(1))
    # KM
    km_m = re.search(r"([\d\s]+)\s*km", all_text)
    if km_m:
        km = parse_price(km_m.group(1))
        if km and km < 1000000: r["km"] = km; r["km_str"] = f"{km:,} km".replace(",",".")
    # Range
    range_m = re.search(r"(\d+)\s*km\s*(rekkevidde|wltp|range)", all_text, re.I)
    if range_m: r["range_km"] = int(range_m.group(1))
    # Location
    loc = art.find("div", class_=lambda x: x and ("location" in x.lower() or "address" in x.lower()) if x else False)
    if loc: r["location"] = loc.get_text(strip=True)
    else:
        loc_m = re.search(r"\b[A-ZÆØÅ][a-zæøå]+(?:\s+[A-ZÆØÅ][a-zæøå]+)?\s*,\s*[A-ZÆØÅ]", all_text)
        r["location"] = loc_m.group(0).strip() if loc_m else ""
    return r

def get_model_key(title: str) -> str:
    """Extract model key for price comparison."""
    t = title.lower().strip()
    # Try to get brand + model (first 2-3 words)
    words = t.split()
    return " ".join(words[:3]) if len(words) >= 3 else t

def fetch_finn_car_avg(model_key: str, year: Optional[int] = None, fuel: str = "") -> Optional[dict]:
    """Fetch average price for same model from finn.no."""
    params = {
        "sort": "RELEVANCE",
        "q": model_key,
        "sales_form": "1",  # private sellers
    }
    if year: params["year_from"] = str(max(year-1, 2010)); params["year_to"] = str(year+1)
    if fuel: params["fuel"] = fuel
    try:
        r = requests.get("https://www.finn.no/car/used/search.html",
                         headers=HEADERS, params=params, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        prices = []
        for art in soup.find_all("article")[:20]:
            txt = art.get_text(" ", strip=True)
            pm = re.search(r"([\d\s\xa0]{4,})\s*kr", txt)
            if pm:
                p = parse_price(pm.group(1))
                if p and 20000 < p < 2000000: prices.append(p)
        if len(prices) >= 3:
            avg = round(sum(prices)/len(prices))
            return {"avg": avg, "count": len(prices), "min": min(prices), "max": max(prices)}
        return None
    except Exception as e:
        log.error(f"Car avg fetch error: {e}"); return None

def fetch_cars(lokasjon=None, maks_pris=None, min_pris=None, merke=None,
               fuel=None, year_from=None, year_to=None,
               max_km=None, page=1):
    params = {"sort": "PUBLISHED_DESC", "sales_form": "1", "page": str(page)}
    if lokasjon: params["location"] = lokasjon
    if maks_pris: params["price_to"] = str(maks_pris)
    if min_pris: params["price_from"] = str(min_pris)
    if merke: params["make"] = merke
    if fuel: params["fuel"] = fuel
    if year_from: params["year_from"] = str(year_from)
    if year_to: params["year_to"] = str(year_to)
    if max_km: params["mileage_to"] = str(max_km)
    try:
        r = requests.get("https://www.finn.no/car/used/search.html",
                         headers=HEADERS, params=params, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        arts = soup.find_all("article")
        results = []
        for art in arts:
            parsed = parse_car_article(art)
            if parsed.get("title") and parsed.get("price_int"):
                results.append(parsed)
        return results
    except Exception as e:
        log.error(f"Car fetch error: {e}"); return []

def analyze_car(car: dict, fuel: str = "") -> dict:
    """Score car by comparing to market average on finn.no."""
    title = car.get("title","")
    price = car.get("price_int")
    year = car.get("year")
    km = car.get("km")

    if not price:
        car["underpriset_pct"] = None
        car["underpriset_kr"] = None
        car["market_avg"] = None
        car["signals"] = ["❓ Ingen pris oppgitt"]
        car["alert_worthy"] = False
        return car

    model_key = get_model_key(title)
    market = fetch_finn_car_avg(model_key, year, fuel)
    time.sleep(0.3)

    signals = []

    # Top seller bonus
    ts = is_top_seller(title)
    if ts: signals.append(f"🏆 OFV topp {ts['rank']}: {ts['label']} — rask omsetning")

    # Battery/range check for EVs
    range_km = car.get("range_km")
    if fuel in ("2","el","electric") or "elbil" in title.lower() or "electric" in title.lower():
        if range_km:
            if range_km >= 400: signals.append(f"⚡ God rekkevidde: {range_km} km")
            elif range_km < 200: signals.append(f"⚠️ Kort rekkevidde: {range_km} km — sjekk batteri")

    # KM-stand
    if km:
        if km < 30000: signals.append(f"✅ Svært lav km-stand: {km:,} km".replace(",","."))
        elif km < 60000: signals.append(f"✅ Lav km-stand: {km:,} km".replace(",","."))
        elif km > 150000: signals.append(f"⚠️ Høy km-stand: {km:,} km".replace(",","."))

    # Year
    if year:
        age = datetime.now().year - year
        if age <= 2: signals.append(f"🆕 Ny bil ({year})")
        elif age <= 4: signals.append(f"📅 Relativt ny ({year})")

    if market and market["count"] >= 3:
        avg = market["avg"]
        diff_kr = avg - price
        diff_pct = (diff_kr / avg) * 100 if avg > 0 else 0
        car["market_avg"] = avg
        car["market_count"] = market["count"]
        car["underpriset_kr"] = round(diff_kr)
        car["underpriset_pct"] = round(diff_pct, 1)

        if diff_pct >= 25:
            signals.append(f"🔥 {diff_pct:.0f}% under markedssnitt ({diff_kr:,.0f} kr billigere)".replace(",","."))
        elif diff_pct >= 15:
            signals.append(f"💰 {diff_pct:.0f}% under markedssnitt ({diff_kr:,.0f} kr billigere)".replace(",","."))
        elif diff_pct >= 5:
            signals.append(f"📉 {diff_pct:.0f}% under markedssnitt")
        elif diff_pct < -10:
            signals.append(f"⚠️ {abs(diff_pct):.0f}% OVER markedssnitt — vanskelig å flipe")
        else:
            signals.append(f"➡️ Rundt markedssnitt ({avg:,.0f} kr snitt)".replace(",","."))

        # Alert worthy?
        car["alert_worthy"] = diff_pct >= CAR_ALERT_PCT and diff_kr >= CAR_ALERT_KR
    else:
        car["market_avg"] = None
        car["underpriset_kr"] = None
        car["underpriset_pct"] = None
        car["alert_worthy"] = False
        signals.append("📊 For få sammenlignbare annonser til prisanalyse")

    car["signals"] = signals
    return car

# ─── E-POST VARSLER ──────────────────────────────────────────────────────────
def send_alert_email(cars: list):
    if not GMAIL_USER or not GMAIL_PASSWORD:
        log.warning("Gmail ikke konfigurert — hopper over e-post"); return
    if not cars:
        return

    body_html = """
    <html><body style="font-family:Georgia,serif;background:#F7F4EF;padding:32px">
    <div style="max-width:600px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,0.08)">
      <div style="background:#1C1C1E;padding:28px 32px">
        <h1 style="color:#fff;font-size:24px;margin:0">🚗 Oppside — Bilvarsel</h1>
        <p style="color:rgba(255,255,255,0.5);margin:6px 0 0;font-size:14px">Eksepsjonelle kjøp oppdaget</p>
      </div>
      <div style="padding:28px 32px">
    """

    for car in cars[:5]:
        pct = car.get("underpriset_pct","")
        kr = car.get("underpriset_kr","")
        avg = car.get("market_avg","")
        pct_str = f"{pct:.0f}%" if pct else "?"
        kr_str = f"{int(kr):,} kr".replace(",",".") if kr else "?"
        avg_str = f"{int(avg):,} kr".replace(",",".") if avg else "?"
        body_html += f"""
        <div style="border:1px solid #E5E0D8;border-radius:10px;padding:20px;margin-bottom:16px">
          <h2 style="font-size:17px;margin:0 0 8px;color:#1C1C1E">{car.get('title','')}</h2>
          <p style="color:#8A8A8E;font-size:12px;margin:0 0 12px">{car.get('location','')}</p>
          <div style="display:flex;gap:12px;margin-bottom:12px;flex-wrap:wrap">
            <span style="background:#EEF3E8;color:#2D5016;padding:5px 12px;border-radius:20px;font-size:13px;font-weight:700">{pct_str} under snitt</span>
            <span style="background:#EEF3E8;color:#2D5016;padding:5px 12px;border-radius:20px;font-size:13px;font-weight:700">{kr_str} billigere</span>
            <span style="background:#F7F4EF;color:#3A3A3C;padding:5px 12px;border-radius:20px;font-size:13px">Pris: {car.get('price','?')}</span>
            <span style="background:#F7F4EF;color:#3A3A3C;padding:5px 12px;border-radius:20px;font-size:13px">Snitt: {avg_str}</span>
          </div>
          {''.join(f'<p style="font-size:13px;color:#3A3A3C;margin:3px 0">{s}</p>' for s in car.get('signals',[])[:3])}
          <a href="{car.get('url','#')}" style="display:inline-block;margin-top:12px;background:#2D5016;color:#fff;padding:10px 20px;border-radius:8px;text-decoration:none;font-size:13px;font-weight:600">Se annonse på Finn.no →</a>
        </div>"""

    body_html += f"""
      <p style="color:#8A8A8E;font-size:12px;margin-top:20px;text-align:center">
        Sendt av Oppside — {datetime.now().strftime('%d.%m.%Y kl. %H:%M')}
      </p>
      </div></div></body></html>"""

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"🚗 Oppside varsel — {len(cars)} eksepsjonell{'e' if len(cars)>1 else ''} bil{'er' if len(cars)>1 else ''} funnet"
        msg["From"] = GMAIL_USER
        msg["To"] = ALERT_EMAIL
        msg.attach(MIMEText(body_html, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_USER, GMAIL_PASSWORD)
            server.sendmail(GMAIL_USER, ALERT_EMAIL, msg.as_string())
        log.info(f"✅ E-post sendt til {ALERT_EMAIL} med {len(cars)} biler")
    except Exception as e:
        log.error(f"E-post feil: {e}")

# Lagrer sist sendte varsler for å unngå spam
_alerted_urls: set = set()

def run_car_alert_scan(lokasjon=None, fuel="2"):
    """Kjøres periodisk — scanner etter eksepsjonelle biler og varsler."""
    global _alerted_urls
    log.info("🔍 Starter bilvarsel-scanning...")
    cars = fetch_cars(lokasjon=lokasjon, fuel=fuel, page=1)
    if not cars:
        log.info("Ingen biler funnet i scanning"); return

    alert_cars = []
    for car in cars[:30]:  # analyser topp 30
        analyzed = analyze_car(car.copy(), fuel)
        url = analyzed.get("url","")
        if analyzed.get("alert_worthy") and url not in _alerted_urls:
            alert_cars.append(analyzed)
            _alerted_urls.add(url)
        time.sleep(0.3)

    if alert_cars:
        log.info(f"📧 Sender varsel med {len(alert_cars)} biler")
        send_alert_email(alert_cars)
    else:
        log.info("Ingen varselverdige biler funnet")


# ─── API ENDEPUNKTER ──────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def root():
    with open("static/index.html", encoding="utf-8") as f: return f.read()

@app.get("/search")
def search_property(
    lokasjon: Optional[str] = None,
    maks_pris: Optional[int] = None,
    min_storrelse: Optional[int] = None,
    boligtype: Optional[str] = None,
    sider: int = Query(3, ge=1, le=10),
    min_score: int = 10,
):
    area_avg = get_property_avg(lokasjon)
    all_listings = []
    for page in range(1, sider+1):
        listings = fetch_properties(lokasjon, maks_pris, min_storrelse, boligtype, page)
        if not listings: break
        all_listings.extend(listings)
        time.sleep(0.4)
    scored = [score_property(l, area_avg) for l in all_listings]
    filtered = sorted(
        [l for l in scored if l.get("score",0) >= min_score],
        key=lambda x: x.get("score",0), reverse=True
    )
    return {"count": len(filtered), "total_scraped": len(all_listings),
            "area_avg_sqm": area_avg, "listings": filtered[:60]}

@app.get("/sold")
def sold_property(lokasjon: Optional[str] = None, limit: int = 20):
    results = fetch_sold_properties(lokasjon, limit)
    avg = None
    prices = [r["pris_per_kvm"] for r in results if r.get("pris_per_kvm")]
    if prices: avg = round(sum(prices)/len(prices))
    return {"count": len(results), "avg_sqm": avg, "sales": results}

@app.get("/cars")
def search_cars(
    lokasjon: Optional[str] = None,
    maks_pris: Optional[int] = None,
    min_pris: Optional[int] = None,
    merke: Optional[str] = None,
    fuel: Optional[str] = "2",
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    max_km: Optional[int] = None,
    sider: int = Query(2, ge=1, le=5),
    background_tasks: BackgroundTasks = None,
):
    all_cars = []
    for page in range(1, sider+1):
        cars = fetch_cars(lokasjon, maks_pris, min_pris, merke, fuel, year_from, year_to, max_km, page)
        if not cars: break
        all_cars.extend(cars)
        time.sleep(0.4)

    analyzed = []
    for car in all_cars[:40]:
        analyzed.append(analyze_car(car, fuel or ""))
        time.sleep(0.3)

    # Check for alert-worthy
    alert_cars = [c for c in analyzed if c.get("alert_worthy") and c.get("url","") not in _alerted_urls]
    if alert_cars and background_tasks:
        for c in alert_cars: _alerted_urls.add(c.get("url",""))
        background_tasks.add_task(send_alert_email, alert_cars)

    sorted_cars = sorted(
        analyzed,
        key=lambda x: x.get("underpriset_pct") or -999,
        reverse=True
    )
    return {
        "count": len(sorted_cars),
        "total_scraped": len(all_cars),
        "alert_sent": len(alert_cars),
        "cars": sorted_cars[:50],
    }

@app.post("/trigger-alert-scan")
def trigger_scan(background_tasks: BackgroundTasks, fuel: str = "2", lokasjon: Optional[str] = None):
    background_tasks.add_task(run_car_alert_scan, lokasjon, fuel)
    return {"message": "Scanning startet i bakgrunnen"}

@app.get("/alert-config")
def alert_config():
    return {
        "configured": bool(GMAIL_USER and GMAIL_PASSWORD),
        "alert_email": ALERT_EMAIL,
        "threshold_pct": CAR_ALERT_PCT,
        "threshold_kr": CAR_ALERT_KR,
    }

@app.get("/health")
def health():
    return {"status": "ok", "app": "Oppside v2"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
