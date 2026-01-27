from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from src.api.routers import insights, menu, operations, resolutions, sql, orders, system, ai, config, today, forecast

app = FastAPI(title="Analytics Backend")

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

@app.get("/api/health")
def health():
    return {"status": "ok"}
