from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

import requests
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Tank Morgen API", version="9.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

STATIONS = [
    {"id":"aral-berlin-demo","brand":"ARAL","name":"ARAL Berlin Mitte","address":"Invalidenstraße 55, Berlin","distanceKm":0.8,"prices":{"diesel":1.629,"e10":1.739,"e5":1.799},"prediction":"lower","confidence":64,"expectedDeltaCents":-4.8},
    {"id":"jet-berlin-demo","brand":"JET","name":"JET Berlin Friedrichshain","address":"Frankfurter Allee 88, Berlin","distanceKm":2.1,"prices":{"diesel":1.599,"e10":1.719,"e5":1.779},"prediction":"lower","confidence":71,"expectedDeltaCents":-5.5},
    {"id":"shell-berlin-demo","brand":"Shell","name":"Shell Berlin Prenzlauer Berg","address":"Prenzlauer Allee 120, Berlin","distanceKm":1.4,"prices":{"diesel":1.649,"e10":1.759,"e5":1.819},"prediction":"higher","confidence":58,"expectedDeltaCents":3.2},
    {"id":"esso-berlin-demo","brand":"Esso","name":"Esso Berlin Charlottenburg","address":"Kantstraße 145, Berlin","distanceKm":3.5,"prices":{"diesel":1.639,"e10":1.749,"e5":1.809},"prediction":"lower","confidence":61,"expectedDeltaCents":-2.9},
]

REASONS = {
    "lower": {
        "en": ["This station often lowers prices overnight.", "Nearby competition is putting downward pressure on prices.", "The current price is above the recent local average.", "Market pressure is not strongly bullish right now."],
        "de": ["Diese Tankstelle senkt nachts häufig die Preise.", "Der Wettbewerb in der Nähe drückt die Preise eher nach unten.", "Der aktuelle Preis liegt über dem jüngsten lokalen Durchschnitt.", "Der Marktdruck ist aktuell nicht stark bullisch."],
    },
    "higher": {
        "en": ["The price already dropped earlier today.", "Nearby stations are slightly more expensive.", "Tomorrow morning signal is weak.", "Market pressure is neutral to slightly upward."],
        "de": ["Der Preis ist heute bereits gefallen.", "Tankstellen in der Nähe sind leicht teurer.", "Das Signal für morgen früh ist schwach.", "Der Marktdruck ist neutral bis leicht steigend."],
    },
}

class PredictRequest(BaseModel):
    stationId: str | None = None
    fuelType: Literal["diesel", "e10", "e5"] = "diesel"
    tankLiters: float = 50

_MARKET_CACHE = {"at": None, "data": None}

def fallback_market() -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "overall": "neutral",
        "summary": {"en": "No strong market direction right now.", "de": "Aktuell keine starke Marktrichtung."},
        "signals": [
            {"label":"Oil pressure", "value":"fallback", "mood":"neutral", "live":False},
            {"label":"EUR/USD", "value":1.085, "mood":"neutral", "live":False},
            {"label":"Energy news", "value":"fallback", "mood":"low", "live":False},
            {"label":"Day pattern", "value":datetime.now().strftime("%A"), "mood":"neutral", "live":True},
        ],
        "updatedAt": now,
        "fast": True,
    }

def fetch_eur_usd() -> dict:
    try:
        r = requests.get("https://api.frankfurter.app/latest?from=EUR&to=USD", timeout=2.2)
        r.raise_for_status()
        rate = float(r.json()["rates"]["USD"])
        mood = "neutral"
        if rate >= 1.11: mood = "downward"
        elif rate <= 1.07: mood = "upward"
        return {"label":"EUR/USD", "value":round(rate,4), "mood":mood, "live":True}
    except Exception:
        return {"label":"EUR/USD", "value":1.085, "mood":"neutral", "live":False}

def fetch_news_pressure() -> dict:
    try:
        url = "https://api.gdeltproject.org/api/v2/doc/doc"
        params = {"query":"oil OR brent OR refinery OR opec OR diesel", "mode":"artlist", "format":"json", "maxrecords":8, "sort":"hybridrel"}
        r = requests.get(url, params=params, timeout=2.5)
        r.raise_for_status()
        count = len(r.json().get("articles", []))
        pressure = "low" if count < 3 else "medium" if count < 7 else "high"
        return {"label":"Energy news", "value":f"{count} recent items", "mood":pressure, "live":True}
    except Exception:
        return {"label":"Energy news", "value":"fallback", "mood":"low", "live":False}

def day_effect() -> dict:
    weekday = datetime.now().weekday()
    mood = "slightly_downward" if weekday in (0,1,2) else "slightly_upward" if weekday in (4,5) else "neutral"
    return {"label":"Day pattern", "value":datetime.now().strftime("%A"), "mood":mood, "live":True}

def live_market() -> dict:
    now = datetime.now(timezone.utc)
    cached_at = _MARKET_CACHE.get("at")
    if cached_at and _MARKET_CACHE.get("data") and (now - cached_at).total_seconds() < 900:
        return _MARKET_CACHE["data"]
    oil = {"label":"Oil pressure", "value":"proxy", "mood":"neutral", "live":False}
    eur = fetch_eur_usd()
    news = fetch_news_pressure()
    day = day_effect()
    score = 0
    for s in [oil, eur, news, day]:
        mood = s["mood"]
        if "downward" in mood or mood == "low": score -= 1
        if "upward" in mood or mood == "high": score += 1
    if score <= -2:
        overall, en, de = "downward", "Slight downward pressure on fuel prices.", "Leichter Druck nach unten auf Kraftstoffpreise."
    elif score >= 2:
        overall, en, de = "upward", "Slight upward pressure on fuel prices.", "Leichter Druck nach oben auf Kraftstoffpreise."
    else:
        overall, en, de = "neutral", "No strong market direction right now.", "Aktuell keine starke Marktrichtung."
    data = {"overall":overall, "summary":{"en":en,"de":de}, "signals":[oil, eur, news, day], "updatedAt":now.isoformat(), "fast":False}
    _MARKET_CACHE["at"] = now
    _MARKET_CACHE["data"] = data
    return data

@app.get("/health")
def health(): return {"status":"ok", "version":"9.0.0"}

@app.get("/dashboard")
def dashboard():
    # Fast first paint: never waits for slow news APIs. Mobile can refresh market later.
    cached = _MARKET_CACHE.get("data") or fallback_market()
    return {"source":"demo_until_tankerkoenig_key", "stations":STATIONS, "market":cached, "loadedAt":datetime.now(timezone.utc).isoformat()}

@app.get("/stations/nearby")
def stations_nearby(lat: float | None = None, lng: float | None = None):
    return {"source":"demo_until_tankerkoenig_key", "stations":STATIONS}

@app.post("/predict/station")
def predict_station(req: PredictRequest):
    station = next((s for s in STATIONS if s["id"] == req.stationId), STATIONS[0])
    prediction = station["prediction"]
    delta = float(station["expectedDeltaCents"])
    savings = abs(delta) / 100 * max(req.tankLiters, 1)
    return {"stationId":station["id"], "fuelType":req.fuelType, "direction":prediction, "confidence":station["confidence"], "expectedDeltaCents":delta, "tankLiters":req.tankLiters, "estimatedMoneyImpact":round(savings,2), "reasons":REASONS[prediction], "disclaimer":{"en":"Prediction is an estimate, not a guarantee.", "de":"Die Prognose ist eine Einschätzung, keine Garantie."}}

@app.get("/market-signals")
def market_signals(): return live_market()

@app.get("/how-it-works")
def how_it_works():
    return {"en":"Tank Morgen combines station price cycles, nearby competition, time of day, market trends and major news signals. Market/news signals are useful, but they usually affect fuel prices with a delay, so confidence scores stay realistic.", "de":"Tank Morgen kombiniert Preiszyklen der Tankstelle, Wettbewerb in der Nähe, Tageszeit, Markttrends und wichtige Nachrichten. Markt- und Nachrichtensignale sind nützlich, wirken aber oft mit Verzögerung auf Kraftstoffpreise. Deshalb bleiben die Vertrauenswerte realistisch."}
