from fastapi import APIRouter, HTTPException
from src.core.db.reset import reset_database

router = APIRouter()

@router.post("/reset")
def reset_db_endpoint():
    success, message = reset_database()
    if not success:
        raise HTTPException(status_code=500, detail=message)
    return {"status": "success", "message": message}
