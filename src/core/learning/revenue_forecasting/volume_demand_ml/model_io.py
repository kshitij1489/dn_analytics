"""
Volume Demand ML — Model I/O

Save and load trained model artifacts (classifier, regressors, feature columns).
Uses a separate directory from item_demand_ml.
"""
import json
import logging
import os
from datetime import datetime
from typing import Any, List, Optional, Tuple

import joblib

from src.core.utils.path_helper import get_data_path, get_resource_path

logger = logging.getLogger(__name__)

_MODEL_RELATIVE_PATH = os.path.join('data', 'models', 'volume_demand_ml')


def _resolve_model_dir(model_dir: Optional[str] = None) -> str:
    """Resolve model directory for saving (writable user data dir)."""
    if model_dir is not None:
        return model_dir
    return get_data_path(_MODEL_RELATIVE_PATH)


def _resolve_model_dir_for_load(model_dir: Optional[str] = None) -> str:
    """Resolve for loading: writable dir first, then bundled."""
    if model_dir is not None:
        return model_dir
    writable = get_data_path(_MODEL_RELATIVE_PATH)
    if os.path.isdir(writable):
        return writable
    return get_resource_path(_MODEL_RELATIVE_PATH)


CLASSIFIER_FILE = 'volume_demand_classifier.pkl'
REGRESSOR_P50_FILE = 'volume_demand_regressor_p50.pkl'
REGRESSOR_P90_FILE = 'volume_demand_regressor_p90.pkl'
FEATURE_COLUMNS_FILE = 'feature_columns.json'


def save_models(
    classifier: Any,
    regressor_p50: Any,
    regressor_p90: Any,
    feature_columns: List[str],
    model_dir: Optional[str] = None,
) -> None:
    """Save all model artifacts to disk."""
    model_dir = _resolve_model_dir(model_dir)
    os.makedirs(model_dir, exist_ok=True)

    required_files = {CLASSIFIER_FILE, REGRESSOR_P50_FILE, REGRESSOR_P90_FILE, FEATURE_COLUMNS_FILE}
    for fname in os.listdir(model_dir):
        if fname in required_files:
            continue
        if fname.endswith('.pkl') or fname.startswith('volume_demand_'):
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

    logger.info(f"Saved volume model artifacts to {model_dir}/")


def load_models(
    model_dir: Optional[str] = None,
) -> Tuple[Any, Any, Any, List[str]]:
    """Load all model artifacts from disk. Raises FileNotFoundError if missing."""
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

    logger.info(f"Loaded volume model artifacts from {model_dir}/")
    return classifier, regressor_p50, regressor_p90, feature_columns


_cached_models: Optional[Tuple[Any, Any, Any, List[str]]] = None


def get_models(model_dir: Optional[str] = None) -> Tuple[Any, Any, Any, List[str]]:
    """Get models with lazy loading."""
    global _cached_models
    if _cached_models is None:
        logger.info("Lazy-loading volume demand models (first call)...")
        _cached_models = load_models(model_dir)
    return _cached_models


def clear_model_cache() -> None:
    """Clear the in-memory model cache."""
    global _cached_models
    _cached_models = None
    logger.info("Volume demand model cache cleared.")


def get_model_trained_date(model_dir: Optional[str] = None) -> Optional[datetime]:
    """Return when the model was last saved, or None."""
    resolved = _resolve_model_dir_for_load(model_dir)
    clf_path = os.path.join(resolved, CLASSIFIER_FILE)
    if not os.path.exists(clf_path):
        return None
    return datetime.fromtimestamp(os.path.getmtime(clf_path))


def is_model_stale(model_dir: Optional[str] = None) -> bool:
    """Check whether the saved model is stale (trained before current business date)."""
    trained = get_model_trained_date(model_dir)
    if trained is None:
        return True

    from src.core.utils.business_date import (
        get_current_business_date,
        get_business_date_from_datetime,
        BUSINESS_DAY_START_HOUR,
        IST,
    )
    now_ist = datetime.now(IST)
    if now_ist.hour < BUSINESS_DAY_START_HOUR:
        return False
    today_biz = get_current_business_date()
    trained_biz = get_business_date_from_datetime(trained)
    return trained_biz < today_biz


def delete_models(model_dir: Optional[str] = None) -> None:
    """Delete all trained model artifacts from disk.

    Always ensures the writable model directory exists (even if empty) so that
    ``_resolve_model_dir_for_load`` prefers it over the bundled resource path.
    This prevents fallback to bundled models after an explicit reset.
    """
    model_dir = _resolve_model_dir(model_dir)

    # Ensure the writable directory exists so the load path never falls back
    # to bundled models after a reset.
    os.makedirs(model_dir, exist_ok=True)

    files_to_remove = [CLASSIFIER_FILE, REGRESSOR_P50_FILE, REGRESSOR_P90_FILE, FEATURE_COLUMNS_FILE]
    for fname in files_to_remove:
        fpath = os.path.join(model_dir, fname)
        if os.path.exists(fpath):
            try:
                os.remove(fpath)
                logger.info(f"Deleted: {fpath}")
            except Exception as e:
                logger.error(f"Failed to delete {fpath}: {e}")
    clear_model_cache()
    logger.info("Volume model artifacts deleted.")
