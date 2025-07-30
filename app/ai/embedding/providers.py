"""
Embedding providers module with support for multiple embedding services.
"""
import os
import logging
import time
import random
import hashlib
import re
from datetime import datetime
from typing import Dict, List, Any, Optional, Union, Callable, Type, TypeVar
from abc import ABC, abstractmethod

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Try to import optional dependencies
try:
    from sentence_transformers import SentenceTransformer
    HAVE_SENTENCE_TRANSFORMERS = True
except ImportError:
    HAVE_SENTENCE_TRANSFORMERS = False
    logger.warning("sentence_transformers not available. HuggingFace embeddings will not work.")

try:
    import openai
    from openai import OpenAI
    HAVE_OPENAI = True
except ImportError:
    HAVE_OPENAI = False
    logger.warning("openai not available. OpenAI embeddings will not work.")

# Import the LangChain Embeddings base class
try:
    from langchain_core.embeddings import Embeddings as LangChainEmbeddings
    HAVE_LANGCHAIN = True
except ImportError:
    HAVE_LANGCHAIN = False
    # Create a mock class to avoid errors if LangChain is not installed
    class LangChainEmbeddings:
        """Mock class for LangChain Embeddings when the package is not installed."""
        pass

class EmbeddingProvider(ABC):
    """Abstract base class for embedding providers."""
    
    @abstractmethod
    def embed_query(self, text: str) -> List[float]:
        """Generate an embedding for a single query text."""
        pass
    
    @abstractmethod
    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for a list of documents."""
        pass
    
    @property
    @abstractmethod
    def dimension(self) -> int:
        """Get the embedding dimension."""
        pass
    
    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Get the provider name."""
        pass
    
    @property
    @abstractmethod
    def model_name(self) -> str:
        """Get the model name."""
        pass

class OpenAIEmbeddingProvider(EmbeddingProvider):
    """OpenAI embedding provider."""
    
    def __init__(
        self,
        api_key: str,
        model: str = "text-embedding-3-large",
        rate_limit_delay: float = 1.0,
        max_retries: int = 3
    ):
        """
        Initialize the OpenAI embedding provider.
        
        Args:
            api_key: OpenAI API key
            model: Model to use for embeddings
            rate_limit_delay: Delay in seconds between API calls
            max_retries: Maximum number of retries for API calls
        """
        if not HAVE_OPENAI:
            raise ImportError("openai package is required for OpenAIEmbeddingProvider")
            
        self.api_key = api_key
        self._model = model
        self.rate_limit_delay = rate_limit_delay
        self.max_retries = max_retries
        self.client = OpenAI(api_key=api_key)
        
        # Set dimensions based on model
        if model == "text-embedding-3-large":
            self._dimension = 3072
        elif model == "text-embedding-3-small":
            self._dimension = 1536
        elif model == "text-embedding-ada-002":
            self._dimension = 1536
        else:
            # Default to ada dimension for unknown models
            self._dimension = 3072
            
        logger.info(f"OpenAI embedding provider initialized (model: {model}, dimension: {self._dimension})")
    
    def embed_query(self, text: str) -> List[float]:
        """
        Generate an embedding for a query.
        
        Args:
            text: Text to generate an embedding for
            
        Returns:
            Embedding vector as a list of floats
        """
        return self.embed_documents([text])[0]
    
    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """
        Generate embeddings for multiple texts.
        
        Args:
            texts: List of texts to generate embeddings for
            
        Returns:
            List of embedding vectors
        """
        if not texts:
            return []
            
        for attempt in range(self.max_retries):
            try:
                # Add jitter to rate limit delay to avoid thundering herd
                jitter = random.uniform(0, 0.1)
                time.sleep(self.rate_limit_delay + jitter)
                
                # Generate embeddings
                response = self.client.embeddings.create(
                    model=self._model,
                    input=texts
                )
                
                # Extract embeddings from response
                embeddings = [item.embedding for item in response.data]
                
                return embeddings
                
            except Exception as e:
                error_message = str(e).lower()
                
                # Check if we've exceeded rate limits or quota
                if "rate limit" in error_message:
                    retry_delay = min(2 ** attempt + random.uniform(0, 1), 60)
                    logger.warning(f"Rate limit exceeded. Retrying in {retry_delay:.2f} seconds...")
                    time.sleep(retry_delay)
                elif "exceeded your current quota" in error_message:
                    logger.error("OpenAI API quota exceeded.")
                    raise  # Propagate quota errors to trigger fallback
                else:
                    logger.error(f"Error generating embedding with OpenAI: {e}")
                    if attempt == self.max_retries - 1:
                        raise  # Re-raise the last exception if we've run out of retries
                    
                    # Exponential backoff
                    retry_delay = min(2 ** attempt + random.uniform(0, 1), 60)
                    logger.warning(f"Retrying in {retry_delay:.2f} seconds...")
                    time.sleep(retry_delay)
        
        # If we get here, all retries failed
        raise Exception(f"Failed to generate embeddings after {self.max_retries} attempts")
    
    @property
    def dimension(self) -> int:
        """Get the embedding dimension."""
        return self._dimension
    
    @property
    def provider_name(self) -> str:
        """Get the provider name."""
        return "openai"
    
    @property
    def model_name(self) -> str:
        """Get the model name."""
        return self._model

class HuggingFaceEmbeddingProvider(EmbeddingProvider):
    """HuggingFace embedding provider using the sentence-transformers library."""
    
    def __init__(
        self,
        model: str = "all-MiniLM-L6-v2",
        cache_dir: Optional[str] = None
    ):
        """
        Initialize the HuggingFace embedding provider.
        
        Args:
            model: Model to use for embeddings
            cache_dir: Directory to cache models
        """
        if not HAVE_SENTENCE_TRANSFORMERS:
            raise ImportError("sentence_transformers package is required for HuggingFaceEmbeddingProvider")
            
        self._model_name = model
        self.cache_dir = cache_dir
        
        try:
            # Initialize the model
            self.model = SentenceTransformer(model, cache_folder=cache_dir)
            # Get dimension from model
            self._dimension = self.model.get_sentence_embedding_dimension()
            logger.info(f"HuggingFace embedding provider initialized (model: {model}, dimension: {self._dimension})")
        except Exception as e:
            logger.error(f"Error initializing HuggingFace model: {e}")
            self.model = None
            self._dimension = 384  # Default dimension for MiniLM models
    
    def embed_query(self, text: str) -> List[float]:
        """
        Generate an embedding for a query.
        
        Args:
            text: Text to generate an embedding for
            
        Returns:
            Embedding vector as a list of floats
        """
        return self.embed_documents([text])[0]
    
    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """
        Generate embeddings for multiple texts.
        
        Args:
            texts: List of texts to generate embeddings for
            
        Returns:
            List of embedding vectors
        """
        if not self.model:
            logger.error("HuggingFace model not initialized")
            return [[] for _ in texts]
            
        try:
            # Generate embeddings
            embeddings = self.model.encode(texts)
            return embeddings.tolist()
        except Exception as e:
            logger.error(f"Error generating embedding with HuggingFace: {e}")
            return [[] for _ in texts]
    
    @property
    def dimension(self) -> int:
        """Get the embedding dimension."""
        return self._dimension
    
    @property
    def provider_name(self) -> str:
        """Get the provider name."""
        return "huggingface"
    
    @property
    def model_name(self) -> str:
        """Get the model name."""
        return self._model_name

class LocalEmbeddingProvider(EmbeddingProvider):
    """
    A simple local embedding provider that uses basic text processing to create embeddings.
    This is a fallback option when no other providers are available or working.
    The quality is much lower than ML-based embeddings but it's better than nothing.
    """
    
    def __init__(self, dimension: int = 1536):
        """
        Initialize the local embedding provider.
        
        Args:
            dimension: Dimension of the embedding vectors
        """
        self._dimension = dimension
        logger.info(f"Local embedding provider initialized (dimension: {dimension})")
    
    def embed_query(self, text: str) -> List[float]:
        """
        Generate an embedding for a query.
        
        Args:
            text: Text to generate an embedding for
            
        Returns:
            Embedding vector as a list of floats
        """
        return self._hash_based_embedding(text)
    
    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """
        Generate embeddings for multiple texts.
        
        Args:
            texts: List of texts to generate embeddings for
            
        Returns:
            List of embedding vectors
        """
        return [self._hash_based_embedding(text) for text in texts]
    
    def _hash_based_embedding(self, text: str) -> List[float]:
        """Generate embedding using a hash-based approach."""
        if not text:
            return [0.0] * self._dimension
            
        # Normalize and clean text
        text = text.lower().strip()
        
        # Initialize vector with zeros
        vector = [0.0] * self._dimension
        
        # Use word tokens and character 3-grams
        tokens = text.split()
        
        # Add all character trigrams
        for i in range(len(text) - 2):
            trigram = text[i:i+3]
            tokens.append(trigram)
        
        # Fill the vector based on token hashes
        for token in tokens:
            # Hash the token to get a position and value
            hash_val = int(hashlib.md5(token.encode()).hexdigest(), 16)
            pos = hash_val % self._dimension
            val = (hash_val % 10000) / 10000.0  # Convert to value between 0 and 1
            vector[pos] += val
        
        # Normalize the vector to unit length
        magnitude = sum(x**2 for x in vector) ** 0.5
        if magnitude > 0:
            vector = [x/magnitude for x in vector]
            
        return vector
    
    @property
    def dimension(self) -> int:
        """Get the embedding dimension."""
        return self._dimension
    
    @property
    def provider_name(self) -> str:
        """Get the provider name."""
        return "local"
    
    @property
    def model_name(self) -> str:
        """Get the model name."""
        return "hash-based"

class MultiProviderEmbeddings(LangChainEmbeddings):
    """
    Embedding service that supports multiple providers with fallback capabilities.
    Implements the same interface as LangChain's Embeddings.
    """
    
    def __init__(self, providers: List[EmbeddingProvider]):
        """
        Initialize the multi-provider embedding service.
        
        Args:
            providers: List of embedding providers, in order of preference
        """
        self.providers = providers
        self.current_provider_index = 0
        
        if not providers:
            logger.error("No embedding providers specified")
        else:
            logger.info(f"Initialized with {len(providers)} providers:")
            for i, provider in enumerate(providers):
                logger.info(f"  {i+1}. {provider.provider_name} - {provider.model_name} (dim: {provider.dimension})")
    
    def get_default_dimension(self) -> int:
        """Get the default dimension to use for embeddings."""
        if self.providers:
            return self.providers[0].dimension
        return 3072  # Default to a conservative dimension if no providers
    
    def normalize_embedding(self, embedding: List[float], target_dim: Optional[int] = None) -> List[float]:
        """
        Normalize an embedding to ensure it has the correct dimension.
        
        Args:
            embedding: The embedding vector to normalize
            target_dim: The target dimension (if None, uses the default dimension)
            
        Returns:
            Normalized embedding vector
        """
        if not embedding:
            dim = target_dim or self.get_default_dimension()
            return [0.0] * dim
            
        current_dim = len(embedding)
        if target_dim is None:
            return embedding
            
        # No normalization needed if dimensions match
        if current_dim == target_dim:
            return embedding
            
        # Truncate if too long
        if current_dim > target_dim:
            logger.warning(f"Truncating embedding from {current_dim} to {target_dim}")
            return embedding[:target_dim]
            
        # Pad with zeros if too short
        logger.warning(f"Padding embedding from {current_dim} to {target_dim}")
        return embedding + [0.0] * (target_dim - current_dim)
    
    def embed_query(self, text: str) -> List[float]:
        """
        Generate an embedding for a single query text.
        
        Args:
            text: Text to generate an embedding for
            
        Returns:
            Embedding vector as a list of floats
        """
        if not self.providers:
            logger.error("No embedding providers available")
            return [0.0] * self.get_default_dimension()
        
        # Try each provider in order
        for i in range(len(self.providers)):
            provider_index = (self.current_provider_index + i) % len(self.providers)
            provider = self.providers[provider_index]
            
            try:
                logger.info(f"Generating query embedding using {provider.provider_name} - {provider.model_name}")
                embedding = provider.embed_query(text)
                
                if embedding and len(embedding) > 0:
                    # Success - update current provider index for next request
                    self.current_provider_index = provider_index
                    logger.info(f"Successfully generated embedding with {provider.provider_name}")
                    return embedding
            except Exception as e:
                logger.warning(f"Provider {provider.provider_name} - {provider.model_name} failed: {e}")
            
            logger.warning(f"Provider {provider.provider_name} - {provider.model_name} failed, trying next")
        
        # All providers failed
        logger.error("All embedding providers failed")
        # Return zeros as a last resort
        return [0.0] * self.get_default_dimension()
    
    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """
        Generate embeddings for a list of documents.
        
        Args:
            texts: List of texts to generate embeddings for
            
        Returns:
            List of embedding vectors
        """
        if not texts:
            logger.warning("Empty texts list provided to embed_documents")
            return []
            
        if not self.providers:
            logger.error("No embedding providers available")
            dim = self.get_default_dimension()
            return [[0.0] * dim for _ in texts]
        
        # Try each provider in order
        for i in range(len(self.providers)):
            provider_index = (self.current_provider_index + i) % len(self.providers)
            provider = self.providers[provider_index]
            
            try:
                logger.info(f"Generating document embeddings using {provider.provider_name} - {provider.model_name}")
                embeddings = provider.embed_documents(texts)
                
                # Validate embeddings
                if not embeddings:
                    logger.warning(f"Provider {provider.provider_name} returned empty embeddings list")
                    continue
                    
                if len(embeddings) != len(texts):
                    logger.warning(f"Provider {provider.provider_name} returned incorrect number of embeddings: {len(embeddings)} vs {len(texts)} expected")
                    continue
                
                if all(len(emb) > 0 for emb in embeddings):
                    # Success - update current provider index for next request
                    self.current_provider_index = provider_index
                    logger.info(f"Successfully generated embeddings with {provider.provider_name}")
                    
                    # Ensure consistent dimensions
                    target_dim = provider.dimension
                    return [self.normalize_embedding(emb, target_dim) for emb in embeddings]
                else:
                    logger.warning(f"Provider {provider.provider_name} returned some empty embeddings")
            except Exception as e:
                logger.warning(f"Provider {provider.provider_name} - {provider.model_name} failed: {e}")
            
            logger.warning(f"Provider {provider.provider_name} - {provider.model_name} failed, trying next")
        
        # All providers failed
        logger.error("All embedding providers failed")
        # Return zeros as a last resort
        dim = self.get_default_dimension()
        return [[0.0] * dim for _ in texts]
        
    # Adding required method from LangChain Embeddings interface
    def embed_query_with_retrieval_metadata(self, text: str) -> Dict[str, Any]:
        """
        Generate an embedding with metadata for a single query text.
        
        Args:
            text: Text to generate an embedding for
            
        Returns:
            Dict with embedding and metadata
        """
        embedding = self.embed_query(text)
        
        # Get provider info safely
        provider_name = "none"
        model_name = "none"
        
        if self.providers and self.current_provider_index < len(self.providers):
            provider = self.providers[self.current_provider_index]
            provider_name = provider.provider_name
            model_name = provider.model_name
        
        return {
            "embedding": embedding,
            "metadata": {
                "provider": provider_name,
                "model": model_name,
                "dimension": len(embedding)
            }
        }

def create_embeddings_from_config() -> MultiProviderEmbeddings:
    """
    Create a multi-provider embedding service from environment variables.
    
    Returns:
        Configured MultiProviderEmbeddings
    """
    providers = []
    
    # Add OpenAI provider if configured
    openai_api_key = os.getenv('OPENAI_API_KEY')
    if openai_api_key and HAVE_OPENAI:
        # Primary model
        primary_model = os.getenv('OPENAI_EMBEDDING_MODEL', 'text-embedding-3-large')
        rate_limit_delay = float(os.getenv('RATE_LIMIT_DELAY', 1.0))
        max_retries = int(os.getenv('MAX_RETRIES', 3))
        
        try:
            providers.append(OpenAIEmbeddingProvider(
                api_key=openai_api_key,
                model=primary_model,
                rate_limit_delay=rate_limit_delay,
                max_retries=max_retries
            ))
            
            # Add fallback model if different from primary
            fallback_model = os.getenv('OPENAI_FALLBACK_EMBEDDING_MODEL')
            if fallback_model and fallback_model != primary_model:
                providers.append(OpenAIEmbeddingProvider(
                    api_key=openai_api_key,
                    model=fallback_model,
                    rate_limit_delay=rate_limit_delay,
                    max_retries=max_retries
                ))
        except Exception as e:
            logger.error(f"Failed to initialize OpenAI provider: {e}")
    
    # Add HuggingFace provider if enabled
    use_huggingface = os.getenv('USE_HUGGINGFACE_EMBEDDINGS', 'false').lower() == 'true'
    if use_huggingface and HAVE_SENTENCE_TRANSFORMERS:
        hf_model = os.getenv('HUGGINGFACE_MODEL', 'all-MiniLM-L6-v2')
        hf_cache_dir = os.getenv('HUGGINGFACE_CACHE_DIR')
        
        try:
            providers.append(HuggingFaceEmbeddingProvider(
                model=hf_model,
                cache_dir=hf_cache_dir
            ))
        except Exception as e:
            logger.error(f"Failed to initialize HuggingFace provider: {e}")
    
    # Always add local provider as last resort
    use_local = os.getenv('USE_LOCAL_EMBEDDINGS', 'true').lower() == 'true'
    if use_local:
        dimension = int(os.getenv('LOCAL_EMBEDDING_DIMENSION', '3072'))
        
        providers.append(LocalEmbeddingProvider(dimension=dimension))
    
    return MultiProviderEmbeddings(providers)

# Create a singleton instance - lazy loaded
_multi_provider_embeddings = None

def get_multi_provider_embeddings() -> MultiProviderEmbeddings:
    """Get the singleton instance of multi-provider embeddings, creating it if needed."""
    global _multi_provider_embeddings
    if _multi_provider_embeddings is None:
        _multi_provider_embeddings = create_embeddings_from_config()
    return _multi_provider_embeddings

# For backward compatibility
multi_provider_embeddings = get_multi_provider_embeddings() 