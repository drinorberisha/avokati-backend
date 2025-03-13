"""
Logging utility module.

This module provides consistent logging configuration across the application,
including formatters, handlers, and convenience functions for logging.
"""

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# Configure base logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)

# Create logger
logger = logging.getLogger("app")

def setup_file_logging(log_dir: str = "logs"):
    """
    Set up file logging in addition to console logging.
    
    Args:
        log_dir: Directory to store log files
    """
    # Create log directory if it doesn't exist
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    
    # Create file handler
    timestamp = datetime.now().strftime("%Y%m%d")
    file_handler = logging.FileHandler(
        log_path / f"app_{timestamp}.log",
        encoding="utf-8"
    )
    
    # Set formatter
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    file_handler.setFormatter(formatter)
    
    # Add handler to logger
    logger.addHandler(file_handler)

def log_error(error: Exception, context: Optional[str] = None):
    """
    Log an error with optional context.
    
    Args:
        error: Exception to log
        context: Optional context about where/why the error occurred
    """
    if context:
        logger.error(f"{context}: {str(error)}")
    else:
        logger.error(str(error))
    
    # Log full stack trace at debug level
    logger.debug("Stack trace:", exc_info=True)

def log_warning(message: str, context: Optional[str] = None):
    """
    Log a warning with optional context.
    
    Args:
        message: Warning message
        context: Optional context about the warning
    """
    if context:
        logger.warning(f"{context}: {message}")
    else:
        logger.warning(message)

def log_info(message: str, context: Optional[str] = None):
    """
    Log an info message with optional context.
    
    Args:
        message: Info message
        context: Optional context about the message
    """
    if context:
        logger.info(f"{context}: {message}")
    else:
        logger.info(message)

def log_debug(message: str, context: Optional[str] = None):
    """
    Log a debug message with optional context.
    
    Args:
        message: Debug message
        context: Optional context about the message
    """
    if context:
        logger.debug(f"{context}: {message}")
    else:
        logger.debug(message)

# Set up file logging by default
setup_file_logging() 