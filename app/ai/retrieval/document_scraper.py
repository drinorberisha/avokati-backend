import logging
import json
import os
from typing import List, Dict, Any, Optional
import aiohttp
import asyncio
from datetime import datetime

from app.core.config import settings

logger = logging.getLogger(__name__)


class DocumentScraper:
    """Scraper for legal documents from external sources."""
    
    def __init__(self):
        """Initialize the document scraper."""
        self.base_url = settings.LEGAL_DOCUMENT_API_URL
        self.output_dir = os.path.join(settings.UPLOAD_DIR, "legal_documents")
        os.makedirs(self.output_dir, exist_ok=True)
    
    async def fetch_document(self, document_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetch a single legal document from the API.
        
        Args:
            document_id: ID of the document to fetch
            
        Returns:
            Document data or None if not found
        """
        url = f"{self.base_url}/documents/{document_id}"
        
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        logger.error(f"Failed to fetch document {document_id}: {response.status}")
                        return None
            except Exception as e:
                logger.error(f"Error fetching document {document_id}: {str(e)}")
                return None
    
    async def fetch_documents(
        self, 
        document_type: Optional[str] = None,
        from_date: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Fetch legal documents from the API.
        
        Args:
            document_type: Optional type of documents to fetch
            from_date: Optional date to fetch documents from (ISO format)
            limit: Maximum number of documents to fetch
            
        Returns:
            List of document data
        """
        url = f"{self.base_url}/documents"
        params = {"limit": limit}
        
        if document_type:
            params["type"] = document_type
        
        if from_date:
            params["from_date"] = from_date
        
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, params=params) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        logger.error(f"Failed to fetch documents: {response.status}")
                        return []
            except Exception as e:
                logger.error(f"Error fetching documents: {str(e)}")
                return []
    
    async def save_documents_to_json(
        self, 
        documents: List[Dict[str, Any]],
        output_file: Optional[str] = None
    ) -> str:
        """
        Save documents to a JSON file.
        
        Args:
            documents: List of document data
            output_file: Optional output file path
            
        Returns:
            Path to the saved file
        """
        if not output_file:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_file = os.path.join(self.output_dir, f"legal_documents_{timestamp}.json")
        
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(documents, f, ensure_ascii=False, indent=2)
        
        logger.info(f"Saved {len(documents)} documents to {output_file}")
        return output_file
    
    async def load_documents_from_json(self, input_file: str) -> List[Dict[str, Any]]:
        """
        Load documents from a JSON file.
        
        Args:
            input_file: Path to the JSON file
            
        Returns:
            List of document data
        """
        try:
            with open(input_file, "r", encoding="utf-8") as f:
                documents = json.load(f)
            
            logger.info(f"Loaded {len(documents)} documents from {input_file}")
            return documents
        except Exception as e:
            logger.error(f"Error loading documents from {input_file}: {str(e)}")
            return []
    
    async def analyze_document_relationships(
        self, documents: List[Dict[str, Any]]
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Analyze relationships between legal documents.
        
        Args:
            documents: List of document data
            
        Returns:
            Dictionary with abolished, updated, and new documents
        """
        # Group documents by type
        documents_by_type = {}
        for doc in documents:
            doc_type = doc.get("type", "unknown")
            if doc_type not in documents_by_type:
                documents_by_type[doc_type] = []
            documents_by_type[doc_type].append(doc)
        
        # Analyze relationships
        abolished_docs = []
        updated_docs = []
        new_docs = []
        
        for doc in documents:
            # Check if document abolishes other documents
            if "abolishes" in doc and doc["abolishes"]:
                for abolished_id in doc["abolishes"]:
                    abolished_docs.append({
                        "id": abolished_id,
                        "abolished_by": doc["id"]
                    })
            
            # Check if document updates other documents
            if "updates" in doc and doc["updates"]:
                for updated_id in doc["updates"]:
                    updated_docs.append({
                        "id": updated_id,
                        "updated_by": doc["id"]
                    })
            
            # Check if document is new
            if "replaces" not in doc or not doc["replaces"]:
                new_docs.append({
                    "id": doc["id"]
                })
        
        return {
            "abolished": abolished_docs,
            "updated": updated_docs,
            "new": new_docs
        }


# Singleton instance
document_scraper = DocumentScraper() 