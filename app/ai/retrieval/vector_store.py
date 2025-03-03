import os
import logging
from typing import List, Dict, Any, Optional, Tuple
from pinecone import Pinecone, ServerlessSpec
from langchain_openai import OpenAIEmbeddings
from langchain_pinecone import PineconeVectorStore
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.core.config import settings

logger = logging.getLogger(__name__)

# Initialize embeddings model
embeddings = OpenAIEmbeddings(
    model=settings.OPENAI_EMBEDDING_MODEL,
    openai_api_key=settings.OPENAI_API_KEY
)


class VectorStoreClient:
    """Client for interacting with vector store."""
    
    def __init__(self):
        """Initialize the vector store client."""
        self.index_name = settings.PINECONE_INDEX_NAME
        self.namespace = settings.PINECONE_NAMESPACE
        self.use_pinecone = bool(settings.PINECONE_API_KEY and settings.PINECONE_API_KEY != "your-api-key")
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000,
            chunk_overlap=100,
            separators=["\n\n", "\n", " ", ""]
        )
        
        # Initialize vector store
        if self.use_pinecone:
            try:
                self.pc = Pinecone(api_key=settings.PINECONE_API_KEY)
                self._ensure_index_exists()
                self.vector_store = self._get_pinecone_store()
                logger.info("Successfully initialized Pinecone vector store")
            except Exception as e:
                logger.error(f"Failed to initialize Pinecone: {e}")
                self.use_pinecone = False
                self.vector_store = self._get_faiss_store()
                logger.info("Falling back to FAISS vector store")
        else:
            logger.info("Pinecone API key not configured, using FAISS vector store")
            self.vector_store = self._get_faiss_store()
        
    def _ensure_index_exists(self) -> None:
        """Ensure that the Pinecone index exists, creating it if necessary."""
        if not self.use_pinecone:
            return
            
        try:
            # Check if index already exists
            existing_indexes = [index.name for index in self.pc.list_indexes()]
            
            if self.index_name not in existing_indexes:
                logger.info(f"Creating Pinecone index: {self.index_name}")
                # Create the index
                self.pc.create_index(
                    name=self.index_name,
                    dimension=1536,  # OpenAI embeddings dimension
                    metric="cosine",
                    spec=ServerlessSpec(
                        cloud=settings.PINECONE_CLOUD,
                        region=settings.PINECONE_REGION
                    )
                )
                logger.info(f"Created Pinecone index: {self.index_name}")
            else:
                logger.info(f"Pinecone index already exists: {self.index_name}")
        except Exception as e:
            logger.error(f"Error ensuring Pinecone index exists: {e}")
            self.use_pinecone = False
    
    def _get_pinecone_store(self) -> PineconeVectorStore:
        """Get the Pinecone vector store."""
        index = self.pc.Index(self.index_name)
        return PineconeVectorStore(
            index=index, 
            embedding=embeddings, 
            namespace=self.namespace,
            text_key="text"  # Required parameter for the new PineconeVectorStore
        )
    
    def _get_faiss_store(self) -> FAISS:
        """Get a FAISS vector store as fallback."""
        # Create an empty FAISS index
        return FAISS.from_texts(["Initial document"], embeddings)
    
    async def add_documents(self, texts: List[str], metadatas: List[Dict[str, Any]]) -> List[str]:
        """
        Add documents to the vector store.
        
        Args:
            texts: List of document texts
            metadatas: List of metadata dictionaries for each document
            
        Returns:
            List of vector IDs
        """
        # Split texts into chunks
        all_chunks = []
        all_metadatas = []
        all_doc_ids = []
        
        for i, (text, metadata) in enumerate(zip(texts, metadatas)):
            chunks = self.text_splitter.split_text(text)
            
            # Create metadata for each chunk
            chunk_metadatas = []
            for j in range(len(chunks)):
                chunk_metadata = metadata.copy()
                chunk_metadata["chunk"] = j
                chunk_metadata["total_chunks"] = len(chunks)
                # Add text field for Pinecone
                chunk_metadata["text"] = chunks[j]
                chunk_metadatas.append(chunk_metadata)
            
            all_chunks.extend(chunks)
            all_metadatas.extend(chunk_metadatas)
            
            # Generate document IDs
            doc_id = metadata.get("id", f"doc_{i}")
            doc_ids = [f"{doc_id}_chunk_{j}" for j in range(len(chunks))]
            all_doc_ids.extend(doc_ids)
        
        try:
            # Add documents to vector store
            if self.use_pinecone:
                self.vector_store.add_texts(
                    texts=all_chunks,
                    metadatas=all_metadatas,
                    ids=all_doc_ids
                )
            else:
                # For FAISS, we need to recreate the index
                new_faiss = FAISS.from_texts(all_chunks, embeddings, metadatas=all_metadatas)
                self.vector_store = new_faiss
            
            return all_doc_ids
        except Exception as e:
            logger.error(f"Error adding documents to vector store: {e}")
            return []
    
    async def search(
        self, 
        query: str, 
        filter: Optional[Dict[str, Any]] = None,
        top_k: int = 5
    ) -> List[Tuple[Document, float]]:
        """
        Search for documents similar to the query.
        
        Args:
            query: The search query
            filter: Optional filter for the search
            top_k: Number of results to return
            
        Returns:
            List of (document, score) tuples
        """
        try:
            if self.use_pinecone:
                return self.vector_store.similarity_search_with_score(
                    query=query,
                    k=top_k,
                    filter=filter
                )
            else:
                # FAISS doesn't support filtering in the same way
                return self.vector_store.similarity_search_with_score(
                    query=query,
                    k=top_k
                )
        except Exception as e:
            logger.error(f"Error searching vector store: {e}")
            return []
    
    async def delete(self, ids: List[str]) -> None:
        """
        Delete documents from the vector store.
        
        Args:
            ids: List of document IDs to delete
        """
        if not self.use_pinecone:
            logger.warning("Delete operation not supported with FAISS fallback")
            return
            
        try:
            index = self.pc.Index(self.index_name)
            index.delete(ids=ids, namespace=self.namespace)
        except Exception as e:
            logger.error(f"Error deleting documents from vector store: {e}")
    
    async def delete_all(self) -> None:
        """Delete all documents from the vector store."""
        if not self.use_pinecone:
            # For FAISS, recreate an empty index
            self.vector_store = FAISS.from_texts(["Initial document"], embeddings)
            return
            
        try:
            index = self.pc.Index(self.index_name)
            index.delete(delete_all=True, namespace=self.namespace)
        except Exception as e:
            logger.error(f"Error deleting all documents from vector store: {e}")


# Singleton instance
vector_store_client = VectorStoreClient() 