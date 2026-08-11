from fastapi import APIRouter, Depends, HTTPException
from src.api.dependencies import get_authorized_restaurant_profile
from src.core.db.reset import reset_database

router = APIRouter()

@router.post("/reset")
def reset_db_endpoint(profile=Depends(get_authorized_restaurant_profile)):
    success, message = reset_database(profile)
    if not success:
        raise HTTPException(status_code=500, detail=message)
    return {"status": "success", "message": message}
