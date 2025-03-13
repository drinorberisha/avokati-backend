"""
Text extraction module for document processing pipeline.

This module handles text extraction from various file formats:
- PDF files using PyPDF2
- DOCX files using python-docx
- Text files with encoding detection
"""
import io
from typing import BinaryIO, Dict
import PyPDF2
from docx import Document
import chardet
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Supported MIME types and their handlers
SUPPORTED_MIME_TYPES: Dict[str, str] = {
    'application/pdf': 'pdf',
    'application/msword': 'docx',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'docx',
    'text/plain': 'txt'
}

def extract_text_from_file(file: BinaryIO, mime_type: str) -> str:
    """
    Extract text from a file based on its mime type.
    
    Args:
        file: File-like object
        mime_type: MIME type of the file
        
    Returns:
        Extracted text as string
        
    Raises:
        ValueError: If file type is not supported
    """
    try:
        if mime_type not in SUPPORTED_MIME_TYPES:
            raise ValueError(f"Unsupported file type: {mime_type}")
            
        handler_type = SUPPORTED_MIME_TYPES[mime_type]
        
        if handler_type == 'pdf':
            return extract_from_pdf(file)
        elif handler_type == 'docx':
            return extract_from_docx(file)
        elif handler_type == 'txt':
            return extract_from_txt(file)
            
    except Exception as e:
        logger.error(f"Error extracting text: {str(e)}")
        raise Exception(f"Error extracting text: {str(e)}")

def extract_from_pdf(file: BinaryIO) -> str:
    """
    Extract text from PDF file.
    
    Args:
        file: PDF file object
        
    Returns:
        Extracted text as string
        
    Raises:
        Exception: If text extraction fails
    """
    try:
        pdf_reader = PyPDF2.PdfReader(file)
        text = []
        
        for page in pdf_reader.pages:
            page_text = page.extract_text()
            if page_text:  # Only add non-empty pages
                text.append(page_text)
                
        return "\n\n".join(text).strip()
        
    except Exception as e:
        logger.error(f"Error extracting text from PDF: {str(e)}")
        raise Exception(f"Error extracting text from PDF: {str(e)}")

def extract_from_docx(file: BinaryIO) -> str:
    """
    Extract text from DOCX file.
    
    Args:
        file: DOCX file object
        
    Returns:
        Extracted text as string
        
    Raises:
        Exception: If text extraction fails
    """
    try:
        doc = Document(file)
        paragraphs = []
        
        for paragraph in doc.paragraphs:
            text = paragraph.text.strip()
            if text:  # Only add non-empty paragraphs
                paragraphs.append(text)
                
        return "\n\n".join(paragraphs)
        
    except Exception as e:
        logger.error(f"Error extracting text from DOCX: {str(e)}")
        raise Exception(f"Error extracting text from DOCX: {str(e)}")

def extract_from_txt(file: BinaryIO) -> str:
    """
    Extract text from TXT file with encoding detection.
    
    Args:
        file: Text file object
        
    Returns:
        Extracted text as string
        
    Raises:
        Exception: If text extraction or encoding detection fails
    """
    try:
        # Read the content
        content = file.read()
        
        # Handle bytes content
        if isinstance(content, bytes):
            # Detect encoding
            detection = chardet.detect(content)
            encoding = detection['encoding'] if detection and detection['encoding'] else 'utf-8'
            
            try:
                return content.decode(encoding).strip()
            except UnicodeDecodeError:
                # Fallback to utf-8 if detected encoding fails
                return content.decode('utf-8', errors='replace').strip()
        
        # Handle string content
        return content.strip() if isinstance(content, str) else str(content).strip()
        
    except Exception as e:
        logger.error(f"Error extracting text from TXT: {str(e)}")
        raise Exception(f"Error extracting text from TXT: {str(e)}") 