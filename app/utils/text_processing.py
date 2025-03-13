"""
Text processing module for cleaning and preprocessing document text.

This module provides functions for:
- Removing excessive whitespace
- Normalizing line breaks
- Removing control characters
- Basic text cleaning and normalization
"""

import re
from typing import Optional
import unicodedata

def preprocess_text(text: str, remove_urls: bool = True, normalize_unicode: bool = True) -> str:
    """
    Preprocess and clean text.
    
    Args:
        text: Raw text to process
        remove_urls: Whether to remove URLs from text
        normalize_unicode: Whether to normalize Unicode characters
        
    Returns:
        Preprocessed text
    """
    if not text:
        return ""
        
    # Normalize Unicode if requested
    if normalize_unicode:
        text = unicodedata.normalize('NFKC', text)
    
    # Remove control characters
    text = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', text)
    
    # Remove URLs if requested
    if remove_urls:
        text = re.sub(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', '', text)
    
    # Normalize whitespace
    text = re.sub(r'\s+', ' ', text)
    
    # Normalize line breaks
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    
    # Remove lines that are just whitespace or punctuation
    lines = [line.strip() for line in text.split('\n')]
    lines = [line for line in lines if line and not re.match(r'^[\s\.,;:!?]*$', line)]
    
    return '\n'.join(lines).strip()

def clean_text(text: str) -> str:
    """
    Basic text cleaning without extensive preprocessing.
    
    Args:
        text: Raw text to clean
        
    Returns:
        Cleaned text
    """
    if not text:
        return ""
        
    # Remove control characters
    text = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', text)
    
    # Normalize basic whitespace
    text = re.sub(r'\s+', ' ', text)
    
    return text.strip()

def normalize_text(text: str, lowercase: bool = False) -> str:
    """
    Normalize text for consistency.
    
    Args:
        text: Text to normalize
        lowercase: Whether to convert to lowercase
        
    Returns:
        Normalized text
    """
    if not text:
        return ""
        
    # Normalize Unicode
    text = unicodedata.normalize('NFKC', text)
    
    # Convert to lowercase if requested
    if lowercase:
        text = text.lower()
    
    return text.strip()

def extract_paragraphs(text: str, min_length: Optional[int] = None) -> list[str]:
    """
    Extract paragraphs from text.
    
    Args:
        text: Text to extract paragraphs from
        min_length: Minimum length for a paragraph to be included
        
    Returns:
        List of paragraphs
    """
    if not text:
        return []
        
    # Split by double newlines
    paragraphs = re.split(r'\n\s*\n', text)
    
    # Clean each paragraph
    paragraphs = [clean_text(p) for p in paragraphs]
    
    # Filter out empty paragraphs and those below minimum length
    paragraphs = [p for p in paragraphs if p]
    if min_length:
        paragraphs = [p for p in paragraphs if len(p) >= min_length]
    
    return paragraphs 