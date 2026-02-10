"""
Item Demand ML — Model Training

Two-stage global model:
  Stage 1 (Classification): Predict P(item sells today) — LightGBM Classifier
  Stage 2 (Regression): Predict quantity if sold — LightGBM quantile regression (p50 + p90)

Falls back to XGBoost if LightGBM is unavailable.
"""
import json
import logging
import os
import warnings
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import (
    roc_auc_score, accuracy_score, precision_score, recall_score,
    mean_absolute_error, mean_squared_error,
)

from src.core.learning.revenue_forecasting.item_demand_ml.dataset import densify_daily_grid
from src.core.learning.revenue_forecasting.item_demand_ml.features import (
    build_features, prepare_train_data, get_feature_columns,
)
from src.core.learning.revenue_forecasting.item_demand_ml.model_io import save_models

logger = logging.getLogger(__name__)

# File written by tune.py containing optimised hyper-parameters
BEST_PARAMS_FILE = 'best_params.json'

# ---- Backend Selection ----
_BACKEND = None


def _get_backend() -> str:
    """Detect available ML backend (LightGBM preferred, XGBoost fallback)."""
    global _BACKEND
    if _BACKEND is not None:
        return _BACKEND

    try:
        import lightgbm  # noqa: F401
        _BACKEND = 'lightgbm'
        logger.info(f"ML backend: LightGBM {lightgbm.__version__}")
    except (ImportError, OSError) as lgb_err:
        logger.warning(f"LightGBM unavailable ({lgb_err}), trying XGBoost...")
        try:
            import xgboost  # noqa: F401
            _BACKEND = 'xgboost'
            logger.info(f"ML backend: XGBoost {xgboost.__version__}")
        except (ImportError, OSError):
            raise ImportError(
                "Neither LightGBM nor XGBoost is installed/loadable. "
                "Install one: pip install lightgbm  OR  pip install xgboost"
            )
    return _BACKEND


def _load_best_params(model_dir: Optional[str] = None) -> Optional[Dict]:
    """
    Load tuned hyper-parameters from ``best_params.json`` if the file exists.

    The file is written by ``tune.py`` after a manual tuning run.
    Returns ``None`` when no tuned params are available (safe fallback to
    hard-coded defaults).
    """
    from src.core.learning.revenue_forecasting.item_demand_ml.model_io import (
        _resolve_model_dir_for_load,
    )
    resolved = _resolve_model_dir_for_load(model_dir)
    path = os.path.join(resolved, BEST_PARAMS_FILE)
    if os.path.exists(path):
        try:
            with open(path) as f:
                params = json.load(f)
            logger.info(f"Loaded tuned hyper-parameters from {path}")
            return params
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(f"Could not read {path}: {exc} — using defaults")
    return None


def _create_classifier(param_overrides: Optional[Dict] = None) -> Any:
    """
    Create classification model for P(sold > 0).

    Args:
        param_overrides: Optional dict from ``best_params.json`` that overrides
                         default hyper-parameters.  Native LightGBM names like
                         ``num_leaves``, ``min_data_in_leaf``, ``feature_fraction``
                         are accepted and conflicting sklearn aliases are removed
                         automatically.
    """
    backend = _get_backend()
    if backend == 'lightgbm':
        from lightgbm import LGBMClassifier
        params: Dict[str, Any] = dict(
            n_estimators=300,
            max_depth=6,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            min_child_samples=10,
            random_state=42,
            verbose=-1,
        )
        if param_overrides:
            # Remove sklearn aliases that conflict with native LightGBM names
            if 'min_data_in_leaf' in param_overrides:
                params.pop('min_child_samples', None)
            if 'feature_fraction' in param_overrides:
                params.pop('colsample_bytree', None)
            params.update(param_overrides)
        return LGBMClassifier(**params)
    else:
        from xgboost import XGBClassifier
        params = dict(
            n_estimators=300,
            max_depth=6,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            eval_metric='logloss',
            verbosity=0,
        )
        if param_overrides:
            overrides = param_overrides.copy()  # avoid mutating caller's dict
            if 'feature_fraction' in overrides:
                params['colsample_bytree'] = overrides.pop('feature_fraction')
            if 'num_leaves' in overrides:
                overrides.pop('num_leaves')  # XGBoost uses max_depth instead
            params.update(overrides)
        return XGBClassifier(**params)


def _create_regressor(
    quantile: float = 0.5,
    param_overrides: Optional[Dict] = None,
) -> Any:
    """
    Create quantile regression model for quantity prediction.

    Args:
        quantile: Target quantile (0.5 for median, 0.9 for upper bound).
        param_overrides: Optional dict from ``best_params.json`` — same alias
                         handling as ``_create_classifier``.
    """
    backend = _get_backend()
    if backend == 'lightgbm':
        from lightgbm import LGBMRegressor
        params: Dict[str, Any] = dict(
            objective='quantile',
            alpha=quantile,
            n_estimators=300,
            max_depth=6,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            min_child_samples=10,
            random_state=42,
            verbose=-1,
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
            objective='reg:quantileerror',
            quantile_alpha=quantile,
            n_estimators=300,
            max_depth=6,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            verbosity=0,
        )
        if param_overrides:
            overrides = param_overrides.copy()
            if 'feature_fraction' in overrides:
                params['colsample_bytree'] = overrides.pop('feature_fraction')
            if 'num_leaves' in overrides:
                overrides.pop('num_leaves')
            params.update(overrides)
        return XGBRegressor(**params)


def train_pipeline(
    df: pd.DataFrame,
    save_path: str = 'data/models/item_demand_ml',
    evaluate: bool = True,
) -> Dict[str, Any]:
    """
    Full training pipeline: feature engineering → train → evaluate → retrain → save.

    The pipeline splits data 80/20 by date, trains on the first 80% of dates,
    evaluates on the held-out 20%, then retrains on ALL data for production.
    This avoids data leakage while maximising production model quality.

    Args:
        df: Raw item-sales DataFrame (output of dataset.load_item_sales).
        save_path: Directory to save trained model artifacts.
        evaluate: If True, evaluate on held-out 20% before final retrain.

    Returns:
        Dict with keys: classifier, regressor_p50, regressor_p90, feature_columns, metrics.
    """
    logger.info("Starting item demand training pipeline...")

    # 1. Densify + Feature Engineering
    logger.info("Densifying daily grid (adding zero-sales rows)...")
    df = densify_daily_grid(df)

    logger.info("Building features...")
    df_feat = build_features(df, is_future=False)

    feature_cols = get_feature_columns()

    # 2. Time-based split: train on first 80% of dates, evaluate on last 20%
    dates_sorted = sorted(df_feat['date'].unique())
    split_idx = int(len(dates_sorted) * 0.8)
    cutoff_date = dates_sorted[split_idx]

    train_mask = df_feat['date'] < cutoff_date
    test_mask = df_feat['date'] >= cutoff_date

    df_train = df_feat[train_mask]
    df_test = df_feat[test_mask]

    X_clf_train, y_clf_train, X_reg_train, y_reg_train = prepare_train_data(df_train)

    # Warn if classifier training data is degenerate (no/few zero-sales rows)
    n_positive = int(y_clf_train.sum())
    n_zero = len(y_clf_train) - n_positive
    if n_zero == 0:
        logger.warning(
            "Classifier training data has NO zero-sales rows. "
            "This produces a degenerate model. Check that densify_daily_grid "
            "is generating zero-fill rows correctly."
        )
    else:
        logger.info(
            f"Classifier class balance: {n_positive} positive, {n_zero} zero "
            f"({n_zero / len(y_clf_train):.1%} zero-rate)"
        )

    # ---- Load tuned hyper-parameters if available (written by tune.py) ----
    best_params = _load_best_params(save_path)
    clf_overrides = best_params.get('classifier') if best_params else None
    reg50_overrides = best_params.get('regressor_p50') if best_params else None
    reg90_overrides = best_params.get('regressor_p90') if best_params else None
    if best_params:
        logger.info(
            f"Using tuned hyper-parameters "
            f"(from {best_params.get('created_at', 'unknown')})"
        )
    else:
        logger.info("No tuned params found — using default hyper-parameters")

    # 3. Train on train split
    # PART 2: Probability calibration — tree classifiers output uncalibrated
    # probabilities. Wrapping with CalibratedClassifierCV (isotonic, cv=3)
    # produces well-calibrated P(sold) values, improving production quantities.
    logger.info("Training classifier (P(sold > 0)) on train split...")
    clf_base = _create_classifier(clf_overrides)
    clf = CalibratedClassifierCV(clf_base, method='isotonic', cv=3)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        clf.fit(X_clf_train, y_clf_train)
    # Preserve feature importances for notebook inspection
    # (CalibratedClassifierCV does not expose them directly)
    if hasattr(clf, 'calibrated_classifiers_') and len(clf.calibrated_classifiers_) > 0:
        inner = clf.calibrated_classifiers_[0].estimator
        if hasattr(inner, 'feature_importances_'):
            clf.feature_importances_ = inner.feature_importances_
    logger.info("Classifier trained (calibrated, isotonic, cv=3).")

    logger.info("Training regressor (p50 — median quantity) on train split...")
    reg_p50 = _create_regressor(quantile=0.5, param_overrides=reg50_overrides)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        reg_p50.fit(X_reg_train, y_reg_train)
    logger.info("Regressor p50 trained.")

    logger.info("Training regressor (p90 — upper bound quantity) on train split...")
    reg_p90 = _create_regressor(quantile=0.9, param_overrides=reg90_overrides)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        reg_p90.fit(X_reg_train, y_reg_train)
    logger.info("Regressor p90 trained.")

    # 4. Evaluate on held-out test data (no leakage)
    metrics = {}
    if evaluate:
        metrics = _evaluate_models(df_test, clf, reg_p50, reg_p90, feature_cols)

    # 5. Retrain on ALL data for production deployment
    logger.info("Retraining all models on full dataset for production...")
    X_clf_all, y_clf_all, X_reg_all, y_reg_all = prepare_train_data(df_feat)

    # Train base classifier on 100% of data for maximum model quality,
    # then calibrate with cv='prefit' on the held-out test split.
    # This ensures every training example is seen by the base model,
    # unlike cv=3 where each internal estimator only sees ~67%.
    clf_base_full = _create_classifier(clf_overrides)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        clf_base_full.fit(X_clf_all, y_clf_all)
        reg_p50.fit(X_reg_all, y_reg_all)
        reg_p90.fit(X_reg_all, y_reg_all)

    # Calibrate on the 20% test split (cv='prefit' = no internal CV,
    # just learn isotonic mapping on the provided data)
    X_cal = df_test[feature_cols]
    y_cal = (df_test['quantity_sold'] > 0).astype(int)
    clf = CalibratedClassifierCV(clf_base_full, method='isotonic', cv='prefit')
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        clf.fit(X_cal, y_cal)

    # Preserve feature importances (CalibratedClassifierCV hides them)
    if hasattr(clf_base_full, 'feature_importances_'):
        clf.feature_importances_ = clf_base_full.feature_importances_
    logger.info("Full-data retrain complete (base trained on 100%, calibrated on test split).")

    # 6. Save
    logger.info(f"Saving models to {save_path}...")
    save_models(clf, reg_p50, reg_p90, feature_cols, save_path)
    logger.info("Training pipeline complete.")

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
    """
    Evaluate models on held-out test data.

    The caller is responsible for providing a df_test that was NOT used
    during training — this prevents data leakage.

    Returns dict of metric name → value.
    """
    logger.info("Evaluating models on held-out test data...")

    X_test = df_test[feature_cols]
    y_test_clf = (df_test['quantity_sold'] > 0).astype(int)

    metrics = {}

    # Classification metrics
    try:
        y_prob = clf.predict_proba(X_test)[:, 1]
        y_pred = clf.predict(X_test)

        # Guard AUC against single-class test data
        n_unique_classes = len(np.unique(y_test_clf))
        if n_unique_classes > 1:
            metrics['clf_auc'] = round(roc_auc_score(y_test_clf, y_prob), 4)
        else:
            logger.warning(
                f"Skipping AUC — test data has only {n_unique_classes} class. "
                "This usually means the test period has no zero-sales rows."
            )
            metrics['clf_auc'] = None

        metrics['clf_accuracy'] = round(accuracy_score(y_test_clf, y_pred), 4)
        metrics['clf_precision'] = round(precision_score(y_test_clf, y_pred, zero_division=0), 4)
        metrics['clf_recall'] = round(recall_score(y_test_clf, y_pred, zero_division=0), 4)

        auc_str = f"{metrics['clf_auc']:.4f}" if metrics['clf_auc'] is not None else 'N/A'
        logger.info(f"Classification — AUC={auc_str} "
                    f"Acc={metrics['clf_accuracy']:.4f} "
                    f"Prec={metrics['clf_precision']:.4f} Rec={metrics['clf_recall']:.4f}")
    except Exception as e:
        logger.warning(f"Classification eval failed: {e}")

    # Regression metrics (on positive-sales subset)
    mask_pos = df_test['quantity_sold'] > 0
    if mask_pos.sum() > 0:
        X_test_pos = df_test.loc[mask_pos, feature_cols]
        y_test_pos = df_test.loc[mask_pos, 'quantity_sold']

        try:
            pred_p50 = reg_p50.predict(X_test_pos)
            pred_p90 = reg_p90.predict(X_test_pos)

            metrics['reg_mae_p50'] = round(mean_absolute_error(y_test_pos, pred_p50), 4)
            metrics['reg_rmse_p50'] = round(np.sqrt(mean_squared_error(y_test_pos, pred_p50)), 4)
            metrics['reg_mae_p90'] = round(mean_absolute_error(y_test_pos, pred_p90), 4)

            # Coverage: what fraction of actuals fall below p90?
            metrics['reg_p90_coverage'] = round((y_test_pos.values <= pred_p90).mean(), 4)

            logger.info(f"Regression p50 — MAE={metrics['reg_mae_p50']:.2f} "
                        f"RMSE={metrics['reg_rmse_p50']:.2f}")
            logger.info(f"Regression p90 — MAE={metrics['reg_mae_p90']:.2f} "
                        f"Coverage={metrics['reg_p90_coverage']:.2%}")
        except Exception as e:
            logger.warning(f"Regression eval failed: {e}")

    return metrics
