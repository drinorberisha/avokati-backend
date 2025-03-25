#!/bin/bash
set -e  # Exit on error

echo "=== Setting up Poetry environment for Law Office Backend ==="

# Step 1: Remove any existing Poetry environments for this project
echo "Removing existing virtual environments..."
rm -rf venv ~/.cache/pypoetry/virtualenvs/backend-*

# Step 2: Install core dependencies
echo "Installing core dependencies..."
poetry install

# Step 3: Testing if the server starts with basic dependencies
echo "Testing if server starts with basic dependencies..."
poetry run uvicorn main:app --reload &
SERVER_PID=$!
sleep 5
kill $SERVER_PID
echo "Core setup complete"

# Step 4: Adding Supabase dependencies
echo "Adding Supabase dependencies..."
poetry add httpx=">=0.23.0,<0.24.0" supabase="1.0.3" gotrue="1.2.0" realtime="1.0.0" postgrest="^0.10.0" storage3=">=0.5.2,<0.6.0" websockets="10.4"

# Step 5: Adding AWS/S3 dependencies
echo "Adding AWS dependencies..."
poetry add aioboto3

# Step 6: Adding LangChain and ML dependencies
echo "Adding LangChain and ML dependencies..."
poetry add langchain="^0.3.0" langchain-community="^0.3.0" langchain-core="^0.3.0" langchain-openai="^0.3.0" langchain-pinecone="^0.2.0" langchain-text-splitters="^0.3.0" openai="^1.1.0" pinecone="^5.0.0" langdetect

# Step 7: Adding utility dependencies
echo "Adding utility dependencies..."
poetry add aiofiles="^23.1.0" alembic="^1.10.0" email_validator="^2.0.0" pypdf2="^3.0.1" python-docx celery="^5.3.0" redis="^4.5.0"

# Step 8: Adding development dependencies
echo "Adding development dependencies..."
poetry add --group dev pytest="^8.0.0" pytest-asyncio="^0.23.0" pytest-cov="^4.1.0" pytest-mock="^3.10.0"

# Final test
echo "Final test of server startup..."
poetry run uvicorn main:app --reload &
SERVER_PID=$!
sleep 10
kill $SERVER_PID

echo "=== Setup complete! ==="
echo "To activate the Poetry environment, run: poetry shell"
echo "To start the server, run: poetry run uvicorn main:app --reload" 