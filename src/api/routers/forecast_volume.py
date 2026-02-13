"""
Volume Forecast API Router

Menu-item-level volume forecasting: predicts gms/ml/units sold per menu item over 14 days.
Same pattern as forecast_items; uses volume_demand_ml module.
Entity = menu_item_id. Target = volume_sold (cumulative gms or count per day).
"""
import logging
import threading
from datetime import timedelta, datetime
from typing import Optional

import pandas as pd

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse

from src.api.routers import forecast_training_status

from src.api.dependencies import get_db
from src.core.utils.business_date import BUSINESS_DATE_SQL, get_current_business_date

try:
    from src.core.learning.revenue_forecasting.volume_demand_ml.predict import forecast_volumes
    from src.core.learning.revenue_forecasting.volume_demand_ml.model_io import (
        get_models,
        clear_model_cache,
        is_model_stale,
        _resolve_model_dir,
    )
    from src.core.learning.revenue_forecasting.volume_demand_ml.train import train_pipeline
    from src.core.learning.revenue_forecasting.volume_demand_ml.dataset import load_volume_sales, densify_daily_grid
    from src.core.learning.revenue_forecasting.volume_demand_ml.backtest_point_in_time import (
        train_and_predict_for_date,
    )
    VOLUME_ML_AVAILABLE = True
except (ImportError, OSError) as e:
    logging.getLogger(__name__).warning(f"Volume Demand ML not available: {e}")
    VOLUME_ML_AVAILABLE = False

from src.core.learning.revenue_forecasting.forecast_cache import (
    is_volume_cache_fresh,
    get_latest_volume_cache_generated_on,
    load_volume_forecasts,
    save_volume_forecasts,
    get_missing_volume_backtest_dates,
    load_volume_backtest_forecasts,
    save_volume_backtest_forecasts,
)

logger = logging.getLogger(__name__)
router = APIRouter()

_volume_training_lock = threading.Lock()


def _get_volume_historical_data(
    conn,
    item_id: Optional[str] = None,
    days: int = 90,
) -> pd.DataFrame:
    """
    Fetch menu-item-level volume history.
    Volume = sum across all variants of each menu item per day.
    Units: Count → units; mg/ml/g/kg → g.
    """
    today_str = get_current_business_date()
    today_date = datetime.strptime(today_str, "%Y-%m-%d").date()
    start_dt = today_date - timedelta(days=days)
    item_filter = "AND mi.menu_item_id = ?" if item_id else ""
    params = [start_dt.isoformat(), today_str]
    if item_id:
        params.append(item_id)

    query = f"""
        SELECT
            {BUSINESS_DATE_SQL} as date,
            mi.menu_item_id as item_id,
            mi.name as item_name,
            mi.type as category,
            AVG(oi.unit_price) as price,
            w.temp_max as temperature,
            COALESCE(w.rain_sum, 0) as rain,
            SUM(
                CASE
                    WHEN UPPER(COALESCE(v.unit, 'MG')) = 'COUNT' THEN oi.quantity * COALESCE(v.value, 1)
                    WHEN UPPER(COALESCE(v.unit, 'MG')) = 'ML' THEN oi.quantity * COALESCE(v.value, 1)
                    WHEN UPPER(COALESCE(v.unit, 'MG')) IN ('GMS', 'G') THEN oi.quantity * COALESCE(v.value, 1)
                    WHEN UPPER(COALESCE(v.unit, 'MG')) = 'KG' THEN oi.quantity * COALESCE(v.value, 1) * 1000
                    WHEN UPPER(COALESCE(v.unit, 'MG')) = 'MG' THEN oi.quantity * COALESCE(v.value, 1) / 1000.0
                    ELSE oi.quantity * COALESCE(v.value, 1) / 1000.0 -- Default to mg -> g
                END
            ) as volume_sold
        FROM order_items oi
        JOIN orders o ON oi.order_id = o.order_id
        JOIN menu_items mi ON oi.menu_item_id = mi.menu_item_id
        JOIN variants v ON oi.variant_id = v.variant_id
        LEFT JOIN weather_daily w ON {BUSINESS_DATE_SQL} = w.date AND w.city = 'Gurugram'
        WHERE o.order_status = 'Success'
          AND oi.menu_item_id IS NOT NULL
          AND oi.variant_id IS NOT NULL
          AND {BUSINESS_DATE_SQL} >= ?
          AND {BUSINESS_DATE_SQL} <= ?
          {item_filter}
        GROUP BY {BUSINESS_DATE_SQL}, mi.menu_item_id
        ORDER BY date, item_id
    """
    cursor = conn.execute(query, params)
    rows = [dict(row) for row in cursor.fetchall()]
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df['date'] = pd.to_datetime(df['date'])
    df['volume_sold'] = pd.to_numeric(df['volume_sold'], errors='coerce').fillna(0).astype(float)
    df['price'] = pd.to_numeric(df['price'], errors='coerce').fillna(0)
    df['temperature'] = pd.to_numeric(df['temperature'], errors='coerce').ffill().fillna(25.0)
    df['rain'] = pd.to_numeric(df['rain'], errors='coerce').fillna(0)
    df['unit'] = 'g'

    mixed = _get_mixed_unit_item_ids(conn)
    if mixed:
        before = len(df)
        df = df[~df['item_id'].isin(mixed)]
        if len(df) < before:
            logger.info(f"Excluded {before - len(df)} rows from {len(mixed)} mixed-unit menu items (Count + mg/ml)")

    return df


def _get_item_unit_from_variants(conn, menu_item_id: str) -> str:
    """Get primary unit for a menu item from its variants (g or units)."""
    try:
        cur = conn.execute("""
            SELECT DISTINCT UPPER(COALESCE(v.unit, 'MG')) as u
            FROM menu_item_variants miv
            JOIN variants v ON miv.variant_id = v.variant_id
            WHERE miv.menu_item_id = ?
        """, (menu_item_id,))
        units = [r[0] for r in cur.fetchall()]
        if not units:
            return 'g'
        if 'COUNT' in units:
            return 'units'
        return 'g'
    except Exception:
        return 'g'


def _get_mixed_unit_item_ids(conn) -> set:
    """
    Return menu_item_ids that have BOTH Count and mass/volume variants.
    These indicate uncleaned data — exclude from training and forecasting.
    """
    try:
        cur = conn.execute("""
            SELECT miv.menu_item_id
            FROM menu_item_variants miv
            JOIN variants v ON miv.variant_id = v.variant_id
            WHERE UPPER(COALESCE(v.unit, 'MG')) IN ('COUNT', 'MG', 'ML', 'GMS', 'KG', 'G')
            GROUP BY miv.menu_item_id
            HAVING COUNT(DISTINCT CASE WHEN UPPER(COALESCE(v.unit, 'MG')) = 'COUNT' THEN 1 END) > 0
               AND COUNT(DISTINCT CASE WHEN UPPER(COALESCE(v.unit, 'MG')) NOT IN ('COUNT') THEN 1 END) > 0
        """)
        return {r[0] for r in cur.fetchall()}
    except Exception as e:
        logger.warning(f"Could not compute mixed-unit items: {e}")
        return set()


def _get_volume_historical_data_range(
    conn,
    start_date_str: str,
    end_date_str: str,
    item_id: Optional[str] = None,
) -> pd.DataFrame:
    """Fetch volume history for an explicit date range (aggregated by menu item)."""
    item_filter = "AND mi.menu_item_id = ?" if item_id else ""
    params = [start_date_str, end_date_str]
    if item_id:
        params.append(item_id)

    query = f"""
        SELECT
            {BUSINESS_DATE_SQL} as date,
            mi.menu_item_id as item_id,
            mi.name as item_name,
            mi.type as category,
            AVG(oi.unit_price) as price,
            w.temp_max as temperature,
            COALESCE(w.rain_sum, 0) as rain,
            SUM(
                CASE
                    WHEN UPPER(COALESCE(v.unit, 'MG')) = 'COUNT' THEN oi.quantity * COALESCE(v.value, 1)
                    WHEN UPPER(COALESCE(v.unit, 'MG')) = 'ML' THEN oi.quantity * COALESCE(v.value, 1)
                    WHEN UPPER(COALESCE(v.unit, 'MG')) IN ('GMS', 'G') THEN oi.quantity * COALESCE(v.value, 1)
                    WHEN UPPER(COALESCE(v.unit, 'MG')) = 'KG' THEN oi.quantity * COALESCE(v.value, 1) * 1000
                    WHEN UPPER(COALESCE(v.unit, 'MG')) = 'MG' THEN oi.quantity * COALESCE(v.value, 1) / 1000.0
                    ELSE oi.quantity * COALESCE(v.value, 1) / 1000.0 -- Default to mg -> g
                END
            ) as volume_sold
        FROM order_items oi
        JOIN orders o ON oi.order_id = o.order_id
        JOIN menu_items mi ON oi.menu_item_id = mi.menu_item_id
        JOIN variants v ON oi.variant_id = v.variant_id
        LEFT JOIN weather_daily w ON {BUSINESS_DATE_SQL} = w.date AND w.city = 'Gurugram'
        WHERE o.order_status = 'Success'
          AND oi.menu_item_id IS NOT NULL
          AND oi.variant_id IS NOT NULL
          AND {BUSINESS_DATE_SQL} >= ?
          AND {BUSINESS_DATE_SQL} <= ?
          {item_filter}
        GROUP BY {BUSINESS_DATE_SQL}, mi.menu_item_id
        ORDER BY date, item_id
    """
    cursor = conn.execute(query, params)
    rows = [dict(row) for row in cursor.fetchall()]
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df['date'] = pd.to_datetime(df['date'])
    df['volume_sold'] = pd.to_numeric(df['volume_sold'], errors='coerce').fillna(0).astype(float)
    df['price'] = pd.to_numeric(df['price'], errors='coerce').fillna(0)
    df['temperature'] = pd.to_numeric(df['temperature'], errors='coerce').ffill().fillna(25.0)
    df['rain'] = pd.to_numeric(df['rain'], errors='coerce').fillna(0)
    df['unit'] = 'g'

    mixed = _get_mixed_unit_item_ids(conn)
    if mixed:
        df = df[~df['item_id'].isin(mixed)]

    return df


def _fill_volume_backtest(
    conn,
    items_info: pd.DataFrame,
    active_items: set,
    today_date,
    n_days: int = 30,
) -> None:
    """
    CPU-heavy: fill volume backtest cache for missing dates.
    Called ONLY during training. Checks is_shutting_down() per date.
    """
    backtest_end = today_date - timedelta(days=1)
    backtest_start = backtest_end - timedelta(days=n_days - 1)
    forecast_dates = [
        (backtest_start + timedelta(days=i)).isoformat()
        for i in range(n_days)
    ]
    item_ids = list(active_items)
    missing_dates = get_missing_volume_backtest_dates(conn, forecast_dates, item_ids) if item_ids else []

    for fd in missing_dates:
        if forecast_training_status.is_shutting_down():
            forecast_training_status.log("Shutdown requested — stopping volume backtest fill.")
            return
        if not VOLUME_ML_AVAILABLE:
            break
        try:
            d = datetime.strptime(fd, "%Y-%m-%d").date()
            start_str = (d - timedelta(days=120)).isoformat()
            end_str = (d - timedelta(days=1)).isoformat()
            df_train = _get_volume_historical_data_range(conn, start_str, end_str, item_id=None)
            if df_train.empty or len(df_train) < 30:
                continue
            df_train = load_volume_sales(df=df_train)
            result = train_and_predict_for_date(
                df_history=df_train,
                items_info=items_info,
                forecast_date=fd,
            )
            if result.empty:
                continue
            model_through = (d - timedelta(days=1)).isoformat()
            to_save = [
                {
                    "date": row["date"].strftime("%Y-%m-%d") if hasattr(row["date"], "strftime") else str(row["date"])[:10],
                    "item_id": row["item_id"],
                    "volume_value": float(row.get("predicted_p50", 0) or 0),
                    "p50": float(row["predicted_p50"]),
                    "p90": float(row["predicted_p90"]),
                    "probability": float(row["probability_of_sale"]),
                }
                for _, row in result.iterrows()
            ]
            save_volume_backtest_forecasts(conn, to_save, model_through)
            forecast_training_status.log(f"  Volume backtest filled for {fd}")
        except Exception as e:
            logger.warning(f"Volume backtest failed for {fd}: {e}")


def _load_volume_backtest(
    conn,
    items_info: pd.DataFrame,
    active_items: set,
    today_date,
    n_days: int = 30,
    item_id: Optional[str] = None,
) -> list:
    """
    Read-only: load volume backtest from cache. No fill — fast path for GET handler.
    """
    backtest_end = today_date - timedelta(days=1)
    backtest_start = backtest_end - timedelta(days=n_days - 1)
    forecast_dates = [
        (backtest_start + timedelta(days=i)).isoformat()
        for i in range(n_days)
    ]
    item_ids = list(active_items)
    cached = load_volume_backtest_forecasts(conn, forecast_dates, item_ids if item_id is None else None)
    name_by_id = dict(zip(items_info["item_id"], items_info["item_name"]))
    unit_by_id = dict(zip(items_info["item_id"], items_info["unit"]))
    backtest_rows = []
    for r in cached:
        if r["item_id"] not in active_items or (item_id and r["item_id"] != item_id):
            continue
        backtest_rows.append({
            "date": r["date"],
            "item_id": r["item_id"],
            "item_name": name_by_id.get(r["item_id"], r["item_id"]),
            "unit": unit_by_id.get(r["item_id"], "mg"),
            "p50": round(float(r.get("p50") or 0), 2),
            "p90": round(float(r.get("p90") or 0), 2),
            "probability": round(float(r.get("probability") or 0), 4),
        })
    return backtest_rows


def _train_volume_task():
    """Background task: retrain volume demand models."""
    if not _volume_training_lock.acquire(blocking=False):
        logger.warning("Volume training already in progress, skipping.")
        return

    conn = None
    try:
        from src.api.routers.config import get_db_connection
        conn, _ = get_db_connection()
        logger.info("Starting volume demand training...")

        df = _get_volume_historical_data(conn, days=120)
        if df.empty:
            logger.warning("No volume history — skipping training.")
            return

        df = load_volume_sales(df=df)
        model_dir = _resolve_model_dir(None)
        train_pipeline(df=df, save_path=model_dir, evaluate=True)

        today_str = get_current_business_date()
        today_date = datetime.strptime(today_str, "%Y-%m-%d").date()
        cutoff = today_date - timedelta(days=14)
        active = set(df[df['date'] >= pd.Timestamp(cutoff)]['item_id'].unique())
        df_active = df[df['item_id'].isin(active)]
        items_info = df_active.groupby('item_id').agg({
            'item_name': 'first', 'unit': 'first',
        }).reset_index()

        future_dates = pd.date_range(today_date, periods=14, freq='D')
        rows = []
        for _, it in items_info.iterrows():
            for d in future_dates:
                rows.append({
                    "date": d,
                    "item_id": it["item_id"],
                    "item_name": it["item_name"],
                    "unit": it["unit"],
                })
        df_future = pd.DataFrame(rows)

        result = forecast_volumes(
            df_future_dates=df_future,
            df_history=df,
            model_dir=model_dir,
        )

        to_save = []
        for _, row in result.iterrows():
            unit_val = row.get("unit", "mg")
            to_save.append({
                "date": row["date"].strftime("%Y-%m-%d") if hasattr(row["date"], "strftime") else str(row["date"])[:10],
                "item_id": row["item_id"],
                "volume_value": float(row.get("predicted_p50", 0) or 0),
                "unit": unit_val,
                "p50": float(row["predicted_p50"]),
                "p90": float(row["predicted_p90"]),
                "probability": float(row["probability_of_sale"]),
                "recommended_volume": float(row.get("recommended_volume", 0) or 0),
            })
        save_volume_forecasts(conn, to_save, today_str)
        forecast_training_status.log(f"Cached {len(to_save)} volume forecast rows.")

        # ── Fill volume backtest cache ───────────────────────────
        forecast_training_status.log("Filling volume backtest cache (30 days)…")
        _fill_volume_backtest(conn, items_info, active, today_date, n_days=30)

        clear_model_cache()
        logger.info("Volume training complete.")
        forecast_training_status.log("Volume training complete.")
    except Exception as e:
        logger.exception(f"Volume training failed: {e}")
        forecast_training_status.log(f"ERROR in volume training: {e}")
    finally:
        _volume_training_lock.release()
        if conn:
            conn.close()


@router.post("/train-volume")
def trigger_volume_training(background_tasks: BackgroundTasks):
    """Manually trigger volume demand model retraining."""
    if not VOLUME_ML_AVAILABLE:
        raise HTTPException(status_code=503, detail="Volume Demand ML not available.")
    background_tasks.add_task(_train_volume_task)
    return {"message": "Volume training started in background"}


@router.get("/volume")
def get_volume_forecast(item_id: Optional[str] = None, days: int = 14, conn=Depends(get_db)):
    """
    Menu-item-level volume forecast endpoint.

    Returns:
        items, history, forecast, backtest, etc.
    """
    # 503 during training — frontend shows overlay instead of fetching data
    if forecast_training_status.is_training():
        return JSONResponse(
            status_code=503,
            content={"detail": "Training in progress", "training_status": forecast_training_status.get_status()}
        )

    if not VOLUME_ML_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail="Volume Demand ML not available.",
        )

    models_loaded = True
    try:
        get_models()
    except FileNotFoundError:
        models_loaded = False

    model_stale = False
    try:
        model_stale = is_model_stale()
        if model_stale:
            logger.info("Volume model stale. Use Full Retrain to refresh.")
    except Exception:
        pass

    today_str = get_current_business_date()
    today_date = datetime.strptime(today_str, "%Y-%m-%d").date()

    cache_generated_on = today_str if is_volume_cache_fresh(conn, today_str) else get_latest_volume_cache_generated_on(conn)

    df_history_all = _get_volume_historical_data(conn, item_id=item_id, days=90)
    df_history = df_history_all[df_history_all['date'] < pd.Timestamp(today_date)].copy()

    if cache_generated_on and not df_history.empty:
        cutoff = today_date - timedelta(days=14)
        active_items = set(df_history[df_history['date'] >= pd.Timestamp(cutoff)]['item_id'].unique())
        df_active = df_history[df_history['item_id'].isin(active_items)]
        items_info = df_active.groupby('item_id').agg({
            'item_name': 'first', 'unit': 'first',
        }).reset_index()
        items_info['unit'] = items_info['item_id'].apply(lambda x: _get_item_unit_from_variants(conn, x))
        items_list = [{"item_id": r["item_id"], "item_name": r["item_name"], "unit": r["unit"]} for _, r in items_info.iterrows()]

        hist_start = pd.Timestamp(today_date - timedelta(days=30))
        df_hist_recent = df_history[df_history['date'] >= hist_start]
        hist_grid_dates = pd.date_range(hist_start, today_date - timedelta(days=1), freq='D')
        if not hist_grid_dates.empty and not items_info.empty:
            hist_bloat = items_info[['item_id', 'item_name', 'unit']].assign(key=1).merge(
                pd.DataFrame({'date': hist_grid_dates, 'key': 1}), on='key'
            ).drop('key', axis=1)
            df_hist_final = hist_bloat.merge(
                df_hist_recent[['date', 'item_id', 'volume_sold']],
                on=['date', 'item_id'],
                how='left'
            )
            df_hist_final['volume_sold'] = df_hist_final['volume_sold'].fillna(0).astype(float)
        else:
            df_hist_final = pd.DataFrame(columns=['date', 'item_id', 'item_name', 'unit', 'volume_sold'])

        history_rows = [{"date": row['date'].strftime('%Y-%m-%d'), "item_id": row['item_id'], "volume": float(row['volume_sold'])} for _, row in df_hist_final.iterrows()]
        backtest_rows = _load_volume_backtest(conn, items_info, active_items, today_date, n_days=30, item_id=item_id)
        cached = load_volume_forecasts(conn, cache_generated_on)
        name_by_id = dict(zip(items_info['item_id'], items_info['item_name']))
        unit_by_id = dict(zip(items_info['item_id'], items_info['unit']))
        filtered = [r for r in cached if r['item_id'] in active_items and (item_id is None or r['item_id'] == item_id) and r['date'] >= today_str]
        forecast_rows = [
            {"date": r['date'], "item_id": r['item_id'], "item_name": name_by_id.get(r['item_id'], r['item_id']),
             "unit": unit_by_id.get(r['item_id'], 'mg'),
             "p50": round(float(r.get('p50') or 0), 2), "p90": round(float(r.get('p90') or 0), 2),
             "probability": round(float(r.get('probability') or 0), 4),
             "volume_value": round(float(r.get('volume_value') or 0), 2),
             "recommended_volume": round(float(r.get('recommended_volume') or 0), 2)}
            for r in filtered
        ]
        forecast_rows.sort(key=lambda x: x['date'])
        distinct_dates = sorted({r['date'] for r in forecast_rows})
        limit_dates = set(distinct_dates[:days])
        forecast_rows = [r for r in forecast_rows if r['date'] in limit_dates]

        return {
            "items": items_list,
            "history": history_rows,
            "forecast": forecast_rows,
            "backtest": backtest_rows,
            "model_stale": model_stale,
            "training_in_progress": _volume_training_lock.locked(),
        }

    # No cache available — need models + history to compute, or user action
    if not models_loaded:
        from src.core.forecast_bootstrap import get_bootstrap_endpoint
        cloud_configured = bool(get_bootstrap_endpoint(conn))
        message = "Volume forecast cache empty. Use Pull from Cloud or Full Retrain."
        if not cloud_configured:
            message = "Volume forecast cache empty. Configure Cloud URL or use Full Retrain."
        return {
            "items": [], "history": [], "forecast": [], "backtest": [],
            "model_stale": True, "training_in_progress": False,
            "awaiting_action": True, "cloud_not_configured": not cloud_configured,
            "message": message,
        }

    if df_history.empty:
        return {"items": [], "history": [], "forecast": [], "backtest": [], "model_stale": model_stale, "training_in_progress": _volume_training_lock.locked()}

    cutoff = today_date - timedelta(days=14)
    active_items = set(df_history[df_history['date'] >= pd.Timestamp(cutoff)]['item_id'].unique())
    df_active = df_history[df_history['item_id'].isin(active_items)]
    items_info = df_active.groupby('item_id').agg({'item_name': 'first', 'unit': 'first'}).reset_index()
    items_info['unit'] = items_info['item_id'].apply(lambda x: _get_item_unit_from_variants(conn, x))
    items_list = [{"item_id": r["item_id"], "item_name": r["item_name"], "unit": r["unit"]} for _, r in items_info.iterrows()]

    future_dates = pd.date_range(today_date, periods=days, freq='D')
    rows = []
    for _, it in items_info.iterrows():
        for d in future_dates:
            rows.append({"date": d, "item_id": it["item_id"], "item_name": it["item_name"], "unit": it["unit"]})
    df_future = pd.DataFrame(rows)

    df_history_loaded = load_volume_sales(df=df_history)
    model_dir = _resolve_model_dir(None)
    result = forecast_volumes(df_future_dates=df_future, df_history=df_history_loaded, model_dir=model_dir)

    today_str = get_current_business_date()
    to_save = []
    for _, row in result.iterrows():
        to_save.append({
            "date": row["date"].strftime("%Y-%m-%d") if hasattr(row["date"], "strftime") else str(row["date"])[:10],
            "item_id": row["item_id"],
            "volume_value": float(row.get("predicted_p50", 0) or 0),
            "unit": row.get("unit", "mg"),
            "p50": float(row["predicted_p50"]),
            "p90": float(row["predicted_p90"]),
            "probability": float(row["probability_of_sale"]),
            "recommended_volume": float(row.get("recommended_volume", 0) or 0),
        })
    save_volume_forecasts(conn, to_save, today_str)

    hist_start = pd.Timestamp(today_date - timedelta(days=30))
    df_hist_recent = df_history[df_history['date'] >= hist_start]
    hist_grid_dates = pd.date_range(hist_start, today_date - timedelta(days=1), freq='D')
    if not hist_grid_dates.empty and not items_info.empty:
        hist_bloat = items_info[['item_id', 'item_name', 'unit']].assign(key=1).merge(
            pd.DataFrame({'date': hist_grid_dates, 'key': 1}), on='key'
        ).drop('key', axis=1)
        df_hist_final = hist_bloat.merge(
            df_hist_recent[['date', 'item_id', 'volume_sold']],
            on=['date', 'item_id'],
            how='left'
        )
        df_hist_final['volume_sold'] = df_hist_final['volume_sold'].fillna(0).astype(float)
    else:
        df_hist_final = pd.DataFrame(columns=['date', 'item_id', 'item_name', 'unit', 'volume_sold'])

    history_rows = [{"date": row['date'].strftime('%Y-%m-%d'), "item_id": row['item_id'], "volume": float(row['volume_sold'])} for _, row in df_hist_final.iterrows()]
    backtest_rows = _load_volume_backtest(conn, items_info, active_items, today_date, n_days=30, item_id=item_id)

    forecast_rows = [
        {"date": r["date"].strftime("%Y-%m-%d") if hasattr(r["date"], "strftime") else str(r["date"])[:10],
         "item_id": r["item_id"], "item_name": r["item_name"],
         "unit": r.get("unit", "mg"),
         "p50": round(float(r["predicted_p50"]), 2), "p90": round(float(r["predicted_p90"]), 2),
         "probability": round(float(r["probability_of_sale"]), 4),
         "volume_value": round(float(r["predicted_p50"]), 2),
         "recommended_volume": round(float(r.get("recommended_volume", 0) or 0), 2)}
        for _, r in result.iterrows()
    ]
    forecast_rows.sort(key=lambda x: x['date'])

    return {
        "items": items_list,
        "history": history_rows,
        "forecast": forecast_rows,
        "backtest": backtest_rows,
        "model_stale": model_stale,
        "training_in_progress": _volume_training_lock.locked(),
    }
