# haptics/runner.py
import threading, time, random, json
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, asdict
from pathlib import Path
from haptics.tokens import TokenCompiler
from device.handy import HandyClient

@dataclass
class StoryState:
    state: str
    started_at: float
    ends_at: float
    t_remaining_ms: int
    last_line: str
    act: str
    seed: int

class StoryRunner(threading.Thread):
    TICK_S = 0.06
    JITTER_FACTOR = 0.20 # General jitter for speed and range
    PLAYLIST_OVERLAP = 0.4 # Overlap between patterns in playlist
    BURST_PULSE_JITTER_MS = 30 # Max +/- ms jitter for on/off times
    FORCED_NARRATIVE_INTERVAL_S = 18 # Time in seconds to force a new narrative line if none occurred

    def __init__(self, device: HandyClient, compiler: TokenCompiler,
                 narrative_templates: Dict, depth_min_mm: float, depth_max_mm: float,
                 speed_min_hz: float, speed_max_hz: float,
                 length_min: int, name: str = "", seed: Optional[int] = None):
        super().__init__(daemon=True)
        self.device = device
        self.compiler = compiler
        self.narrative_templates = narrative_templates
        self.depth_min = depth_min_mm
        self.depth_max = depth_max_mm
        self.speed_min = speed_min_hz
        self.speed_max = speed_max_hz
        self.length_min = 10
        self.name = name
        self.seed = seed if seed is not None else random.randint(1000, 999999)
        self._stop_event = threading.Event()
        self._pause = threading.Event()
        self.started_at = time.time()
        self.ends_at = self.started_at + self.length_min * 60
        self.last_line = ""
        self.last_narrative_line_time = 0.0 # Single cooldown for all narrative
        self.act = 'The Trap'

    def stop(self):
        self._stop_event.set()
        try:
            self.device.set_speed_hz(0)
            self.device.stop_motion()
        except Exception as e:
            print(f"[Runner] Error during stop: {e}")

    def pause(self):
        self._pause.set()
        self.device.stop_motion()

    def resume(self):
        self._pause.clear()

    def state_snapshot(self) -> Dict[str, Any]:
        now = time.time()
        rem = max(0, int((self.ends_at - now) * 1000))
        return asdict(StoryState(
            state=('paused' if self._pause.is_set() else ('running' if not self._stop_event.is_set() else 'stopped')),
            started_at=self.started_at, ends_at=self.ends_at,
            t_remaining_ms=rem, last_line=self.last_line,
            act=self.act, seed=self.seed
        ))

    def run(self):
        rng = random.Random(self.seed)
        act_timeline = [
            ("The Trap", 90), ("The Revelation", 120),
            ("The Test", 210), ("The Gaze", 120)
        ]
        
        # Get all available pattern names from the compiler's motif library
        all_pattern_names = list(self.compiler.motifs.motifs_by_name.keys())

        for act_name, act_duration in act_timeline:
            if self._should_stop(): break
            self.act = act_name
            
            # Announce act start. These are always narrative updates and reset the timer.
            if act_name == "The Trap":
                self._announce("INVITE")
            else:
                key_name = act_name.replace("The ", "").upper()
                self._announce(f"STORY_{key_name}")

            if self.act == "The Gaze":
                # Specific sequence for "The Gaze" act remains hardcoded
                self._play_events(self.compiler.compile_by_name("snake_freeze"))
                if self._should_stop(): break
                time.sleep(rng.uniform(4.0, 6.0))
                events = self.compiler.compile_by_name("snake_pass")
                self._play_events(events, apply_jitter=False)
                continue

            act_playlist = []
            playlist_duration_s = 0.0
            last_pattern_info = {"name": None, "dp": None, "rng": None, "band": None} # Added band

            while playlist_duration_s < act_duration and not self._should_stop():
                eligible_patterns = all_pattern_names
                
                pattern_name = None
                if last_pattern_info["name"]:
                    candidate_patterns = []
                    for p_name_candidate in eligible_patterns:
                        motif_candidate = self.compiler.motifs.get_pattern(p_name_candidate)
                        if not motif_candidate or 'pattern' not in motif_candidate: continue
                        
                        p_data_candidate = motif_candidate['pattern']
                        current_dp = p_data_candidate.get('dp', 50)
                        
                        # Get the band for the current candidate
                        current_band = self.compiler._dp_to_band(current_dp)

                        # Prioritize patterns that:
                        # 1. Are in a different band (to simulate "fighting" across depth)
                        # 2. Have a significant DP change
                        # 3. Are simply a different pattern if band/DP change isn't large enough
                        if last_pattern_info["band"] and current_band != last_pattern_info["band"]:
                            candidate_patterns.append(p_name_candidate)
                        elif last_pattern_info["dp"] is not None and abs(current_dp - last_pattern_info["dp"]) > 30:
                            candidate_patterns.append(p_name_candidate)
                        elif p_name_candidate != last_pattern_info["name"]:
                            candidate_patterns.append(p_name_candidate)
                    
                    if candidate_patterns:
                        pattern_name = rng.choice(candidate_patterns)
                    else: # Fallback if no contrasting pattern found, just pick a different one
                        pattern_name = rng.choice([p for p in eligible_patterns if p != last_pattern_info["name"]]) if len(eligible_patterns) > 1 else eligible_patterns[0]
                else: # First pattern in playlist
                    pattern_name = rng.choice(eligible_patterns)

                if pattern_name is None:
                    time.sleep(self.TICK_S)
                    continue

                new_events = self.compiler.compile_by_name(pattern_name)
                if not new_events: continue

                # Retrieve the motif for the *selected* pattern_name before accessing its properties
                motif = self.compiler.motifs.get_pattern(pattern_name)
                if not motif or 'pattern' not in motif:
                    # Default values if motif data is missing
                    last_pattern_info["dp"] = 50
                    last_pattern_info["rng"] = 20
                    last_pattern_info["band"] = 'B'
                else:
                    first_seg = new_events[0]
                    # Update last_pattern_info using data from the actual motif and its first segment
                    pattern_dp = first_seg.get('dp', motif['pattern'].get('dp', 50))
                    last_pattern_info["dp"] = pattern_dp
                    last_pattern_info["rng"] = first_seg.get('range_mm', motif['pattern'].get('rng', 20))
                    last_pattern_info["band"] = self.compiler._dp_to_band(pattern_dp)
                last_pattern_info["name"] = pattern_name

                pattern_duration = max(e.get('offset_s', 0) + e.get('duration_s', 0) for e in new_events) if new_events else 0
                if pattern_duration <= 0.1: continue

                for event in new_events:
                    evt_copy = event.copy()
                    evt_copy['offset_s'] += playlist_duration_s
                    act_playlist.append(evt_copy)
                playlist_duration_s += pattern_duration * (1.0 - self.PLAYLIST_OVERLAP)
            
            # The general narrative update check will now happen inside _play_events
            if act_playlist and not self._should_stop():
                self._play_events(act_playlist)

        if self._should_stop(): return
        self.act = "The Release"
        self._announce("STORY_RELEASE")
        self._play_events(self.compiler.release_phase())
        time.sleep(10)
        self.device.stop_motion()
        self._announce("OUTRO")

    def _announce(self, key: str): # Simplified _announce
        lines = self.narrative_templates.get(key)
        if lines:
            self.last_line = random.choice(lines)
            self.last_narrative_line_time = time.time() # Always update for any announcement
        else:
            self.last_line = f"Narrative key not found: {key}"
            
    def _get_scaled_hz(self, base_hz):
        normalized_speed = min(1.0, max(0.0, base_hz / 3.0))
        return self.speed_min + (self.speed_max - self.speed_min) * normalized_speed

    def _play_default(self, seg: Dict[str, Any], apply_jitter: bool):
        hz = seg.get('hz', seg.get('sp', 50) / 100 * 3.0)
        mm = seg.get('range_mm', seg.get('rng', 20))
        
        if apply_jitter:
            hz *= random.uniform(1.0 - self.JITTER_FACTOR, 1.0 + self.JITTER_FACTOR)
            mm *= random.uniform(1.0 - self.JITTER_FACTOR, 1.0 + self.JITTER_FACTOR)
            
            # Add micro-hesitations for sine/triangle/hold types to simulate "slips"
            if seg.get('type') in ['sine', 'triangle', 'hold'] and random.random() < 0.05: # 5% chance
                hz = 0 # Brief stop
                
        scaled_hz = self._get_scaled_hz(hz)
        self.device.set_slide_window(*self._band_to_window(seg['band'], mm))
        self.device.set_speed_hz(scaled_hz)

    def _play_burst(self, seg: Dict[str, Any], elapsed_in_seg: float):
        # Apply jitter to on/off times for more erratic bursts
        jitter_ms_on = random.uniform(-self.BURST_PULSE_JITTER_MS, self.BURST_PULSE_JITTER_MS)
        jitter_ms_off = random.uniform(-self.BURST_PULSE_JITTER_MS, self.BURST_PULSE_JITTER_MS)

        on_ms = max(50, (seg.get('burst_on_ms', 200) + jitter_ms_on)) / 1000.0
        off_ms = max(50, (seg.get('burst_off_ms', 200) + jitter_ms_off)) / 1000.0

        cycle_dur = on_ms + off_ms
        if cycle_dur == 0: return self._play_default(seg, True)
        phase = elapsed_in_seg % cycle_dur
        is_on = phase < on_ms
        hz = seg.get('hz', seg.get('sp', 70) / 100 * 3.0) if is_on else 0
        scaled_hz = self._get_scaled_hz(hz)
        self.device.set_slide_window(*self._band_to_window(seg['band'], seg.get('range_mm', seg.get('rng', 30))))
        self.device.set_speed_hz(scaled_hz)

    def _play_pulse(self, seg: Dict[str, Any], elapsed_in_seg: float):
        cycles = seg.get('cycles', 4)
        cycle_dur_base = seg['duration_s'] / cycles if cycles > 0 else seg['duration_s']

        # Apply jitter to cycle duration for more erratic pulses
        jitter_ms_cycle = random.uniform(-self.BURST_PULSE_JITTER_MS, self.BURST_PULSE_JITTER_MS)
        cycle_dur = max(0.1, cycle_dur_base + (jitter_ms_cycle / 1000.0))

        phase = (elapsed_in_seg % cycle_dur) / cycle_dur
        sp1 = seg.get('sp', 40)
        sp2 = seg.get('sp2', 80)
        hz = (seg.get('hz', (sp1 if phase < 0.5 else sp2) / 100 * 3.0))
        scaled_hz = self._get_scaled_hz(hz)
        self.device.set_slide_window(*self._band_to_window(seg['band'], seg.get('range_mm', seg.get('rng', 25))))
        self.device.set_speed_hz(scaled_hz)

    def _play_events(self, events: List[Dict], apply_jitter: bool = True):
        if not events or self._should_stop(): return
        self.device.start_motion()
        start_t = time.time()
        total_duration = max(e.get('offset_s', 0) + e.get('duration_s', 0) for e in events) if events else 0
        end_t = start_t + total_duration
        
        while time.time() < end_t:
            if self._should_stop(): break
            if self._pause.is_set():
                time.sleep(self.TICK_S); continue
            
            # Check for forced narrative update within the active playback loop
            now = time.time()
            if now - self.last_narrative_line_time > self.FORCED_NARRATIVE_INTERVAL_S:
                current_act_key = self.act.replace("The ", "").upper()
                narrative_pool = self.narrative_templates.get(f"STORY_{current_act_key}")
                # Ensure there are lines for the current act, and not just INVITE/OUTRO/RELEASE
                if narrative_pool and current_act_key not in ["INVITE", "OUTRO", "RELEASE"]:
                    # Select a general story line for the current act
                    self._announce(f"STORY_{current_act_key}")
                elif current_act_key == "GAZE": # Specific case for Gaze act
                     self._announce("STORY_GAZE")


            now_off = time.time() - start_t
            active = [e for e in events if e['offset_s'] <= now_off < e['offset_s'] + e['duration_s']]
            
            if active:
                seg = random.choice(active)
                
                pattern_type = seg.get('type', 'sine')
                elapsed_in_seg = now_off - seg['offset_s']
                
                if pattern_type == 'burst':
                    self._play_burst(seg, elapsed_in_seg)
                elif pattern_type == 'pulse':
                    self._play_pulse(seg, elapsed_in_seg)
                else:
                    self._play_default(seg, apply_jitter)
            else:
                 self.device.set_speed_hz(0)

            time.sleep(self.TICK_S)

    def _band_to_window(self, band: str, rng_mm: float) -> tuple[float, float]:
        lo, hi = self.depth_min, self.depth_max
        span = max(8.0, hi - lo)
        third = span / 3.0
        if band == 'A': center = lo + third * 0.5
        elif band == 'B': center = lo + third * 1.5
        else: center = lo + third * 2.5
        half = max(4.0, min(rng_mm, third * 0.9)) / 2.0
        return (max(lo, center - half), min(hi, center + half))

    def _should_stop(self) -> bool:
        return self._stop_event.is_set() or time.time() >= self.ends_at