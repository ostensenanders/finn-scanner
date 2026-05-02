"""
Finn.no Oppussingsobjekt-scanner
Full-stack app: backend + frontend i én server
"""

import os, re, time
import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from typing import Optional

app = FastAPI(title="Finn-scanner")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static files (frontend)
app.mount("/static", StaticFiles(directory="static"), name="static")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "nb-NO,nb;q=0.9",
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
    "moderniseringsbehov", "slitt", "gammel", "original", "trenger",
    "behov for", "mulighet for", "kan oppgraderes", "godt potensiale",
    "stort potensial", "stor oppside", "utrolig potensial",
]

POSITIVE = [
    "hjørne", "toppetasje", "penthouse", "utsikt", "sjøutsikt",
    "sørvest", "sørvendt", "vestvendt", "balkong", "terrasse",
    "takterrasse", "heis", "garasje", "parkering", "sentralt",
    "strand", "strandlinje", "innglasset", "rooftop",
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

    org = art.find("span", class_=lambda x: x and "whitespace-normal" in x if x else False)
    result["megler"] = org.get_text(strip=True) if org else ""

    visning = art.find("span", class_=lambda x: x and "rounded-full" in x if x else False)
    result["visning"] = visning.get_text(strip=True) if visning else ""

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
            reasons.append(f"⚠️ Oppussingssignal: «{kw}»")
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
        reasons.append(f"✨ Positive trekk: {', '.join(pos)}")

    if "selveier" in desc:
        score += 5
        reasons.append("🏠 Selveier")

    listing["score"] = score
    listing["reasons"] = reasons
    return listing

def fetch_page(url, params):
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        arts = soup.find_all("article", class_=lambda x: x and "sf-search-ad" in x)
        return [parse_article(a) for a in arts if parse_article(a).get("title")]
    except Exception as e:
        print(f"Fetch error: {e}")
        return []

def get_area_avg(lokasjon=None):
    params = {"sort": "RELEVANCE", "is_new_property": "false"}
    if lokasjon: params["location"] = lokasjon
    try:
        r = requests.get("https://www.finn.no/realestate/homes/search.html",
                        headers=HEADERS, params=params, timeout=15)
        soup = BeautifulSoup(r.text, "lxml")
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

    base = {"sort": "PRICE_SQM_ASC", "is_new_property": "false"}
    if lokasjon: base["location"] = lokasjon
    if maks_pris: base["price_to"] = str(maks_pris)
    if min_storrelse: base["area_from"] = str(min_storrelse)
    if boligtype: base["property_type"] = boligtype

    all_listings = []
    for page in range(1, sider + 1):
        listings = fetch_page(
            "https://www.finn.no/realestate/homes/search.html",
            {**base, "page": str(page)}
        )
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

@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
