"""
simulator.py
Simulates an ambulance's GPS stream by walking along a route's coordinate
list at a fixed speed, emitting a ping every `interval_s` seconds, with
small positional noise. Can optionally "jump" onto a different route
partway through, to simulate a real deviation for the demo.

This has no dependency on OSMnx at runtime — it only needs a list of
(lat, lon) coordinates, so it works the same whether those coords came
from a real OSRM/OSMnx route or a synthetic test route.
"""

import math
import random
import time
from dataclasses import dataclass, asdict
from typing import List, Tuple, Optional, Iterator


@dataclass
class GpsPing:
    vehicle_id: str
    lat: float
    lon: float
    timestamp: float
    speed_kmh: float
    heading_deg: float
    on_deviated_path: bool  # ground truth flag, for evaluating your detector


def _haversine_m(p1: Tuple[float, float], p2: Tuple[float, float]) -> float:
    lat1, lon1 = p1
    lat2, lon2 = p2
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _bearing_deg(p1: Tuple[float, float], p2: Tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, p1)
    lat2, lon2 = map(math.radians, p2)
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def _densify(coords: List[Tuple[float, float]], step_m: float = 15.0) -> List[Tuple[float, float]]:
    """Interpolate extra points so consecutive points are ~step_m apart,
    giving smooth, realistic-looking movement instead of teleporting
    between sparse route nodes."""
    if len(coords) < 2:
        return coords
    dense = [coords[0]]
    for a, b in zip(coords, coords[1:]):
        dist = _haversine_m(a, b)
        n_steps = max(1, int(dist // step_m))
        for i in range(1, n_steps + 1):
            t = i / n_steps
            lat = a[0] + (b[0] - a[0]) * t
            lon = a[1] + (b[1] - a[1]) * t
            dense.append((lat, lon))
    return dense


def simulate_trip(
    vehicle_id: str,
    planned_route_coords: List[Tuple[float, float]],
    speed_kmh: float = 40.0,
    interval_s: float = 2.0,
    gps_noise_m: float = 5.0,
    deviation_route_coords: Optional[List[Tuple[float, float]]] = None,
    deviation_at_fraction: Optional[float] = None,
    realtime: bool = False,
) -> Iterator[GpsPing]:
    """
    Yields GpsPing objects as if streaming live.

    - planned_route_coords: the original planned path, e.g. from get_route()
    - deviation_route_coords: an alternate path (e.g. from a different
      get_route() call) to switch onto partway through, simulating the
      ambulance going off-plan
    - deviation_at_fraction: 0..1, how far along the planned route to
      trigger the switch (e.g. 0.4 = deviate 40% of the way there)
    - realtime: if True, actually sleeps interval_s between pings
      (useful for a live demo); if False, yields as fast as possible
      (useful for feeding a detector in a test script)
    """
    dense_planned = _densify(planned_route_coords)
    switch_index = None
    dense_deviation = None

    if deviation_route_coords and deviation_at_fraction is not None:
        switch_index = int(len(dense_planned) * deviation_at_fraction)
        dense_deviation = _densify(deviation_route_coords)

    step_m = (speed_kmh * 1000 / 3600) * interval_s

    path = dense_planned
    on_deviated = False
    t0 = time.time()
    i = 0
    pos_pointer = 0.0  # fractional index into `path`, since densify spacing != step_m exactly

    while pos_pointer < len(path) - 1:
        idx = int(pos_pointer)
        cur = path[idx]
        nxt = path[min(idx + 1, len(path) - 1)]

        # inject noise (~gps_noise_m)
        noisy_lat = cur[0] + random.uniform(-1, 1) * (gps_noise_m / 111_320)
        noisy_lon = cur[1] + random.uniform(-1, 1) * (gps_noise_m / (111_320 * math.cos(math.radians(cur[0]))))

        heading = _bearing_deg(cur, nxt)

        yield GpsPing(
            vehicle_id=vehicle_id,
            lat=round(noisy_lat, 6),
            lon=round(noisy_lon, 6),
            timestamp=t0 + i * interval_s,
            speed_kmh=round(speed_kmh + random.uniform(-3, 3), 1),
            heading_deg=round(heading, 1),
            on_deviated_path=on_deviated,
        )

        if realtime:
            time.sleep(interval_s)

        # advance
        seg_len = max(_haversine_m(cur, nxt), 1e-6)
        pos_pointer += step_m / seg_len
        i += 1

        # trigger deviation: splice onto the deviation route from here on
        if switch_index is not None and idx >= switch_index and not on_deviated:
            path = dense_deviation
            pos_pointer = 0.0
            on_deviated = True


if __name__ == "__main__":
    # Quick smoke test with a synthetic straight-line "route" so this
    # runs with zero external dependencies.
    planned = [(12.9352, 77.6146), (12.9345, 77.6101), (12.9330, 77.6080)]
    deviated = [(12.9345, 77.6101), (12.9360, 77.6090), (12.9330, 77.6080)]

    for ping in simulate_trip(
        "AMB-104", planned,
        deviation_route_coords=deviated,
        deviation_at_fraction=0.5,
    ):
        print(asdict(ping))