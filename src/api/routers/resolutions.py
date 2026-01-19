from fastapi import APIRouter, Depends, HTTPException, Body
from typing import List, Dict, Any, Optional
import pandas as pd
from src.core.db.connection import get_db_connection
from scripts.resolve_unclustered import get_unverified_items, verify_item
from utils.menu_utils import merge_menu_items, resolve_item_rename
from pydantic import BaseModel

router = APIRouter()

def get_db():
    conn, err = get_db_connection()
    if conn is None:
        raise HTTPException(status_code=500, detail=f"Database connection failed: {err}")
    try:
        yield conn
    finally:
        conn.close()

class MergeRequest(BaseModel):
    menu_item_id: str
    target_menu_item_id: str

class RenameRequest(BaseModel):
    menu_item_id: str
    new_name: str
    new_type: str

class VerifyRequest(BaseModel):
    menu_item_id: str

@router.get("/unclustered")
def get_unclustered_items(conn = Depends(get_db)):
    items = get_unverified_items(conn)
    return items

@router.post("/merge")
def merge_items(req: MergeRequest, conn = Depends(get_db)):
    res = merge_menu_items(conn, req.menu_item_id, req.target_menu_item_id)
    if res['status'] != 'success':
         raise HTTPException(status_code=400, detail=res['message'])
    return res

@router.post("/rename")
def rename_item(req: RenameRequest, conn = Depends(get_db)):
    res = resolve_item_rename(conn, req.menu_item_id, req.new_name, req.new_type)
    if res['status'] != 'success':
         raise HTTPException(status_code=400, detail=res['message'])
    return res

@router.post("/verify")
def verify_item_endpoint(req: VerifyRequest, conn = Depends(get_db)):
    # verify_item returns void or raises? helper script logic:
    # def verify_item(conn, menu_item_id): ...
    try:
        verify_item(conn, req.menu_item_id)
        return {"status": "success", "message": "Item verified"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
