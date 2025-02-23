import aioboto3
from botocore.exceptions import ClientError
from app.core.config import settings
import uuid
from datetime import datetime, timedelta
from typing import Optional
from app.core.constants import S3_BUCKET_NAME

class S3Service:
    def __init__(self):
        self.session = aioboto3.Session(
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

    async def get_client(self):
        """
        Get an async S3 client.
        """
        return await self.session.client('s3')

    async def generate_presigned_url(
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
            async with self.session.client('s3') as s3_client:
                params = {
                    'Bucket': self.bucket_name,
                    'Key': file_key,
                    **(extra_args or {})
                }
                url = await s3_client.generate_presigned_url(
                    ClientMethod=action,
                    Params=params,
                    ExpiresIn=expiration
                )
                return url
        except ClientError as e:
            print(f"Error generating presigned URL: {e}")
            return None

    async def delete_file(self, file_key: str) -> bool:
        """
        Delete a file from S3.
        """
        try:
            async with self.session.client('s3') as s3_client:
                await s3_client.delete_object(
                    Bucket=self.bucket_name,
                    Key=file_key
                )
            return True
        except ClientError as e:
            print(f"Error deleting file from S3: {e}")
            return False

    async def upload_file(self, file_obj, file_key: str, content_type: str = None) -> bool:
        """
        Upload a file to S3.
        """
        try:
            extra_args = {'ContentType': content_type} if content_type else {}
            async with self.session.client('s3') as s3_client:
                await s3_client.upload_fileobj(
                    file_obj,
                    self.bucket_name,
                    file_key,
                    ExtraArgs=extra_args
                )
            return True
        except ClientError as e:
            print(f"Error uploading file to S3: {e}")
            return False

# Create singleton instance
s3 = S3Service()

# Export the instance
__all__ = ['s3'] 