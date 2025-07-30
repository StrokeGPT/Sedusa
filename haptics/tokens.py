# haptics/tokens.py
import random
from typing import List, Dict
from haptics.motifs import MotifLibrary

class TokenCompiler:
    """
    Fetches patterns from a MotifLibrary and converts them into timed,
    overlapping haptic events for the StoryRunner.
    """
    def __init__(self, motif_library: MotifLibrary):
        self.motifs = motif_library

    def _dp_to_band(self, dp: int) -> str:
        """Maps a depth percentage (0-100) to a band ('A', 'B', 'C')."""
        if dp < 33: return 'A'
        if dp < 66: return 'B'
        return 'C'

    def compile_by_name(self, name: str, overlap: float = 0.3) -> List[Dict]:
        """
        Looks up a pattern by name and converts its 'combo' into a list of
        overlapping timed events. Publicly accessible.
        """
        motif = self.motifs.get_pattern(name)
        if not motif or 'pattern' not in motif:
            # Return a silent, 1-second pattern as a fallback.
            return [{'band': 'B', 'hz': 0, 'range_mm': 0, 'offset_s': 0, 'duration_s': 1}]

        p = motif['pattern']
        # Handle single-segment patterns directly
        if p.get('type') != 'combo' or not p.get('combo'):
            return [{
                'band': self._dp_to_band(p.get('dp', 50)),
                'hz': p.get('sp', 50) / 100 * 3.0, # Convert % to approx Hz
                'range_mm': p.get('rng', 20),
                'offset_s': 0,
                'duration_s': p.get('duration_ms', 5000) / 1000.0
            }]

        combo = p['combo']
        total_d = p['duration_ms'] / 1000.0
        band_count = len(combo)
        
        # Calculate duration and step for overlap
        band_dur = total_d / (band_count - overlap * (band_count - 1)) if band_count > 1 else total_d
        step = band_dur * (1 - overlap)
        
        events = []
        for idx, seg in enumerate(combo):
            offset = idx * step
            events.append({
                'band': self._dp_to_band(seg.get('dp', p.get('dp', 50))),
                'hz': seg.get('sp', p.get('sp', 50)) / 100 * 3.0,
                'range_mm': seg.get('rng', p.get('rng', 20)),
                'offset_s': offset,
                'duration_s': band_dur
            })
        return events

    # --- Shortcut methods for specific story beats ---
    def coil_invite(self, *args, **kwargs) -> List[Dict]:
        return self.compile_by_name("snake_coil")

    def braid3_block(self, *args, **kwargs) -> List[Dict]:
        return self.compile_by_name("snake_braid3")

    def braid_with_pass(self, *args, **kwargs) -> List[Dict]:
        return self.compile_by_name("snake_pass")

    def staggered_pairs(self, *args, **kwargs) -> List[Dict]:
        return self.compile_by_name("snake_staggered")

    def freeze_beat(self, *args, **kwargs) -> List[Dict]:
        return self.compile_by_name("snake_freeze")
    
    def release_phase(self, *args, **kwargs) -> List[Dict]:
        return self.compile_by_name("wave_train_progressive")