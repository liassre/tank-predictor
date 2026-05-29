from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

import httpx
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Tank Morgen API", description="Germany-first fuel station and prediction API.", version="4.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

DEMO_STATIONS = [
    {"id":"aral-berlin-demo","brand":"ARAL","name":"ARAL Berlin Mitte","address":"Invalidenstraße 55, Berlin","distance":"0.8 km","lat":52.5321,"lng":13.3849,"prices":{"diesel":1.629,"e10":1.739,"e5":1.799},"prediction":"lower","confidence":64,"reasons":["This station often lowers prices overnight.","Nearby competitors reduced prices today.","Current price is above the daily average.","Market signal is neutral to slightly lower."],"reasonsDe":["Diese Tankstelle senkt nachts häufig die Preise.","Nahegelegene Wettbewerber haben heute die Preise gesenkt.","Der aktuelle Preis liegt über dem Tagesdurchschnitt.","Das Marktsignal ist neutral bis leicht fallend."]},
    {"id":"shell-berlin-demo","brand":"Shell","name":"Shell Berlin Prenzlauer Berg","address":"Prenzlauer Allee 120, Berlin","distance":"1.4 km","lat":52.5392,"lng":13.4247,"prices":{"diesel":1.649,"e10":1.759,"e5":1.819},"prediction":"higher","confidence":58,"reasons":["Price already dropped earlier today.","Competitor prices are slightly higher nearby.","Tomorrow morning signal is weak.","Market signal is neutral."],"reasonsDe":["Der Preis ist heute bereits gefallen.","Wettbewerber in der Nähe sind leicht teurer.","Das Signal für morgen früh ist schwach.","Das Marktsignal ist neutral."]},
    {"id":"jet-berlin-demo","brand":"JET","name":"JET Berlin Friedrichshain","address":"Frankfurter Allee 88, Berlin","distance":"2.1 km","lat":52.5146,"lng":13.4673,"prices":{"diesel":1.599,"e10":1.719,"e5":1.779},"prediction":"lower","confidence":71,"reasons":["This station shows strong evening-to-morning price cycles.","Nearby competition is pushing prices down.","Diesel price is still above its recent low.","Short-term market pressure is not strongly bullish."],"reasonsDe":["Diese Tankstelle zeigt starke Preiszyklen von Abend zu Morgen.","Der Wettbewerb in der Nähe drückt die Preise.","Der Dieselpreis liegt noch über dem jüngsten Tief.","Der kurzfristige Marktdruck ist nicht stark bullisch."]},
]



def pct_change(old: Optional[float], new: Optional[float]) -> Optional[float]:
    if old in (None, 0) or new is None:
        return None
    return round(((new - old) / old) * 100, 2)

async def fetch_yahoo_chart(symbol: str) -> Optional[Dict]:
    """Lightweight public market check. No key required, but it can fail, so we always fall back safely."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {"range": "5d", "interval": "1d"}
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        async with httpx.AsyncClient(timeout=8, headers=headers) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            result = r.json()["chart"]["result"][0]
        closes = [x for x in result.get("indicators", {}).get("quote", [{}])[0].get("close", []) if x is not None]
        if len(closes) < 2:
            return None
        return {"symbol": symbol, "latest": round(closes[-1], 2), "previous": round(closes[-2], 2), "change_pct": pct_change(closes[-2], closes[-1])}
    except Exception:
        return None

async def fetch_eur_usd() -> Optional[Dict]:
    """EUR/USD from Frankfurter. No API key required."""
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=10)
    url = f"https://api.frankfurter.dev/v1/{start}..{end}"
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(url, params={"from": "EUR", "to": "USD"})
            r.raise_for_status()
            data = r.json().get("rates", {})
        points = [(d, v.get("USD")) for d, v in sorted(data.items()) if v.get("USD")]
        if len(points) < 2:
            return None
        return {"latest": round(points[-1][1], 4), "previous": round(points[-2][1], 4), "change_pct": pct_change(points[-2][1], points[-1][1]), "date": points[-1][0]}
    except Exception:
        return None

async def fetch_energy_news_pressure() -> Optional[Dict]:
    """Simple open news-pressure signal using GDELT DOC API. No key required."""
    query = '(oil OR brent OR diesel OR refinery OR OPEC OR fuel) (Germany OR Europe OR EU)'
    url = "https://api.gdeltproject.org/api/v2/doc/doc"
    params = {"query": query, "mode": "artlist", "format": "json", "maxrecords": 15, "sort": "hybridrel"}
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            articles = r.json().get("articles", [])
        titles = [a.get("title", "") for a in articles[:5] if a.get("title")]
        risk_words = ["strike", "sanction", "war", "attack", "shutdown", "shortage", "disruption", "opec", "refinery"]
        joined = " ".join(titles).lower()
        hits = sum(1 for w in risk_words if w in joined)
        pressure = "elevated" if hits >= 2 else "normal"
        return {"count": len(articles), "pressure": pressure, "headlines": titles[:3]}
    except Exception:
        return None

async def build_live_market_signals() -> Dict:
    brent, wti, eurusd, news = await fetch_yahoo_chart("BZ=F"), await fetch_yahoo_chart("CL=F"), await fetch_eur_usd(), await fetch_energy_news_pressure()
    signals = []
    score = 50
    live_parts = 0

    if brent:
        live_parts += 1
        ch = brent["change_pct"] or 0
        impact = "lower" if ch < -0.8 else "higher" if ch > 0.8 else "neutral"
        score += -8 if impact == "lower" else 8 if impact == "higher" else 0
        signals.append({"name":"Brent crude trend","nameDe":"Brent-Öl Trend","status":f"{ch:+.2f}% latest move","statusDe":f"{ch:+.2f}% letzte Bewegung","impact":impact,"note":f"Latest Brent reference: {brent['latest']}"})
    else:
        signals.append(MARKET_SIGNALS["signals"][0])

    if eurusd:
        live_parts += 1
        ch = eurusd["change_pct"] or 0
        # EUR stronger makes USD-priced oil slightly cheaper in EUR terms
        impact = "lower" if ch > 0.25 else "higher" if ch < -0.25 else "neutral"
        score += -4 if impact == "lower" else 4 if impact == "higher" else 0
        signals.append({"name":"EUR/USD","nameDe":"EUR/USD","status":f"{ch:+.2f}% daily move","statusDe":f"{ch:+.2f}% Tagesbewegung","impact":impact,"note":f"EUR/USD {eurusd['latest']} on {eurusd['date']}"})
    else:
        signals.append(MARKET_SIGNALS["signals"][1])

    if news:
        live_parts += 1
        impact = "higher" if news["pressure"] == "elevated" else "neutral"
        score += 7 if impact == "higher" else 0
        signals.append({"name":"Energy news pressure","nameDe":"Energie-News Druck","status":f"{news['pressure']} • {news['count']} recent articles checked","statusDe":f"{news['pressure']} • {news['count']} aktuelle Artikel geprüft","impact":impact,"note":"; ".join(news.get("headlines", [])[:2]) or "No major headline cluster detected."})
    else:
        signals.append(MARKET_SIGNALS["signals"][2])

    if wti:
        live_parts += 1
        ch = wti["change_pct"] or 0
        impact = "lower" if ch < -0.8 else "higher" if ch > 0.8 else "neutral"
        score += -4 if impact == "lower" else 4 if impact == "higher" else 0
        signals.append({"name":"WTI crude confirmation","nameDe":"WTI-Öl Bestätigung","status":f"{ch:+.2f}% latest move","statusDe":f"{ch:+.2f}% letzte Bewegung","impact":impact,"note":f"Latest WTI reference: {wti['latest']}"})
    else:
        signals.append(MARKET_SIGNALS["signals"][3])

    score = max(20, min(80, score))
    if score <= 45:
        overall = "slightly lower pressure"
    elif score >= 58:
        overall = "slightly higher pressure"
    else:
        overall = "neutral"
    return {"source":"live-partial" if live_parts else "demo", "overall":overall, "score":score, "updated_at":datetime.now(timezone.utc).isoformat(), "signals":signals, "live_parts":live_parts}

MARKET_SIGNALS = {
    "source": "demo",
    "overall": "neutral",
    "score": 52,
    "updated_at": datetime.now(timezone.utc).isoformat(),
    "signals": [
        {"name":"Brent crude trend","nameDe":"Brent-Öl Trend","status":"slightly down","statusDe":"leicht fallend","impact":"lower","note":"Demo placeholder. Later this will use a real commodity data API."},
        {"name":"EUR/USD","nameDe":"EUR/USD","status":"stable","statusDe":"stabil","impact":"neutral","note":"Currency movements matter because oil is usually priced in USD."},
        {"name":"Breaking energy news","nameDe":"Wichtige Energie-Nachrichten","status":"no strong alert","statusDe":"kein starkes Signal","impact":"neutral","note":"Later this can use a news API with oil/refinery/OPEC keywords."},
        {"name":"Holiday / travel pressure","nameDe":"Feiertags- / Reiseverkehr","status":"normal","statusDe":"normal","impact":"neutral","note":"Later we can add German holidays and school vacation periods."},
    ],
}

class PredictionRequest(BaseModel):
    station_id: str
    fuel_type: str = "diesel"

def normalize_tankerkoenig_station(raw: Dict) -> Dict:
    return {"id": raw.get("id","unknown"), "brand": raw.get("brand") or "Station", "name": raw.get("name") or raw.get("brand") or "Station", "address": f"{raw.get('street','')} {raw.get('houseNumber','')}, {raw.get('place','')}".strip(" ,"), "distance": f"{float(raw.get('dist',0)):.1f} km", "lat": raw.get("lat"), "lng": raw.get("lng"), "prices": {"diesel": raw.get("diesel") or 0, "e10": raw.get("e10") or 0, "e5": raw.get("e5") or 0}, "prediction": "lower", "confidence": 61, "reasons": ["Live station data is connected.","First simple rule model is active.","Station history will improve predictions over time.","Market/news signals are prepared in the backend."], "reasonsDe": ["Live-Tankstellendaten sind verbunden.","Das erste einfache Regelmodell ist aktiv.","Stationshistorie verbessert die Prognosen mit der Zeit.","Markt- und Nachrichtensignale sind im Backend vorbereitet."]}

async def fetch_tankerkoenig(lat: float, lng: float, radius: float, fuel_type: str) -> Optional[List[Dict]]:
    api_key = os.getenv("TANKERKOENIG_API_KEY")
    if not api_key:
        return None
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get("https://creativecommons.tankerkoenig.de/json/list.php", params={"lat":lat,"lng":lng,"rad":radius,"sort":"dist","type":fuel_type,"apikey":api_key})
        r.raise_for_status()
        data = r.json()
    if not data.get("ok"):
        return None
    return [normalize_tankerkoenig_station(s) for s in data.get("stations", [])]

@app.get("/")
def root():
    return {"name":"Tank Morgen API", "status":"online", "version":"4.0.0"}

@app.get("/health")
def health():
    return {"status":"ok", "version":"4.0.0"}

@app.get("/stations/nearby")
async def nearby_stations(lat: float = Query(52.52), lng: float = Query(13.405), radius: float = Query(5.0, ge=1, le=25), fuel_type: str = Query("diesel", pattern="^(diesel|e10|e5)$")):
    live = await fetch_tankerkoenig(lat, lng, radius, fuel_type)
    if live:
        return {"source":"tankerkoenig", "stations":live, "updated_at":datetime.now(timezone.utc).isoformat()}
    return {"source":"demo", "stations":DEMO_STATIONS, "updated_at":datetime.now(timezone.utc).isoformat()}

@app.post("/predict/station")
def predict_station(payload: PredictionRequest):
    station = next((s for s in DEMO_STATIONS if s["id"] == payload.station_id), DEMO_STATIONS[0])
    return {"station_id":station["id"], "fuel_type":payload.fuel_type, "direction":station["prediction"], "confidence":station["confidence"], "reasons":station["reasons"], "reasonsDe":station["reasonsDe"], "market_signal": MARKET_SIGNALS["overall"], "updated_at":datetime.now(timezone.utc).isoformat(), "model_note":"Demo hybrid rule model. Real price history and market feeds come next."}

@app.get("/market-signals")
async def market_signals():
    return await build_live_market_signals()

@app.get("/how-it-works")
def how_it_works():
    return {"title":"How Tank Morgen Works", "important_truth":"Market/news signals are useful, but they usually affect fuel prices with a delay. For tomorrow morning, the strongest signals are often: station price cycle, nearby competition, time of day, wholesale/futures trend and major breaking news."}
