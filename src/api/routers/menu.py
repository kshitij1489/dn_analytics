from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Dict, Any, Optional
import pandas as pd
import numpy as np
from src.core.db.connection import get_db_connection
from src.core.queries import menu_queries

router = APIRouter()

def get_db():
    conn, err = get_db_connection()
    if conn is None:
        raise HTTPException(status_code=500, detail=f"Database connection failed: {err}")
    try:
        yield conn
    finally:
        conn.close()

def df_to_json(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """Convert DataFrame to JSON-safe list of dicts"""
    # Convert all object-type numeric columns to float
    # This handles columns that might contain Decimal values
    for col in df.columns:
        if df[col].dtype == 'object':
            try:
                # Try to convert to numeric (handles Decimal gracefully)
                df[col] = pd.to_numeric(df[col], errors='ignore')
            except:
                pass
    
    # Replace inf and nan with None
    df = df.replace([np.inf, -np.inf, np.nan], None)
    return df.to_dict(orient='records')

@router.get("/items")
def get_menu_stats(
    name_search: Optional[str] = None,
    type_choice: str = "All",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    days: Optional[List[str]] = Query(None),
    conn = Depends(get_db)
):
    df = menu_queries.fetch_menu_stats(
        conn, 
        name_search=name_search,  
        type_choice=type_choice, 
        start_date=start_date, 
        end_date=end_date, 
        selected_weekdays=days
    )
    return df_to_json(df)

@router.get("/types")
def get_menu_types(conn = Depends(get_db)):
    types = menu_queries.fetch_menu_types(conn)
    return types
