# Gaussian Process Ice-Cream Revenue Forecaster (Rewritten Stable Version)
# Key fixes:
# - Correct log-normal prediction moments
# - Cyclic encoding (sin/cos) instead of periodic kernels
# - Removed DotProduct extrapolation instability
# - Standardized temperature feature
# - No refit after hyperparameter optimization
# - Strict numerical checks (no NaN masking)

import glob
import os
import logging
import warnings
from typing import Tuple, Optional

import numpy as np
import pandas as pd
import joblib

from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import RBF, WhiteKernel, ConstantKernel
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.exceptions import ConvergenceWarning

logger = logging.getLogger(__name__)
try:
    import sklearn
    logger.info(f"Gaussian Process Module Loaded. Scikit-learn version: {sklearn.__version__} Path: {sklearn.__path__}")
except Exception as e:
    logger.error(f"Failed to inspect scikit-learn: {e}")

# ------------------------------------------------------------
# Feature Engineering
# ------------------------------------------------------------
class FeatureEngineer:
    def __init__(self):
        self.temp_mean = None
        self.temp_std = None
        self.t0 = None

    def fit(self, df: pd.DataFrame):
        self.temp_mean = df['temp_max'].mean()
        self.temp_std = df['temp_max'].std() + 1e-6
        self.t0 = pd.to_datetime(df['ds']).min()

    def transform(self, df: pd.DataFrame) -> np.ndarray:
        if df[['ds','temp_max']].isna().any().any():
            raise ValueError("Input contains NaN values")

        dates = pd.to_datetime(df['ds'])

        # cyclic encodings (stationary — no extrapolation issues)
        doy = dates.dt.dayofyear.values
        dow = dates.dt.weekday.values

        year_sin = np.sin(2*np.pi*doy/365)
        year_cos = np.cos(2*np.pi*doy/365)
        week_sin = np.sin(2*np.pi*dow/7)
        week_cos = np.cos(2*np.pi*dow/7)

        lag1 = np.log1p(df['lag1'].values)
        lag7 = np.log1p(df['lag7'].values)

        # standardized temperature
        temperature = (df['temp_max'].values - self.temp_mean) / self.temp_std

        # Only stationary features — no time_index to avoid extrapolation issues
        return np.column_stack([
            year_sin, year_cos,
            week_sin, week_cos,
            temperature, lag1, lag7])

# ------------------------------------------------------------
# Kernel
# ------------------------------------------------------------

def create_kernel():
    # ARD kernel: one length_scale per feature dimension
    # Features: [year_sin, year_cos, week_sin, week_cos, temperature, lag1, lag7] = 7 dimensions
    # Note: No time_index — avoids extrapolation issues with limited data
    k_signal = ConstantKernel(1.0, (0.01, 100.0)) * RBF(
        length_scale=[1.0] * 7,   # 7 dimensions (7 features)
        length_scale_bounds=[(0.1, 100.0)] * 7    # safe bound (prevents singularity)
    )
    k_noise = WhiteKernel(noise_level=0.1, noise_level_bounds=(1e-5, 0.5))
    return k_signal + k_noise

# ------------------------------------------------------------
# Model
# ------------------------------------------------------------
class GaussianProcessForecaster:
    def __init__(self):
        self.kernel = create_kernel()
        self.model = GaussianProcessRegressor(
            kernel=self.kernel,
            n_restarts_optimizer=5,
            normalize_y=True,                     # improved stability
            alpha=1e-6
        )
        self.fe = FeatureEngineer()
        self.fitted = False

    # ---- lognormal moment conversion ----
    @staticmethod
    def _lognormal_stats(mean_log, std_log):
        # Professional clipping to prevent overflow in exp()
        # ln(1e12) ~ 27.6, so 50 is extremely safe for revenue estimation
        mean_log = np.clip(mean_log, -50, 50)
        std_log = np.clip(std_log, 0, 5)           # std of 5 on log scale is massive
        
        mean = np.expm1(mean_log + 0.5*std_log**2)
        var = (np.exp(std_log**2)-1)*np.exp(2*mean_log+std_log**2)
        std = np.sqrt(np.maximum(var, 0))
        return mean, std

    def fit(self, df: pd.DataFrame, target='y', warm_start=False):
        if (df[target] < 0).any():
            raise ValueError("Revenue cannot be negative — data error detected")
        self.fe.fit(df)
        X = self.fe.transform(df)
        y = np.log1p(df[target].values)

        # Warm-start logic: reuse optimized kernel from previous fit if available
        if warm_start and self.fitted and hasattr(self.model, 'kernel_'):
            logger.info(f"Warm-starting with kernel: {self.model.kernel_}")
            # We must create a new optimizer instance or update the kernel properly
            # Scikit-learn GPR uses 'kernel' arg as the starting point.
            # We update the internal model's kernel specification to the previously optimized one.
            self.model.kernel = self.model.kernel_ 
            
            # Reduce restarts since we are already near the optimum (tracking mode)
            # We keep a few restarts to allow for some adaptation/exploration but avoid full random search
            old_restarts = self.model.n_restarts_optimizer
            self.model.n_restarts_optimizer = 1 # 0 or 1 is sufficient for tracking
            
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", category=ConvergenceWarning)
                self.model.fit(X, y)
                
            # Restore configuration (optional, but good practice if reused without warm start later)
            self.model.n_restarts_optimizer = old_restarts
            
        else:
            # Cold start
            self.model.kernel = self.kernel # Reset to initial prior
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", category=ConvergenceWarning)
                self.model.fit(X, y)
                
        self.fitted = True
        logger.info(f"Optimized kernel: {self.model.kernel_}")

    def predict(self, df: pd.DataFrame)->Tuple[np.ndarray,np.ndarray]:
        if not self.fitted:
            raise RuntimeError("Model not fitted")

        X = self.fe.transform(df)
        
        # Suppress transient numerical warnings that don't affect final result quality
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            mean_log, std_log = self.model.predict(X, return_std=True)

        if not np.isfinite(mean_log).all():
            raise RuntimeError("Numerical instability in GP prediction")

        return self._lognormal_stats(mean_log, std_log)

    def evaluate(self, df: pd.DataFrame, target='y'):
        if not self.fitted:
            raise RuntimeError("Model not fitted")

        y_true = df[target].values
        mean, std = self.predict(df)

        lower = mean - 1.96*std
        upper = mean + 1.96*std

        mae = mean_absolute_error(y_true, mean)
        rmse = np.sqrt(mean_squared_error(y_true, mean))
        coverage = np.mean((y_true >= lower) & (y_true <= upper))

        logger.info(f"MAE={mae:.2f} RMSE={rmse:.2f} Coverage95={coverage:.2%}")

    def save(self, path: str):
        dir_name = os.path.dirname(path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)
        joblib.dump((self.model, self.fe), path)

    def load(self, path: str):
        self.model, self.fe = joblib.load(path)
        self.fitted = True

# ------------------------------------------------------------
# Lag Feature Helpers
# ------------------------------------------------------------

def add_lag_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add lag1 and lag7 features to dataframe. Drops rows with NaN lags."""
    df = df.copy()
    df['lag1'] = df['y'].shift(1)
    df['lag7'] = df['y'].shift(7)
    return df.dropna(subset=['lag1', 'lag7']).reset_index(drop=True)

# ------------------------------------------------------------
# Recursive Forecasting
# ------------------------------------------------------------

def make_next_row(date, temp: float, history: pd.DataFrame) -> pd.DataFrame:
    """
    Create a single-row dataframe for the next forecast day.
    
    Args:
        date: Target date for prediction
        temp: Temperature forecast for that day
        history: DataFrame containing at least last 7 known revenues with 'y' column
    
    Returns:
        DataFrame with columns [ds, temp_max, lag1, lag7]
    """
    lag1 = history.iloc[-1]['y']
    lag7 = history.iloc[-7]['y'] if len(history) >= 7 else history.iloc[0]['y']
    
    return pd.DataFrame({
        'ds': [pd.Timestamp(date)],
        'temp_max': [temp],
        'lag1': [lag1],
        'lag7': [lag7]
    })


def append_prediction(history: pd.DataFrame, date, pred_mean: float) -> pd.DataFrame:
    """
    Append a prediction to the history for roll-forward simulation.
    
    Args:
        history: DataFrame with [ds, y] columns
        date: Date of the prediction
        pred_mean: Predicted revenue value
    
    Returns:
        Updated history DataFrame
    """
    new_row = pd.DataFrame({
        'ds': [pd.Timestamp(date)],
        'y': [float(pred_mean)]
    })
    return pd.concat([history, new_row], ignore_index=True)


def forecast_days(model: GaussianProcessForecaster, df_history: pd.DataFrame, 
                  future_weather: pd.DataFrame) -> pd.DataFrame:
    """
    Recursive forecaster for multi-day predictions.
    
    This function implements roll-forward simulation: each day's forecast
    uses the previous day's predicted value as lag1, and 7-days-ago value as lag7.
    
    Args:
        model: Fitted GaussianProcessForecaster instance
        df_history: Historical dataframe with 'ds' and 'y' columns (after lag creation)
        future_weather: DataFrame with columns [ds, temp_max] for future dates
    
    Returns:
        DataFrame with columns [ds, pred_mean, pred_std, lower, upper]
    """
    history = df_history[['ds', 'y']].copy()
    results = []
    
    for _, row in future_weather.iterrows():
        next_row = make_next_row(
            row['ds'],
            row['temp_max'],
            history
        )
        
        mean, std = model.predict(next_row)
        
        results.append({
            'ds': row['ds'],
            'pred_mean': mean[0],
            'pred_std': std[0],
            'lower': mean[0] - 1.96 * std[0],
            'upper': mean[0] + 1.96 * std[0]
        })
        
        # CRITICAL: Feed prediction back into history for next iteration
        history = append_prediction(history, row['ds'], mean[0])
    
    return pd.DataFrame(results)


# ------------------------------------------------------------
# Daily Rolling Trainer
# ------------------------------------------------------------

class RollingGPForecaster:
    """
    Manages the lifecycle of the Gaussian Process model for daily rolling updates.
    
    Responsibilities:
    1. Maintain 90-day sliding window of training data.
    2. Persist model and window metadata.
    3. Warm-start retraining daily.
    
    Note on FeatureEngineer re-fitting:
        The FeatureEngineer is intentionally re-fitted to the current 90-day window on every
        training run. This ensures the temp_mean/temp_std used for feature standardization
        are always representative of the recent data distribution. This is the correct
        online learning approach, though it means feature statistics may shift over time.
    """
    def __init__(self, storage_path: str = None):
        if storage_path is None:
            # Use the same path_helper utility as item demand models.
            # - Production (.dmg): resolves to ~/Library/Application Support/.../data/
            # - Dev: resolves to <project_root>/data/
            from src.core.utils.path_helper import get_data_path
            self.storage_path = get_data_path(os.path.join('data', 'gp_model.pkl'))
        else:
            self.storage_path = storage_path

        logger.info(f"RollingGPForecaster storage_path: {self.storage_path}")

        self.model = GaussianProcessForecaster()
        self.window_start: Optional[pd.Timestamp] = None
        self.window_end: Optional[pd.Timestamp] = None
        self.training_data: Optional[pd.DataFrame] = None

    def load(self) -> bool:
        """Load model and metadata if exists. Returns True if loaded successfully."""
        if os.path.exists(self.storage_path):
            try:
                data = joblib.load(self.storage_path)
                if isinstance(data, dict):
                    self.model = data.get('model', GaussianProcessForecaster())
                    self.window_start = data.get('window_start')
                    self.window_end = data.get('window_end')
                    self.training_data = data.get('training_data')
                    logger.info(f"Loaded RollingGP model. Window: {self.window_start} to {self.window_end}")
                    return True
            except Exception as e:
                logger.warning(f"Failed to load existing model: {e}")
        return False

    def save(self) -> None:
        """Persist model and metadata."""
        dir_name = os.path.dirname(self.storage_path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)

        # Remove deprecated gp_model_*.pkl files (e.g. gp_model_v1.pkl)
        pattern = os.path.join(dir_name, 'gp_model_*.pkl')
        for fpath in glob.glob(pattern):
            if os.path.abspath(fpath) != os.path.abspath(self.storage_path):
                try:
                    os.remove(fpath)
                    logger.info(f"Removed deprecated GP model file: {fpath}")
                except Exception as e:
                    logger.warning(f"Failed to remove deprecated GP file {fpath}: {e}")

        payload = {
            'model': self.model,
            'window_start': self.window_start,
            'window_end': self.window_end,
            'training_data': self.training_data
        }
        joblib.dump(payload, self.storage_path)
        logger.info(f"Saved RollingGP model to {self.storage_path}")

    def save_daily_forecast_snapshot(self, conn, run_date: str, forecast_df: pd.DataFrame) -> None:
        """
        Save the forecast snapshot to the database for auditing.
        
        Raises:
            RuntimeError: If the snapshot could not be saved (prevents silent failures).
        """
        try:
            cursor = conn.cursor()
            rows_to_insert = []
            
            for _, row in forecast_df.iterrows():
                rows_to_insert.append((
                    run_date,
                    row['ds'].strftime('%Y-%m-%d'),
                    float(row['pred_mean']),
                    float(row['pred_std']),
                    float(row['lower']),
                    float(row['upper']),
                    self.window_start.strftime('%Y-%m-%d') if self.window_start else None,
                    self.window_end.strftime('%Y-%m-%d') if self.window_end else None
                ))
            
            cursor.executemany("""
                INSERT OR REPLACE INTO forecast_snapshots 
                (forecast_run_date, target_date, pred_mean, pred_std, lower_95, upper_95, model_window_start, model_window_end)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, rows_to_insert)
            conn.commit()
            logger.info(f"Saved {len(rows_to_insert)} forecast snapshots for run_date={run_date}")
            
        except Exception as e:
            logger.error(f"Failed to save forecast snapshot: {e}")
            raise RuntimeError(f"Failed to save forecast snapshot: {e}") from e

    def update_and_fit(self, latest_db_data: pd.DataFrame) -> None:
        """
        Daily update routine.
        
        Args:
            latest_db_data: DataFrame containing full history or at least recent data
                            Columns: [ds, y, temp_max, ...]
        """
        # 1. Load previous state
        exists = self.load()
        
        # 2. Prepare Data Window: construct 90-day window ending TODAY
        # CRITICAL: Compute lags BEFORE filtering y > 0 to preserve temporal ordering
        df = add_lag_features(latest_db_data.sort_values('ds'))
        
        # Now filter zero-revenue rows (holidays, closures) from training target
        # Lags are already computed correctly from the full calendar
        original_len = len(df)
        df = df[df['y'] > 0].copy()
        if original_len != len(df):
            logger.info(f"Filtered {original_len - len(df)} zero-revenue rows from training data")
        
        # Keep only last 90 days
        window_size = 90
        if len(df) > window_size:
            df = df.iloc[-window_size:].reset_index(drop=True)
            
        self.window_start = df['ds'].min()
        self.window_end = df['ds'].max()
        self.training_data = df
        
        logger.info(f"Training Window: {len(df)} rows, {self.window_start} to {self.window_end}")
        
        # 3. Fit (Warm Start if prior model exists)
        warm_start = exists and self.model.fitted
        
        logger.info(f"Starting training (Warm Start: {warm_start})...")
        self.model.fit(df, warm_start=warm_start)
        
        # 4. Save immediately
        self.save()

    def predict_historical(self, n_days: int = 30) -> pd.DataFrame:
        """
        Generate in-sample predictions for the last n_days of training data.
        
        This produces fitted values analogous to Holt-Winters' fittedvalues
        and Prophet's predict() on historical dates.  The GP model interpolates
        through (or very close to) training points, so in-sample std is small.
        
        Returns:
            DataFrame with columns [ds, pred_mean, pred_std, lower, upper]
            or empty DataFrame if model is not fitted.
        """
        if self.training_data is None or not self.model.fitted:
            return pd.DataFrame(columns=['ds', 'pred_mean', 'pred_std', 'lower', 'upper'])
        
        df = self.training_data.tail(n_days).copy()
        if df.empty:
            return pd.DataFrame(columns=['ds', 'pred_mean', 'pred_std', 'lower', 'upper'])
        
        try:
            mean, std = self.model.predict(df)
            return pd.DataFrame({
                'ds': df['ds'].values,
                'pred_mean': mean,
                'pred_std': std,
                'lower': mean - 1.96 * std,
                'upper': mean + 1.96 * std,
            })
        except Exception as e:
            logger.warning(f"GP historical prediction failed: {e}")
            return pd.DataFrame(columns=['ds', 'pred_mean', 'pred_std', 'lower', 'upper'])

    def predict_next_days(self, future_weather: pd.DataFrame) -> pd.DataFrame:
        """Generate multi-day forecast using roll-forward simulation."""
        if self.training_data is None or not self.model.fitted:
            raise RuntimeError("Model not trained or not available for prediction")
              
        return forecast_days(self.model, self.training_data, future_weather)


# ------------------------------------------------------------
# Sample Data
# ------------------------------------------------------------

def sample_data(n=365):
    """Generate synthetic revenue data with temperature and lag features."""
    dates = pd.date_range('2024-01-01', periods=n)
    df = pd.DataFrame({'ds': dates})
    d = df['ds'].dt.dayofyear
    df['temp_max'] = 15 + 25 * np.sin(2 * np.pi * (d - 100) / 365) + np.random.normal(0, 2, n)
    df['y'] = 5000 + df['temp_max'] * 200
    df.loc[df['ds'].dt.weekday >= 4, 'y'] += 2000
    df['y'] += np.random.normal(0, 500, n)
    
    # Add lag features
    df = add_lag_features(df)
    return df


if __name__ == '__main__':
    # Generate and fit
    df = sample_data()
    gp = GaussianProcessForecaster()
    gp.fit(df)
    gp.evaluate(df)
    
    # Recursive forecast for next 7 days
    last_date = df['ds'].max()
    future_weather = pd.DataFrame({
        'ds': pd.date_range(last_date + pd.Timedelta(days=1), periods=7),
        'temp_max': [25, 26, 24, 23, 27, 29, 30]
    })
    
    forecast = forecast_days(gp, df, future_weather)
    print("\n7-Day Forecast:")
    print(forecast.to_string(index=False))

def delete_gp_model():
    """Delete the persisted GP model file to force retraining."""
    try:
        # Re-use logic from __init__ to resolve path
        from src.core.utils.path_helper import get_data_path
        storage_path = get_data_path(os.path.join('data', 'gp_model.pkl'))
        
        if os.path.exists(storage_path):
            os.remove(storage_path)
            logger.info(f"Deleted GP model file at {storage_path}")
        else:
            logger.info("No GP model file found to delete.")
            
    except Exception as e:
        logger.error(f"Failed to delete GP model: {e}")
        # Don't raise, just log - we want the DB clear to succeed regardless