from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.core.database import get_db

router = APIRouter()

@router.get("")
async def health_check(db: Session = Depends(get_db)):
    try:
        # Try to execute a simple query
        db.execute("SELECT 1")
        db_status = "connected"
    except Exception as e:
        db_status = f"error: {str(e)}"

    return {
        "status": "ok",
        "message": "API is running",
        "database": db_status
    } 