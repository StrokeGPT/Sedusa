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
        self.length_min = length_min
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

        # Dynamically build the story timeline based on the session length
        act_defs = []
        if self.length_min >= 60:
            act_defs = [("Trap", 0.10), ("Revelation", 0.10), ("Test", 0.20), ("Interrogation", 0.15), ("Worship", 0.15), ("Gaze", 0.10), ("Claiming", 0.15)]
        elif self.length_min >= 30:
            act_defs = [("Trap", 0.15), ("Revelation", 0.15), ("Test", 0.25), ("Interrogation", 0.15), ("Worship", 0.15), ("Gaze", 0.10)]
        else: # Default for 10, 15, 20 min sessions
            act_defs = [("Trap", 0.15), ("Revelation", 0.20), ("Test", 0.40), ("Gaze", 0.20)]
        
        # Reserve ~10% of total time for the final release/outro phase
        total_story_s = (self.length_min * 60) * 0.9
        
        # Create the final timeline with absolute durations in seconds
        act_timeline = []
        total_pct = sum(pct for _, pct in act_defs)
        for name, pct in act_defs:
            duration = int((pct / total_pct) * total_story_s)
            # Ensure each act is at least a minute long if possible
            act_timeline.append((f"The {name}", max(60, duration)))

        all_pattern_names = list(self.compiler.motifs.motifs_by_name.keys())

        for act_name, act_duration in act_timeline:
            if self._should_stop(): break
            self.act = act_name
            
            # Announce act start. These are always narrative updates and reset the timer.
            key_name = act_name.replace("The ", "").upper()
            if key_name == "TRAP":
                 self._announce("INVITE") # The Trap still starts with an Invite
            else:
                self._announce(f"STORY_{key_name}")

            if self.act == "The Gaze":
                self._play_events(self.compiler.compile_by_name("snake_freeze"))
                if self._should_stop(): break
                time.sleep(rng.uniform(4.0, 6.0))
                events = self.compiler.compile_by_name("snake_pass")
                self._play_events(events, apply_jitter=False)
                continue

            act_playlist = []
            playlist_duration_s = 0.0
            last_pattern_info = {"name": None, "dp": None, "rng": None, "band": None}

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
                        current_band = self.compiler._dp_to_band(current_dp)

                        if last_pattern_info["band"] and current_band != last_pattern_info["band"]:
                            candidate_patterns.append(p_name_candidate)
                        elif last_pattern_info["dp"] is not None and abs(current_dp - last_pattern_info["dp"]) > 30:
                            candidate_patterns.append(p_name_candidate)
                        elif p_name_candidate != last_pattern_info["name"]:
                            candidate_patterns.append(p_name_candidate)
                    
                    if candidate_patterns:
                        pattern_name = rng.choice(candidate_patterns)
                    else:
                        pattern_name = rng.choice([p for p in eligible_patterns if p != last_pattern_info["name"]]) if len(eligible_patterns) > 1 else eligible_patterns[0]
                else:
                    pattern_name = rng.choice(eligible_patterns)

                if pattern_name is None:
                    time.sleep(self.TICK_S)
                    continue

                new_events = self.compiler.compile_by_name(pattern_name)
                if not new_events: continue

                motif = self.compiler.motifs.get_pattern(pattern_name)
                if not motif or 'pattern' not in motif:
                    last_pattern_info["dp"], last_pattern_info["rng"], last_pattern_info["band"] = 50, 20, 'B'
                else:
                    first_seg = new_events[0]
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
            
            if act_playlist and not self._should_stop():
                self._play_events(act_playlist)

        if self._should_stop(): return
        self.act = "The Release"
        self._announce("STORY_RELEASE")
        self._play_events(self.compiler.release_phase())
        time.sleep(10)
        self.device.stop_motion()
        self._announce("OUTRO")

    def _announce(self, key: str):
        lines = self.narrative_templates.get(key)
        if lines:
            full_line_with_meta = random.choice(lines)
            bracket_pos = full_line_with_meta.find('[')
            if bracket_pos != -1:
                self.last_line = full_line_with_meta[:bracket_pos].strip()
            else:
                self.last_line = full_line_with_meta
            self.last_narrative_line_time = time.time()
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
            if seg.get('type') in ['sine', 'triangle', 'hold'] and random.random() < 0.05:
                hz = 0
                
        scaled_hz = self._get_scaled_hz(hz)
        self.device.set_slide_window(*self._band_to_window(seg['band'], mm))
        self.device.set_speed_hz(scaled_hz)

    def _play_burst(self, seg: Dict[str, Any], elapsed_in_seg: float):
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
        jitter_ms_cycle = random.uniform(-self.BURST_PULSE_JITTER_MS, self.BURST_PULSE_JITTER_MS)
        cycle_dur = max(0.1, cycle_dur_base + (jitter_ms_cycle / 1000.0))
        phase = (elapsed_in_seg % cycle_dur) / cycle_dur
        sp1, sp2 = seg.get('sp', 40), seg.get('sp2', 80)
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
            
            now = time.time()
            if now - self.last_narrative_line_time > self.FORCED_NARRATIVE_INTERVAL_S:
                current_act_key = self.act.replace("The ", "").upper()
                if self.narrative_templates.get(f"STORY_{current_act_key}"):
                    self._announce(f"STORY_{current_act_key}")

            now_off = time.time() - start_t
            active = [e for e in events if e['offset_s'] <= now_off < e['offset_s'] + e['duration_s']]
            
            if active:
                seg = None
                dominant_band = next((s.get('dominant_band') for s in active if 'dominant_band' in s), None)
                
                if dominant_band:
                    dominant_segs = [s for s in active if s['band'] == dominant_band]
                    other_segs = [s for s in active if s['band'] != dominant_band]
                    if random.random() < 0.8 and dominant_segs:
                        seg = random.choice(dominant_segs)
                    elif other_segs:
                        seg = random.choice(other_segs)
                    else:
                        seg = random.choice(dominant_segs) # Fallback
                else:
                    seg = random.choice(active)

                pattern_type = seg.get('type', 'sine')
                elapsed_in_seg = now_off - seg['offset_s']
                if pattern_type == 'burst': self._play_burst(seg, elapsed_in_seg)
                elif pattern_type == 'pulse': self._play_pulse(seg, elapsed_in_seg)
                else: self._play_default(seg, apply_jitter)
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