"""
Document parsing module for legal documents.

This module handles parsing of different types of legal documents:
- Laws and regulations
- Contracts and agreements
- Court decisions
- Legal articles and publications

It extracts structured information including:
- Document sections and articles
- Metadata (dates, references, titles)
- Document hierarchy and structure
"""

import re
from typing import Dict, List, Optional, Any
from datetime import datetime
import logging
from enum import Enum

logger = logging.getLogger(__name__)

class DocumentType(str, Enum):
    LAW = "law"
    REGULATION = "regulation"
    CASE_LAW = "case_law"
    CONTRACT = "contract"
    ARTICLE = "article"
    OTHER = "other"

class DocumentSection:
    def __init__(self, title: str, content: str, section_type: str, number: Optional[str] = None):
        self.title = title
        self.content = content
        self.section_type = section_type
        self.number = number
        self.subsections: List[DocumentSection] = []

def parse_document(content: str, document_type: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Parse a document based on its type and extract structured information.
    
    Args:
        content: Document content
        document_type: Type of document (law, regulation, case_law, etc.)
        metadata: Optional metadata about the document
        
    Returns:
        Dictionary containing parsed document information
    """
    try:
        doc_type = DocumentType(document_type.lower())
        
        # Initialize result dictionary
        result = {
            "content": content,
            "document_type": doc_type,
            "metadata": metadata or {},
            "parsed_at": datetime.now().isoformat(),
            "sections": [],
            "structure": {}
        }
        
        # Extract title and basic metadata
        title, remaining_content = extract_title(content)
        result["title"] = title
        
        # Parse based on document type
        if doc_type == DocumentType.LAW or doc_type == DocumentType.REGULATION:
            result.update(parse_law_or_regulation(remaining_content))
        elif doc_type == DocumentType.CASE_LAW:
            result.update(parse_case_law(remaining_content))
        elif doc_type == DocumentType.CONTRACT:
            result.update(parse_contract(remaining_content))
        elif doc_type == DocumentType.ARTICLE:
            result.update(parse_article(remaining_content))
        else:
            result.update(parse_generic_document(remaining_content))
            
        return result
        
    except Exception as e:
        logger.error(f"Error parsing document: {str(e)}")
        # Return basic structure if parsing fails
        return {
            "content": content,
            "document_type": document_type,
            "metadata": metadata or {},
            "parsed_at": datetime.now().isoformat(),
            "error": str(e)
        }

def extract_title(content: str) -> tuple[str, str]:
    """Extract title from document content."""
    lines = content.split('\n')
    title_lines = []
    content_start = 0
    
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            if title_lines:  # We've found the end of the title
                break
            continue
        if not title_lines or len(line) < 200:  # Assume titles aren't very long
            title_lines.append(line)
            content_start = i + 1
        else:
            break
    
    title = ' '.join(title_lines).strip()
    remaining_content = '\n'.join(lines[content_start:]).strip()
    
    return title, remaining_content

def parse_law_or_regulation(content: str) -> Dict[str, Any]:
    """Parse laws and regulations."""
    result = {
        "sections": [],
        "articles": [],
        "structure": {"type": "law_or_regulation"}
    }
    
    # Find chapters/sections
    chapter_pattern = r"(?:CHAPTER|Chapter|SECTION|Section)\s+([IVXLCDM0-9]+)[.\s\n]+([^\n]+)"
    chapters = re.split(chapter_pattern, content)
    
    # Find articles
    article_pattern = r"Article\s+(\d+)[.\s\n]+([^\n]+)"
    articles = re.findall(article_pattern, content)
    
    result["sections"] = parse_sections(content)
    result["articles"] = [{"number": num, "title": title.strip()} for num, title in articles]
    
    return result

def parse_case_law(content: str) -> Dict[str, Any]:
    """Parse court decisions and case law."""
    result = {
        "sections": [],
        "structure": {"type": "case_law"}
    }
    
    # Common sections in case law
    sections = [
        "FACTS",
        "BACKGROUND",
        "PROCEDURAL HISTORY",
        "ISSUES",
        "ANALYSIS",
        "DISCUSSION",
        "CONCLUSION",
        "ORDER",
        "JUDGMENT"
    ]
    
    current_section = None
    section_content = []
    parsed_sections = []
    
    for line in content.split('\n'):
        line = line.strip()
        if not line:
            continue
            
        # Check if line is a section header
        is_section = False
        for section in sections:
            if line.upper().startswith(section):
                if current_section:
                    parsed_sections.append({
                        "title": current_section,
                        "content": '\n'.join(section_content).strip()
                    })
                current_section = line
                section_content = []
                is_section = True
                break
                
        if not is_section and current_section:
            section_content.append(line)
            
    # Add last section
    if current_section and section_content:
        parsed_sections.append({
            "title": current_section,
            "content": '\n'.join(section_content).strip()
        })
        
    result["sections"] = parsed_sections
    return result

def parse_contract(content: str) -> Dict[str, Any]:
    """Parse legal contracts and agreements."""
    result = {
        "sections": [],
        "clauses": [],
        "structure": {"type": "contract"}
    }
    
    # Find sections/articles
    section_pattern = r"(?:\d+\.|\([a-z]\)|\([0-9]\))\s+([^\n]+)"
    sections = re.finditer(section_pattern, content)
    
    parsed_sections = []
    for section in sections:
        parsed_sections.append({
            "number": section.group(0).strip(),
            "content": section.group(1).strip()
        })
    
    result["sections"] = parsed_sections
    
    # Extract key clauses (dates, parties, terms)
    date_pattern = r"(?:dated|effective|as of).*?(\d{1,2}(?:st|nd|rd|th)?\s+(?:January|February|March|April|May|June|July|August|September|October|November|December),?\s+\d{4})"
    dates = re.findall(date_pattern, content, re.IGNORECASE)
    
    party_pattern = r"(?:between|party of the first part|party of the second part)\s+([^,\n]+)"
    parties = re.findall(party_pattern, content, re.IGNORECASE)
    
    result["metadata"] = {
        "dates": dates,
        "parties": parties
    }
    
    return result

def parse_article(content: str) -> Dict[str, Any]:
    """Parse legal articles and publications."""
    result = {
        "sections": [],
        "structure": {"type": "article"}
    }
    
    # Common sections in legal articles
    sections = [
        "ABSTRACT",
        "INTRODUCTION",
        "BACKGROUND",
        "METHODOLOGY",
        "ANALYSIS",
        "DISCUSSION",
        "CONCLUSION",
        "REFERENCES"
    ]
    
    current_section = None
    section_content = []
    parsed_sections = []
    
    for line in content.split('\n'):
        line = line.strip()
        if not line:
            continue
            
        # Check if line is a section header
        is_section = False
        for section in sections:
            if line.upper().startswith(section):
                if current_section:
                    parsed_sections.append({
                        "title": current_section,
                        "content": '\n'.join(section_content).strip()
                    })
                current_section = line
                section_content = []
                is_section = True
                break
                
        if not is_section and current_section:
            section_content.append(line)
            
    # Add last section
    if current_section and section_content:
        parsed_sections.append({
            "title": current_section,
            "content": '\n'.join(section_content).strip()
        })
        
    result["sections"] = parsed_sections
    
    # Extract citations and references
    citation_pattern = r"\(\d{4}\)"  # Basic citation pattern
    citations = re.findall(citation_pattern, content)
    result["citations"] = citations
    
    return result

def parse_generic_document(content: str) -> Dict[str, Any]:
    """Parse any document without specific structure."""
    result = {
        "sections": [],
        "structure": {"type": "generic"}
    }
    
    # Split into sections based on line breaks and headers
    sections = []
    current_section = None
    section_content = []
    
    for line in content.split('\n'):
        line = line.strip()
        if not line:
            continue
            
        # Heuristic: lines in all caps that aren't too long might be headers
        if line.isupper() and len(line) < 100:
            if current_section:
                sections.append({
                    "title": current_section,
                    "content": '\n'.join(section_content).strip()
                })
            current_section = line
            section_content = []
        elif current_section:
            section_content.append(line)
        else:
            # If no section has been started, create "MAIN" section
            current_section = "MAIN"
            section_content.append(line)
            
    # Add last section
    if current_section and section_content:
        sections.append({
            "title": current_section,
            "content": '\n'.join(section_content).strip()
        })
        
    result["sections"] = sections
    return result

def parse_sections(content: str) -> List[Dict[str, Any]]:
    """Parse document into hierarchical sections."""
    sections = []
    current_section = None
    section_content = []
    
    # Common section indicators
    section_patterns = [
        r"^(?:CHAPTER|Chapter)\s+([IVXLCDM0-9]+)",
        r"^(?:SECTION|Section)\s+(\d+)",
        r"^(?:ARTICLE|Article)\s+(\d+)",
        r"^\d+\.\s+",
        r"^[A-Z][A-Za-z\s]+:$"
    ]
    
    for line in content.split('\n'):
        line = line.strip()
        if not line:
            continue
            
        # Check if line is a section header
        is_section = False
        for pattern in section_patterns:
            if re.match(pattern, line):
                if current_section:
                    sections.append({
                        "title": current_section,
                        "content": '\n'.join(section_content).strip()
                    })
                current_section = line
                section_content = []
                is_section = True
                break
                
        if not is_section and current_section:
            section_content.append(line)
            
    # Add last section
    if current_section and section_content:
        sections.append({
            "title": current_section,
            "content": '\n'.join(section_content).strip()
        })
        
    return sections 