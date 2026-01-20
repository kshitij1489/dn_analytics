"""
Shared Utilities for FastAPI Routers

This module contains common utility functions used across multiple routers.
"""

import pandas as pd
import numpy as np
from typing import List, Dict, Any


def df_to_json(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """
    Convert a pandas DataFrame to a JSON-safe list of dictionaries.
    
    Handles:
    - Converting object-type columns to numeric where possible
    - Replacing inf and nan values with None for JSON serialization
    
    Args:
        df: A pandas DataFrame to convert
        
    Returns:
        List of dictionaries, safe for JSON serialization
    """
    # Convert all object-type numeric columns to float
    for col in df.columns:
        if df[col].dtype == 'object':
            try:
                df[col] = pd.to_numeric(df[col], errors='ignore')
            except:
                pass
    
    # Replace inf and nan with None for JSON compatibility
    df = df.replace([np.inf, -np.inf, np.nan], None)
    return df.to_dict(orient='records')
