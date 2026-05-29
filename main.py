from __future__ import annotations

from datetime import datetime, timezone
from statistics import mean
from typing import List, Optional

from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

app = FastAPI(
    title="Tank Predictor API",
    description="Simple fuel-price direction predictor MVP.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DEMO_PRICES = [
    1.829, 1.819, 1.809, 1.799, 1.819, 1.839, 1.849,
    1.839, 1.829, 1.809, 1.789, 1.799, 1.819, 1.829,
    1.819, 1.799, 1.789, 1.779, 1.799, 1.819, 1.839,
]

class PriceInput(BaseModel):
    prices: Optional[List[float]] = Field(
        default=None,
        description="Recent fuel prices ordered oldest to newest. If omitted, demo data is used.",
    )


def predict_direction(prices: List[float]) -> dict:
    clean = [float(p) for p in prices if p is not None and float(p) > 0]
    if len(clean) < 5:
        clean = DEMO_PRICES

    last = clean[-1]
    short_avg = mean(clean[-3:])
    long_avg = mean(clean[-10:]) if len(clean) >= 10 else mean(clean)
    momentum = short_avg - long_avg
    recent_change = clean[-1] - clean[-2]

    # Simple MVP heuristic: if short-term price is below longer average and falling,
    # tomorrow morning is more likely to be higher due to mean reversion; otherwise lower.
    score = (-momentum * 100) + (-recent_change * 60)
    probability_higher = max(0.35, min(0.75, 0.50 + score))
    direction = "higher" if probability_higher >= 0.50 else "lower"
    confidence = probability_higher if direction == "higher" else 1 - probability_higher

    recommendation = (
        "Better to tank today" if direction == "higher" else "Maybe wait until tomorrow"
    )

    return {
        "direction": direction,
        "confidence": round(confidence * 100, 1),
        "probability_higher": round(probability_higher * 100, 1),
        "last_price": round(last, 3),
        "recommendation": recommendation,
        "explanation": [
            f"Last price: €{last:.3f}",
            f"3-sample average: €{short_avg:.3f}",
            f"Longer average: €{long_avg:.3f}",
            "This is an MVP heuristic, not financial advice.",
        ],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

@app.get("/")
def root():
    return {
        "name": "Tank Predictor API",
        "status": "online",
        "try": "/health or /predict",
    }

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/predict")
def predict_get():
    return predict_direction(DEMO_PRICES)

@app.post("/predict")
def predict_post(payload: PriceInput):
    return predict_direction(payload.prices or DEMO_PRICES)

@app.post("/upload-csv")
async def upload_csv(file: UploadFile = File(...)):
    raw = await file.read()
    text = raw.decode("utf-8", errors="ignore")
    prices = []
    for line in text.splitlines():
        parts = [p.strip() for p in line.replace(";", ",").split(",")]
        for p in reversed(parts):
            try:
                prices.append(float(p))
                break
            except ValueError:
                continue
    return predict_direction(prices)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000)
