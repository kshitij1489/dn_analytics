from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from src.api.routers import insights, menu, operations, resolutions, sql, orders, system, ai, config, today, forecast, forecast_items, weather, conversations

app = FastAPI(title="Analytics Backend")

@app.on_event("startup")
async def start_background_tasks():
    from src.core.services.cloud_sync_scheduler import background_sync_task
    import asyncio
    asyncio.create_task(background_sync_task())

@app.on_event("startup")
def startup_db_check():
    # Ensure error log file handler is attached (logs/errors.jsonl)
    from src.core.error_log import get_error_logger
    get_error_logger()
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
                query_id TEXT,
                query_status TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_messages_conversation ON ai_messages(conversation_id);")
            # Migration: client-learning uploaded_at on ai_logs and ai_feedback
            for table, col in [("ai_logs", "uploaded_at"), ("ai_feedback", "uploaded_at")]:
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT;")
                    conn.commit()
                except Exception:
                    conn.rollback()
                    pass  # Column already exists
            # Migration: forecast cloud sync uploaded_at on backtest caches
            for table in ["revenue_backtest_cache", "item_backtest_cache"]:
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN uploaded_at TEXT;")
                    conn.commit()
                except Exception:
                    conn.rollback()
                    pass  # Column already exists
            # Migration: app_users — drop old table (user_id schema) if present, create new (employee_id PK), seed if empty
            try:
                cur = conn.execute("SELECT user_id FROM app_users LIMIT 1")
                cur.fetchone()
                conn.execute("DROP TABLE app_users")
                conn.commit()
            except Exception:
                conn.rollback()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS app_users (
                    employee_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    is_active BOOLEAN DEFAULT 1,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cur = conn.execute("SELECT COUNT(*) FROM app_users")
            if cur.fetchone()[0] == 0:
                conn.execute("INSERT INTO app_users (name, employee_id, is_active) VALUES ('Owner', '0001', 1)")
                conn.commit()
            # Forecast bootstrap: DISABLED at startup. Use manual "Pull from Cloud"
            # or "Full Retrain" buttons in Configuration → Forecast section.

            conn.close()
            print("Startup: Verified weather_daily, ai_conversations, and app_users schema.")
    except Exception as e:
        print(f"Startup DB Check Failed: {e}")
        try:
            get_error_logger().exception("Startup DB check failed")
        except Exception:
            pass

@app.exception_handler(Exception)
def global_exception_handler(request, exc):
    """Log uncaught exceptions to the error log file, then return 500."""
    try:
        from src.core.error_log import get_error_logger
        path = getattr(getattr(request, "url", None), "path", None)
        get_error_logger().exception(
            f"Uncaught exception: {exc}",
            extra={"context": {"path": path}},
        )
    except Exception:
        pass
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=500, content={"detail": str(exc)})


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
app.include_router(forecast_items.router, prefix="/api/forecast", tags=["Forecast Items"])
app.include_router(weather.router, prefix="/api/weather", tags=["Weather"])
app.include_router(conversations.router, prefix="/api/conversations", tags=["Conversations"])

@app.get("/api/health")
def health():
    return {"status": "ok"}
