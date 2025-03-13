"""
Language detection utility module.

This module provides functions for detecting the language of text content
using the langdetect library with error handling and caching.
"""

import logging
from typing import Optional
from functools import lru_cache
from langdetect import detect, detect_langs, DetectorFactory
from langdetect.lang_detect_exception import LangDetectException

# Set seed for consistent results
DetectorFactory.seed = 0

logger = logging.getLogger(__name__)

@lru_cache(maxsize=1000)
def detect_language(text: str, return_confidence: bool = False) -> Optional[str | tuple[str, float]]:
    """
    Detect the language of a text string.
    
    Args:
        text: Text to analyze
        return_confidence: Whether to return confidence score
        
    Returns:
        Language code (ISO 639-1) or tuple of (language code, confidence)
        Returns None if detection fails
    """
    if not text or len(text.strip()) < 20:
        logger.warning("Text too short for reliable language detection")
        return None
        
    try:
        if return_confidence:
            # Get language with confidence score
            langs = detect_langs(text)
            if langs:
                return langs[0].lang, langs[0].prob
            return None
        else:
            # Just get language
            return detect(text)
            
    except LangDetectException as e:
        logger.error(f"Language detection failed: {str(e)}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error in language detection: {str(e)}")
        return None

def is_supported_language(text: str, supported_languages: list[str]) -> bool:
    """
    Check if text is in one of the supported languages.
    
    Args:
        text: Text to check
        supported_languages: List of supported language codes
        
    Returns:
        True if language is supported, False otherwise
    """
    lang = detect_language(text)
    return lang in supported_languages if lang else False

def get_language_confidence(text: str) -> Optional[float]:
    """
    Get confidence score for language detection.
    
    Args:
        text: Text to analyze
        
    Returns:
        Confidence score between 0 and 1, or None if detection fails
    """
    result = detect_language(text, return_confidence=True)
    return result[1] if result else None 