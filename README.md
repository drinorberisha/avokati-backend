# Law Office Management System Backend

A FastAPI-based backend system for managing law office operations including cases, clients, documents, and user management.

## Features

- User Authentication and Authorization
- Case Management
- Client Management
- Document Management with S3 Storage
- Secure API Endpoints
- PostgreSQL Database Integration

## Tech Stack

- FastAPI
- PostgreSQL
- SQLAlchemy
- Pydantic
- Python 3.9+
- AWS S3 for Document Storage
- Supabase Integration

## Getting Started

### Prerequisites

- Python 3.9 or higher
- PostgreSQL
- AWS Account (for S3)
- Supabase Account

### Installation

1. Clone the repository:
```bash
git clone https://github.com/drinorberisha/avokati-backend.git
cd avokati-backend
```

2. Create and activate virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Set up environment variables:
```bash
cp .env.example .env
# Edit .env with your configuration
```

5. Run the application:
```bash
uvicorn main:app --reload
```

The API will be available at `http://localhost:8000`

### API Documentation

Once the server is running, you can access:
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

## Environment Variables

Required environment variables in `.env`:

- `DATABASE_URL`: PostgreSQL connection string
- `SECRET_KEY`: JWT secret key
- `AWS_ACCESS_KEY_ID`: AWS access key
- `AWS_SECRET_ACCESS_KEY`: AWS secret key
- `S3_BUCKET_NAME`: AWS S3 bucket name
- `SUPABASE_URL`: Supabase project URL
- `SUPABASE_KEY`: Supabase API key

## Deployment

This backend is configured for deployment on Render.com.

## License

[MIT License](LICENSE)

## Author

Drinor Berisha 