from fastapi import UploadFile
import aiofiles
import os
from typing import Optional
from app.core.config import settings

# Create uploads directory if it doesn't exist
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

async def upload_file(file: UploadFile, filename: str) -> str:
    """
    Upload a file to local storage.
    Returns the file path.
    """
    file_path = os.path.join(UPLOAD_DIR, filename)
    
    async with aiofiles.open(file_path, 'wb') as out_file:
        content = await file.read()
        await out_file.write(content)
    
    return file_path

async def delete_file(file_path: str) -> bool:
    """
    Delete a file from storage.
    Returns True if successful, False otherwise.
    """
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            return True
    except Exception:
        pass
    return False

async def get_file_size(file_path: str) -> Optional[int]:
    """
    Get file size in bytes.
    Returns None if file doesn't exist.
    """
    try:
        if os.path.exists(file_path):
            return os.path.getsize(file_path)
    except Exception:
        pass
    return None 