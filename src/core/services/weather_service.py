import requests
import sqlite3
import pandas as pd
import json
import os
import shutil
from datetime import date, datetime, timedelta
from src.core.db.connection import get_db_connection
from src.core.utils.path_helper import get_resource_path

# Open-Meteo Endpoints
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
HISTORICAL_FORECAST_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"

# City Coordinates (Gurugram)
LAT = 28.4595
LON = 77.0266
TIMEZONE = "Asia/Kolkata"

class WeatherService:
    def __init__(self):
         pass

    def get_lat_lon(self, city: str):
        if city.lower() == "gurugram" or city.lower() == "gurgaon":
            return LAT, LON
        return LAT, LON

    def regenerate_weather_data(self, city: str = "Gurugram"):
        """
        Complete regeneration of weather data:
        1. Clears existing CSV and DB table.
        2. Fetches Archive Data (Actuals) from July 2025 to Today.
        3. Fetches Forecast Snapshots (Retrospective & Live) for EVERY row.
        4. Exports new CSV.
        """
        conn, _ = get_db_connection()
        if not conn:
            return False, "Database connection failed"

        try:
            lat, lon = self.get_lat_lon(city)
            
            # 1. Clear Data
            self._clear_data(conn)
            
            # 2. Determine Date Range
            start_date_str = "2025-07-01"
            today = date.today()
            
            print(f"Regenerating weather data from {start_date_str} to {today}")

            # 3. Fetch & Store actuals (Bulk Archive)
            # This populates the rows with observed weather
            self._fetch_and_store_archive(conn, lat, lon, city, start_date_str, today.isoformat())
            
            # 4. Fetch & Store Forecast Snapshots (Row-by-Row)
            # We iterate through the inserted dates and attach the forecast snapshot
            self._populate_forecast_snapshots(conn, lat, lon, city, start_date_str, today)
            
            # 5. Export
            self.export_weather_csv(conn, city)
            
            conn.commit()
            return True, "Weather data regenerated."
            
        except Exception as e:
            conn.rollback()
            return False, str(e)
        finally:
            conn.close()

    def sync_weather_data(self, city: str = "Gurugram"):
        """
        Incremental sync (for daily usage).
        For now, we can just alias this to regenerate to ensure consistency, 
        or implement smart logic later. Given the user request, 
        we primarily want strict correctness now.
        """
        # TODO: Optimize to only fetch missing days for production.
        # For now, per user instruction to "always write forecast", regeneration is safest 
        # but slow. Let's do a smart sync: Check missing days -> fetch them.
        return self.regenerate_weather_data(city)


    def _clear_data(self, conn):
        print("Clearing existing weather data...")
        conn.execute("DELETE FROM weather_daily")
        
        # Use absolute path handling for frozen app
        csv_path = get_resource_path(os.path.join("data", "weather_history.csv"))
        
        if os.path.exists(csv_path):
            os.remove(csv_path)

    def _fetch_and_store_archive(self, conn, lat, lon, city, start_date, end_date):
        print("Fetching archive data (actuals)...")
        params = {
            "latitude": lat,
            "longitude": lon,
            "start_date": start_date,
            "end_date": end_date,
            "daily": ["temperature_2m_max", "temperature_2m_min", "temperature_2m_mean", 
                      "precipitation_sum", "rain_sum", "wind_speed_10m_max", "weather_code"],
            "timezone": TIMEZONE
        }
        
        resp = requests.get(ARCHIVE_URL, params=params)
        data = resp.json()
        
        if "daily" not in data:
            print(f"Error fetching archive: {data}")
            return

        daily = data["daily"]
        dates = daily["time"]
        
        for i, d in enumerate(dates):
            conn.execute("""
                INSERT INTO weather_daily 
                (date, city, temp_max, temp_min, temp_mean, precipitation_sum, rain_sum, wind_speed_max, weather_code, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                d, city,
                daily["temperature_2m_max"][i],
                daily["temperature_2m_min"][i],
                daily["temperature_2m_mean"][i],
                daily["precipitation_sum"][i],
                daily["rain_sum"][i],
                daily["wind_speed_10m_max"][i],
                daily["weather_code"][i]
            ))

    def _populate_forecast_snapshots(self, conn, lat, lon, city, start_date_str, today_date):
        print("Populating forecast snapshots for all dates...")
        
        curr = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        
        while curr <= today_date:
            d_str = curr.isoformat()
            
            # Decide which API to use
            if curr == today_date:
                # Live Forecast (from today onwards)
                print(f"  - Fetching LIVE forecast for {d_str}")
                self._fetch_snapshot_live(conn, lat, lon, city, d_str)
            else:
                # Historical Forecast (retrospective)
                # print(f"  - Fetching HISTORICAL forecast for {d_str}") 
                # (Commented out print to reduce noise, or keep for debugging)
                self._fetch_snapshot_historical(conn, lat, lon, city, d_str)
                
            curr += timedelta(days=1)

    def _fetch_snapshot_historical(self, conn, lat, lon, city, date_str):
        try:
            params = {
                "latitude": lat,
                "longitude": lon,
                "start_date": date_str,
                "end_date": (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=6)).strftime("%Y-%m-%d"),
                "daily": ["temperature_2m_max", "temperature_2m_min", "precipitation_sum", "weather_code"],
                "timezone": TIMEZONE
            }
            resp = requests.get(HISTORICAL_FORECAST_URL, params=params)
            data = resp.json()
            self._save_snapshot(conn, date_str, city, data)
        except Exception as e:
            print(f"Failed historical forecast for {date_str}: {e}")

    def _fetch_snapshot_live(self, conn, lat, lon, city, date_str):
        try:
            params = {
                "latitude": lat,
                "longitude": lon,
                "daily": ["temperature_2m_max", "temperature_2m_min", "precipitation_sum", 
                          "rain_sum", "wind_speed_10m_max", "weather_code"],
                "timezone": TIMEZONE,
                "forecast_days": 7
            }
            resp = requests.get(FORECAST_URL, params=params)
            data = resp.json()
            self._save_snapshot(conn, date_str, city, data)
        except Exception as e:
            print(f"Failed live forecast for {date_str}: {e}")

    def _save_snapshot(self, conn, date_str, city, data):
        if "daily" in data:
            snapshot_json = json.dumps(data["daily"])
            conn.execute("""
                UPDATE weather_daily 
                SET forecast_snapshot = ? 
                WHERE date = ? AND city = ?
            """, (snapshot_json, date_str, city))

    def export_weather_csv(self, conn, city):
        df = pd.read_sql_query(
            "SELECT * FROM weather_daily WHERE city = ? ORDER BY date ASC",
            conn,
            params=(city,)
        )
        
        # Use absolute path handling
        output_dir = get_resource_path("data")
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
            
        output_path = os.path.join(output_dir, "weather_history.csv")
        df.to_csv(output_path, index=False)
        print(f"Weather data exported to {output_path}")
