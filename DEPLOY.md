# 🚀 Deploy Finn-scanner til internett (gratis)

Følg disse stegene én gang — etterpå åpner du bare en URL i nettleseren.

---

## Alternativ 1: Railway (anbefalt — enklest)

### Steg 1 — GitHub-konto
Gå til **github.com** og lag gratis konto om du ikke har det.

### Steg 2 — Last opp filene til GitHub
1. Gå til **github.com/new** og lag et nytt repository, f.eks. `finn-scanner`
2. Klikk **"uploading an existing file"**
3. Dra alle fire filene inn:
   - `main.py`
   - `requirements.txt`
   - `Procfile`
   - `static/index.html` (last opp til mappen `static/`)
4. Klikk **"Commit changes"**

### Steg 3 — Deploy på Railway
1. Gå til **railway.app** og logg inn med GitHub
2. Klikk **"New Project" → "Deploy from GitHub repo"**
3. Velg `finn-scanner` repositoryet ditt
4. Railway oppdager automatisk Python og deployer

### Steg 4 — Få din URL
1. I Railway, gå til prosjektet → **Settings → Networking**
2. Klikk **"Generate Domain"**
3. Du får en URL som `https://finn-scanner-production.up.railway.app`

**Ferdig!** Del URL-en med hvem du vil.

---

## Alternativ 2: Render (100% gratis, litt treigere)

1. Gå til **render.com** → logg inn med GitHub
2. **New → Web Service → Connect repository**
3. Velg `finn-scanner`
4. Fyll inn:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python main.py`
5. Klikk **Deploy**
6. Får URL som `https://finn-scanner.onrender.com`

⚠️ Render gratis-tier sover etter 15 min inaktivitet — første søk tar ~30 sek å vekke.

---

## Pris

| Tjeneste | Pris | Kommentar |
|---|---|---|
| Railway | Gratis $5 kreditt/mnd | Holder for lite trafikk |
| Render | 100% gratis | Sover ved inaktivitet |
| GitHub | Gratis | Nødvendig for begge |

---

## Oppdatering

Når du vil oppdatere siden: bare last opp nye filer til GitHub, så re-deployer Railway/Render automatisk.
