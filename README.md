# Tank Predictor Backend - Clean Railway Version

This is a clean FastAPI backend for Railway.

Endpoints:
- `/health`
- `/predict`
- POST `/predict` with JSON: `{ "prices": [1.82, 1.81, 1.79, 1.80, 1.83] }`
- POST `/upload-csv` with CSV file

Railway start command:
`uvicorn main:app --host 0.0.0.0 --port $PORT`
