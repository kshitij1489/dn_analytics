from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from src.api.routers import (
    customer_analytics,
    insights,
    menu,
    operations,
    sql,
    orders,
    system,
    ai,
    config,
    today,
    forecast,
    forecast_items,
    forecast_volume,
    weather,
    conversations,
    profiles,
)

app = FastAPI(title="Analytics Backend")

@app.on_event("startup")
async def start_background_tasks():
    from src.core.services.cloud_sync_scheduler import background_sync_task
    import asyncio
    asyncio.create_task(background_sync_task())


@app.on_event("shutdown")
def shutdown_handler():
    print("[Shutdown] Analytics backend stopping.")


@app.on_event("startup")
def startup_db_check():
    # Ensure error log file handler is attached (logs/errors.jsonl)
    from src.core.error_log import get_error_logger
    get_error_logger()
    try:
        from src.core.db.control import (
            copy_legacy_global_config_once,
            ensure_control_schema,
        )
        from src.core.db.connection import get_profile_connection
        from src.core.profiles import ProfileError, selected_profile

        ensure_control_schema()
        try:
            profile = selected_profile()
        except ProfileError:
            copy_legacy_global_config_once()
            print("Startup: control database ready; waiting for restaurant selection.")
            return
        if profile.clean_rebuild_status == "required":
            print(
                f"Startup: restaurant profile {profile.restaurant_id} is awaiting "
                "its archived clean rebuild; old database left unopened."
            )
            return
        copy_legacy_global_config_once()
        conn, _ = get_profile_connection(profile, apply_schema=True)
        try:
            from src.core.sync_identity import get_device_identity

            get_device_identity(conn)
            conn.commit()
        finally:
            conn.close()
        print(f"Startup: verified restaurant profile {profile.restaurant_id}.")
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
app.include_router(customer_analytics.router, prefix="/api/insights/customer", tags=["Customer Analytics"])
app.include_router(menu.router, prefix="/api/menu", tags=["Menu"])
app.include_router(operations.router, prefix="/api/sync", tags=["Operations"])
app.include_router(orders.router, prefix="/api/orders", tags=["Orders"])
app.include_router(sql.router, prefix="/api/sql", tags=["SQL"])
app.include_router(system.router, prefix="/api/system", tags=["System"])
app.include_router(ai.router, prefix="/api/ai", tags=["AI"])
app.include_router(config.router, prefix="/api/config", tags=["Config"])
app.include_router(profiles.router, prefix="/api/config", tags=["Restaurant Profiles"])
app.include_router(today.router)
app.include_router(forecast.router, prefix="/api/forecast", tags=["Forecast"])
app.include_router(forecast_items.router, prefix="/api/forecast", tags=["Forecast Items"])
app.include_router(forecast_volume.router, prefix="/api/forecast", tags=["Forecast Volume"])
app.include_router(weather.router, prefix="/api/weather", tags=["Weather"])
app.include_router(conversations.router, prefix="/api/conversations", tags=["Conversations"])

@app.get("/api/health")
def health():
    return {"status": "ok"}
