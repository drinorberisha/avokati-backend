"""Google Cloud Storage wrapper (EU bucket) — replaces AWS S3 for file storage.

Keyless on Cloud Run: the runtime service account accesses GCS via ADC. Signed
download/upload URLs use IAM-based signing (signBlob), which needs the SA to
have `roles/iam.serviceAccountTokenCreator` on itself (granted in the Phase 1
infra step). See docs/COMPLIANCE_PLAN.md.
"""

import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional

import google.auth
from google.auth.transport import requests as ga_requests
from google.cloud import storage as gcs_storage

from app.core.config import settings

logger = logging.getLogger(__name__)


class GCSStorage:
    def __init__(self) -> None:
        self.bucket_name = settings.GCS_BUCKET_NAME
        self._client: Optional[gcs_storage.Client] = None

    @property
    def client(self) -> gcs_storage.Client:
        if self._client is None:
            self._client = gcs_storage.Client()
        return self._client

    def _bucket(self):
        return self.client.bucket(self.bucket_name)

    def generate_file_key(self, filename: str, *, prefix: str = "documents", scope_id: str = "") -> str:
        """Object key: {prefix}/{scope_id}/{year}/{month}/{uuid}_{filename}."""
        now = datetime.now()
        scope = f"{scope_id}/" if scope_id else ""
        return f"{prefix}/{scope}{now.year}/{now.month:02d}/{uuid.uuid4()}_{filename}"

    async def upload_file(self, file_obj, file_key: str, content_type: Optional[str] = None) -> bool:
        try:
            blob = self._bucket().blob(file_key)
            if hasattr(file_obj, "seek"):
                try:
                    file_obj.seek(0)
                except Exception:  # noqa: BLE001
                    pass
            blob.upload_from_file(file_obj, content_type=content_type, rewind=True)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("GCS upload failed for %s: %s", file_key, exc)
            return False

    def _signing_kwargs(self) -> dict:
        """Credentials for V4 signing without a key file (Cloud Run)."""
        try:
            creds, _ = google.auth.default()
            creds.refresh(ga_requests.Request())
            email = getattr(creds, "service_account_email", None)
            if not email or "@" not in str(email):
                email = settings.GCS_SIGNER_SA or None
            token = getattr(creds, "token", None)
            if email and token:
                return {"service_account_email": email, "access_token": token}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Falling back to default signing creds: %s", exc)
        return {}

    async def generate_signed_url(
        self,
        file_key: str,
        operation: str = "get_object",
        expiration: int = 3600,
        content_type: Optional[str] = None,
    ) -> Optional[str]:
        try:
            blob = self._bucket().blob(file_key)
            method = "PUT" if operation == "put_object" else "GET"
            return blob.generate_signed_url(
                version="v4",
                expiration=timedelta(seconds=expiration),
                method=method,
                content_type=content_type if method == "PUT" else None,
                **self._signing_kwargs(),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("GCS signed-url failed for %s: %s", file_key, exc)
            return None

    async def delete_file(self, file_key: str) -> bool:
        try:
            self._bucket().blob(file_key).delete()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("GCS delete failed for %s: %s", file_key, exc)
            return False


# Singleton used by the endpoints.
gcs = GCSStorage()
