"""
Entry point for ingestion. Run from project root: python ingestion.py
Or: python -m scripts.ingestion
"""
import sys
from pathlib import Path

# Ensure project root on path
_root = Path(__file__).resolve().parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from scripts.ingestion import main

if __name__ == "__main__":
    main()
