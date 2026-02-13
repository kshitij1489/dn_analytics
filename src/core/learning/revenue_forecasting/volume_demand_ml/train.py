"""
Volume Demand ML — Model Training

Two-stage global model:
  Stage 1 (Classification): Predict P(variant sells today)
  Stage 2 (Regression): Predict volume if sold — LightGBM/XGBoost quantile regression

Same structure as item demand; target is volume_sold (float) instead of quantity_sold (int).
"""
import json
import logging
import os
import warnings
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import (
    roc_auc_score, accuracy_score, precision_score, recall_score,
    mean_absolute_error, mean_squared_error,
)

from src.core.learning.revenue_forecasting.volume_demand_ml.dataset import densify_daily_grid
from src.core.learning.revenue_forecasting.volume_demand_ml.features import (
    build_features, prepare_train_data, get_feature_columns,
)
from src.core.learning.revenue_forecasting.volume_demand_ml.model_io import save_models

logger = logging.getLogger(__name__)

BEST_PARAMS_FILE = 'best_params.json'
_BACKEND = None


def _get_backend() -> str:
    """Detect LightGBM or XGBoost."""
    global _BACKEND
    if _BACKEND is not None:
        return _BACKEND
    try:
        import lightgbm  # noqa: F401
        _BACKEND = 'lightgbm'
        logger.info(f"ML backend: LightGBM {lightgbm.__version__}")
    except (ImportError, OSError):
        try:
            import xgboost  # noqa: F401
            _BACKEND = 'xgboost'
            logger.info(f"ML backend: XGBoost {xgboost.__version__}")
        except (ImportError, OSError):
            raise ImportError("Install lightgbm or xgboost")
    return _BACKEND


def _load_best_params(model_dir: Optional[str] = None) -> Optional[Dict]:
    """Load tuned hyper-parameters from best_params.json if present."""
    from src.core.learning.revenue_forecasting.volume_demand_ml.model_io import (
        _resolve_model_dir_for_load,
    )
    resolved = _resolve_model_dir_for_load(model_dir)
    path = os.path.join(resolved, BEST_PARAMS_FILE)
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return None


def _create_classifier(param_overrides: Optional[Dict] = None) -> Any:
    """Create classification model for P(volume > 0)."""
    backend = _get_backend()
    if backend == 'lightgbm':
        from lightgbm import LGBMClassifier
        params = dict(
            n_estimators=300, max_depth=6, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8, min_child_samples=10,
            random_state=42, verbose=-1,
        )
        if param_overrides:
            if 'min_data_in_leaf' in param_overrides:
                params.pop('min_child_samples', None)
            if 'feature_fraction' in param_overrides:
                params.pop('colsample_bytree', None)
            params.update(param_overrides)
        return LGBMClassifier(**params)
    else:
        from xgboost import XGBClassifier
        params = dict(
            n_estimators=300, max_depth=6, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8, random_state=42,
            eval_metric='logloss', verbosity=0,
        )
        if param_overrides:
            overrides = param_overrides.copy()
            if 'feature_fraction' in overrides:
                params['colsample_bytree'] = overrides.pop('feature_fraction')
            overrides.pop('num_leaves', None)
            params.update(overrides)
        return XGBClassifier(**params)


def _create_regressor(quantile: float = 0.5, param_overrides: Optional[Dict] = None) -> Any:
    """Create quantile regression model for volume prediction."""
    backend = _get_backend()
    if backend == 'lightgbm':
        from lightgbm import LGBMRegressor
        params = dict(
            objective='quantile', alpha=quantile,
            n_estimators=300, max_depth=6, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8, min_child_samples=10,
            random_state=42, verbose=-1,
        )
        if param_overrides:
            if 'min_data_in_leaf' in param_overrides:
                params.pop('min_child_samples', None)
            if 'feature_fraction' in param_overrides:
                params.pop('colsample_bytree', None)
            params.update(param_overrides)
        return LGBMRegressor(**params)
    else:
        from xgboost import XGBRegressor
        params = dict(
            objective='reg:quantileerror', quantile_alpha=quantile,
            n_estimators=300, max_depth=6, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8, random_state=42, verbosity=0,
        )
        if param_overrides:
            overrides = param_overrides.copy()
            if 'feature_fraction' in overrides:
                params['colsample_bytree'] = overrides.pop('feature_fraction')
            overrides.pop('num_leaves', None)
            params.update(overrides)
        return XGBRegressor(**params)


def train_pipeline(
    df: pd.DataFrame,
    save_path: str = 'data/models/volume_demand_ml',
    evaluate: bool = True,
) -> Dict[str, Any]:
    """
    Full training pipeline for volume demand model.

    Args:
        df: Raw volume-sales DataFrame (output of load_volume_sales).
        save_path: Directory to save model artifacts.
        evaluate: If True, evaluate on held-out 20% before final retrain.

    Returns:
        Dict with classifier, regressor_p50, regressor_p90, feature_columns, metrics.
    """
    logger.info("Starting volume demand training pipeline...")

    df = densify_daily_grid(df)
    df_feat = build_features(df, is_future=False)
    feature_cols = get_feature_columns()

    dates_sorted = sorted(df_feat['date'].unique())
    split_idx = int(len(dates_sorted) * 0.8)
    cutoff_date = dates_sorted[split_idx]
    train_mask = df_feat['date'] < cutoff_date
    test_mask = df_feat['date'] >= cutoff_date
    df_train = df_feat[train_mask]
    df_test = df_feat[test_mask]

    X_clf_train, y_clf_train, X_reg_train, y_reg_train = prepare_train_data(df_train)

    n_zero = len(y_clf_train) - int(y_clf_train.sum())
    if n_zero == 0:
        logger.warning("Classifier has NO zero-volume rows — degenerate model risk.")
    else:
        logger.info(f"Classifier balance: {int(y_clf_train.sum())} positive, {n_zero} zero")

    best_params = _load_best_params(save_path)
    clf_overrides = best_params.get('classifier') if best_params else None
    reg50_overrides = best_params.get('regressor_p50') if best_params else None
    reg90_overrides = best_params.get('regressor_p90') if best_params else None

    logger.info("Training classifier (P(volume > 0))...")
    clf_base = _create_classifier(clf_overrides)
    clf = CalibratedClassifierCV(clf_base, method='isotonic', cv=3)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        clf.fit(X_clf_train, y_clf_train)
    if hasattr(clf, 'calibrated_classifiers_') and len(clf.calibrated_classifiers_) > 0:
        inner = clf.calibrated_classifiers_[0].estimator
        if hasattr(inner, 'feature_importances_'):
            clf.feature_importances_ = inner.feature_importances_

    logger.info("Training regressor p50...")
    reg_p50 = _create_regressor(quantile=0.5, param_overrides=reg50_overrides)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        reg_p50.fit(X_reg_train, y_reg_train)

    logger.info("Training regressor p90...")
    reg_p90 = _create_regressor(quantile=0.9, param_overrides=reg90_overrides)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        reg_p90.fit(X_reg_train, y_reg_train)

    metrics = {}
    if evaluate:
        metrics = _evaluate_models(df_test, clf, reg_p50, reg_p90, feature_cols)

    logger.info("Retraining on full dataset...")
    X_clf_all, y_clf_all, X_reg_all, y_reg_all = prepare_train_data(df_feat)
    clf_base_full = _create_classifier(clf_overrides)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        clf_base_full.fit(X_clf_all, y_clf_all)
        reg_p50.fit(X_reg_all, y_reg_all)
        reg_p90.fit(X_reg_all, y_reg_all)

    X_cal = df_test[feature_cols]
    y_cal = (df_test['volume_sold'] > 0).astype(int)
    clf = CalibratedClassifierCV(clf_base_full, method='isotonic', cv='prefit')
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        clf.fit(X_cal, y_cal)
    if hasattr(clf_base_full, 'feature_importances_'):
        clf.feature_importances_ = clf_base_full.feature_importances_

    logger.info(f"Saving models to {save_path}...")
    save_models(clf, reg_p50, reg_p90, feature_cols, save_path)

    return {
        'classifier': clf,
        'regressor_p50': reg_p50,
        'regressor_p90': reg_p90,
        'feature_columns': feature_cols,
        'metrics': metrics,
    }


def _evaluate_models(
    df_test: pd.DataFrame,
    clf: Any,
    reg_p50: Any,
    reg_p90: Any,
    feature_cols: list,
) -> Dict[str, float]:
    """Evaluate on held-out test data."""
    logger.info("Evaluating on held-out test data...")
    X_test = df_test[feature_cols]
    y_test_clf = (df_test['volume_sold'] > 0).astype(int)
    metrics = {}

    try:
        y_prob = clf.predict_proba(X_test)[:, 1]
        y_pred = clf.predict(X_test)
        if len(np.unique(y_test_clf)) > 1:
            metrics['clf_auc'] = round(roc_auc_score(y_test_clf, y_prob), 4)
        else:
            metrics['clf_auc'] = None
        metrics['clf_accuracy'] = round(accuracy_score(y_test_clf, y_pred), 4)
        metrics['clf_precision'] = round(precision_score(y_test_clf, y_pred, zero_division=0), 4)
        metrics['clf_recall'] = round(recall_score(y_test_clf, y_pred, zero_division=0), 4)
    except Exception as e:
        logger.warning(f"Classification eval failed: {e}")

    mask_pos = df_test['volume_sold'] > 0
    if mask_pos.sum() > 0:
        X_test_pos = df_test.loc[mask_pos, feature_cols]
        y_test_pos = df_test.loc[mask_pos, 'volume_sold'].values
        scale = df_test.loc[mask_pos, 'item_median_volume'].values
        try:
            pred_p50_norm = reg_p50.predict(X_test_pos)
            pred_p90_norm = reg_p90.predict(X_test_pos)
            pred_p50 = pred_p50_norm * scale
            pred_p90 = pred_p90_norm * scale
            metrics['reg_mae_p50'] = round(mean_absolute_error(y_test_pos, pred_p50), 4)
            metrics['reg_rmse_p50'] = round(np.sqrt(mean_squared_error(y_test_pos, pred_p50)), 4)
            metrics['reg_p90_coverage'] = round((y_test_pos <= pred_p90).mean(), 4)
        except Exception as e:
            logger.warning(f"Regression eval failed: {e}")

    return metrics
