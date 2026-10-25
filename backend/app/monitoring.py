"""
monitoring.py
Phase 1: Real-time vehicle deviation detection + control room alerting.

Wires detector.DeviationDetector into a LIVE per-vehicle session fed by
incoming GPS pings (from the simulator today, from a real vehicle feed
later -- this module doesn't care which). Produces a control-room-style
alert the moment a deviation is CONFIRMED, not on every noisy ping, and
keeps a rolling alert log so a dashboard can list active/past alerts
across multiple vehicles.
"""

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from detector import DeviationDetector

# GPS jitter in dense urban areas typically runs 5-15m, occasionally up
# to ~20m in signal-degraded conditions (tall buildings, underpasses).
# This threshold sits at 2.5x that stated 20m ceiling, so normal jitter
# never even registers as "off-route" in the first place -- it's not
# borderline, there's real margin.
#
# consecutive_needed adds a SECOND, independent layer of protection: even
# an abnormal single-ping GPS spike past the distance threshold can't
# trigger an alert alone -- it has to persist across 3 consecutive pings
# (~6 seconds at the normal 2s GPS interval) before being confirmed. A
# real deviation (the vehicle actually being on a different road) stays
# off-route on every subsequent ping; a noise spike, by definition,
# doesn't repeat in the same direction 3 times running.
DEVIATION_THRESHOLD_M = 50.0
CONSECUTIVE_PINGS_NEEDED = 3

# Default session inactivity TTL (15 minutes) before a session is purged
SESSION_TTL_SECONDS = 900.0


@dataclass
class Alert:
    vehicle_id: str
    deviation_point: Tuple[float, float]
    detected_at: float  # unix timestamp
    message: str


@dataclass
class TripSession:
    vehicle_id: str
    detector: DeviationDetector
    destination: Tuple[float, float]
    alerts: List[Alert] = field(default_factory=list)
    last_position: Optional[Tuple[float, float]] = None
    last_ping_at: Optional[float] = None
    created_at: float = field(default_factory=time.time)


# Thread-safe in-memory session store. Keyed by vehicle_id so multiple
# ambulances can be tracked concurrently without race conditions.
_sessions: Dict[str, TripSession] = {}
_sessions_lock = threading.Lock()


def cleanup_stale_sessions(ttl_seconds: float = SESSION_TTL_SECONDS) -> int:
    """Removes sessions that have had no ping or activity for longer than ttl_seconds.
    Prevents memory leaks from unclosed sessions (e.g. browser refreshes/closes).
    Returns the count of purged sessions."""
    now = time.time()
    stale_keys = []
    with _sessions_lock:
        for vehicle_id, session in _sessions.items():
            last_active = session.last_ping_at if session.last_ping_at is not None else session.created_at
            if now - last_active > ttl_seconds:
                stale_keys.append(vehicle_id)
        for key in stale_keys:
            _sessions.pop(key, None)
    return len(stale_keys)


def start_trip(vehicle_id: str, planned_route_coords, destination: Tuple[float, float]) -> TripSession:
    """Call once when a trip is dispatched -- registers a fresh detection
    session for this vehicle against its planned route. Calling this
    again for the same vehicle_id replaces any existing session (e.g. a
    fresh route was planned)."""
    cleanup_stale_sessions()
    detector = DeviationDetector(
        list(planned_route_coords),
        threshold_m=DEVIATION_THRESHOLD_M,
        consecutive_needed=CONSECUTIVE_PINGS_NEEDED,
    )
    session = TripSession(
        vehicle_id=vehicle_id,
        detector=detector,
        destination=tuple(destination),
        created_at=time.time(),
    )
    with _sessions_lock:
        _sessions[vehicle_id] = session
    return session


def get_session(vehicle_id: str) -> Optional[TripSession]:
    with _sessions_lock:
        return _sessions.get(vehicle_id)


def end_trip(vehicle_id: str) -> bool:
    """Returns True if a session existed and was removed."""
    with _sessions_lock:
        return _sessions.pop(vehicle_id, None) is not None


def process_ping(vehicle_id: str, lat: float, lon: float) -> dict:
    """
    Feed one live GPS ping into the vehicle's detection session.
    Returns a dict describing current state, plus a fresh `alert` ONLY on
    the ping where a deviation first becomes CONFIRMED (never repeats for
    an already-known, ongoing deviation -- that's what keeps a control
    room dashboard from being spammed with duplicate alerts).

    Raises ValueError if no session is active for this vehicle_id --
    call start_trip() first.
    """
    cleanup_stale_sessions()
    with _sessions_lock:
        session = _sessions.get(vehicle_id)
        if session is None:
            raise ValueError(f"No active trip session for vehicle_id={vehicle_id!r}. Call start_trip() first.")

        was_deviated = session.detector.state.is_deviated
        state = session.detector.process_ping(lat, lon)
        session.last_position = (lat, lon)
        session.last_ping_at = time.time()

        newly_confirmed = state.is_deviated and not was_deviated
        alert_out = None
        if newly_confirmed:
            alert = Alert(
                vehicle_id=vehicle_id,
                deviation_point=state.deviation_point,
                detected_at=session.last_ping_at,
                message=(
                    f"Vehicle {vehicle_id} has deviated from its planned route "
                    f"near {state.deviation_point[0]:.5f}, {state.deviation_point[1]:.5f}."
                ),
            )
            session.alerts.append(alert)
            alert_out = {
                "vehicle_id": alert.vehicle_id,
                "deviation_point": list(alert.deviation_point),
                "detected_at": alert.detected_at,
                "message": alert.message,
            }

        return {
            "vehicle_id": vehicle_id,
            "is_deviated": state.is_deviated,
            "consecutive_off_route": state.consecutive_off_route,
            "newly_confirmed": newly_confirmed,
            "alert": alert_out,
        }


def list_alerts(vehicle_id: Optional[str] = None) -> List[dict]:
    """For a control-room dashboard: all alerts (optionally filtered to
    one vehicle), newest first."""
    with _sessions_lock:
        sessions = [_sessions[vehicle_id]] if vehicle_id and vehicle_id in _sessions else (
            list(_sessions.values()) if vehicle_id is None else []
        )
        out = []
        for s in sessions:
            for a in s.alerts:
                out.append({
                    "vehicle_id": a.vehicle_id,
                    "deviation_point": list(a.deviation_point),
                    "detected_at": a.detected_at,
                    "message": a.message,
                })
    out.sort(key=lambda a: a["detected_at"], reverse=True)
    return out