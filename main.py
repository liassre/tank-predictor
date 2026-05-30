import os
import time
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

APP_VERSION = "1.0-stability-real-data"
TANKERKOENIG_API_KEY = os.getenv("TANKERKOENIG_API_KEY", "").strip()
TANKERKOENIG_BASE = "https://creativecommons.tankerkoenig.de/json"

app = FastAPI(title="Tank Morgen API", version=APP_VERSION)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

CACHE: Dict[str, Dict[str, Any]] = {}
CACHE_TTL_SECONDS = 300  # be polite to Tankerkönig; keep data fresh but not spammy

DEMO_STATIONS = [
    {
        "id": "demo-aral-ludwigshafen",
        "brand": "ARAL",
        "name": "ARAL Ludwigshafen Demo",
        "address": "Mannheimer Straße 85, Ludwigshafen",
        "lat": 49.4875,
        "lng": 8.4660,
        "distance": 0.8,
        "isOpen": True,
        "prices": {"diesel": 1.629, "e10": 1.739, "e5": 1.799},
        "source": "demo",
    },
    {
        "id": "demo-shell-mannheim",
        "brand": "Shell",
        "name": "Shell Mannheim Demo",
        "address": "Bismarckstraße 10, Mannheim",
        "lat": 49.4889,
        "lng": 8.4692,
        "distance": 1.4,
        "isOpen": True,
        "prices": {"diesel": 1.649, "e10": 1.759, "e5": 1.819},
        "source": "demo",
    },
    {
        "id": "demo-jet-ludwigshafen",
        "brand": "JET",
        "name": "JET Ludwigshafen Demo",
        "address": "Frankenthaler Straße 44, Ludwigshafen",
        "lat": 49.4920,
        "lng": 8.4450,
        "distance": 2.1,
        "isOpen": True,
        "prices": {"diesel": 1.599, "e10": 1.719, "e5": 1.779},
        "source": "demo",
    },
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def cache_get(key: str) -> Optional[Any]:
    item = CACHE.get(key)
    if not item:
        return None
    if time.time() - item["ts"] > CACHE_TTL_SECONDS:
        return None
    return item["value"]


def cache_set(key: str, value: Any) -> Any:
    CACHE[key] = {"ts": time.time(), "value": value}
    return value


def safe_float(v: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if v is None or v is False:
            return default
        return float(v)
    except Exception:
        return default


def format_address(station: Dict[str, Any]) -> str:
    street = station.get("street") or ""
    house = station.get("houseNumber") or ""
    post = station.get("postCode") or ""
    place = station.get("place") or ""
    first = f"{street} {house}".strip()
    second = f"{post} {place}".strip()
    return ", ".join([x for x in [first, second] if x]) or station.get("address") or "Address unavailable"


def normalize_station(raw: Dict[str, Any]) -> Dict[str, Any]:
    # Tankerkönig list.php usually returns only the requested fuel price as "price".
    # prices.php returns all three prices by station id. We support both shapes.
    prices = {
        "diesel": safe_float(raw.get("diesel")),
        "e10": safe_float(raw.get("e10")),
        "e5": safe_float(raw.get("e5")),
    }
    requested_type = raw.get("_requestedFuel")
    list_price = safe_float(raw.get("price"))
    if requested_type in prices and prices.get(requested_type) is None and list_price is not None:
        prices[requested_type] = list_price

    return {
        "id": raw.get("id"),
        "brand": raw.get("brand") or "Station",
        "name": raw.get("name") or raw.get("brand") or "Petrol station",
        "address": format_address(raw),
        "lat": safe_float(raw.get("lat")),
        "lng": safe_float(raw.get("lng")),
        "distance": safe_float(raw.get("dist") or raw.get("distance"), 0),
        "isOpen": bool(raw.get("isOpen", True)),
        "prices": prices,
        "source": "Tankerkönig / MTS-K",
    }


def merge_live_prices(stations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Fetch all E5/E10/Diesel prices for the nearby station ids and merge them.

    This is the important price fix: list.php is good for nearby search, but
    prices.php is the correct endpoint for current prices for multiple station ids.
    """
    if not stations or not TANKERKOENIG_API_KEY:
        return stations

    ids = [s.get("id") for s in stations if s.get("id") and not str(s.get("id")).startswith("demo-")]
    if not ids:
        return stations

    try:
        res = requests.get(
            f"{TANKERKOENIG_BASE}/prices.php",
            params={"ids": ",".join(ids[:25]), "apikey": TANKERKOENIG_API_KEY},
            timeout=8,
        )
        res.raise_for_status()
        data = res.json()
        if not data.get("ok"):
            return stations
        live_prices = data.get("prices", {}) or {}
        for station in stations:
            station_id = station.get("id")
            price_block = live_prices.get(station_id) or {}
            if not isinstance(price_block, dict):
                continue

            station["isOpen"] = bool(price_block.get("status") == "open" or price_block.get("isOpen") is True)
            merged = dict(station.get("prices") or {})
            for fuel_key in ["diesel", "e10", "e5"]:
                value = safe_float(price_block.get(fuel_key))
                if value is not None:
                    merged[fuel_key] = value
            station["prices"] = merged
        return stations
    except Exception:
        # Keep nearby stations even if the secondary price request fails.
        return stations


def get_tankerkoenig_stations(lat: float, lng: float, radius: float, fuel: str) -> Dict[str, Any]:
    if not TANKERKOENIG_API_KEY:
        return {"ok": False, "error": "missing_api_key", "stations": DEMO_STATIONS, "source": "demo"}

    radius = max(1.0, min(float(radius), 25.0))
    fuel = fuel if fuel in {"diesel", "e5", "e10", "all"} else "diesel"
    cache_key = f"tk:list:{lat:.3f}:{lng:.3f}:{radius:.1f}:{fuel}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    try:
        res = requests.get(
            f"{TANKERKOENIG_BASE}/list.php",
            params={
                "lat": lat,
                "lng": lng,
                "rad": radius,
                "sort": "dist",
                "type": fuel if fuel != "all" else "all",
                "apikey": TANKERKOENIG_API_KEY,
            },
            timeout=8,
        )
        res.raise_for_status()
        data = res.json()
        if not data.get("ok"):
            return {"ok": False, "error": data.get("message") or data.get("status") or "tankerkoenig_error", "stations": DEMO_STATIONS, "source": "demo"}
        raw_stations = []
        for item in data.get("stations", []):
            if item.get("id"):
                item["_requestedFuel"] = fuel if fuel != "all" else "diesel"
                raw_stations.append(item)
        stations = [normalize_station(s) for s in raw_stations]
        stations = merge_live_prices(stations)
        payload = {
            "ok": True,
            "stations": stations[:20] if stations else DEMO_STATIONS,
            "source": "Tankerkönig / MTS-K" if stations else "demo",
            "license": data.get("license", "CC BY 4.0 - Tankerkönig"),
            "updatedAt": now_iso(),
        }
        return cache_set(cache_key, payload)
    except Exception as e:
        return {"ok": False, "error": str(e), "stations": DEMO_STATIONS, "source": "demo"}


def weekday_signal() -> Dict[str, Any]:
    # Early heuristic. Later replace with actual station history.
    day = datetime.now().weekday()
    if day in [1, 2]:
        return {"label": "Mildly favorable", "direction": "down", "score": -0.10}
    if day in [4, 5]:
        return {"label": "Travel demand risk", "direction": "up", "score": 0.12}
    return {"label": "Neutral", "direction": "neutral", "score": 0.0}


def get_market_signals() -> Dict[str, Any]:
    cache_key = "market:v1"
    cached = cache_get(cache_key)
    if cached:
        return cached

    signals: List[Dict[str, Any]] = []
    pressure = 0.0

    # EUR/USD: free no-key via Frankfurter.
    try:
        r = requests.get("https://api.frankfurter.app/latest", params={"from": "EUR", "to": "USD"}, timeout=5)
        r.raise_for_status()
        fx = r.json().get("rates", {}).get("USD")
        if fx:
            # No prior rate here; use a conservative neutral indicator.
            signals.append({"name": "EUR/USD", "status": "Live", "tone": "neutral", "text": f"EUR/USD currently {fx:.4f}. Currency impact is treated as neutral until trend history is stored."})
    except Exception:
        signals.append({"name": "EUR/USD", "status": "Fallback", "tone": "neutral", "text": "Currency signal unavailable; using neutral fallback."})

    # GDELT news count, no-key. Conservative impact only.
    try:
        q = '("oil price" OR brent OR refinery OR opec OR diesel OR fuel OR gasoline)'
        r = requests.get(
            "https://api.gdeltproject.org/api/v2/doc/doc",
            params={"query": q, "mode": "timelinevolraw", "format": "json", "timespan": "1d", "maxrecords": 75},
            timeout=6,
        )
        r.raise_for_status()
        timeline = r.json().get("timeline", [])
        count = sum(int(x.get("value", 0)) for x in timeline[:24]) if isinstance(timeline, list) else 0
        if count > 120:
            tone, text, pressure = "warning", "Elevated energy-news activity. This can increase short-term uncertainty.", pressure + 0.08
        elif count > 40:
            tone, text = "neutral", "Normal energy-news activity. No strong shock signal detected."
        else:
            tone, text, pressure = "positive", "Low energy-news pressure. No major upward news signal detected.", pressure - 0.04
        signals.append({"name": "Energy News", "status": "Live", "tone": tone, "text": text})
    except Exception:
        signals.append({"name": "Energy News", "status": "Connection issue", "tone": "neutral", "text": "Live news signal could not be reached. Using neutral fallback instead of guessing."})

    wd = weekday_signal()
    pressure += wd["score"]
    signals.append({"name": "Day pattern", "status": "Local", "tone": "positive" if wd["score"] < 0 else "warning" if wd["score"] > 0 else "neutral", "text": wd["label"]})

    if pressure <= -0.07:
        overall = {"tone": "positive", "label": "Slight downward pressure", "summary": "Current signals slightly support lower fuel prices."}
    elif pressure >= 0.07:
        overall = {"tone": "warning", "label": "Upward risk", "summary": "Current signals show some upward price risk."}
    else:
        overall = {"tone": "neutral", "label": "Neutral", "summary": "Current market signals are mixed or weak."}

    payload = {"ok": True, "updatedAt": now_iso(), "overall": overall, "signals": signals}
    return cache_set(cache_key, payload)


def make_prediction(station: Dict[str, Any], fuel: str, market: Dict[str, Any]) -> Dict[str, Any]:
    price = station.get("prices", {}).get(fuel)
    if price is None:
        price = station.get("prices", {}).get("diesel") or 0

    # Transparent heuristic for V1 real data. Later replaced with history model.
    hour = datetime.now().hour
    market_tone = market.get("overall", {}).get("tone", "neutral")
    score = 0.0
    reasons_en = []
    reasons_de = []

    if hour >= 18 or hour <= 5:
        score -= 0.20
        reasons_en.append("Evening/night timing often creates opportunities for lower prices by the next morning.")
        reasons_de.append("Abend- und Nachtzeiten bieten oft Chancen auf niedrigere Preise bis morgen früh.")
    elif 6 <= hour <= 10:
        score += 0.08
        reasons_en.append("Morning prices can already include part of the overnight adjustment.")
        reasons_de.append("Morgenpreise können einen Teil der nächtlichen Anpassung bereits enthalten.")

    if market_tone == "positive":
        score -= 0.10
        reasons_en.append("Market signals currently show slight downward pressure.")
        reasons_de.append("Die Marktsignale zeigen aktuell leichten Abwärtsdruck.")
    elif market_tone == "warning":
        score += 0.12
        reasons_en.append("Market signals show some upward price risk.")
        reasons_de.append("Die Marktsignale zeigen ein gewisses Risiko für steigende Preise.")
    else:
        reasons_en.append("Market signals are neutral, so local station behavior matters more.")
        reasons_de.append("Die Marktsignale sind neutral, daher ist das lokale Tankstellenverhalten wichtiger.")

    # Use relative current price among station's fuels as a weak volatility proxy.
    if price >= 1.75:
        score -= 0.05
        reasons_en.append("The current price is relatively high, which can leave room for a correction.")
        reasons_de.append("Der aktuelle Preis ist relativ hoch, was Raum für eine Korrektur lassen kann.")

    direction = "lower" if score <= 0 else "higher"
    confidence = int(max(55, min(76, 62 + abs(score) * 55)))
    delta_per_liter = round(max(0.012, min(0.055, 0.018 + abs(score) * 0.06)), 3)
    return {
        "direction": direction,
        "confidence": confidence,
        "expectedDeltaPerLiter": delta_per_liter,
        "reasons": reasons_en[:4],
        "reasonsDe": reasons_de[:4],
        "model": "V1 heuristic: live station price + time pattern + market signals",
    }


@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "version": APP_VERSION,
        "tankersKeyConfigured": bool(TANKERKOENIG_API_KEY),
        "time": now_iso(),
    }


@app.get("/stations/nearby")
def nearby_stations(
    lat: float = Query(49.4875),
    lng: float = Query(8.4660),
    radius: float = Query(5.0, ge=1, le=25),
    fuel: str = Query("diesel"),
) -> Dict[str, Any]:
    return get_tankerkoenig_stations(lat, lng, radius, fuel)


@app.get("/market-signals")
def market_signals() -> Dict[str, Any]:
    return get_market_signals()


@app.get("/dashboard")
def dashboard(
    lat: float = Query(49.4875),
    lng: float = Query(8.4660),
    radius: float = Query(5.0, ge=1, le=25),
    fuel: str = Query("diesel"),
    station_id: Optional[str] = Query(None),
) -> Dict[str, Any]:
    fuel = fuel if fuel in {"diesel", "e10", "e5"} else "diesel"
    stations_payload = get_tankerkoenig_stations(lat, lng, radius, fuel)
    market = get_market_signals()
    stations = stations_payload.get("stations", DEMO_STATIONS)
    selected = next((s for s in stations if s.get("id") == station_id), stations[0] if stations else DEMO_STATIONS[0])
    prediction = make_prediction(selected, fuel, market)
    return {
        "ok": True,
        "version": APP_VERSION,
        "liveStationData": stations_payload.get("source") != "demo",
        "dataSource": stations_payload.get("source", "demo"),
        "license": stations_payload.get("license"),
        "updatedAt": now_iso(),
        "stations": stations,
        "selectedStation": selected,
        "fuel": fuel,
        "prediction": prediction,
        "market": market,
        "trust": {
            "stationData": stations_payload.get("source", "demo"),
            "marketData": "Frankfurter + GDELT + local rules",
            "confidenceNote": "Confidence reflects signal strength, not certainty. We avoid unrealistic 90–100% claims.",
        },
    }
