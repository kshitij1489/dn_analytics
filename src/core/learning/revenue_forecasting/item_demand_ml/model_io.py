"""
Item Demand ML — Model I/O

Save and load trained model artifacts (classifier, regressors, feature columns).
Uses joblib for model serialization and JSON for metadata.
"""
import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import joblib

from src.core.utils.path_helper import get_data_path, get_resource_path

logger = logging.getLogger(__name__)

_MODEL_RELATIVE_PATH = os.path.join('data', 'models', 'item_demand_ml')


def _resolve_model_dir(model_dir: Optional[str] = None) -> str:
    """
    Resolve model directory for both dev and frozen (.dmg) builds.

    For saving (write): uses get_data_path → writable user data dir in production.
    For loading (read): checks writable dir first, then falls back to bundled resources.
    """
    if model_dir is not None:
        return model_dir
    return get_data_path(_MODEL_RELATIVE_PATH)


def _resolve_model_dir_for_load(model_dir: Optional[str] = None) -> str:
    """
    Resolve model directory for loading, checking writable dir first, then bundled.
    """
    if model_dir is not None:
        return model_dir

    # Check writable user data dir first (retrained models)
    writable = get_data_path(_MODEL_RELATIVE_PATH)
    if os.path.isdir(writable):
        return writable

    # Fall back to bundled read-only resources (shipped models)
    return get_resource_path(_MODEL_RELATIVE_PATH)

# File names
CLASSIFIER_FILE = 'item_demand_classifier.pkl'
REGRESSOR_P50_FILE = 'item_demand_regressor_p50.pkl'
REGRESSOR_P90_FILE = 'item_demand_regressor_p90.pkl'
FEATURE_COLUMNS_FILE = 'feature_columns.json'


def save_models(
    classifier: Any,
    regressor_p50: Any,
    regressor_p90: Any,
    feature_columns: List[str],
    model_dir: Optional[str] = None,
) -> None:
    """
    Save all model artifacts to disk.

    Before saving, removes deprecated files (any .pkl or item_demand_* not in
    the current required set) so renamed/versioned models do not accumulate.

    Args:
        classifier: Trained classification model.
        regressor_p50: Trained p50 quantile regressor.
        regressor_p90: Trained p90 quantile regressor.
        feature_columns: Ordered list of feature column names.
        model_dir: Directory to save artifacts in. Resolved automatically if None.
    """
    model_dir = _resolve_model_dir(model_dir)
    os.makedirs(model_dir, exist_ok=True)

    # Remove deprecated model files (not in required set) before saving
    required_files = {CLASSIFIER_FILE, REGRESSOR_P50_FILE, REGRESSOR_P90_FILE, FEATURE_COLUMNS_FILE}
    for fname in os.listdir(model_dir):
        if fname in required_files:
            continue
        if fname.endswith('.pkl') or fname.startswith('item_demand_'):
            fpath = os.path.join(model_dir, fname)
            try:
                os.remove(fpath)
                logger.info(f"Removed deprecated model file: {fpath}")
            except Exception as e:
                logger.warning(f"Failed to remove deprecated file {fpath}: {e}")

    joblib.dump(classifier, os.path.join(model_dir, CLASSIFIER_FILE))
    joblib.dump(regressor_p50, os.path.join(model_dir, REGRESSOR_P50_FILE))
    joblib.dump(regressor_p90, os.path.join(model_dir, REGRESSOR_P90_FILE))

    with open(os.path.join(model_dir, FEATURE_COLUMNS_FILE), 'w') as f:
        json.dump(feature_columns, f, indent=2)

    logger.info(f"Saved all model artifacts to {model_dir}/")


def load_models(
    model_dir: Optional[str] = None,
) -> Tuple[Any, Any, Any, List[str]]:
    """
    Load all model artifacts from disk.

    Returns:
        (classifier, regressor_p50, regressor_p90, feature_columns)

    Raises:
        FileNotFoundError: If any required file is missing.
    """
    model_dir = _resolve_model_dir_for_load(model_dir)
    required_files = [CLASSIFIER_FILE, REGRESSOR_P50_FILE, REGRESSOR_P90_FILE, FEATURE_COLUMNS_FILE]
    for fname in required_files:
        fpath = os.path.join(model_dir, fname)
        if not os.path.exists(fpath):
            raise FileNotFoundError(f"Model file not found: {fpath}")

    classifier = joblib.load(os.path.join(model_dir, CLASSIFIER_FILE))
    regressor_p50 = joblib.load(os.path.join(model_dir, REGRESSOR_P50_FILE))
    regressor_p90 = joblib.load(os.path.join(model_dir, REGRESSOR_P90_FILE))

    with open(os.path.join(model_dir, FEATURE_COLUMNS_FILE), 'r') as f:
        feature_columns = json.load(f)

    logger.info(f"Loaded model artifacts from {model_dir}/")
    return classifier, regressor_p50, regressor_p90, feature_columns


# ---- Lazy Loading Singleton ----
# Models are loaded once on first use, then cached in memory.

_cached_models: Optional[Tuple[Any, Any, Any, List[str]]] = None


def get_models(model_dir: Optional[str] = None) -> Tuple[Any, Any, Any, List[str]]:
    """
    Get models with lazy loading. Models are loaded once and cached.

    Returns:
        (classifier, regressor_p50, regressor_p90, feature_columns)
    """
    global _cached_models
    if _cached_models is None:
        logger.info("Lazy-loading item demand models (first call)...")
        _cached_models = load_models(model_dir)
    return _cached_models


def clear_model_cache() -> None:
    """Clear the in-memory model cache (useful after retraining)."""
    global _cached_models
    _cached_models = None
    logger.info("Item demand model cache cleared.")


# ---------------------------------------------------------------------------
# Staleness detection — used by the API to trigger background retraining
# ---------------------------------------------------------------------------

def get_model_trained_date(model_dir: Optional[str] = None) -> Optional[datetime]:
    """
    Return when the model was last saved (file modification time of classifier).

    Returns None if no trained model exists on disk.
    """
    resolved = _resolve_model_dir_for_load(model_dir)
    clf_path = os.path.join(resolved, CLASSIFIER_FILE)
    if not os.path.exists(clf_path):
        return None
    return datetime.fromtimestamp(os.path.getmtime(clf_path))


def is_model_stale(model_dir: Optional[str] = None) -> bool:
    """
    Check whether the saved model is stale and should be retrained.

    Uses **business dates** (5 AM IST boundary) instead of calendar dates
    to avoid the edge case where a model trained between midnight and 5 AM
    appears "fresh" by calendar date but was actually trained during the
    previous business day and is missing yesterday's data.

    Logic:
      1. If current time < 5 AM IST: return False (business day hasn't started,
         the previous day's data isn't finalized yet — no point retraining).
      2. Convert the model's file mtime to a business date.
      3. If the model's business-date < today's business-date: stale.

    Returns True when:
      - No model file exists on disk.
      - Current time >= 5 AM IST and the model was last saved during a
        previous business day.
    """
    trained = get_model_trained_date(model_dir)
    if trained is None:
        return True

    from src.core.utils.business_date import (
        get_current_business_date,
        get_business_date_from_datetime,
        BUSINESS_DAY_START_HOUR,
        IST,
    )

    # Guard: only trigger retraining after the business day starts (5 AM IST).
    # Before 5 AM the previous day's orders are still coming in — retraining
    # now would still miss yesterday's data.
    now_ist = datetime.now(IST)
    if now_ist.hour < BUSINESS_DAY_START_HOUR:
        return False

    today_biz = get_current_business_date()

    # Convert file mtime to the business date it belongs to.
    # A model saved at 3 AM Feb 11 belongs to business day Feb 10.
    trained_biz = get_business_date_from_datetime(trained)

    return trained_biz < today_biz


def delete_models(model_dir: Optional[str] = None) -> None:
    """
    Delete all trained model artifacts from disk to force full retraining.
    """
    model_dir = _resolve_model_dir(model_dir)
    if not os.path.exists(model_dir):
        logger.info(f"No model directory found at {model_dir}, nothing to delete.")
        return

    files_to_remove = [CLASSIFIER_FILE, REGRESSOR_P50_FILE, REGRESSOR_P90_FILE, FEATURE_COLUMNS_FILE]
    deleted_count = 0
    
    for fname in files_to_remove:
        fpath = os.path.join(model_dir, fname)
        if os.path.exists(fpath):
            try:
                os.remove(fpath)
                deleted_count += 1
                logger.info(f"Deleted model artifact: {fpath}")
            except Exception as e:
                logger.error(f"Failed to delete {fpath}: {e}")

    # Also clear the in-memory cache
    clear_model_cache()
    logger.info(f"Deleted {deleted_count} model artifacts from {model_dir}/")
