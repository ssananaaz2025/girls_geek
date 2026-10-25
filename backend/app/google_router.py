"""
google_router.py
Client for Google Cloud Routes API with GeoAgent spatial obstacle filtering.

Workflow:
  1. Requests alternative driving routes from Google Routes API (computeAlternativeRoutes: true).
  2. Decodes polyline geometries into [lat, lon] coordinates.
  3. Evaluates each candidate route against the simulated roadblock coordinate.
  4. Filters out any route that passes through the blocked road segment.
  5. Returns the cleanest bypass route, or None if the API key is inactive/unreachable
     (allowing graceful fallback to the local OSMnx engine).
"""

import os
import json
import math
import urllib.request
import urllib.error
from typing import List, Tuple, Optional, Dict, Any

def _get_api_key() -> str:
    # Check environment variable or read from .env
    key = os.environ.get("ROUTE_API_KEY", "").strip()
    if key:
        return key
    
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("ROUTE_API_KEY="):
                    return line.split("=", 1)[1].strip()
    return ""

def _haversine_m(p1: Tuple[float, float], p2: Tuple[float, float]) -> float:
    R = 6371000
    phi1, phi2 = math.radians(p1[0]), math.radians(p2[0])
    dphi = math.radians(p2[0] - p1[0])
    dlambda = math.radians(p2[1] - p1[1])
    a = math.sin(dphi / 2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2)**2
    return 2 * R * math.asin(math.sqrt(a))

def decode_polyline(polyline_str: str) -> List[Tuple[float, float]]:
    """Standard Google Encoded Polyline decoder into list of (lat, lon) coordinates."""
    index, lat, lng = 0, 0, 0
    coordinates = []
    length = len(polyline_str)
    while index < length:
        shift, result = 0, 0
        while True:
            byte = ord(polyline_str[index]) - 63
            index += 1
            result |= (byte & 0x1f) << shift
            shift += 5
            if not byte >= 0x20:
                break
        dlat = ~(result >> 1) if (result & 1) else (result >> 1)
        lat += dlat

        shift, result = 0, 0
        while True:
            byte = ord(polyline_str[index]) - 63
            index += 1
            result |= (byte & 0x1f) << shift
            shift += 5
            if not byte >= 0x20:
                break
        dlng = ~(result >> 1) if (result & 1) else (result >> 1)
        lng += dlng

        coordinates.append((lat / 1e5, lng / 1e5))
    return coordinates

def _passes_through_blockage(route_coords: List[Tuple[float, float]], obstacle_pos: Tuple[float, float], buffer_m: float = 60.0) -> bool:
    """Returns True if the route comes within buffer_m meters of the roadblock."""
    for pt in route_coords:
        if _haversine_m(pt, obstacle_pos) <= buffer_m:
            return True
    return False

def compute_google_reroute(
    origin_latlon: Tuple[float, float],
    dest_latlon: Tuple[float, float],
    obstacle_pos: Optional[Tuple[float, float]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Calls Google Routes API, retrieves alternatives, and selects the best candidate
    that does not cross obstacle_pos.
    Returns {coords, length_m, eta_s, summary} or None on error/blockage.
    """
    key = _get_api_key()
    if not key or key.startswith("AIzaSy") and len(key) < 20:
        return None

    url = "https://routes.googleapis.com/directions/v2:computeRoutes"
    body = {
        "origin": {"location": {"latLng": {"latitude": origin_latlon[0], "longitude": origin_latlon[1]}}},
        "destination": {"location": {"latLng": {"latitude": dest_latlon[0], "longitude": dest_latlon[1]}}},
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_AWARE",
        "computeAlternativeRoutes": True,
    }
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": key,
        "X-Goog-FieldMask": "routes.duration,routes.distanceMeters,routes.description,routes.polyline.encodedPolyline",
    }

    try:
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
        with urllib.request.urlopen(req, timeout=5) as res:
            data = json.loads(res.read().decode())
    except (urllib.error.HTTPError, urllib.error.URLError, Exception) as e:
        # Fall back cleanly without raising or crashing
        print(f"[google_router] Google Routes API request bypassed: {e}")
        return None

    routes = data.get("routes", [])
    if not routes:
        return None

    # Evaluate candidates
    candidate_options = []
    for r in routes:
        encoded = r.get("polyline", {}).get("encodedPolyline", "")
        if not encoded:
            continue
        coords = decode_polyline(encoded)
        duration_str = r.get("duration", "0s").replace("s", "")
        eta_s = float(duration_str) if duration_str else 0.0
        distance_m = float(r.get("distanceMeters", 0.0))
        description = r.get("description", "Alternative Arterial")

        is_blocked = False
        if obstacle_pos:
            is_blocked = _passes_through_blockage(coords, obstacle_pos)

        candidate_options.append({
            "coords": coords,
            "eta_s": eta_s,
            "length_m": distance_m,
            "description": description,
            "is_blocked": is_blocked,
        })

    # Pick the best route that is NOT blocked
    clean_routes = [c for c in candidate_options if not c["is_blocked"]]
    if clean_routes:
        best = min(clean_routes, key=lambda x: x["eta_s"])
        best["source"] = "Google Routes API (Live Traffic Bypass)"
        return best

    # If all candidates passed near the obstacle, return None to let OSMnx calculate strict detour
    return None
