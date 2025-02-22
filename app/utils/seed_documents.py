import boto3
import os
from datetime import datetime
import uuid
from supabase import create_client, Client
from app.core.constants import S3_BUCKET_NAME
from app.core.config import settings
from typing import Optional

# Initialize Supabase client
supabase: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)

# Initialize S3 client
s3_client = boto3.client(
    's3',
    aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    region_name=settings.AWS_REGION,
)

# Add debug info
print("AWS Configuration:")
print(f"Access Key ID: {settings.AWS_ACCESS_KEY_ID[:5]}...")
print(f"Secret Key: {settings.AWS_SECRET_ACCESS_KEY[:5]}...")
print(f"Region: {settings.AWS_REGION}")
print(f"Bucket: {S3_BUCKET_NAME}")

# Convert string to UUID object
USER_ID = uuid.UUID("cf1322d0-f17f-404a-a0af-232df016fa6c")

# Function to get or create a test client
def get_or_create_test_client() -> Optional[str]:
    try:
        # First try to get an existing client
        response = supabase.table('clients').select('id').limit(1).execute()
        if response.data and len(response.data) > 0:
            return response.data[0]['id']
        
        # If no client exists, create one
        client_data = {
            "id": str(uuid.uuid4()),  # Explicitly set UUID
            "name": "Test Client",
            "email": "test@example.com",
            "phone": "+1234567890",
            "status": "active",  # USER-DEFINED type
            "address": "Test Address",
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat()
        }
        
        response = supabase.table('clients').insert(client_data).execute()
        if response.data and len(response.data) > 0:
            return response.data[0]['id']
            
    except Exception as e:
        print(f"Error getting/creating test client: {e}")
        return None

def get_or_create_test_case(client_id: str) -> Optional[str]:
    try:
        # First try to get an existing case
        response = supabase.table('cases').select('id').limit(1).execute()
        if response.data and len(response.data) > 0:
            return response.data[0]['id']
        
        # If no case exists, create one
        case_data = {
            "id": str(uuid.uuid4()),
            "case_number": "TC-2024-001",
            "title": "Test Case",
            "type": "civil",
            "status": "open",  # USER-DEFINED type
            "court": "Test Court",
            "judge": "Test Judge",
            "client_id": client_id,
            "primary_attorney_id": str(USER_ID),  # Using our user as primary attorney
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat()
        }
        
        response = supabase.table('cases').insert(case_data).execute()
        if response.data and len(response.data) > 0:
            return response.data[0]['id']
            
    except Exception as e:
        print(f"Error getting/creating test case: {e}")
        return None

def create_document_collaborator(document_id: str) -> bool:
    try:
        collaborator_data = {
            "id": str(uuid.uuid4()),
            "document_id": document_id,
            "user_id": str(USER_ID),
            "role": "owner",  # USER-DEFINED type
            "added_at": datetime.utcnow().isoformat()
        }
        
        response = supabase.table('document_collaborators').insert(collaborator_data).execute()
        return bool(response.data)
    except Exception as e:
        print(f"Error creating document collaborator: {e}")
        return False

def create_document_version(document_id: str, file_path: str, size: str) -> bool:
    try:
        version_data = {
            "id": str(uuid.uuid4()),
            "document_id": document_id,
            "version_number": 1,
            "file_path": file_path,
            "size": size,
            "created_by_id": str(USER_ID),
            "changes_description": "Initial version",
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat()
        }
        
        response = supabase.table('document_versions').insert(version_data).execute()
        return bool(response.data)
    except Exception as e:
        print(f"Error creating document version: {e}")
        return False

def create_audit_log(entity_id: str, action: str) -> bool:
    try:
        audit_data = {
            "id": str(uuid.uuid4()),
            "user_id": str(USER_ID),
            "action": action,
            "entity_type": "document",
            "entity_id": entity_id,
            "changes": {"action": "create", "details": "Initial document creation"},
            "ip_address": "127.0.0.1",
            "user_agent": "Seed Script",
            "description": "Document created via seed script",
            "created_at": datetime.utcnow().isoformat()
        }
        
        response = supabase.table('audit_logs').insert(audit_data).execute()
        return bool(response.data)
    except Exception as e:
        print(f"Error creating audit log: {e}")
        return False

def get_or_create_test_user() -> Optional[str]:
    try:
        # First check if our user already exists
        response = supabase.table('users').select('*').eq('id', str(USER_ID)).execute()
        if response.data and len(response.data) > 0:
            return str(USER_ID)
        
        # If user doesn't exist, create one
        user_data = {
            "id": str(USER_ID),
            "email": "bacadrin@gmail.com",
            "full_name": "Bacadrin Presheva",
            "hashed_password": "not_needed_for_test",  # This is just for testing
            "is_active": True,
            "is_superuser": True,
            "role": "attorney",  # Valid user_role enum value
            "phone": "+1234567890",
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat()
        }
        
        response = supabase.table('users').insert(user_data).execute()
        if response.data and len(response.data) > 0:
            return str(USER_ID)
            
    except Exception as e:
        print(f"Error getting/creating test user: {e}")
    return None

def seed_sample_data():
    try:
        # 0. Create or get user
        print("\nCreating/getting test user...")
        user_id = get_or_create_test_user()
        if not user_id:
            raise Exception("Could not get or create test user")
        print(f"✅ User ID: {user_id}")

        # 1. Create or get client
        print("\nCreating/getting test client...")
        client_id = get_or_create_test_client()
        if not client_id:
            raise Exception("Could not get or create test client")
        print(f"✅ Client ID: {client_id}")

        # 2. Create or get case
        print("\nCreating/getting test case...")
        case_id = get_or_create_test_case(client_id)
        if not case_id:
            raise Exception("Could not get or create test case")
        print(f"✅ Case ID: {case_id}")

        # 3. Create document
        print("\nCreating test document...")
        return seed_sample_document(client_id, case_id)

    except Exception as e:
        print(f"❌ Error seeding sample data: {str(e)}")
        return False

def seed_sample_document(client_id: str, case_id: str) -> bool:
    try:
        # Sample PDF file path (you'll need to provide a sample PDF)
        sample_file_path = "app/utils/sample_files/sample.pdf"
        file_name = "sample_contract.pdf"
        
        # Generate unique file key
        file_key = f"documents/sample/{uuid.uuid4()}-{file_name}"
        
        # Upload file to S3
        with open(sample_file_path, 'rb') as file:
            print(f"Uploading file to S3 bucket: {S3_BUCKET_NAME}")
            print(f"File key: {file_key}")
            s3_client.upload_fileobj(
                file,
                S3_BUCKET_NAME,
                file_key,
                ExtraArgs={
                    "ContentType": "application/pdf"
                }
            )
        
        # Generate download URL
        download_url = s3_client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': S3_BUCKET_NAME,
                'Key': file_key
            },
            ExpiresIn=3600
        )
        
        # Get file size
        file_size = os.path.getsize(sample_file_path)
        
        # Create document record in Supabase
        document = {
            "id": str(uuid.uuid4()),
            "title": "Sample Contract",
            "type": "application/pdf",
            "category": "Contracts",
            "status": "draft",  # USER-DEFINED type
            "size": f"{file_size / 1024 / 1024:.2f} MB",
            "version": 1,
            "file_path": file_key,
            "file_name": file_name,
            "file_size": file_size,
            "mime_type": "application/pdf",
            "download_url": download_url,
            "tags": ["sample", "contract", "draft"],
            "case_id": case_id,
            "client_id": client_id,
            "created_by": str(USER_ID),
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
            "metadata": {
                "author": "Bacadrin Presheva",
                "createdAt": datetime.utcnow().isoformat(),
                "lastModifiedBy": "Bacadrin Presheva",
                "versionHistory": [{
                    "version": 1,
                    "modifiedAt": datetime.utcnow().isoformat(),
                    "modifiedBy": "Bacadrin Presheva",
                    "changes": "Initial document creation"
                }]
            },
            "collaborators": [{
                "id": str(USER_ID),
                "name": "Bacadrin Presheva",
                "email": "bacadrin@gmail.com",
                "role": "owner",
                "addedAt": datetime.utcnow().isoformat()
            }]
        }
        
        response = supabase.table('documents').insert(document).execute()
        
        if response.error:
            raise Exception(f"Error creating document: {response.error.message}")
            
        document_id = response.data[0]['id']
        print(f"✅ Document created: {document_id}")
        
        # Create related records
        if not create_document_collaborator(document_id):
            raise Exception("Failed to create document collaborator")
        print("✅ Document collaborator created")
        
        if not create_document_version(document_id, file_key, document['size']):
            raise Exception("Failed to create document version")
        print("✅ Document version created")
        
        if not create_audit_log(document_id, "create_document"):
            raise Exception("Failed to create audit log")
        print("✅ Audit log created")
        
        return True
        
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return False

def verify_bucket_access():
    try:
        print("\nTesting bucket access...")
        # First, try a simple head bucket call
        try:
            s3_client.head_bucket(Bucket=S3_BUCKET_NAME)
            print("✅ Bucket exists and is accessible")
        except Exception as e:
            print(f"❌ Head bucket failed: {str(e)}")
            return False

        # If head bucket succeeds, try to get location
        response = s3_client.get_bucket_location(Bucket=S3_BUCKET_NAME)
        location = response.get('LocationConstraint') or 'us-east-1'
        print(f"✅ Bucket location verified: {location}")
        
        return True
    except Exception as e:
        print(f"❌ Bucket access failed: {str(e)}")
        if hasattr(e, 'response'):
            print(f"Error response: {e.response}")
        return False

if __name__ == "__main__":
    if verify_bucket_access():
        seed_sample_data()
    else:
        print("Aborting due to bucket access issues") 