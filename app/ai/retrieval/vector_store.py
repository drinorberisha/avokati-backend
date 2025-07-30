import os
import logging
import re
import datetime
from typing import List, Dict, Any, Optional, Tuple
from pinecone import Pinecone, ServerlessSpec
from langchain_pinecone import PineconeVectorStore
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

# Try to import FAISS, but make it optional
try:
    from langchain_community.vectorstores import FAISS
    HAVE_FAISS = True
except ImportError:
    HAVE_FAISS = False

from app.core.config import settings
from app.ai.embedding import multi_provider_embeddings

logger = logging.getLogger(__name__)

# Simple in-memory vector store fallback
class SimpleVectorStore:
    """Simple in-memory vector store as fallback when FAISS is not available."""
    
    def __init__(self, embeddings):
        self.embeddings = embeddings
        self.documents = []
        self.vectors = []
        self.metadatas = []
    
    def add_texts(self, texts, metadatas=None, ids=None):
        """Add texts to the vector store."""
        if metadatas is None:
            metadatas = [{}] * len(texts)
        
        for i, text in enumerate(texts):
            vector = self.embeddings.embed_query(text)
            self.documents.append(text)
            self.vectors.append(vector)
            self.metadatas.append(metadatas[i] if i < len(metadatas) else {})
        
        return ids or [f"doc_{len(self.documents)-len(texts)+i}" for i in range(len(texts))]
    
    def similarity_search_with_score(self, query, k=5, filter=None):
        """Search for similar documents."""
        if not self.documents:
            return []
        
        query_vector = self.embeddings.embed_query(query)
        
        # Simple cosine similarity
        similarities = []
        for i, doc_vector in enumerate(self.vectors):
            # Cosine similarity
            dot_product = sum(a * b for a, b in zip(query_vector, doc_vector))
            norm_a = sum(a * a for a in query_vector) ** 0.5
            norm_b = sum(b * b for b in doc_vector) ** 0.5
            
            if norm_a * norm_b == 0:
                similarity = 0
            else:
                similarity = dot_product / (norm_a * norm_b)
            
            similarities.append((i, similarity))
        
        # Sort by similarity and get top k
        similarities.sort(key=lambda x: x[1], reverse=True)
        results = []
        
        for i, score in similarities[:k]:
            doc = Document(
                page_content=self.documents[i],
                metadata=self.metadatas[i]
            )
            results.append((doc, score))
        
        return results

logger = logging.getLogger(__name__)

# Use our multi-provider embeddings instead of OpenAI
embeddings = multi_provider_embeddings


class VectorStoreClient:
    """Client for interacting with vector store."""
    
    def __init__(self):
        """Initialize the vector store client."""
        self.index_name = settings.PINECONE_INDEX_NAME
        self.namespace = settings.PINECONE_NAMESPACE
        
        # Enhanced logging for debugging
        logger.info(f"Pinecone configuration:")
        logger.info(f"  Index name: {self.index_name}")
        logger.info(f"  Namespace: {self.namespace}")
        logger.info(f"  API key present: {bool(settings.PINECONE_API_KEY)}")
        logger.info(f"  API key starts with: {settings.PINECONE_API_KEY[:10] + '...' if settings.PINECONE_API_KEY else 'None'}")
        
        self.use_pinecone = bool(settings.PINECONE_API_KEY and settings.PINECONE_API_KEY != "your-api-key" and settings.PINECONE_API_KEY != "")
        
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
                
                # Get dimension from embeddings to ensure correct dimension
                # Use a safe approach to get dimension
                try:
                    # Try to get dimension from providers if available
                    if hasattr(embeddings, 'providers') and embeddings.providers:
                        dimension = embeddings.providers[0].dimension
                    # Fallback to generating a test embedding to determine dimension
                    else:
                        test_embedding = embeddings.embed_query("Test query")
                        dimension = len(test_embedding)
                    
                    logger.info(f"Using embedding dimension: {dimension}")
                except Exception as e:
                    logger.warning(f"Could not determine embedding dimension from provider: {e}")
                    # Default to standard OpenAI dimension as fallback
                    dimension = 1536
                    logger.info(f"Using default embedding dimension: {dimension}")
                
                # Create the index with the correct dimension
                self.pc.create_index(
                    name=self.index_name,
                    dimension=dimension,
                    metric="cosine",
                    spec=ServerlessSpec(
                        cloud="aws",
                        region="us-east-1"
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
    
    def _get_faiss_store(self):
        """Get a FAISS vector store as fallback, or simple in-memory store if FAISS unavailable."""
        if HAVE_FAISS:
            # Create an empty FAISS index
            return FAISS.from_texts(["Initial document"], embeddings)
        else:
            logger.warning("FAISS not available, using simple in-memory vector store")
            return SimpleVectorStore(embeddings)
    
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
                # For FAISS or SimpleVectorStore fallback
                if HAVE_FAISS:
                    new_faiss = FAISS.from_texts(all_chunks, embeddings, metadatas=all_metadatas)
                    self.vector_store = new_faiss
                else:
                    # SimpleVectorStore handles add_texts directly
                    self.vector_store.add_texts(all_chunks, all_metadatas, all_doc_ids)
            
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
            logger.info(f"Searching for: {query} with filter: {filter}, top_k: {top_k}")
            
            # Generate embedding for query
            query_embedding = embeddings.embed_query(query)
            
            if self.use_pinecone:
                try:
                    # Query Pinecone index directly
                    index = self.pc.Index(self.index_name)
                    results = index.query(
                        namespace=self.namespace,
                        vector=query_embedding,
                        top_k=top_k,
                        include_metadata=True,
                        filter=filter
                    )
                    
                    # Format results
                    formatted_results = []
                    for match in results.matches:
                        metadata = match.metadata or {}
                        
                        # Use content field if available, fall back to text
                        text = metadata.get("content") or metadata.get("text") or "[No text available]"
                        
                        doc = Document(page_content=text, metadata=metadata)
                        doc, score = self._normalize_document_metadata(doc, match.score)
                        formatted_results.append((doc, score))
                    
                    return formatted_results
                except Exception as e:
                    logger.error(f"Error searching Pinecone directly: {e}")
                    
                    # Fallback to langchain interface
                    results = self.vector_store.similarity_search_with_score(
                        query=query,
                        k=top_k,
                        filter=filter
                    )
                    return [self._normalize_document_metadata(doc, score) for doc, score in results]
            else:
                # FAISS search
                results = self.vector_store.similarity_search_with_score(
                    query=query,
                    k=top_k
                )
                return [self._normalize_document_metadata(doc, score) for doc, score in results]
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

    def _normalize_document_metadata(self, doc: Document, score: float) -> Tuple[Document, float]:
        """
        Normalize document metadata to ensure it meets schema requirements.
        
        Args:
            doc: The document with metadata to normalize
            score: The relevance score for this document
            
        Returns:
            Tuple containing the document with normalized metadata and the score
        """
        metadata = doc.metadata

        # Handle document_type field
        if "document_type" in metadata and metadata["document_type"] == "unknown":
            if "law_number" in metadata:
                metadata["document_type"] = "law"
            else:
                metadata["document_type"] = "other"
        elif "document_type" not in metadata:
            if "law_number" in metadata:
                metadata["document_type"] = "law"
            else:
                metadata["document_type"] = "other"
        
        # Handle created_at field
        if "created_at" not in metadata:
            created_at = datetime.datetime.now().isoformat()
            
            if "law_name" in metadata:
                date_match = re.search(r'(\d{1,2})\.(\d{1,2})\.(\d{4})', metadata.get("law_name", ""))
                if date_match:
                    day, month, year = date_match.groups()
                    try:
                        created_at = datetime.datetime(int(year), int(month), int(day)).isoformat()
                    except ValueError:
                        pass
            
            metadata["created_at"] = created_at
            
        # Handle ID field - prioritize using the chunk_id from metadata 
        if "id" not in metadata and "chunk_id" in metadata:
            metadata["id"] = metadata["chunk_id"]
        elif "id" not in metadata:
            metadata["id"] = "unknown"
            
        # Handle title field - prioritize using chunk_title or law_name
        if "title" not in metadata:
            if "chunk_title" in metadata:
                metadata["title"] = metadata["chunk_title"]
            elif "law_name" in metadata:
                metadata["title"] = metadata["law_name"]
            else:
                metadata["title"] = "Unknown Document"

        return doc, score


# Singleton instance
vector_store_client = VectorStoreClient() 