# haptics/motifs.py
from __future__ import annotations
import json
from pathlib import Path
from typing import List, Dict, Any

class MotifLibrary:
    """
    Loads and manages haptic patterns from one or more JSON bank files.
    Provides a simple lookup by pattern name.
    """
    def __init__(self, bank_paths: List[Path]):
        self.motifs_by_name: Dict[str, Dict[str, Any]] = {}
        self._load(bank_paths)

    def _load(self, bank_paths: List[Path]):
        """Loads all patterns from the given list of file paths."""
        all_motifs = []
        for path in bank_paths:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        all_motifs.extend(data)
            except FileNotFoundError:
                print(f"[MotifLibrary] Warning: Bank file not found at {path}")
            except json.JSONDecodeError:
                print(f"[MotifLibrary] Warning: Could not decode JSON from {path}")

        for motif in all_motifs:
            if 'name' in motif:
                self.motifs_by_name[motif['name']] = motif
        
        print(f"[MotifLibrary] Loaded {len(self.motifs_by_name)} total motifs.")

    def get_pattern(self, name: str) -> Dict[str, Any] | None:
        """Retrieves a pattern by its unique name."""
        return self.motifs_by_name.get(name)