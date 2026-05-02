"""
Oppside — Finn oppussingsobjekter med potensial
"""

import os, re, time
import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from typing import Optional

app = FastAPI(title="Oppside")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept-Language": "nb-NO,nb;q=0.9,no;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

OPPUSSING_STRONG = [
    "oppussingsobjekt", "oppussing", "renovering", "renoveringsbehov",
    "rehabilitering", "totalrenovering", "selges as is", "as is",
    "selges uten garanti", "uten garanti", "oppussingsprosjekt",
    "rivningsobjekt", "settes i stand", "modernisering",
    "rehabiliteringsprosjekt", "selges uoppusset", "uoppusset",
    "i original stand", "oppgraderes", "totaloppussing",
]

OPPUSSING_WEAK = [
    "potensial", "mye potensiale", "oppussingsbehov", "noe oppussing",
    "noe oppussingsbehov", "litt slitt", "enkel standard", "mod.behov",
    "moderniseringsbehov", "slitt", "gammel", "trenger",
    "behov for", "mulighet for", "kan oppgraderes", "godt potensiale",
    "stort potensial", "stor oppside", "utrolig potensial",
]

POSITIVE = [
    "hjørne", "toppetasje", "penthouse", "utsikt", "sjøutsikt",
    "sørvest", "sørvendt", "vestvendt", "balkong", "terrasse",
    "takterrasse", "heis", "garasje", "parkering", "sentralt",
    "strand", "strandlinje", "innglasset",
]


def parse_price(text):
    if not text: return None
    cleaned = re.sub(r"[^\d]", "", str(text))
    return int(cleaned) if cleaned else None

def parse_size(text):
    if not text: return None
    m = re.search(r"(\d+)\s*m²", str(text))
    return int(m.group(1)) if m else None

def parse_article(art):
    result = {}
    h2 = art.find("h2")
    if h2:
        a = h2.find("a")
        result["title"] = (a or h2).get_text(strip=True)

    link = art.find("a", class_=lambda x: x and "sf-search-ad-link" in x)
    if link:
        href = link.get("href", "")
        result["url"] = href if href.startswith("http") else f"https://www.finn.no{href}"
        m = re.search(r"finnkode=(\d+)", href)
        result["finnkode"] = m.group(1) if m else None

    img = art.find("img", alt="Bilde 1 av annonsen")
    result["image"] = img.get("src", "").replace("480w", "640w") if img else ""

    price_block = art.find("div", class_=lambda x: x and "justify-between" in x if x else False)
    if price_block:
        for span in price_block.find_all("span"):
            txt = span.get_text(strip=True)
            if "m²" in txt:
                result["size"] = txt
                result["size_int"] = parse_size(txt)
            elif "kr" in txt:
                result["price"] = txt
                result["price_int"] = parse_price(txt)

    loc_div = art.find("div", class_=lambda x: x and "sf-realestate-location" in x)
    result["location"] = loc_div.get_text(strip=True) if loc_div else ""

    for d in art.find_all("div", class_=lambda x: x and "s-text-subtle" in x if x else False):
        txt = d.get_text(strip=True)
        if "Totalpris" in txt:
            result["total_price_info"] = txt
            m = re.search(r"Totalpris:\s*([\d\s\xa0]+)\s*kr", txt)
            if m: result["total_price_int"] = parse_price(m.group(1))
            for eierform in ["Selveier", "Andel", "Aksje"]:
                if eierform in txt:
                    result["eierform"] = eierform
                    result["property_info"] = txt
                    break

    return result

def parse_sold_article(art):
    result = {}
    h2 = art.find("h2")
    if h2:
        result["title"] = h2.get_text(strip=True)

    link = art.find("a")
    if link:
        href = link.get("href", "")
        result["url"] = href if href.startswith("http") else f"https://www.finn.no{href}"

    img = art.find("img")
    result["image"] = img.get("src", "") if img else ""

    loc_div = art.find("div", class_=lambda x: x and "location" in x.lower() if x else False)
    result["location"] = loc_div.get_text(strip=True) if loc_div else ""

    all_text = art.get_text(" ", strip=True)

    price_m = re.search(r"Solgt for\s*([\d\s\xa0]+)\s*kr", all_text)
    if not price_m:
        price_m = re.search(r"([\d\s\xa0]{6,})\s*kr", all_text)
    if price_m:
        result["sold_price"] = parse_price(price_m.group(1))

    size_m = re.search(r"(\d+)\s*m²", all_text)
    if size_m:
        result["size_int"] = int(size_m.group(1))
        result["size"] = f"{size_m.group(1)} m²"

    date_m = re.search(r"Solgt\s+(\d{1,2}\.\s*\w+\s*\d{4}|\d{4})", all_text)
    result["sold_date"] = date_m.group(1) if date_m else ""

    if result.get("sold_price") and result.get("size_int") and result["size_int"] > 0:
        result["pris_per_kvm"] = round(result["sold_price"] / result["size_int"])

    return result

def score_listing(listing, area_avg_sqm=None):
    title = (listing.get("title") or "").lower()
    desc = (listing.get("property_info") or "").lower()
    score = 0
    reasons = []

    for kw in OPPUSSING_STRONG:
        if kw in title:
            score += 30
            reasons.append(f"🔨 Sterkt signal: «{kw}»")
            break

    for kw in OPPUSSING_WEAK:
        if kw in title:
            score += 15
            reasons.append(f"⚠️ Potensial: «{kw}»")
            break

    price = listing.get("price_int")
    size = listing.get("size_int")

    if price and size and size > 10:
        pris_sqm = price / size
        listing["pris_per_kvm"] = round(pris_sqm)
        if area_avg_sqm and pris_sqm < area_avg_sqm:
            rabatt = (1 - pris_sqm / area_avg_sqm) * 100
            if rabatt > 30:
                score += 25
                reasons.append(f"💰 {rabatt:.0f}% under snittet ({pris_sqm:,.0f} vs {area_avg_sqm:,.0f} kr/m²)")
            elif rabatt > 15:
                score += 15
                reasons.append(f"💰 {rabatt:.0f}% under snittet ({pris_sqm:,.0f} kr/m²)")
            elif rabatt > 5:
                score += 8
                reasons.append(f"💰 Noe under snittet ({pris_sqm:,.0f} kr/m²)")
        if pris_sqm < 20000:
            score += 20
            reasons.append(f"💎 Svært lav kvm-pris: {pris_sqm:,.0f} kr/m²")
        elif pris_sqm < 30000:
            score += 10
            reasons.append(f"💡 Lav kvm-pris: {pris_sqm:,.0f} kr/m²")

    pos = [kw for kw in POSITIVE if kw in title]
    if pos:
        score += len(pos) * 3
        reasons.append(f"✨ {', '.join(pos)}")

    if "selveier" in desc:
        score += 5
        reasons.append("🏠 Selveier")

    listing["score"] = score
    listing["reasons"] = reasons
    return listing

def fetch_listings(lokasjon=None, maks_pris=None, min_storrelse=None, boligtype=None, page=1):
    params = {"sort": "PRICE_SQM_ASC", "is_new_property": "false", "page": str(page)}
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
        return [parse_article(a) for a in arts if parse_article(a).get("title")]
    except Exception as e:
        print(f"Fetch error: {e}")
        return []

def fetch_sold(lokasjon=None, limit=20):
    params = {"sort": "PUBLISHED_DESC"}
    if lokasjon: params["location"] = lokasjon
    try:
        r = requests.get("https://www.finn.no/realestate/sold/search.html",
                         headers=HEADERS, params=params, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        arts = soup.find_all("article")
        results = []
        for a in arts[:limit]:
            parsed = parse_sold_article(a)
            if parsed.get("title") and parsed.get("sold_price"):
                results.append(parsed)
        return results
    except Exception as e:
        print(f"Sold fetch error: {e}")
        return []

def get_area_avg(lokasjon=None):
    params = {"sort": "RELEVANCE", "is_new_property": "false"}
    if lokasjon: params["location"] = lokasjon
    try:
        r = requests.get("https://www.finn.no/realestate/homes/search.html",
                         headers=HEADERS, params=params, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        arts = soup.find_all("article", class_=lambda x: x and "sf-search-ad" in x)
        prices = []
        for art in arts[:30]:
            l = parse_article(art)
            p, s = l.get("price_int"), l.get("size_int")
            if p and s and s > 10:
                prices.append(p / s)
        return round(sum(prices) / len(prices)) if len(prices) >= 5 else None
    except:
        return None


@app.get("/", response_class=HTMLResponse)
def root():
    with open("static/index.html", encoding="utf-8") as f:
        return f.read()

@app.get("/search")
def search(
    lokasjon: Optional[str] = None,
    maks_pris: Optional[int] = None,
    min_storrelse: Optional[int] = None,
    boligtype: Optional[str] = None,
    sider: int = Query(3, ge=1, le=10),
    min_score: int = 10,
):
    area_avg = get_area_avg(lokasjon)
    all_listings = []
    for page in range(1, sider + 1):
        listings = fetch_listings(lokasjon, maks_pris, min_storrelse, boligtype, page)
        if not listings: break
        all_listings.extend(listings)
        time.sleep(0.4)

    scored = [score_listing(l, area_avg) for l in all_listings]
    filtered = sorted(
        [l for l in scored if l.get("score", 0) >= min_score],
        key=lambda x: x.get("score", 0), reverse=True
    )
    return {
        "count": len(filtered),
        "total_scraped": len(all_listings),
        "area_avg_sqm": area_avg,
        "listings": filtered[:60],
    }

@app.get("/sold")
def sold(lokasjon: Optional[str] = None, limit: int = 20):
    results = fetch_sold(lokasjon, limit)
    avg = None
    prices = [r["pris_per_kvm"] for r in results if r.get("pris_per_kvm")]
    if prices:
        avg = round(sum(prices) / len(prices))
    return {"count": len(results), "avg_sqm": avg, "sales": results}

@app.get("/health")
def health():
    return {"status": "ok", "app": "Oppside"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
