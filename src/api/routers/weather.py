from fastapi import APIRouter, Depends
from src.core.services.weather_service import WeatherService
from src.api.dependencies import ScopedReader, get_authorized_restaurant_profile, get_reader
from src.core.queries.multi_store_reducers import union_rows

router = APIRouter(prefix="/weather", tags=["Weather"])

@router.post("/sync")
def sync_weather(
    city: str = "Gurugram",
    profile=Depends(get_authorized_restaurant_profile),
):
    """
    Triggers a sync of weather data:
    1. Fills historical gaps since July 2025
    2. Updates 7-day forecast
    3. Exports CSV for ML
    """
    service = WeatherService(profile)
    success, msg = service.sync_weather_data(city)
    return {"status": "success" if success else "error", "message": msg}

@router.get("/history")
def get_weather_history(city: str = "Gurugram", reader: ScopedReader = Depends(get_reader)):
    def query(conn, _profile):
        cursor = conn.execute(
            "SELECT * FROM weather_daily WHERE city = ? ORDER BY date DESC LIMIT 30",
            (city,)
        )
        return [dict(row) for row in cursor.fetchall()]

    def reduce(pairs):
        # Weather stays profile metadata: rows are listed per store, never
        # averaged into one "All Stores weather" (plan §7.5).
        return union_rows(pairs, key_fields=("date",), sort_by="date", descending=True)

    return reader.read(query, reduce)
