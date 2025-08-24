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
        Looks up a pattern by name and converts it into a list of
        timed events. It now also injects special tags like 'dominant_band'.
        """
        motif = self.motifs.get_pattern(name)
        if not motif or 'pattern' not in motif:
            return [{'band': 'B', 'hz': 0, 'range_mm': 0, 'offset_s': 0, 'duration_s': 1}]

        p = motif['pattern']
        tags = motif.get("tags", {})
        dominant_band = tags.get("dominant_band")

        # Base properties for all segments from this pattern
        base_props = {}
        if dominant_band:
            base_props['dominant_band'] = dominant_band

        # Handle single-segment patterns
        if p.get('type') != 'combo' or not p.get('combo'):
            event = {
                'band': self._dp_to_band(p.get('dp', 50)),
                'hz': p.get('sp', 50) / 100 * 3.0,
                'range_mm': p.get('rng', 20),
                'offset_s': 0,
                'duration_s': p.get('duration_ms', 5000) / 1000.0,
                **p,
                **base_props
            }
            return [event]

        combo = p['combo']
        total_d = p.get('duration_ms', 5000) / 1000.0
        band_count = len(combo)
        
        band_dur = total_d / (band_count - overlap * (band_count - 1)) if band_count > 1 else total_d
        step = band_dur * (1 - overlap)
        
        events = []
        for idx, seg in enumerate(combo):
            offset = idx * step
            event = {
                'band': self._dp_to_band(seg.get('dp', p.get('dp', 50))),
                'hz': seg.get('sp', p.get('sp', 50)) / 100 * 3.0,
                'range_mm': seg.get('rng', p.get('rng', 20)),
                'offset_s': offset,
                'duration_s': seg.get('duration_ms', band_dur * 1000) / 1000.0,
                **seg,
                **base_props
            }
            events.append(event)
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