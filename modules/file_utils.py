"""
Utilities for robust file handling with multiple encodings
"""
import chardet
from pathlib import Path


def _default_encodings():
    return [
        'utf-8',
        'utf-8-sig',
        'cp1252',
        'iso-8859-1',
        'shift-jis',
        'gbk',
        'big5',
        'euc-kr',
        'cp932',
        'cp949',
    ]


def _detect_encoding(file_path: Path):
    try:
        with open(file_path, 'rb') as f:
            raw_data = f.read()
        if not raw_data:
            return None
        detected = chardet.detect(raw_data)
        if detected and detected.get('confidence', 0) > 0.7:
            return detected.get('encoding')
    except Exception:
        return None
    return None

def safe_open_subtitle(file_path, mode='r'):
    """
    Safely open subtitle files with automatic encoding detection
    Tries common subtitle encodings in order of likelihood
    """
    file_path = Path(file_path)
    
    # Common subtitle encodings in order of preference
    encodings = _default_encodings()

    detected_encoding = _detect_encoding(file_path)
    if detected_encoding and detected_encoding not in encodings:
        encodings.insert(0, detected_encoding)
    
    # Try each encoding and return the working one
    for encoding in encodings:
        try:
            # Test if encoding works by reading a small portion
            with open(file_path, 'r', encoding=encoding) as test_f:
                test_f.read(100)  # Test read
            
            # If test passed, return a new file handle
            return open(file_path, mode, encoding=encoding), encoding
        except (UnicodeDecodeError, UnicodeError, LookupError):
            continue
    
    # Last resort: open with errors='replace'
    return open(file_path, mode, encoding='utf-8', errors='replace'), 'utf-8'

def safe_read_subtitle(file_path):
    """Read subtitle file content with automatic encoding detection"""
    file_path = Path(file_path)
    
    # Common subtitle encodings in order of preference
    encodings = _default_encodings()

    detected_encoding = _detect_encoding(file_path)
    
    # Add detected encoding to front of list
    if detected_encoding and detected_encoding not in encodings:
        encodings.insert(0, detected_encoding)
    
    # Try each encoding
    for encoding in encodings:
        try:
            with open(file_path, 'r', encoding=encoding) as f:
                content = f.read()
                return content, encoding
        except (UnicodeDecodeError, UnicodeError, LookupError):
            continue
    
    # Last resort with error replacement
    try:
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
            return content, 'utf-8'
    except Exception as e:
        raise IOError(f"Cannot read file {file_path}: {e}")

def safe_write_subtitle(file_path, content, encoding='utf-8'):
    """Write subtitle file with specified encoding"""
    with open(file_path, 'w', encoding=encoding, newline='') as f:
        f.write(content)