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
- Python 3.10+
- AWS S3 for Document Storage
- Supabase Integration
- Poetry for dependency management

## Getting Started

### Prerequisites

- Python 3.10 or higher
- Poetry (dependency management)
- PostgreSQL
- AWS Account (for S3)
- Supabase Account

### Installation with Poetry

1. Clone the repository:
```bash
git clone https://github.com/drinorberisha/avokati-backend.git
cd avokati-backend
```

2. Install Poetry if you don't have it:
```bash
# Linux/macOS
curl -sSL https://install.python-poetry.org | python3 -

# Windows (PowerShell)
(Invoke-WebRequest -Uri https://install.python-poetry.org -UseBasicParsing).Content | python -
```

3. Install dependencies with Poetry:
```bash
# Install project dependencies
poetry install
```

4. Set up environment variables:
```bash
cp .env.example .env
# Edit .env with your configuration
```

5. Run the application:
```bash
poetry run uvicorn main:app --reload
```

The API will be available at `http://localhost:8000`

### Alternative Setup with Setup Script

We've created a script that helps set up the Poetry environment and install dependencies progressively:

1. Make the script executable:
```bash
chmod +x setup_poetry.sh
```

2. Run the setup script:
```bash
./setup_poetry.sh
```

3. After setup is complete, run the application:
```bash
poetry run uvicorn main:app --reload
```

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

## Deployment to Render

This application is configured for deployment to Render using the `render.yaml` file.

### Deployment Steps

1. Push your code to a Git repository (GitHub, GitLab, or Bitbucket).

2. In Render dashboard, choose "New Web Service" and connect your Git repository.

3. Render will automatically detect the `render.yaml` configuration and use it to deploy your application.

### Environment Variables

Make sure to set any required environment variables in the Render dashboard or in the `render.yaml` file.

## Troubleshooting Dependency Conflicts

If you encounter dependency conflicts during deployment:

1. Check the deployment logs to identify the conflicting packages.

2. Update the Poetry dependencies in `pyproject.toml` with specific compatible versions:
   ```toml
   [tool.poetry.dependencies]
   httpx = ">=0.23.0,<0.24.0"
   supabase = "1.0.3"
   gotrue = "1.2.0"
   realtime = "1.0.0"
   storage3 = ">=0.5.2,<0.6.0"
   websockets = "10.4"
   ```

3. Run `poetry lock --no-update` to update the lock file without changing dependency versions.

### Python Version

This project requires Python 3.10+. Render is configured to use Python 3.10.11 via the `.python-version` file and the `render.yaml` configuration. 