#!/usr/bin/env python3
"""
Script to import legal documents from a text file into the system.
This helps migrate from the standalone model.py to the integrated system.
"""
import asyncio
import os
import re
import sys
import json
import logging
import argparse
from typing import List, Dict, Any, Optional
from uuid import uuid4

# Add the parent directory to the path so we can import from app
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.base import get_db
from app.schemas.legal_document import LegalDocumentCreate
from app.crud import legal_document as crud
from app.ai.retrieval.langchain_service import langchain_service

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)


def custom_article_splitter(text: str) -> List[Dict[str, Any]]:
    """
    Splits the document into articles using a regex pattern.
    Each split will start with "Article <number>".
    
    Args:
        text: The text content to split
        
    Returns:
        List of dictionaries with article information
    """
    # The lookahead pattern ensures we split before every occurrence of "Article" followed by digits.
    pattern = r"(?=Article \d+)"
    splits = re.split(pattern, text)
    
    # Remove any empty chunks and trim whitespace.
    articles = [chunk.strip() for chunk in splits if chunk.strip()]
    
    # Create a list of article dictionaries
    article_dicts = []
    for article in articles:
        # Extract article number
        article_num_match = re.match(r"Article (\d+)", article)
        article_num = article_num_match.group(1) if article_num_match else "Unknown"
        
        # Create a title
        title = f"Article {article_num}"
        
        # Create a dictionary for this article
        article_dict = {
            "id": str(uuid4()),
            "title": title,
            "content": article,
            "document_type": "law",
            "document_metadata": {
                "article_number": article_num,
                "source": "imported"
            }
        }
        
        article_dicts.append(article_dict)
    
    return article_dicts


async def import_documents(file_path: str, document_type: str):
    """
    Import documents from a text file into the system.
    
    Args:
        file_path: Path to the text file
        document_type: Type of document (e.g., law, regulation)
    """
    logger.info(f"Importing documents from {file_path}")
    
    # Read the file
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        logger.error(f"Error reading file: {str(e)}")
        return
    
    # Split into articles
    articles = custom_article_splitter(content)
    logger.info(f"Split into {len(articles)} articles")
    
    # Create document objects
    document_creates = []
    for article in articles:
        document_creates.append(LegalDocumentCreate(
            title=article["title"],
            content=article["content"],
            document_type=document_type,
            document_metadata=article["document_metadata"]
        ))
    
    # Get database session
    async for db in get_db():
        # Create documents in database
        db_documents = await crud.batch_create_legal_documents(db, document_creates)
        logger.info(f"Created {len(db_documents)} documents in database")
        
        # Index documents in vector store
        texts = [doc.content for doc in db_documents]
        metadatas = [{
            "id": doc.id,
            "title": doc.title,
            "document_type": doc.document_type,
            "document_metadata": doc.document_metadata or {}
        } for doc in db_documents]
        
        vector_ids = await langchain_service.index_documents(texts, metadatas)
        logger.info(f"Indexed {len(vector_ids)} documents in vector store")
        
        # Update documents with vector IDs
        for i, doc in enumerate(db_documents):
            if i < len(vector_ids):
                await crud.update_legal_document(
                    db, doc.id, {"vector_id": vector_ids[i]}
                )
        
        logger.info("Import completed successfully")
        break


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import legal documents from a text file")
    parser.add_argument("file_path", help="Path to the text file")
    parser.add_argument("--document-type", default="law", help="Type of document (default: law)")
    args = parser.parse_args()
    
    asyncio.run(import_documents(args.file_path, args.document_type)) 