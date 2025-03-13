# Document Processing Pipeline

This document describes the document processing pipeline script that handles the entire workflow from document collection to Pinecone storage for the legal AI system.

## Overview

The document processor script (`document_processor.py`) is designed to:

1. Collect documents from various sources (files, directories, or API)
2. Preprocess and clean the text
3. Parse documents into structured format
4. Split documents into logical chunks (articles, sections)
5. Generate embeddings using OpenAI
6. Store in Pinecone for efficient retrieval

## Prerequisites

Ensure you have installed all required dependencies:

```bash
pip install -r requirements.txt
```

The script requires the following environment variables to be set:

- `OPENAI_API_KEY`: Your OpenAI API key
- `PINECONE_API_KEY`: Your Pinecone API key (optional, will use FAISS if not provided)
- `PINECONE_ENVIRONMENT`: Your Pinecone environment (required if using Pinecone)

## Usage

The script can be run from the command line with various options:

```bash
python document_processor.py --source [file|directory|api] --path [path] --document-type [type]
```

### Command Line Arguments

| Argument | Description | Required |
|----------|-------------|----------|
| `--source` | Source of documents (`file`, `directory`, or `api`) | Yes |
| `--path` | Path to file or directory (required for `file` and `directory` sources) | Conditional |
| `--document-type` | Type of document (see below) | No (defaults to `other`) |
| `--limit` | Maximum number of documents to fetch from API | No (defaults to 100) |
| `--save-json` | Save processed documents to JSON file | No |
| `--output-file` | Output JSON file path | No |
| `--skip-database` | Skip saving to database | No |

### Document Types

The script supports the following document types:

- `law`: Legal statute or law
- `regulation`: Government regulation
- `case_law`: Court decision or case law
- `contract`: Legal contract or agreement
- `article`: Legal article or publication
- `other`: Other legal document

### Examples

#### Process a Single File

```bash
python document_processor.py --source file --path /path/to/document.pdf --document-type law
```

This will:
1. Extract text from the PDF file
2. Preprocess the text
3. Parse the document into articles (for laws)
4. Index the articles in the vector store
5. Save the articles to the database

#### Process All Files in a Directory

```bash
python document_processor.py --source directory --path /path/to/documents/ --document-type regulation
```

This will process all supported files in the directory and its subdirectories.

#### Fetch Documents from API

```bash
python document_processor.py --source api --document-type case_law --limit 100
```

This will fetch up to 100 case law documents from the configured API, process them, and store them in the vector store and database.

#### Save Processed Documents to JSON

```bash
python document_processor.py --source file --path /path/to/document.pdf --save-json --output-file processed.json
```

This will process the file and save the processed documents to `processed.json`.

## Supported File Types

The script supports the following file types:

- `.txt`: Plain text files
- `.pdf`: PDF documents
- `.docx`: Microsoft Word documents
- `.rtf`: Rich Text Format documents
- `.html`/`.htm`: HTML documents
- `.json`: JSON documents

## Document Processing Flow

1. **Collection**: Documents are collected from the specified source
2. **Text Extraction**: Text is extracted from the documents based on file type
3. **Preprocessing**: Text is cleaned and normalized
4. **Parsing**: Documents are parsed into structured format
5. **Chunking**: Documents are split into logical chunks (articles, sections)
6. **Indexing**: Chunks are indexed in the vector store
7. **Storage**: Documents are saved to the database

## Document Structure

Each processed document has the following structure:

```json
{
  "id": "unique-id",
  "title": "Document Title",
  "content": "Document content...",
  "document_type": "law",
  "document_metadata": {
    "source": "file-path-or-api",
    "document_title": "Original document title",
    "article_number": "123",
    "processed_at": "2023-06-01T12:00:00"
  }
}
```

## Integration with AvokAI

The processed documents are stored in Pinecone and can be retrieved by the AvokAI system when answering legal questions. The system will:

1. Receive a question from the user
2. Convert the question to an embedding
3. Search the vector store for relevant documents
4. Use the retrieved documents as context to generate an answer

## Troubleshooting

### Common Issues

1. **Missing Dependencies**: Ensure all required packages are installed
2. **API Keys**: Verify that the required API keys are set in the environment
3. **File Permissions**: Ensure the script has permission to read the specified files
4. **Unsupported File Types**: Convert unsupported file types to supported formats

### Logs

The script logs information to the console. You can redirect the logs to a file:

```bash
python document_processor.py --source file --path /path/to/document.pdf > processing.log 2>&1
```

## Extending the Script

### Adding Support for New File Types

To add support for a new file type, update the `SUPPORTED_FILE_TYPES` dictionary and implement the extraction logic in the `extract_text` method.

### Customizing Document Parsing

To customize how documents are parsed, modify the `parse_document`, `split_by_articles`, or `split_by_sections` methods in the `DocumentProcessor` class.

### Adding New Document Sources

To add support for a new document source, implement a new method in the `DocumentProcessor` class and update the `main` function to use it. 