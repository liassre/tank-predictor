from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

import httpx
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(
    title="Tank Predictor API",
    description="Germany-first fuel station and price prediction API.",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DEMO_STATIONS = [
    {
        "id": "aral-berlin-demo",
        "brand": "ARAL",
        "name": "ARAL Berlin Mitte",
        "address": "Invalidenstraße 55, Berlin",
        "distance": "0.8 km",
        "lat": 52.5321,
        "lng": 13.3849,
        "prices": {"diesel": 1.629, "e10": 1.739, "e5": 1.799},
        "prediction": "lower",
        "confidence": 64,
        "reasons": [
            "This station often lowers prices overnight.",
            "Nearby competitors reduced prices today.",
            "Current price is above the daily average.",
            "No strong upward market signal detected.",
        ],
        "reasonsDe": [
            "Diese Tankstelle senkt nachts häufig die Preise.",
            "Nahegelegene Wettbewerber haben heute die Preise gesenkt.",
            "Der aktuelle Preis liegt über dem Tagesdurchschnitt.",
            "Kein starkes Aufwärtssignal am Markt erkannt.",
        ],
    },
    {
        "id": "shell-berlin-demo",
        "brand": "Shell",
        "name": "Shell Berlin Prenzlauer Berg",
        "address": "Prenzlauer Allee 120, Berlin",
        "distance": "1.4 km",
        "lat": 52.5392,
        "lng": 13.4247,
        "prices": {"diesel": 1.649, "e10": 1.759, "e5": 1.819},
        "prediction": "higher",
        "confidence": 58,
        "reasons": [
            "Price already dropped earlier today.",
            "Competitor prices are slightly higher nearby.",
            "Tomorrow morning signal is weak.",
            "Market signal is neutral.",
        ],
        "reasonsDe": [
            "Der Preis ist heute bereits gefallen.",
            "Wettbewerber in der Nähe sind leicht teurer.",
            "Das Signal für morgen früh ist schwach.",
            "Das Marktsignal ist neutral.",
        ],
    },
    {
        "id": "jet-berlin-demo",
        "brand": "JET",
        "name": "JET Berlin Friedrichshain",
        "address": "Frankfurter Allee 88, Berlin",
        "distance": "2.1 km",
        "lat": 52.5146,
        "lng": 13.4673,
        "prices": {"diesel": 1.599, "e10": 1.719, "e5": 1.779},
        "prediction": "lower",
        "confidence": 71,
        "reasons": [
            "This station shows strong evening-to-morning price cycles.",
            "Nearby competition is pushing prices down.",
            "Diesel price is still above its recent low.",
            "Short-term market pressure is not strongly bullish.",
        ],
        "reasonsDe": [
            "Diese Tankstelle zeigt starke Preiszyklen von Abend zu Morgen.",
            "Der Wettbewerb in der Nähe drückt die Preise.",
            "Der Dieselpreis liegt noch über dem jüngsten Tief.",
            "Der kurzfristige Marktdruck ist nicht stark bullisch.",
        ],
    },
]

class PredictionRequest(BaseModel):
    station_id: str
    fuel_type: str = "diesel"


def normalize_tankerkoenig_station(raw: Dict) -> Dict:
    prices = {
        "diesel": raw.get("diesel") or 0,
        "e10": raw.get("e10") or 0,
        "e5": raw.get("e5") or 0,
    }
    return {
        "id": raw.get("id", "unknown"),
        "brand": raw.get("brand") or "Station",
        "name": raw.get("name") or raw.get("brand") or "Station",
        "address": f"{raw.get('street', '')} {raw.get('houseNumber', '')}, {raw.get('place', '')}".strip(" ,"),
        "distance": f"{float(raw.get('dist', 0)):.1f} km",
        "lat": raw.get("lat"),
        "lng": raw.get("lng"),
        "prices": prices,
        "prediction": "lower",
        "confidence": 61,
        "reasons": [
            "Live station data is connected.",
            "Prediction is currently using the first simple rule model.",
            "Station history will improve as the database collects more prices.",
            "Market/news signals will be added in a later version.",
        ],
        "reasonsDe": [
            "Live-Tankstellendaten sind verbunden.",
            "Die Prognose nutzt aktuell das erste einfache Regelmodell.",
            "Die Historie wird besser, sobald die Datenbank mehr Preise sammelt.",
            "Markt- und Nachrichtensignale werden später ergänzt.",
        ],
    }

async def fetch_tankerkoenig(lat: float, lng: float, radius: float, fuel_type: str) -> Optional[List[Dict]]:
    api_key = os.getenv("TANKERKOENIG_API_KEY")
    if not api_key:
        return None
    url = "https://creativecommons.tankerkoenig.de/json/list.php"
    params = {"lat": lat, "lng": lng, "rad": radius, "sort": "dist", "type": fuel_type, "apikey": api_key}
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        data = response.json()
    if not data.get("ok"):
        return None
    return [normalize_tankerkoenig_station(s) for s in data.get("stations", [])]

@app.get("/")
def root():
    return {"name": "Tank Predictor API", "status": "online", "version": "2.0.0"}

@app.get("/health")
def health():
    return {"status": "ok", "version": "2.0.0"}

@app.get("/stations/nearby")
async def nearby_stations(
    lat: float = Query(52.52, description="Latitude, defaults to Berlin"),
    lng: float = Query(13.405, description="Longitude, defaults to Berlin"),
    radius: float = Query(5.0, ge=1, le=25),
    fuel_type: str = Query("diesel", pattern="^(diesel|e10|e5)$"),
):
    live_stations = await fetch_tankerkoenig(lat, lng, radius, fuel_type)
    if live_stations:
        return {"source": "tankerkoenig", "stations": live_stations, "updated_at": datetime.now(timezone.utc).isoformat()}
    return {"source": "demo", "stations": DEMO_STATIONS, "updated_at": datetime.now(timezone.utc).isoformat()}

@app.post("/predict/station")
def predict_station(payload: PredictionRequest):
    station = next((s for s in DEMO_STATIONS if s["id"] == payload.station_id), DEMO_STATIONS[0])
    return {
        "station_id": station["id"],
        "fuel_type": payload.fuel_type,
        "direction": station["prediction"],
        "confidence": station["confidence"],
        "reasons": station["reasons"],
        "reasonsDe": station["reasonsDe"],
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "model_note": "Demo rule model. Real historical collection and market signals come next.",
    }

@app.get("/how-it-works")
def how_it_works():
    return {
        "title": "How Tank Predictor Works",
        "important_truth": (
            "Market/news signals are useful, but they usually affect fuel prices with a delay. "
            "For tomorrow morning, the strongest signals are often: station price cycle, nearby competition, "
            "time of day, wholesale/futures trend and major breaking news."
        ),
    }
