# AI Model Training & Retrieval Feature

This document explains how to set up and use the AI Model Training & Retrieval feature for the Law Office Management System.

## Overview

The AI Model Training & Retrieval feature allows you to:

1. Index legal documents in a vector database (Pinecone)
2. Search for similar legal documents using semantic search
3. Ask legal questions and get AI-generated answers based on the indexed documents
4. Automatically track relationships between legal documents (abolished, updated)

## Setup

### 1. Environment Variables

Add the following environment variables to your `.env` file:

```
# AI and Retrieval
OPENAI_API_KEY=your_openai_api_key
OPENAI_MODEL=gpt-4o-mini
OPENAI_EMBEDDING_MODEL=text-embedding-3-large

# Pinecone
PINECONE_API_KEY=your_pinecone_api_key
PINECONE_INDEX_NAME=legal-documents
PINECONE_NAMESPACE=default
PINECONE_CLOUD=aws
PINECONE_REGION=us-west-2

# Legal Document API (optional)
LEGAL_DOCUMENT_API_URL=https://api.example.com/legal
```

### 2. Database Setup

Run the SQL migration script in the Supabase SQL Editor:

```sql
-- Copy the contents of backend/app/db/migrations/create_legal_document_table.sql here
```

### 3. Install Dependencies

Make sure you have all the required dependencies installed:

```bash
pip install -r requirements.txt
```

## Usage

### API Endpoints

The following API endpoints are available:

#### Create a Legal Document

```
POST /api/v1/legal-ai/documents
```

Request body:
```json
{
  "title": "Civil Code Article 123",
  "content": "Article 123. The content of the article...",
  "document_type": "law",
  "document_metadata": {
    "article_number": "123",
    "source": "Civil Code"
  }
}
```

#### Batch Create Legal Documents

```
POST /api/v1/legal-ai/documents/batch
```

Request body:
```json
{
  "documents": [
    {
      "title": "Civil Code Article 123",
      "content": "Article 123. The content of the article...",
      "document_type": "law",
      "document_metadata": {
        "article_number": "123",
        "source": "Civil Code"
      }
    },
    {
      "title": "Civil Code Article 124",
      "content": "Article 124. The content of the article...",
      "document_type": "law",
      "document_metadata": {
        "article_number": "124",
        "source": "Civil Code"
      }
    }
  ]
}
```

#### Upload Legal Documents from JSON File

```
POST /api/v1/legal-ai/documents/upload
```

Form data:
- `file`: JSON file containing legal documents
- `document_type`: Type of documents (e.g., "law", "regulation")

#### Search Legal Documents

```
POST /api/v1/legal-ai/search
```

Request body:
```json
{
  "query": "joint liability",
  "document_type": "law",
  "limit": 5
}
```

#### Ask Legal Question

```
POST /api/v1/legal-ai/ask?query=What is joint liability?&document_type=law
```

#### Scrape Legal Documents

```
POST /api/v1/legal-ai/scrape?document_type=law&from_date=2023-01-01&limit=100
```

### Importing Documents

You can import legal documents from a text file using the provided script:

```bash
python backend/scripts/import_legal_documents.py path/to/your/document.txt --document-type law
```

## Architecture

The AI Model Training & Retrieval feature consists of the following components:

1. **Database Model**: `LegalDocument` - Stores legal documents in PostgreSQL
2. **Vector Store**: Pinecone - Stores document embeddings for semantic search
3. **Retrieval Service**: LangChain - Provides document retrieval and question answering
4. **Document Scraper**: Fetches legal documents from external sources

## Troubleshooting

### Common Issues

1. **Vector Store Connection**: Make sure your Pinecone API key is correct and the index exists
2. **OpenAI API Key**: Ensure your OpenAI API key is valid and has sufficient credits
3. **Database Permissions**: Check that the RLS policies are correctly set up in Supabase

### Logs

Check the application logs for more detailed error messages:

```bash
tail -f logs/app.log
```

## Further Development

Future improvements could include:

1. Adding support for more document types (court decisions, regulations)
2. Implementing document versioning and change tracking
3. Adding a user interface for document management
4. Integrating with external legal databases
5. Implementing more advanced question answering capabilities 