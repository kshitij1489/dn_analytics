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

    Args:
        classifier: Trained classification model.
        regressor_p50: Trained p50 quantile regressor.
        regressor_p90: Trained p90 quantile regressor.
        feature_columns: Ordered list of feature column names.
        model_dir: Directory to save artifacts in. Resolved automatically if None.
    """
    model_dir = _resolve_model_dir(model_dir)
    os.makedirs(model_dir, exist_ok=True)

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

    The model is considered stale if it was last saved *before* the current
    business date.  This means each new business day triggers one background
    retrain so that yesterday's sales are incorporated.

    Returns True when:
      - No model file exists on disk.
      - The model file's modification date is earlier than today's business date.
    """
    trained = get_model_trained_date(model_dir)
    if trained is None:
        return True

    from src.core.utils.business_date import get_current_business_date
    today_biz = datetime.strptime(
        get_current_business_date(), '%Y-%m-%d'
    ).date()

    return trained.date() < today_biz
