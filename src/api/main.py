from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from src.api.routers import insights, menu, operations, resolutions, sql, orders, system, ai, config, today, forecast, weather, conversations

app = FastAPI(title="Analytics Backend")

@app.on_event("startup")
def startup_db_check():
    try:
        from src.core.db.connection import get_db_connection
        conn, _ = get_db_connection()
        if conn:
            # Check/Create weather_daily table if missing (Migration for existing users)
            conn.execute("""
            CREATE TABLE IF NOT EXISTS weather_daily (
                date TEXT,        -- YYYY-MM-DD
                city TEXT,
                
                -- Observed Metrics (Actuals)
                temp_max DECIMAL(4,1),
                temp_min DECIMAL(4,1),
                temp_mean DECIMAL(4,1),
                precipitation_sum DECIMAL(6,1),
                rain_sum DECIMAL(6,1),
                wind_speed_max DECIMAL(4,1),
                
                -- Weather Codes (WMO)
                weather_code INTEGER,
                
                -- Forecast Snapshot (JSON)
                forecast_snapshot TEXT,
                
                -- Metadata
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                
                PRIMARY KEY (date, city)
            );
            """)
            # Migration: AI Conversations tables
            conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_conversations (
                conversation_id TEXT PRIMARY KEY,
                title TEXT,
                started_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                synced_at TEXT
            );
            """)
            conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_messages (
                message_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL REFERENCES ai_conversations(conversation_id) ON DELETE CASCADE,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                type TEXT,
                sql_query TEXT,
                explanation TEXT,
                log_id TEXT,
                query_status TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_messages_conversation ON ai_messages(conversation_id);")
            conn.commit()
            conn.close()
            print("Startup: Verified weather_daily and ai_conversations schema.")
    except Exception as e:
        print(f"Startup DB Check Failed: {e}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Electron local connection
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(insights.router, prefix="/api/insights", tags=["Insights"])
app.include_router(menu.router, prefix="/api/menu", tags=["Menu"])
app.include_router(operations.router, prefix="/api/sync", tags=["Operations"])
app.include_router(resolutions.router, prefix="/api/resolutions", tags=["Resolutions"])
app.include_router(orders.router, prefix="/api/orders", tags=["Orders"])
app.include_router(sql.router, prefix="/api/sql", tags=["SQL"])
app.include_router(system.router, prefix="/api/system", tags=["System"])
app.include_router(ai.router, prefix="/api/ai", tags=["AI"])
app.include_router(config.router, prefix="/api/config", tags=["Config"])
app.include_router(today.router)
app.include_router(forecast.router, prefix="/api/forecast", tags=["Forecast"])
app.include_router(weather.router, prefix="/api/weather", tags=["Weather"])
app.include_router(conversations.router, prefix="/api/conversations", tags=["Conversations"])

@app.get("/api/health")
def health():
    return {"status": "ok"}
