"""Embedding module for vector operations."""

from .providers import get_multi_provider_embeddings

# Export the main embedding interface - lazy loaded
multi_provider_embeddings = get_multi_provider_embeddings()

# Export the main embedding interface
__all__ = ["multi_provider_embeddings", "get_multi_provider_embeddings"] 