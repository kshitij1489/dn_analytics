"""
Item Demand ML — Hyperparameter Tuning

Optimises model parameters using **rolling walk-forward validation** and a
**business cost function** that penalises stockouts more than waste:

  stockout_loss = 2 × (actual - predicted)   when actual > predicted
  waste_loss    = 1 × (predicted - actual)    when predicted > actual

The tuning is run MANUALLY (not during normal training). When finished it
saves ``best_params.json`` alongside the model artifacts.  The regular
``train_pipeline()`` in ``train.py`` will pick up those params automatically
the next time it runs.

Usage
-----
    from src.core.learning.revenue_forecasting.item_demand_ml.tune import (
        run_hyperparameter_search,
    )
    from src.core.learning.revenue_forecasting.item_demand_ml.dataset import (
        load_item_sales,
    )

    df = load_item_sales(...)
    best = run_hyperparameter_search(df, n_trials=35)
"""
import json
import logging
import os
import random
import warnings
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from src.core.learning.revenue_forecasting.item_demand_ml.dataset import densify_daily_grid
from src.core.learning.revenue_forecasting.item_demand_ml.features import (
    build_features,
    get_feature_columns,
    prepare_train_data,
)
from src.core.learning.revenue_forecasting.item_demand_ml.model_io import _resolve_model_dir

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BEST_PARAMS_FILE = 'best_params.json'

# Asymmetric business cost — stockouts are costlier than waste
STOCKOUT_PENALTY = 2.0
WASTE_PENALTY = 1.0

# Rolling walk-forward split percentages
_SPLIT_PCTS = (0.6, 0.7, 0.8, 0.9)

# Forecast horizon per fold (days)
_HORIZON_DAYS = 7

# ---------------------------------------------------------------------------
# Search Spaces  (native LightGBM parameter names)
# ---------------------------------------------------------------------------

CLF_SEARCH_SPACE: Dict[str, list] = {
    'num_leaves':       [15, 31, 63],
    'min_data_in_leaf': [10, 20, 40],
    'learning_rate':    [0.03, 0.05, 0.1],
    'feature_fraction': [0.7, 0.9],
}

REG_SEARCH_SPACE: Dict[str, list] = {
    'num_leaves':       [31, 63, 127],
    'min_data_in_leaf': [5, 15, 30],
    'learning_rate':    [0.03, 0.05, 0.1],
}


# ---------------------------------------------------------------------------
# Backend detection (mirrors train.py)
# ---------------------------------------------------------------------------

def _get_backend() -> str:
    """Detect available ML backend (LightGBM preferred, XGBoost fallback)."""
    try:
        import lightgbm  # noqa: F401
        return 'lightgbm'
    except (ImportError, OSError):
        pass
    try:
        import xgboost  # noqa: F401
        return 'xgboost'
    except (ImportError, OSError):
        raise ImportError(
            "Neither LightGBM nor XGBoost is installed. "
            "Install one: pip install lightgbm  OR  pip install xgboost"
        )


# ---------------------------------------------------------------------------
# Model factories (light versions — no calibration wrapper for speed)
# ---------------------------------------------------------------------------

def _make_classifier(params: dict, backend: str) -> Any:
    """Instantiate a classifier with the given hyper-parameters."""
    if backend == 'lightgbm':
        from lightgbm import LGBMClassifier
        return LGBMClassifier(
            n_estimators=300,
            max_depth=6,
            subsample=0.8,
            random_state=42,
            verbose=-1,
            **params,
        )
    # XGBoost fallback — translate LightGBM native param names
    from xgboost import XGBClassifier
    return XGBClassifier(
        n_estimators=300,
        max_depth=6,
        subsample=0.8,
        random_state=42,
        eval_metric='logloss',
        verbosity=0,
        learning_rate=params.get('learning_rate', 0.05),
        colsample_bytree=params.get('feature_fraction', 0.8),
    )


def _make_regressor(params: dict, quantile: float, backend: str) -> Any:
    """Instantiate a quantile regressor with the given hyper-parameters."""
    if backend == 'lightgbm':
        from lightgbm import LGBMRegressor
        return LGBMRegressor(
            objective='quantile',
            alpha=quantile,
            n_estimators=300,
            max_depth=6,
            subsample=0.8,
            random_state=42,
            verbose=-1,
            **params,
        )
    from xgboost import XGBRegressor
    return XGBRegressor(
        objective='reg:quantileerror',
        quantile_alpha=quantile,
        n_estimators=300,
        max_depth=6,
        subsample=0.8,
        random_state=42,
        verbosity=0,
        learning_rate=params.get('learning_rate', 0.05),
    )


# ---------------------------------------------------------------------------
# Business cost metric
# ---------------------------------------------------------------------------

def business_cost(
    actual: np.ndarray,
    predicted: np.ndarray,
) -> Dict[str, float]:
    """
    Compute asymmetric business cost.

    Stockout (under-prediction) is penalised at ``STOCKOUT_PENALTY`` per unit.
    Waste    (over-prediction)  is penalised at ``WASTE_PENALTY``    per unit.

    Returns dict with ``total_cost``, ``stockout_units``, ``waste_units``,
    ``stockout_cost``, ``waste_cost``.
    """
    diff = actual - predicted  # positive → stockout, negative → waste
    stockout_units = np.maximum(diff, 0)
    waste_units = np.maximum(-diff, 0)

    stockout_cost = stockout_units * STOCKOUT_PENALTY
    waste_cost = waste_units * WASTE_PENALTY

    return {
        'total_cost':     float(stockout_cost.sum() + waste_cost.sum()),
        'stockout_units': float(stockout_units.sum()),
        'waste_units':    float(waste_units.sum()),
        'stockout_cost':  float(stockout_cost.sum()),
        'waste_cost':     float(waste_cost.sum()),
    }


# ---------------------------------------------------------------------------
# Random parameter sampling
# ---------------------------------------------------------------------------

def _sample_params(space: Dict[str, list], rng: random.Random) -> dict:
    """Draw one random combination from a search space."""
    return {key: rng.choice(vals) for key, vals in space.items()}


# ---------------------------------------------------------------------------
# Rolling walk-forward folds
# ---------------------------------------------------------------------------

def _build_folds(
    dates_sorted: List,
    split_pcts: Tuple[float, ...] = _SPLIT_PCTS,
    horizon: int = _HORIZON_DAYS,
) -> List[Tuple]:
    """
    Return a list of (cutoff_idx, test_date_indices) for walk-forward splits.

    Each fold trains on dates[:cutoff_idx] and tests on the next ``horizon``
    dates in the sorted date array.  Data is NEVER shuffled.
    """
    n = len(dates_sorted)
    folds = []
    for pct in split_pcts:
        idx = int(n * pct)
        end = min(idx + horizon, n)
        if end <= idx:
            continue  # not enough future data
        folds.append((idx, end))
    return folds


# ---------------------------------------------------------------------------
# Internal: suppress noisy loggers during repeated training
# ---------------------------------------------------------------------------

_QUIET_LOGGERS = [
    'src.core.learning.revenue_forecasting.item_demand_ml.features',
    'src.core.learning.revenue_forecasting.item_demand_ml.dataset',
]


class _QuietLoggers:
    """Context manager that temporarily raises log levels to WARNING."""

    def __enter__(self):
        self._saved: Dict[str, int] = {}
        for name in _QUIET_LOGGERS:
            lg = logging.getLogger(name)
            self._saved[name] = lg.level
            lg.setLevel(logging.WARNING)
        return self

    def __exit__(self, *exc):
        for name, level in self._saved.items():
            logging.getLogger(name).setLevel(level)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_hyperparameter_search(
    df: pd.DataFrame,
    n_trials: int = 35,
    save_path: Optional[str] = None,
    seed: int = 42,
) -> Dict[str, Any]:
    """
    Random-search hyper-parameter tuning with rolling walk-forward validation.

    Trains classifier + p50 + p90 regressors for each trial, evaluates the
    combined production prediction using the asymmetric business cost, and
    saves the best configuration to ``best_params.json``.

    Note: classifiers are **not** wrapped with ``CalibratedClassifierCV``
    during tuning (for speed).  The normal training pipeline still applies
    calibration.

    Args:
        df:         Raw item-sales DataFrame (pre-densification).
        n_trials:   Number of random search trials (default 35).
        save_path:  Directory for ``best_params.json``.  Auto-resolved if None.
        seed:       Random seed for reproducibility.

    Returns:
        Dict with keys ``classifier``, ``regressor_p50``, ``regressor_p90``,
        ``created_at``, and ``tuning_metadata``.
    """
    rng = random.Random(seed)
    backend = _get_backend()

    logger.info('=' * 70)
    logger.info('  HYPERPARAMETER TUNING — Business Cost Optimisation')
    logger.info('=' * 70)
    logger.info(f'Backend: {backend}')
    logger.info(f'Trials:  {n_trials}')
    logger.info(f'Cost:    stockout = {STOCKOUT_PENALTY}× | waste = {WASTE_PENALTY}×')
    logger.info('')

    # ------------------------------------------------------------------
    # 1. Densify + build features (done once, reused by every trial)
    # ------------------------------------------------------------------
    logger.info('Preparing data (densify + feature engineering)...')
    df_dense = densify_daily_grid(df)
    df_feat = build_features(df_dense, is_future=False)
    feature_cols = get_feature_columns()

    dates_sorted = sorted(df_feat['date'].unique())
    folds = _build_folds(dates_sorted)
    logger.info(
        f'Data: {len(df_feat)} rows, {len(dates_sorted)} dates, '
        f'{df_feat["item_id"].nunique()} items'
    )
    logger.info(
        f'Rolling folds: {len(folds)} '
        f'(splits at {", ".join(f"{p:.0%}" for p in _SPLIT_PCTS)})'
    )
    logger.info('')

    # ------------------------------------------------------------------
    # 2. Trial loop
    # ------------------------------------------------------------------
    best_cost = float('inf')
    best_params: Optional[Dict[str, Any]] = None
    best_trial = -1
    trial_log: List[Dict] = []

    with _QuietLoggers():
        for trial_num in range(1, n_trials + 1):
            clf_params = _sample_params(CLF_SEARCH_SPACE, rng)
            reg_params = _sample_params(REG_SEARCH_SPACE, rng)

            fold_costs: List[float] = []
            total_stockout = 0.0
            total_waste = 0.0

            for cutoff_idx, end_idx in folds:
                cutoff_date = dates_sorted[cutoff_idx]
                test_dates = set(dates_sorted[cutoff_idx:end_idx])

                df_train = df_feat[df_feat['date'] < cutoff_date]
                df_test = df_feat[df_feat['date'].isin(test_dates)]

                if len(df_test) == 0 or len(df_train) == 0:
                    continue

                X_clf_tr, y_clf_tr, X_reg_tr, y_reg_tr = prepare_train_data(
                    df_train,
                )

                # Need both classes for the classifier
                if len(np.unique(y_clf_tr)) < 2:
                    continue

                try:
                    clf = _make_classifier(clf_params, backend)
                    reg50 = _make_regressor(reg_params, 0.5, backend)
                    reg90 = _make_regressor(reg_params, 0.9, backend)

                    with warnings.catch_warnings():
                        warnings.simplefilter('ignore')
                        clf.fit(X_clf_tr, y_clf_tr)
                        reg50.fit(X_reg_tr, y_reg_tr)
                        reg90.fit(X_reg_tr, y_reg_tr)

                    # ---- Predict on test fold ----
                    X_test = df_test[feature_cols].fillna(0)
                    y_actual = df_test['quantity_sold'].values.astype(float)

                    prob_sold = clf.predict_proba(X_test)[:, 1]
                    pred_p50 = np.maximum(0, reg50.predict(X_test))
                    pred_p90 = np.maximum(0, reg90.predict(X_test))
                    pred_p90 = np.maximum(pred_p90, pred_p50)  # quantile crossing fix

                    # Composite prediction: same formula used in production
                    # recommended_prep = ceil(0.7 × p90 + 0.3 × p50)
                    y_pred = np.ceil(
                        0.7 * (prob_sold * pred_p90)
                        + 0.3 * (prob_sold * pred_p50)
                    )

                    cost = business_cost(y_actual, y_pred)
                    fold_costs.append(cost['total_cost'])
                    total_stockout += cost['stockout_units']
                    total_waste += cost['waste_units']

                except Exception as exc:
                    logger.debug(
                        f'Trial {trial_num} fold failed: {exc}'
                    )
                    continue

            if not fold_costs:
                logger.debug(f'Trial {trial_num}: all folds failed — skipped')
                continue

            avg_cost = float(np.mean(fold_costs))
            n_folds = len(fold_costs)
            avg_stockout = total_stockout / n_folds
            avg_waste = total_waste / n_folds

            is_best = avg_cost < best_cost
            tag = ' ** BEST **' if is_best else ''

            logger.info(
                f'Trial {trial_num:3d}/{n_trials} | '
                f'Cost: {avg_cost:9.1f} | '
                f'Stockout: {avg_stockout:7.1f} units | '
                f'Waste: {avg_waste:7.1f} units'
                f'{tag}'
            )

            trial_log.append({
                'trial':              trial_num,
                'clf_params':         clf_params,
                'reg_params':         reg_params,
                'avg_cost':           avg_cost,
                'avg_stockout_units': avg_stockout,
                'avg_waste_units':    avg_waste,
            })

            if is_best:
                best_cost = avg_cost
                best_trial = trial_num
                best_params = {
                    'classifier':    {**clf_params},
                    'regressor_p50': {**reg_params},
                    'regressor_p90': {**reg_params},
                }

    # ------------------------------------------------------------------
    # 3. Report + save
    # ------------------------------------------------------------------
    if best_params is None:
        logger.error(
            'No valid trials completed — cannot determine best params.'
        )
        return {}

    # Attach metadata (not fed to model constructors)
    best_params['created_at'] = datetime.now().isoformat()
    best_params['tuning_metadata'] = {
        'n_trials':         n_trials,
        'n_folds':          len(folds),
        'best_trial':       best_trial,
        'best_avg_cost':    round(best_cost, 2),
        'stockout_penalty': STOCKOUT_PENALTY,
        'waste_penalty':    WASTE_PENALTY,
        'seed':             seed,
    }

    # Save to disk
    save_dir = _resolve_model_dir(save_path)
    os.makedirs(save_dir, exist_ok=True)
    params_path = os.path.join(save_dir, BEST_PARAMS_FILE)
    with open(params_path, 'w') as f:
        json.dump(best_params, f, indent=2)

    logger.info('')
    logger.info('=' * 70)
    logger.info('  BEST PARAMS FOUND')
    logger.info('=' * 70)
    logger.info(f'  Trial:         {best_trial}')
    logger.info(f'  Business cost: {best_cost:.1f}')
    logger.info(f'  Classifier:    {best_params["classifier"]}')
    logger.info(f'  Regressor:     {best_params["regressor_p50"]}')
    logger.info(f'  Saved to:      {params_path}')
    logger.info('=' * 70)

    return best_params


# ---------------------------------------------------------------------------
# CLI entry point  (python -m ...tune)
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s — %(message)s',
    )

    # Try loading real data; fall back to sample data
    try:
        from src.core.learning.revenue_forecasting.item_demand_ml.dataset import (
            generate_sample_data,
            load_item_sales,
        )
        from src.core.db.connection import get_db_connection  # noqa: F401

        logger.info('Attempting to load real sales data...')
        conn = get_db_connection()
        from src.api.routers.forecast_items import _get_item_historical_data
        raw = _get_item_historical_data(conn, days=180)
        df = load_item_sales(raw)
    except Exception:
        logger.info('Real data unavailable — using synthetic sample data.')
        from src.core.learning.revenue_forecasting.item_demand_ml.dataset import (
            generate_sample_data,
        )
        df = generate_sample_data(n_items=15, n_days=180)

    n = int(sys.argv[1]) if len(sys.argv) > 1 else 35
    run_hyperparameter_search(df, n_trials=n)
