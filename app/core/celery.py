from celery import Celery
import os
import logging
import time

from app.core.config import settings

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