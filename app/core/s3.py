import boto3
from botocore.exceptions import ClientError
from app.core.config import settings
import uuid
from datetime import datetime, timedelta
from typing import Optional
from app.core.constants import S3_BUCKET_NAME

class S3Service:
    def __init__(self):
        self.s3_client = boto3.client(
            's3',
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=settings.AWS_REGION
        )
        self.bucket_name = S3_BUCKET_NAME

    def generate_file_key(self, filename: str, user_id: str) -> str:
        """
        Generate a unique file key for S3 storage.
        Format: {user_id}/{year}/{month}/{uuid}_{filename}
        """
        now = datetime.now()
        unique_id = str(uuid.uuid4())
        return f"{user_id}/{now.year}/{now.month:02d}/{unique_id}_{filename}"

    def generate_presigned_url(
        self,
        file_key: str,
        action: str,
        extra_args: Optional[dict] = None,
        expiration: int = 3600
    ) -> Optional[str]:
        """
        Generate a presigned URL for S3 operations.
        """
        try:
            params = {
                'Bucket': self.bucket_name,
                'Key': file_key,
                **(extra_args or {})
            }
            url = self.s3_client.generate_presigned_url(
                ClientMethod=action,
                Params=params,
                ExpiresIn=expiration
            )
            return url
        except ClientError as e:
            print(f"Error generating presigned URL: {e}")
            return None

    def delete_file(self, file_key: str) -> bool:
        """
        Delete a file from S3.
        """
        try:
            self.s3_client.delete_object(
                Bucket=self.bucket_name,
                Key=file_key
            )
            return True
        except ClientError as e:
            print(f"Error deleting file from S3: {e}")
            return False

# Create singleton instances
s3 = S3Service()
s3_client = s3.s3_client  # Export the s3_client directly

# Export both instances
__all__ = ['s3', 's3_client'] 