# device/handy.py
from __future__ import annotations
import threading, requests, time, sys
from typing import Optional, Tuple

class HandyAPIError(RuntimeError):
    pass

class HandyClient:
    """
    HandyClient for Medusa Mode.
    Communicates with the Handy v2 API.
    """
    FULL_TRAVEL_MM = 110.0 # Device's maximum physical travel distance in mm.

    def __init__(
        self,
        mode: str = "simulate",
        base_url: str = "", # This is now ignored, but kept for compatibility.
        api_key: str = "",
        log_device: bool = True,
        timeout_s: float = 5.0,
    ) -> None:
        self.mode = mode
        # The base URL is now hardcoded to the correct v2 endpoint.
        self.base_url = "https://www.handyfeeling.com/api/handy/v2/"
        self.api_key = api_key
        self.log = log_device
        self.timeout = timeout_s

        self._slide_window: Optional[Tuple[float, float]] = None
        self._speed_hz: Optional[float] = None
        self._lock = threading.Lock()

        if self.mode == "handy" and not self.api_key:
            raise ValueError("In mode='handy', api_key must be provided.")

    def _put(self, path: str, body: dict | None = None) -> None:
        """Sends a PUT request to the Handy API."""
        if self.mode == "simulate":
            if self.log:
                t = time.strftime("%H:%M:%S")
                print(f"[{t}] [HANDY SIM] PUT /{path} {body or {}}")
            return

        if not self.api_key:
            print("[HANDY WARNING] No key set, command ignored.", file=sys.stderr)
            return

        url = self.base_url.rstrip("/") + "/" + path
        headers = {
            "Content-Type": "application/json",
            "X-Connection-Key": self.api_key,
        }
        try:
            if self.log:
                t = time.strftime("%H:%M:%S")
                print(f"[{t}] PUT {url} {body or {}}")
            r = requests.put(url, headers=headers, json=body or {}, timeout=self.timeout)
            r.raise_for_status()
        except requests.RequestException as e:
            raise HandyAPIError(f"Handy API error on PUT /{path}: {e}") from e

    # ---------- public interface runner.py calls ----------
    def set_slide_window(self, min_mm: float, max_mm: float) -> None:
        with self._lock:
            win = (round(min_mm, 1), round(max_mm, 1))
            if win == self._slide_window:
                return
            self._slide_window = win
        
        # Convert mm values to percentages.
        min_pct = (min_mm / self.FULL_TRAVEL_MM) * 100
        max_pct = (max_mm / self.FULL_TRAVEL_MM) * 100

        # INVERT coordinate system for the API (0 is deep, 100 is shallow).
        api_min = max(0, round(100 - max_pct))
        api_max = min(100, round(100 - min_pct))

        # Ensure min is always less than max.
        if api_min >= api_max:
            api_max = min(100, api_min + 2)

        self._put("slide", {"min": api_min, "max": api_max})

    def set_speed_hz(self, hz: float) -> None:
        with self._lock:
            if hz == self._speed_hz:
                return
            self._speed_hz = hz

        if not self._slide_window:
            return  # Wait for slide window to be set first.

        # Convert Hz to device velocity (0-100) based on the current window size.
        window_mm = self._slide_window[1] - self._slide_window[0]
        window_pct = (window_mm / self.FULL_TRAVEL_MM) * 100
        
        # CORRECTED FORMULA: This formula provides a much more balanced conversion
        # from frequency (Hz) to the device's 0-100 velocity scale, preventing
        # the speed from maxing out at large stroke ranges.
        velocity = int(window_pct * hz)

        self._put("hamp/velocity", {"velocity": max(0, min(100, velocity))})

    def start_motion(self) -> None:
        self._put("mode", {"mode": 1})  # Set to HAMP mode.
        self._put("hamp/start")

    def stop_motion(self) -> None:
        self._put("hamp/stop")