from celery import Celery
from typing import Dict, Any
import os
import logging
from uuid import UUID
import time

from app.core.config import settings
from app.scripts.document_processor import DocumentProcessor

logger = logging.getLogger(__name__)

celery = Celery(
    "law_office",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND
)

celery.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)

@celery.task(
    name="process_document",
    bind=True,
    max_retries=3,
    soft_time_limit=3600,  # 1 hour timeout
    task_time_limit=7200,  # 2 hours hard timeout
)
async def process_document_task(
    self,
    file_path: str,
    document_type: str,
    original_filename: str,
    user_id: str,
    document_id: UUID,
    metadata: Dict[str, Any] = None
) -> Dict[str, Any]:
    """
    Process a document asynchronously.
    
    Args:
        file_path: Path to the temporary file
        document_type: Type of document (law, regulation, etc.)
        original_filename: Original filename
        user_id: ID of the user who uploaded the document
        document_id: ID of the document in the database
        metadata: Additional metadata for the document
    
    Returns:
        Dictionary containing processing results
    """
    try:
        processor = DocumentProcessor()
        
        # Open and process the file
        with open(file_path, "rb") as file:
            result = await processor.process_file(
                file=file,
                original_filename=original_filename,
                document_type=document_type,
                user_id=user_id,
                document_metadata=metadata
            )
        
        # Clean up temporary file
        if os.path.exists(file_path):
            os.remove(file_path)
        
        return result
        
    except Exception as e:
        logger.error(f"Error processing document {original_filename}: {str(e)}")
        # Retry the task if we haven't exceeded max retries
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e, countdown=60 * (self.request.retries + 1))
        raise
    finally:
        # Ensure temporary file is cleaned up even if there's an error
        if os.path.exists(file_path):
            os.remove(file_path)

@celery.task(name="cleanup_temp_files")
def cleanup_temp_files() -> None:
    """Periodic task to clean up any leftover temporary files."""
    temp_dir = settings.UPLOAD_DIR
    if not os.path.exists(temp_dir):
        return
        
    current_time = time.time()
    for filename in os.listdir(temp_dir):
        file_path = os.path.join(temp_dir, filename)
        # Remove files older than 24 hours
        if os.path.isfile(file_path) and current_time - os.path.getmtime(file_path) > 86400:
            try:
                os.remove(file_path)
                logger.info(f"Cleaned up temporary file: {filename}")
            except Exception as e:
                logger.error(f"Error cleaning up file {filename}: {str(e)}")

# Configure periodic tasks
celery.conf.beat_schedule = {
    "cleanup-temp-files": {
        "task": "cleanup_temp_files",
        "schedule": 3600.0,  # Run every hour
    }
} 