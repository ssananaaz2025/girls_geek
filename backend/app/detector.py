"""
detector.py
Detects whether a stream of GPS pings is deviating from a planned route.

Approach (matches what we discussed):
  1. For each ping, find perpendicular distance to the nearest segment
     of the planned route polyline.
  2. If that distance exceeds `threshold_m`, mark the ping "off-route".
  3. Debounce: only fire a DEVIATION event once `consecutive_needed`
     off-route pings have occurred in a row (avoids false alarms from
     single noisy GPS readings).
  4. Track route progress (which segment index we're nearest to) so we
     can tell forward progress from a genuine departure from the path.
"""

import math
from dataclasses import dataclass
from typing import List, Tuple, Optional


def _haversine_m(p1, p2):
    lat1, lon1 = p1
    lat2, lon2 = p2
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _point_to_segment_distance_m(p, a, b):
    """Approx perpendicular distance from point p to segment a-b, in meters,
    using an equirectangular projection (fine at city scale)."""
    lat0 = math.radians(p[0])
    R = 6371000

    def project(pt):
        x = math.radians(pt[1]) * math.cos(lat0) * R
        y = math.radians(pt[0]) * R
        return x, y

    px, py = project(p)
    ax, ay = project(a)
    bx, by = project(b)

    dx, dy = bx - ax, by - ay
    if dx == dy == 0:
        return math.hypot(px - ax, py - ay)

    t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    proj_x, proj_y = ax + t * dx, ay + t * dy
    return math.hypot(px - proj_x, py - proj_y)


@dataclass
class DeviationState:
    consecutive_off_route: int = 0
    max_segment_index_reached: int = 0
    is_deviated: bool = False
    deviation_point: Optional[Tuple[float, float]] = None


class DeviationDetector:
    def __init__(
        self,
        planned_route_coords: List[Tuple[float, float]],
        threshold_m: float = 60.0,
        consecutive_needed: int = 3,
    ):
        self.route = planned_route_coords
        self.threshold_m = threshold_m
        self.consecutive_needed = consecutive_needed
        self.state = DeviationState()

    def _nearest_segment_distance(self, p) -> Tuple[float, int]:
        best_dist = float("inf")
        best_idx = 0
        # only search near current progress + a lookahead window, so we
        # don't "snap" backwards onto an already-passed part of the route
        lo = max(0, self.state.max_segment_index_reached - 5)
        hi = min(len(self.route) - 1, self.state.max_segment_index_reached + 40)
        for i in range(lo, hi):
            d = _point_to_segment_distance_m(p, self.route[i], self.route[i + 1])
            if d < best_dist:
                best_dist, best_idx = d, i
        return best_dist, best_idx

    def process_ping(self, lat: float, lon: float) -> DeviationState:
        p = (lat, lon)
        dist, seg_idx = self._nearest_segment_distance(p)

        if dist <= self.threshold_m:
            self.state.consecutive_off_route = 0
            self.state.max_segment_index_reached = max(
                self.state.max_segment_index_reached, seg_idx
            )
            # NOTE: we deliberately do NOT auto-clear is_deviated here —
            # once a real deviation is confirmed, clearing it is a
            # separate "re-route accepted" decision made upstream.
        else:
            self.state.consecutive_off_route += 1
            if (
                self.state.consecutive_off_route >= self.consecutive_needed
                and not self.state.is_deviated
            ):
                self.state.is_deviated = True
                self.state.deviation_point = p

        return self.state


if __name__ == "__main__":
    from simulator import simulate_trip

    planned = [(12.9352, 77.6146), (12.9345, 77.6101), (12.9330, 77.6080)]
    deviated = [(12.9345, 77.6101), (12.9380, 77.6130), (12.9330, 77.6080)]  # detours further away

    detector = DeviationDetector(planned, threshold_m=60.0, consecutive_needed=3)

    for ping in simulate_trip("AMB-104", planned, deviation_route_coords=deviated, deviation_at_fraction=0.5):
        state = detector.process_ping(ping.lat, ping.lon)
        if state.is_deviated:
            print(f"🚨 DEVIATION CONFIRMED at ~{state.deviation_point}, "
                  f"ground truth on_deviated_path={ping.on_deviated_path}")
            break
    else:
        print("No deviation detected over the whole trip.")