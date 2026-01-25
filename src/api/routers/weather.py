from fastapi import APIRouter, Depends
from src.core.services.weather_service import WeatherService
from src.core.db.connection import get_db_connection

router = APIRouter(prefix="/weather", tags=["Weather"])

@router.post("/sync")
def sync_weather(city: str = "Gurugram"):
    """
    Triggers a sync of weather data:
    1. Fills historical gaps since July 2025
    2. Updates 7-day forecast
    3. Exports CSV for ML
    """
    service = WeatherService()
    success, msg = service.sync_weather_data(city)
    return {"status": "success" if success else "error", "message": msg}

@router.get("/history")
def get_weather_history(city: str = "Gurugram"):
    conn, _ = get_db_connection()
    cursor = conn.execute(
        "SELECT * FROM weather_daily WHERE city = ? ORDER BY date DESC LIMIT 30", 
        (city,)
    )
    data = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return data
