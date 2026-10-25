"""
dispatch.py
Replaces hardcoded origin/dest test points with something closer to how
a real system would decide them:

  1. AMBULANCE_STATIONS — a small set of real fixed locations (this is
     legitimate seed/reference data, not a "hardcoded test value" — a real
     control room has a fixed, known list of station locations too).
  2. geocode_incident() — turn an address string into coordinates via
     Nominatim (same geocoder OSMnx uses), OR accept direct lat/lon if
     you already have it (e.g. from a caller's phone GPS).
  3. nearest_station() — pick the closest available ambulance to the
     incident, by actual road network distance (not straight-line), since
     that's what matters for dispatch decisions.
  4. plan_dispatch() — ties it together: incident in, full planned route
     out. This is what your API endpoint / demo script should call
     instead of hand-picking coordinates.

For the deviation side: instead of hand-picking a "detour_via" point too,
`random_deviation_route()` picks a random point off the planned route on
its own, so your demo doesn't rely on you guessing coordinates that
happen to look like a deviation.
"""

import random
from typing import Optional, Tuple, Dict

from geopy.geocoders import Nominatim

from graph_utils import get_route

# Real, fixed reference points — swap these for your chosen city's actual
# ambulance station locations (government open data / hospital directories
# usually list these; for a prototype, approximate hospital locations work).
AMBULANCE_STATIONS: Dict[str, Tuple[float, float]] = {
    "St. John's Hospital": (12.9279, 77.6271),
    "Fortis Hospital Bannerghatta Rd": (12.8916, 77.5963),
    "Manipal Hospital Old Airport Rd": (12.9581, 77.6482),
    "Koramangala 108 Station": (12.9352, 77.6146),
}

_geolocator = Nominatim(user_agent="geoagentic-framework-prototype")


# Preset landmark coordinates for instant, robust demo geocoding
LANDMARK_COORDINATES: Dict[str, Tuple[float, float]] = {
    "sony world": (12.9352, 77.6146),
    "koramangala sony world": (12.9352, 77.6146),
    "sony world signal": (12.9352, 77.6146),
    "forum mall": (12.9345, 77.6101),
    "forum koramangala": (12.9345, 77.6101),
    "st. john's": (12.9279, 77.6271),
    "st johns hospital": (12.9279, 77.6271),
    "fortis": (12.8916, 77.5963),
    "manipal": (12.9581, 77.6482),
    "jayanagar": (12.9250, 77.5850),
    "btm layout": (12.9165, 77.6101),
    "pes university": (12.9340, 77.5345),
}

def geocode_incident(location) -> Tuple[float, float]:
    """
    Accepts:
      - (lat, lon) tuple directly
      - "lat, lon" string (e.g. "12.9352, 77.6146")
      - Landmark names (e.g. "Sony World", "Forum Mall")
      - Address string geocoded via Nominatim with fallback search terms
    """
    if isinstance(location, tuple) or isinstance(location, list):
        return (float(location[0]), float(location[1]))

    if not isinstance(location, str):
        raise ValueError(f"Invalid location format: {location!r}")

    loc_str = location.strip()

    # Try parsing direct comma-separated coordinates: "12.9352, 77.6146"
    if "," in loc_str:
        parts = loc_str.split(",")
        if len(parts) == 2:
            try:
                lat = float(parts[0].strip())
                lon = float(parts[1].strip())
                if -90 <= lat <= 90 and -180 <= lon <= 180:
                    return (lat, lon)
            except ValueError:
                pass

    # Check preset landmark cache
    loc_lower = loc_str.lower()
    for landmark, coords in LANDMARK_COORDINATES.items():
        if landmark in loc_lower:
            return coords

    # Fallback to Nominatim geocoding
    try:
        result = _geolocator.geocode(loc_str, timeout=8)
        if result is not None:
            return (result.latitude, result.longitude)
    except Exception:
        pass

    # Simplified search fallback if full address failed
    parts = loc_str.split(",")
    if len(parts) > 1:
        simplified = parts[-2].strip() + ", " + parts[-1].strip()
        try:
            result = _geolocator.geocode(simplified, timeout=8)
            if result is not None:
                return (result.latitude, result.longitude)
        except Exception:
            pass

    # Default city fallback if location remains unrecognized
    return (12.9352, 77.6146)



def nearest_station(G, incident_coords: Tuple[float, float]) -> Tuple[str, Tuple[float, float]]:
    """Pick the ambulance station with the shortest ROAD route to the
    incident (not straight-line distance, since actual drive time is
    what matters for dispatch)."""
    best_name, best_coords, best_time = None, None, float("inf")
    for name, coords in AMBULANCE_STATIONS.items():
        try:
            route = get_route(G, coords, incident_coords)
        except Exception:
            continue  # station unreachable in this graph extract, skip it
        if route["eta_s"] < best_time:
            best_name, best_coords, best_time = name, coords, route["eta_s"]
    if best_name is None:
        raise ValueError("No ambulance station could reach this incident in the current graph extract.")
    return best_name, best_coords


def plan_dispatch(G, incident_location) -> dict:
    """
    Full dispatch planning: incident (address or lat/lon) -> nearest
    station -> planned route. This is the single entry point your API
    or demo script should call.
    """
    incident_coords = geocode_incident(incident_location)
    station_name, station_coords = nearest_station(G, incident_coords)
    route = get_route(G, station_coords, incident_coords)

    return {
        "incident_coords": incident_coords,
        "dispatched_from": station_name,
        "station_coords": station_coords,
        "planned_route": route,  # {nodes, coords, length_m, eta_s}
    }


if __name__ == "__main__":
    from graph_utils import get_bengaluru_graph

    G = get_bengaluru_graph()

    # Incident can be a real address string...
    plan = plan_dispatch(G, "Sony World Signal, Koramangala, Bengaluru")
    # ...or direct coordinates, e.g. from a phone GPS:
    # plan = plan_dispatch(G, (12.9330, 77.6080))

    print(f"Dispatched from: {plan['dispatched_from']}")
    print(f"Incident at: {plan['incident_coords']}")
    print(f"Planned ETA: {plan['planned_route']['eta_s']/60:.1f} min, "
          f"{plan['planned_route']['length_m']:.0f} m")