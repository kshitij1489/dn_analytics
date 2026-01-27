"""
Resolutions Router - Menu item verification and resolution endpoints

Provides endpoints for handling unverified/unclustered menu items.
"""

from fastapi import APIRouter, Depends, HTTPException
from scripts.resolve_unclustered import get_unverified_items, verify_item
from utils.menu_utils import merge_menu_items, resolve_item_rename
from src.api.dependencies import get_db
from src.api.models import ResolutionMergeRequest, RenameRequest

router = APIRouter()


# Note: Using ResolutionMergeRequest to avoid confusion with menu router's MergeRequest
# which has different field names (source_id vs menu_item_id)


@router.get("/unclustered")
def get_unclustered_items(conn=Depends(get_db)):
    """Get list of unclustered/unverified items"""
    items = get_unverified_items(conn)
    return items


@router.post("/merge")
def merge_items(req: ResolutionMergeRequest, conn=Depends(get_db)):
    """Merge a menu item into a target item"""
    res = merge_menu_items(conn, req.menu_item_id, req.target_menu_item_id)
    if res['status'] != 'success':
        raise HTTPException(status_code=400, detail=res['message'])
    return res


@router.post("/rename")
def rename_item(req: RenameRequest, conn=Depends(get_db)):
    """Rename a menu item"""
    res = resolve_item_rename(conn, req.menu_item_id, req.new_name, req.new_type)
    if res['status'] != 'success':
        raise HTTPException(status_code=400, detail=res['message'])
    return res


@router.post("/verify")
def verify_item_endpoint(req, conn=Depends(get_db)):
    """Verify a menu item"""
    try:
        verify_item(conn, req.menu_item_id)
        return {"status": "success", "message": "Item verified"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
