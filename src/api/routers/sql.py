from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import pandas as pd
from src.core.db.connection import get_db_connection
from src.core.queries import table_queries
from src.api.models import QueryRequest
from typing import List, Dict, Any, Optional

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
    return df.where(pd.notnull(df), None).to_dict(orient='records')

@router.post("/query")
def execute_query(request: QueryRequest, conn = Depends(get_db)):
    df, err = table_queries.execute_raw_query(conn, request.query)
    if err:
        raise HTTPException(status_code=400, detail=err)
    if df is not None:
        return {
            "columns": list(df.columns),
            "rows": df_to_json(df)
        }
    return {"columns": [], "rows": []}
