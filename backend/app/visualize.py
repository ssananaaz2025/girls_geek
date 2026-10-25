"""
visualize.py
Builds an interactive Leaflet map (via Folium) showing:
  - planned route (blue)
  - actual/simulated ambulance path (black dashed, with a marker trail)
  - deviation point (red marker)
  - alternative/recalculated route (green), if provided

Output: an .html file you can open directly in a browser, or embed.
No OSMnx dependency here — just coordinate lists, so it works with
either real or synthetic routes.
"""

import folium
from typing import List, Tuple, Optional


def build_map(
    planned_coords: List[Tuple[float, float]],
    actual_coords: List[Tuple[float, float]],
    deviation_point: Optional[Tuple[float, float]] = None,
    alt_route_coords: Optional[List[Tuple[float, float]]] = None,
    out_path: str = "output/map.html",
):
    center = planned_coords[len(planned_coords) // 2]
    m = folium.Map(location=center, zoom_start=15, tiles="OpenStreetMap")

    folium.PolyLine(
        planned_coords, color="blue", weight=5, opacity=0.7, tooltip="Planned route"
    ).add_to(m)

    folium.PolyLine(
        actual_coords, color="black", weight=3, dash_array="6,6",
        opacity=0.8, tooltip="Actual (simulated) path"
    ).add_to(m)

    folium.Marker(
        planned_coords[0], tooltip="Start (dispatch)",
        icon=folium.Icon(color="green", icon="play"),
    ).add_to(m)
    folium.Marker(
        planned_coords[-1], tooltip="Destination",
        icon=folium.Icon(color="darkred", icon="flag"),
    ).add_to(m)

    if deviation_point:
        folium.Marker(
            deviation_point, tooltip="Deviation detected here",
            icon=folium.Icon(color="red", icon="exclamation-sign"),
        ).add_to(m)

    if alt_route_coords:
        folium.PolyLine(
            alt_route_coords, color="green", weight=5, opacity=0.8,
            tooltip="Recommended alternative route"
        ).add_to(m)

    import os
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    m.save(out_path)
    print(f"[visualize] Map saved to {out_path}")
    return out_path


if __name__ == "__main__":
    from simulator import simulate_trip
    from detector import DeviationDetector

    planned = [(12.9352, 77.6146), (12.9345, 77.6101), (12.9330, 77.6080)]
    deviated = [(12.9345, 77.6101), (12.9380, 77.6130), (12.9330, 77.6080)]

    detector = DeviationDetector(planned, threshold_m=60.0, consecutive_needed=3)
    actual_path = []
    dev_point = None

    for ping in simulate_trip("AMB-104", planned, deviation_route_coords=deviated, deviation_at_fraction=0.5):
        actual_path.append((ping.lat, ping.lon))
        state = detector.process_ping(ping.lat, ping.lon)
        if state.is_deviated and dev_point is None:
            dev_point = state.deviation_point

    build_map(
        planned_coords=planned,
        actual_coords=actual_path,
        deviation_point=dev_point,
        alt_route_coords=deviated,
        out_path="output/demo_map.html",
    )