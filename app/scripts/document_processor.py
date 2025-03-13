#!/usr/bin/env python3
"""
Document Processing Pipeline Script

This script handles the entire document processing pipeline:
1. Collecting documents from various sources
2. Preprocessing and cleaning text
3. Parsing documents into structured format
4. Splitting documents into chunks
5. Generating embeddings
6. Storing in Pinecone for retrieval

Usage:
    python document_processor.py --source [file|directory|api] --path [path] --document-type [type]
    
Examples:
    # Process a single file
    python document_processor.py --source file --path /path/to/document.pdf --document-type law
    
    # Process all files in a directory
    python document_processor.py --source directory --path /path/to/documents/ --document-type regulation
    
    # Fetch documents from API
    python document_processor.py --source api --document-type case_law --limit 100
"""
import asyncio
import os
import sys
import re
import json
import logging
import argparse
import glob
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple, Set, BinaryIO
from uuid import uuid4
import langdetect
from pathlib import Path
import mimetypes
import aiofiles
from langdetect import detect

# Add the parent directory to the path so we can import from app
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import get_db
from app.schemas.legal_document import LegalDocumentCreate, LegalDocumentUpdate, LegalDocumentVersionCreate
from app.crud import legal_document as crud
from app.ai.retrieval.langchain_service import langchain_service
from app.ai.retrieval.document_scraper import document_scraper
from app.ai.retrieval.vector_store import vector_store_client
from app.core.config import settings
from app.core.s3 import s3
from app.crud.legal_document import update_legal_document, create_legal_document_version
from .text_extraction import extract_text_from_file
from app.utils.text_processing import preprocess_text
from app.utils.document_parsing import parse_document
from app.utils.language_detection import detect_language
from app.utils.logging import logger

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Define supported file types and their handlers
SUPPORTED_FILE_TYPES = {
    ".txt": "text",
    ".pdf": "pdf",
    ".docx": "docx",
    ".doc": "doc",
    ".rtf": "rtf",
    ".html": "html",
    ".htm": "html",
    ".json": "json",
}

# Document type mapping
DOCUMENT_TYPES = {
    "law": "Legal statute or law",
    "regulation": "Government regulation",
    "case_law": "Court decision or case law",
    "contract": "Legal contract or agreement",
    "article": "Legal article or publication",
    "other": "Other legal document"
}


class DocumentProcessor:
    """Handles the entire document processing pipeline."""
    
    def __init__(self, db=None):
        """
        Initialize the document processor.
        
        Args:
            db: Optional database session. If not provided, will be obtained from get_db()
        """
        self.db = db
        self.output_dir = os.path.join(settings.UPLOAD_DIR, "processed_documents")
        os.makedirs(self.output_dir, exist_ok=True)
        self.stats = {
            "documents_collected": 0,
            "documents_processed": 0,
            "chunks_created": 0,
            "documents_indexed": 0,
            "errors": 0
        }
        
    async def ensure_db(self):
        """Ensure we have a database session."""
        if not self.db:
            async for session in get_db():
                self.db = session
                break
        return self.db
        
    async def process_file(
        self,
        file: BinaryIO,
        original_filename: str,
        document_type: str,
        user_id: str,
        title: Optional[str] = None,
        document_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Process a file immediately by:
        1. Extracting and processing text content
        2. Parsing the document
        3. Saving to S3
        4. Creating document record with processed content
        """
        try:
            # Ensure we have a database session
            self.db = await self.ensure_db()
            
            # Extract and process text
            text = extract_text_from_file(file, file.content_type)
            processed_text = preprocess_text(text)
            language = detect_language(processed_text)
            
            # Parse document content based on type
            parsed_content = parse_document(processed_text, document_type)
            
            # Get file size and reset file pointer
            file.seek(0, os.SEEK_END)
            file_size = file.tell()
            file.seek(0)
            mime_type = mimetypes.guess_type(original_filename)[0] or 'application/octet-stream'

            # Generate unique S3 key
            file_key = await s3.generate_file_key(original_filename, user_id)
            
            # Upload file to S3
            await s3.upload_file(file, file_key, mime_type)
            
            # Create document record with processed content
            document_data = LegalDocumentCreate(
                title=title or original_filename,
                document_type=document_type,
                content=processed_text,
                user_id=user_id,
                file_key=file_key,
                file_name=original_filename,
                file_size=file_size,
                mime_type=mime_type,
                status="processed",
                document_metadata={
                    **(document_metadata or {}),
                    "processed_at": datetime.now().isoformat(),
                    "language": language,
                    "word_count": len(processed_text.split()),
                    "char_count": len(processed_text),
                    "parsed_structure": parsed_content
                }
            )
            
            document = await crud.create_legal_document(self.db, document_data)
            
            # Create initial version
            version_data = LegalDocumentVersionCreate(
                document_id=document.id,
                file_key=file_key,
                file_name=original_filename,
                file_size=file_size,
                mime_type=mime_type,
                created_by_id=user_id,
                changes_description="Initial version"
            )
            await create_legal_document_version(self.db, version_data)

            # Generate download URL
            download_url = await s3.generate_presigned_url(file_key, 'get_object')
            
            return {
                "status": "success",
                "document_id": document.id,
                "download_url": download_url,
                "message": "Document processed successfully",
                "parsed_content": parsed_content
            }

        except Exception as e:
            logger.error(f"Error processing file: {str(e)}")
            return {
                "status": "error",
                "message": f"Error processing file: {str(e)}"
            }
    
    async def process_directory(self, directory_path: str, document_type: str) -> List[Dict[str, Any]]:
        """
        Process all supported files in a directory.
        
        Args:
            directory_path: Path to the directory
            document_type: Type of document
            
        Returns:
            List of processed document dictionaries
        """
        logger.info(f"Processing directory: {directory_path}")
        
        # Check if directory exists
        if not os.path.exists(directory_path) or not os.path.isdir(directory_path):
            logger.error(f"Directory not found: {directory_path}")
            self.stats["errors"] += 1
            return []
        
        # Get all supported files
        all_documents = []
        for ext in SUPPORTED_FILE_TYPES:
            files = glob.glob(os.path.join(directory_path, f"**/*{ext}"), recursive=True)
            for file_path in files:
                documents = await self.process_file(file_path, "", "", "")
                if documents["status"] == "success":
                    all_documents.append(documents)
        
        return all_documents
    
    async def process_api(self, document_type: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Fetch and process documents from API.
        
        Args:
            document_type: Optional type of documents to fetch
            limit: Maximum number of documents to fetch
            
        Returns:
            List of processed document dictionaries
        """
        logger.info(f"Fetching documents from API, type: {document_type or 'all'}, limit: {limit}")
        
        # Fetch documents from API
        api_documents = await document_scraper.fetch_documents(
            document_type=document_type,
            limit=limit
        )
        
        if not api_documents:
            logger.warning("No documents fetched from API")
            return []
        
        self.stats["documents_collected"] += len(api_documents)
        
        # Process each document
        processed_documents = []
        for doc in api_documents:
            # Extract text and metadata
            text = doc.get("content", "")
            if not text:
                logger.warning(f"Empty content in document {doc.get('id', 'unknown')}")
                continue
            
            # Preprocess text
            text = self.preprocess_text(text)
            
            # Create document dictionary
            document = {
                "id": doc.get("id", str(uuid4())),
                "title": doc.get("title", "Untitled Document"),
                "content": text,
                "document_type": doc.get("type", document_type or "other"),
                "document_metadata": {
                    "source": "api",
                    "original_id": doc.get("id", ""),
                    "publication_date": doc.get("publication_date", ""),
                    "author": doc.get("author", ""),
                    "jurisdiction": doc.get("jurisdiction", ""),
                    "fetched_at": datetime.now().isoformat()
                }
            }
            
            processed_documents.append(document)
        
        self.stats["documents_processed"] += len(processed_documents)
        return processed_documents
    
    def extract_text(self, file_path: str, file_type: str) -> str:
        """
        Extract text from a file based on its type.
        
        Args:
            file_path: Path to the file
            file_type: Type of file
            
        Returns:
            Extracted text
        """
        if file_type == "text":
            with open(file_path, "r", encoding="utf-8") as f:
                return f.read()
        
        elif file_type == "pdf":
            try:
                import PyPDF2
                with open(file_path, "rb") as f:
                    reader = PyPDF2.PdfReader(f)
                    text = ""
                    for page in reader.pages:
                        text += page.extract_text() + "\n\n"
                    return text
            except ImportError:
                logger.error("PyPDF2 not installed. Install with: pip install PyPDF2")
                raise
        
        elif file_type == "docx":
            try:
                import docx
                doc = docx.Document(file_path)
                return "\n\n".join([para.text for para in doc.paragraphs])
            except ImportError:
                logger.error("python-docx not installed. Install with: pip install python-docx")
                raise
        
        elif file_type == "doc":
            logger.error("DOC format requires external conversion. Convert to DOCX or TXT first.")
            raise NotImplementedError("DOC format not directly supported")
        
        elif file_type == "rtf":
            try:
                import striprtf.striprtf
                with open(file_path, "r", encoding="utf-8") as f:
                    rtf_text = f.read()
                return striprtf.striprtf.rtf_to_text(rtf_text)
            except ImportError:
                logger.error("striprtf not installed. Install with: pip install striprtf")
                raise
        
        elif file_type == "html":
            try:
                from bs4 import BeautifulSoup
                with open(file_path, "r", encoding="utf-8") as f:
                    soup = BeautifulSoup(f.read(), "html.parser")
                    # Remove script and style elements
                    for script in soup(["script", "style"]):
                        script.extract()
                    return soup.get_text()
            except ImportError:
                logger.error("BeautifulSoup not installed. Install with: pip install beautifulsoup4")
                raise
        
        elif file_type == "json":
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Assuming the JSON has a "content" field
                if isinstance(data, dict) and "content" in data:
                    return data["content"]
                # If it's a list of documents
                elif isinstance(data, list) and all(isinstance(item, dict) for item in data):
                    return "\n\n".join([item.get("content", "") for item in data if "content" in item])
                else:
                    return json.dumps(data)
        
        else:
            logger.error(f"Unsupported file type: {file_type}")
            raise ValueError(f"Unsupported file type: {file_type}")
    
    def preprocess_text(self, text: str) -> str:
        """
        Preprocess and clean text.
        
        Args:
            text: Raw text
            
        Returns:
            Preprocessed text
        """
        # Remove excessive whitespace
        text = re.sub(r'\s+', ' ', text)
        
        # Remove control characters
        text = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', text)
        
        # Normalize line breaks
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        
        # Detect language
        try:
            lang = langdetect.detect(text)
            logger.info(f"Detected language: {lang}")
        except:
            logger.warning("Could not detect language")
        
        return text.strip()
    
    def parse_document(self, text: str, document_type: str, source: str) -> List[Dict[str, Any]]:
        """
        Parse document into structured format.
        
        Args:
            text: Preprocessed text
            document_type: Type of document
            source: Source of the document
            
        Returns:
            List of document dictionaries
        """
        # For laws and regulations, try to split by articles
        if document_type in ["law", "regulation"]:
            return self.split_by_articles(text, document_type, source)
        
        # For case law, try to split by sections
        elif document_type == "case_law":
            return self.split_by_sections(text, document_type, source)
        
        # For other document types, treat as a single document
        else:
            # Create a title from the source filename or first line
            title = os.path.basename(source) if os.path.exists(source) else text.split('\n')[0][:100]
            
            return [{
                "id": str(uuid4()),
                "title": title,
                "content": text,
                "document_type": document_type,
                "document_metadata": {
                    "source": source,
                    "processed_at": datetime.now().isoformat()
                }
            }]
    
    def split_by_articles(self, text: str, document_type: str, source: str) -> List[Dict[str, Any]]:
        """
        Split document by articles.
        
        Args:
            text: Preprocessed text
            document_type: Type of document
            source: Source of the document
            
        Returns:
            List of article dictionaries
        """
        # Extract document title from first line or filename
        title_lines = text.split('\n', 1)[0]
        document_title = title_lines if len(title_lines) < 100 else os.path.basename(source)
        
        # The lookahead pattern ensures we split before every occurrence of "Article" followed by digits
        pattern = r"(?=Article \d+)"
        splits = re.split(pattern, text)
        
        # Remove any empty chunks and trim whitespace
        articles = [chunk.strip() for chunk in splits if chunk.strip()]
        
        # If no articles found, treat as a single document
        if len(articles) <= 1:
            return [{
                "id": str(uuid4()),
                "title": document_title,
                "content": text,
                "document_type": document_type,
                "document_metadata": {
                    "source": source,
                    "processed_at": datetime.now().isoformat()
                }
            }]
        
        # Create a list of article dictionaries
        article_dicts = []
        for article in articles:
            # Extract article number
            article_num_match = re.match(r"Article (\d+)", article)
            article_num = article_num_match.group(1) if article_num_match else "Unknown"
            
            # Create a title
            article_title = f"{document_title} - Article {article_num}"
            
            # Create a dictionary for this article
            article_dict = {
                "id": str(uuid4()),
                "title": article_title,
                "content": article,
                "document_type": document_type,
                "document_metadata": {
                    "document_title": document_title,
                    "article_number": article_num,
                    "source": source,
                    "processed_at": datetime.now().isoformat()
                }
            }
            
            article_dicts.append(article_dict)
        
        return article_dicts
    
    def split_by_sections(self, text: str, document_type: str, source: str) -> List[Dict[str, Any]]:
        """
        Split document by sections.
        
        Args:
            text: Preprocessed text
            document_type: Type of document
            source: Source of the document
            
        Returns:
            List of section dictionaries
        """
        # Extract document title from first line or filename
        title_lines = text.split('\n', 1)[0]
        document_title = title_lines if len(title_lines) < 100 else os.path.basename(source)
        
        # Common section headers in legal documents
        section_patterns = [
            r"(?=\n[IVX]+\.\s)",  # Roman numerals with period
            r"(?=\n\d+\.\s)",     # Numbers with period
            r"(?=\nSection \d+)",  # "Section" followed by number
            r"(?=\nPART [IVX]+)",  # "PART" followed by Roman numerals
            r"(?=\nCHAPTER \d+)"   # "CHAPTER" followed by number
        ]
        
        # Try each pattern until we get a reasonable number of sections
        sections = [text]
        for pattern in section_patterns:
            temp_sections = re.split(pattern, text)
            # If we get at least 2 sections and not too many, use this split
            if 2 <= len(temp_sections) <= 20:
                sections = temp_sections
                break
        
        # Remove any empty chunks and trim whitespace
        sections = [chunk.strip() for chunk in sections if chunk.strip()]
        
        # Create a list of section dictionaries
        section_dicts = []
        for i, section in enumerate(sections):
            # Extract section title from first line
            section_title_line = section.split('\n', 1)[0]
            section_title = f"{document_title} - {section_title_line}"
            
            # Create a dictionary for this section
            section_dict = {
                "id": str(uuid4()),
                "title": section_title[:100],  # Limit title length
                "content": section,
                "document_type": document_type,
                "document_metadata": {
                    "document_title": document_title,
                    "section_number": i + 1,
                    "section_title": section_title_line,
                    "source": source,
                    "processed_at": datetime.now().isoformat()
                }
            }
            
            section_dicts.append(section_dict)
        
        return section_dicts
    
    async def index_documents(self, documents: List[Dict[str, Any]]) -> List[str]:
        """
        Index documents in the vector store.
        
        Args:
            documents: List of document dictionaries
            
        Returns:
            List of vector IDs
        """
        if not documents:
            logger.warning("No documents to index")
            return []
        
        logger.info(f"Indexing {len(documents)} documents in vector store")
        
        # Prepare texts and metadatas
        texts = [doc["content"] for doc in documents]
        metadatas = [{
            "id": doc["id"],
            "title": doc["title"],
            "document_type": doc["document_type"],
            "document_metadata": doc["document_metadata"]
        } for doc in documents]
        
        # Index documents
        try:
            vector_ids = await langchain_service.index_documents(texts, metadatas)
            self.stats["documents_indexed"] += len(vector_ids)
            return vector_ids
        except Exception as e:
            logger.error(f"Error indexing documents: {str(e)}")
            self.stats["errors"] += 1
            return []
    
    async def save_to_database(self, documents: List[Dict[str, Any]], vector_ids: List[str]) -> List[str]:
        """
        Save documents to database.
        
        Args:
            documents: List of document dictionaries
            vector_ids: List of vector IDs
            
        Returns:
            List of database IDs
        """
        if not documents:
            logger.warning("No documents to save to database")
            return []
        
        logger.info(f"Saving {len(documents)} documents to database")
        
        # Create document objects
        document_creates = []
        for doc in documents:
            document_creates.append(LegalDocumentCreate(
                title=doc["title"],
                content=doc["content"],
                document_type=doc["document_type"],
                document_metadata=doc["document_metadata"]
            ))
        
        # Get database session
        db_ids = []
        async for db in get_db():
            try:
                # Create documents in database
                db_documents = await crud.batch_create_legal_documents(db, document_creates)
                logger.info(f"Created {len(db_documents)} documents in database")
                
                # Update documents with vector IDs
                for i, doc in enumerate(db_documents):
                    if i < len(vector_ids):
                        await crud.update_legal_document(
                            db, doc.id, {"vector_id": vector_ids[i]}
                        )
                
                db_ids = [doc.id for doc in db_documents]
            except Exception as e:
                logger.error(f"Error saving documents to database: {str(e)}")
                self.stats["errors"] += 1
            
            break
        
        return db_ids
    
    async def save_to_json(self, documents: List[Dict[str, Any]], output_file: Optional[str] = None) -> str:
        """
        Save documents to a JSON file.
        
        Args:
            documents: List of document dictionaries
            output_file: Optional output file path
            
        Returns:
            Path to the saved file
        """
        if not output_file:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_file = os.path.join(self.output_dir, f"processed_documents_{timestamp}.json")
        
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(documents, f, ensure_ascii=False, indent=2)
        
        logger.info(f"Saved {len(documents)} documents to {output_file}")
        return output_file
    
    def print_stats(self):
        """Print processing statistics."""
        logger.info("=== Document Processing Statistics ===")
        logger.info(f"Documents collected: {self.stats['documents_collected']}")
        logger.info(f"Documents processed: {self.stats['documents_processed']}")
        logger.info(f"Documents indexed: {self.stats['documents_indexed']}")
        logger.info(f"Errors: {self.stats['errors']}")
        logger.info("=====================================")
    
    def detect_language(self, text: str) -> str:
        """Detect the language of the text."""
        try:
            return detect(text)
        except:
            return "unknown"

    async def get_document_download_url(self, document_id: str, version_number: Optional[int] = None) -> Optional[str]:
        """Generate a presigned URL for downloading a document."""
        try:
            if version_number:
                # Get specific version
                version = await get_legal_document_version(self.db, document_id, version_number)
                if not version:
                    return None
                file_key = version.file_key
            else:
                # Get current version
                document = await get_legal_document(self.db, document_id)
                if not document or not document.file_key:
                    return None
                file_key = document.file_key

            # Generate presigned URL using the S3 service
            return await s3.generate_presigned_url(file_key, 'get_object')

        except Exception as e:
            logger.error(f"Error generating download URL for document {document_id}: {str(e)}")
            return None

    async def create_new_version(
        self,
        document_id: str,
        file: BinaryIO,
        original_filename: str,
        user_id: str,
        changes_description: str
    ) -> Dict[str, Any]:
        """Create a new version of an existing document"""
        try:
            # Generate unique S3 key for new version
            file_key = await s3.generate_file_key(original_filename, user_id)
            
            # Get file size and mime type
            file.seek(0, os.SEEK_END)
            file_size = file.tell()
            file.seek(0)
            mime_type = mimetypes.guess_type(original_filename)[0] or 'application/octet-stream'

            # Upload new version to S3
            await s3.upload_file(file, file_key, mime_type)

            # Create version record
            version_data = LegalDocumentVersionCreate(
                document_id=document_id,
                file_key=file_key,
                file_name=original_filename,
                file_size=file_size,
                mime_type=mime_type,
                created_by_id=user_id,
                changes_description=changes_description
            )
            version = await create_legal_document_version(self.db, version_data)

            # Extract and process text
            try:
                text = await extract_text_from_file(file, mime_type)
                processed_text = preprocess_text(text)
                language = detect_language(processed_text)

                # Update document with new content
                document = await update_legal_document(
                    self.db,
                    document_id,
                    {
                        "content": processed_text,
                        "status": "processed",
                        "document_metadata": {
                            "processed_at": datetime.now().isoformat(),
                            "language": language,
                            "word_count": len(processed_text.split()),
                            "char_count": len(processed_text)
                        }
                    }
                )

                # Generate download URL
                download_url = await s3.generate_presigned_url(file_key, 'get_object')
                
                return {
                    "status": "success",
                    "document_id": document_id,
                    "version_id": version.id,
                    "download_url": download_url,
                    "message": "New version created successfully"
                }

            except Exception as e:
                logger.error(f"Error processing document content: {str(e)}")
                await update_legal_document(
                    self.db,
                    document_id,
                    {"status": "failed"}
                )
                return {
                    "status": "error",
                    "message": f"Error processing document content: {str(e)}"
                }

        except Exception as e:
            logger.error(f"Error creating new version: {str(e)}")
            return {
                "status": "error",
                "message": f"Error creating new version: {str(e)}"
            }


async def main():
    """Main function."""
    parser = argparse.ArgumentParser(description="Process legal documents")
    parser.add_argument("--source", choices=["file", "directory", "api"], required=True,
                        help="Source of documents")
    parser.add_argument("--path", help="Path to file or directory")
    parser.add_argument("--document-type", default="other",
                        choices=list(DOCUMENT_TYPES.keys()),
                        help="Type of document")
    parser.add_argument("--limit", type=int, default=100,
                        help="Maximum number of documents to fetch from API")
    parser.add_argument("--save-json", action="store_true",
                        help="Save processed documents to JSON file")
    parser.add_argument("--output-file", help="Output JSON file path")
    parser.add_argument("--skip-database", action="store_true",
                        help="Skip saving to database")
    args = parser.parse_args()
    
    # Validate arguments
    if args.source in ["file", "directory"] and not args.path:
        parser.error("--path is required for file or directory source")
    
    processor = DocumentProcessor()
    
    # Process documents based on source
    if args.source == "file":
        documents = await processor.process_file(args.path, "", "", "")
    elif args.source == "directory":
        documents = await processor.process_directory(args.path, args.document_type)
    elif args.source == "api":
        documents = await processor.process_api(args.document_type, args.limit)
    
    # Save to JSON if requested
    if args.save_json or args.output_file:
        await processor.save_to_json(documents, args.output_file)
    
    # Index documents
    vector_ids = await processor.index_documents(documents)
    
    # Save to database
    if not args.skip_database:
        await processor.save_to_database(documents, vector_ids)
    
    # Print statistics
    processor.print_stats()


if __name__ == "__main__":
    asyncio.run(main()) 