import requests
import sqlite3
import pandas as pd
import json
import os
import shutil
from datetime import date, datetime, timedelta
from src.core.db.connection import get_db_connection
from src.core.utils.path_helper import get_resource_path, get_data_path

# Open-Meteo Endpoints
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
HISTORICAL_FORECAST_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"

# City Coordinates (Gurugram)
LAT = 28.4595
LON = 77.0266
TIMEZONE = "Asia/Kolkata"

# Sync Configuration
CACHE_DURATION_HOURS = 8  # Minimum hours between API syncs
ARCHIVE_REFRESH_DAYS = 7  # Always refresh this many days of historical data to update forecast->actual values

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
        Smart sync for weather data. Called automatically when Forecast page opens.
        
        Sync Lifecycle:
        1. Skip if last sync was < CACHE_DURATION_HOURS ago.
        2. Fetch ARCHIVE data (actuals) for last ARCHIVE_REFRESH_DAYS.
           - This overwrites any forecast placeholder values with real observed data.
        3. Fetch FORECAST snapshots for today (contains next 7 days predictions).
           - Today's row stores the snapshot for chart display.
           - If today's row doesn't exist, it's created with forecast values as placeholders.
        4. Export updated data to weather_history.csv.
        """
        conn, _ = get_db_connection()
        if not conn:
            return False, "Database connection failed"

        try:
            # 0. Check Cache
            cursor = conn.execute("SELECT MAX(updated_at) FROM weather_daily WHERE city = ?", (city,))
            last_update_row = cursor.fetchone()
            if last_update_row and last_update_row[0]:
                try:
                    last_update = datetime.strptime(last_update_row[0], "%Y-%m-%d %H:%M:%S")
                    if datetime.now() - last_update < timedelta(hours=CACHE_DURATION_HOURS):
                        print(f"Weather data for {city} is cached (last update: {last_update}). Skipping sync.")
                        return True, "Weather data is up to date (cached)."
                except ValueError:
                    # If parsing fails, ignore cache and re-sync
                    pass

            lat, lon = self.get_lat_lon(city)
            today = date.today()
            
            # Check for existing data
            cursor = conn.execute("SELECT MAX(date) FROM weather_daily WHERE city = ?", (city,))
            row = cursor.fetchone()
            last_date_str = row[0] if row else None

            if not last_date_str:
                print(f"Weather DB empty. Initializing with last 365 days for {city}...")
                start_date = today - timedelta(days=365)
                start_date_str = start_date.isoformat()
            else:
                last_date = datetime.strptime(last_date_str, "%Y-%m-%d").date()
                if last_date >= today:
                    print(f"Weather data up to date ({last_date_str}). Syncing forecast only.")
                    # Still refresh today and future snapshots
                    start_date_str = today.isoformat()
                else:
                    # Start from the day after the last date
                    start_date = last_date + timedelta(days=1)
                    start_date_str = start_date.isoformat()
                    print(f"Syncing remaining weather data from {start_date_str} to today...")

            # 1. Fetch Archive (Actuals) - Always refresh last N days
            # This ensures forecast placeholder values get overwritten with real observed data.
            archive_end = today - timedelta(days=1)  # Archive API has ~1 day lag
            archive_start = today - timedelta(days=ARCHIVE_REFRESH_DAYS)
            
            # Also fetch any older missing data if start_date is before the 7-day window
            start_date_obj = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            if start_date_obj < archive_start:
                archive_start = start_date_obj
            
            if archive_start <= archive_end:
                print(f"Fetching archive (actuals) from {archive_start} to {archive_end}...")
                self._fetch_and_store_archive(conn, lat, lon, city, archive_start.isoformat(), archive_end.isoformat())
            else:
                print(f"Skipping archive fetch: archive_start {archive_start} is after archive_end {archive_end}")
            
            # 2. Populate/Refresh Forecast Snapshots
            # We refresh the last 7 days to ensure we have the latest retrospective forecasts
            # and today's live forecast.
            forecast_start = today - timedelta(days=7)
            self._populate_forecast_snapshots(conn, lat, lon, city, forecast_start.isoformat(), today)
            
            # 3. Export
            self.export_weather_csv(conn, city)
            
            conn.commit()
            return True, f"Weather data synced from {start_date_str}."
            
        except Exception as e:
            conn.rollback()
            return False, str(e)
        finally:
            conn.close()


    def _clear_data(self, conn):
        print("Clearing existing weather data...")
        conn.execute("DELETE FROM weather_daily")
        
        # Use absolute path handling for frozen app
        csv_path = get_data_path(os.path.join("data", "weather_history.csv"))
        
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
                INSERT OR REPLACE INTO weather_daily 
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
            
            # Extract today's forecast values (index 0) to populate the main columns as placeholders
            # These will be overwritten by actuals (Archive API) in subsequent days.
            try:
                # API returns arrays, index 0 matches the start_date (which is date_str)
                t_max = data["daily"]["temperature_2m_max"][0]
                t_min = data["daily"]["temperature_2m_min"][0]
                # Calculate mean if available, else avg
                t_mean = (t_max + t_min) / 2 if t_max is not None and t_min is not None else None
                
                precip = data["daily"].get("precipitation_sum", [0])[0]
                rain = data["daily"].get("rain_sum", [0])[0]
                wind = data["daily"].get("wind_speed_10m_max", [0])[0]
                w_code = data["daily"].get("weather_code", [0])[0]
            except (IndexError, KeyError, TypeError):
                t_max = t_min = t_mean = precip = rain = wind = w_code = None

            # Try UPDATE first
            cursor = conn.execute("""
                UPDATE weather_daily 
                SET forecast_snapshot = ?, 
                    temp_max = COALESCE(temp_max, ?), 
                    temp_min = COALESCE(temp_min, ?), 
                    temp_mean = COALESCE(temp_mean, ?),
                    precipitation_sum = COALESCE(precipitation_sum, ?),
                    rain_sum = COALESCE(rain_sum, ?),
                    wind_speed_max = COALESCE(wind_speed_max, ?),
                    weather_code = COALESCE(weather_code, ?),
                    updated_at = CURRENT_TIMESTAMP
                WHERE date = ? AND city = ?
            """, (snapshot_json, t_max, t_min, t_mean, precip, rain, wind, w_code, date_str, city))
            
            # If no row updated, INSERT (upsert for today's missing row)
            if cursor.rowcount == 0:
                print(f"  - No existing row for {date_str}, inserting new row with snapshot values.")
                conn.execute("""
                    INSERT INTO weather_daily (
                        date, city, forecast_snapshot, 
                        temp_max, temp_min, temp_mean, 
                        precipitation_sum, rain_sum, wind_speed_max, weather_code,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (
                    date_str, city, snapshot_json,
                    t_max, t_min, t_mean, precip, rain, wind, w_code
                ))

    def export_weather_csv(self, conn, city):
        df = pd.read_sql_query(
            "SELECT * FROM weather_daily WHERE city = ? ORDER BY date ASC",
            conn,
            params=(city,)
        )
        
        # Use absolute path handling
        output_dir = get_data_path("data")
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
            
        output_path = os.path.join(output_dir, "weather_history.csv")
        df.to_csv(output_path, index=False)
        print(f"Weather data exported to {output_path}")
