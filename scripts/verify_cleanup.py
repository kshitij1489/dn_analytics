import os
import sys

# Add project root so `src` is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import shutil
import joblib
import json
import psutil
import logging
from typing import List

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Configuration ---
_PROJECT_ROOT = os.path.join(os.path.dirname(__file__), '..')
DATA_DIR = os.path.abspath(os.path.join(_PROJECT_ROOT, 'data'))
ITEM_MODEL_DIR = os.path.join(DATA_DIR, 'models', 'item_demand_ml')

# Ensure directories exist
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(ITEM_MODEL_DIR, exist_ok=True)

# --- Helper Functions ---

def create_dummy_file(path: str, content: str = "dummy content"):
    with open(path, 'w') as f:
        f.write(content)
    logger.info(f"Created dummy file: {path}")

def terminate_process_holding_file(file_path):
    for proc in psutil.process_iter(['pid', 'name', 'open_files']):
        try:
            for f in proc.info['open_files'] or []:
                if f.path == file_path:
                    logger.warning(f"Process {proc.info['name']} (PID {proc.info['pid']}) is holding {file_path}. Terminating...")
                    proc.terminate()
                    proc.wait()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass

def clean_directory(directory: str):
    if os.path.exists(directory):
        shutil.rmtree(directory)
    os.makedirs(directory, exist_ok=True)

# --- Test 1: Item Demand Model Cleanup ---

def test_item_demand_cleanup():
    logger.info("\n--- Test 1: Item Demand Model Cleanup ---")
    
    # 1. Setup: Create valid and "junk" files
    valid_files = [
        'item_demand_classifier.pkl',
        'item_demand_regressor_p50.pkl',
        'item_demand_regressor_p90.pkl',
        'feature_columns.json'
    ]
    junk_files = [
        'item_demand_classifier_v1.pkl',       # Old version
        'item_demand_random_junk.pkl',         # Random pkl
        'old_model.pkl',                       # Genuine junk
        'item_demand_something_else.txt'       # Should NOT remain? Wait, logic says remove .pkl OR item_demand_*. 
                                               # item_demand_something_else.txt STARTS with item_demand_, so should be removed.
    ]
    
    # Create them
    for fname in valid_files + junk_files:
        create_dummy_file(os.path.join(ITEM_MODEL_DIR, fname))
        
    # 2. Run the save method (which triggers cleanup)
    from src.core.learning.revenue_forecasting.item_demand_ml.model_io import save_models
    
    logger.info("Running save_models() with dummy data...")
    # Pass string data - joblib doesn't care what the object is
    save_models(
        classifier="new_classifier",
        regressor_p50="new_p50",
        regressor_p90="new_p90",
        feature_columns=["col1", "col2"],
        model_dir=ITEM_MODEL_DIR
    )
    
    # 3. Assertions
    remaining_files = os.listdir(ITEM_MODEL_DIR)
    logger.info(f"Remaining files in {ITEM_MODEL_DIR}: {remaining_files}")
    
    # Check that ONLY valid files exist
    for junk in junk_files:
        if junk in remaining_files:
            logger.error(f"FAILURE: Junk file '{junk}' was NOT removed!")
        else:
            logger.info(f"SUCCESS: Junk file '{junk}' was removed.")
            
    for valid in valid_files:
        if valid not in remaining_files:
            logger.error(f"FAILURE: Valid file '{valid}' is missing!")
        else:
            logger.info(f"SUCCESS: Valid file '{valid}' is present.")

# --- Test 2: GP Model Cleanup ---

def test_gp_cleanup():
    logger.info("\n--- Test 2: GP Model Cleanup ---")
    
    # 1. Setup: Create valid and junk files in data/
    # Note: The code defaults to data/gp_model.pkl
    
    target_file = os.path.join(DATA_DIR, 'gp_model.pkl')
    junk_files = [
        os.path.join(DATA_DIR, 'gp_model_v1.pkl'),      # The old standard
        os.path.join(DATA_DIR, 'gp_model_2023.pkl'),    # Some other version
        os.path.join(DATA_DIR, 'gp_model_temp.pkl')     # Temp file
    ]
    
    # Create target (will be overwritten) and junk
    create_dummy_file(target_file)
    for path in junk_files:
        create_dummy_file(path)
        
    # 2. Run the save method
    from src.core.learning.revenue_forecasting.gaussianprocess import RollingGPForecaster
    
    logger.info("Running RollingGPForecaster.save()...")
    # Initialize with specific path to test that logic
    gp = RollingGPForecaster(storage_path=target_file)
    
    # We need to forcefully close any open handles to the files we just created/touched
    # But for a simple script, just running save() should be fine as long as we closed the file handles (with open...)
    
    gp.save()
    
    # 3. Assertions
    remaining_files = os.listdir(DATA_DIR)
    # Filter for gp_model files
    remaining_gp_files = [f for f in remaining_files if f.startswith('gp_model')]
    logger.info(f"Remaining GP model files in {DATA_DIR}: {remaining_gp_files}")
    
    # Only gp_model.pkl should exist
    if 'gp_model.pkl' not in remaining_gp_files:
         logger.error("FAILURE: Target file 'gp_model.pkl' is missing!")
    else:
         logger.info("SUCCESS: Target file 'gp_model.pkl' is present.")
         
    for junk_path in junk_files:
        fname = os.path.basename(junk_path)
        if fname in remaining_gp_files:
            logger.error(f"FAILURE: Junk file '{fname}' was NOT removed!")
        else:
            logger.info(f"SUCCESS: Junk file '{fname}' was removed.")


if __name__ == "__main__":
    try:
        test_item_demand_cleanup()
        test_gp_cleanup()
        logger.info("\nAll tests completed.")
    except Exception as e:
        logger.error(f"Test failed with exception: {e}")
        raise
