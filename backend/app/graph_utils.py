"""
graph_utils.py
Fetch (and cache) the Bengaluru road network with OSMnx, and provide
a helper to compute a shortest-path route between two lat/lon points.

Run this LOCALLY (not in a network-restricted sandbox) the first time —
it needs to reach nominatim.openstreetmap.org and overpass-api.de.
After the first run, the graph is cached to data/bengaluru.graphml so
every later run is instant and offline.
"""

import math
import os
import osmnx as ox
import networkx as nx

GRAPH_CACHE_PATH = os.path.join(os.path.dirname(__file__), "data", "bengaluru.graphml")

# Point-and-radius instead of a place name: many neighborhoods (Koramangala
# included) have no administrative boundary POLYGON in OSM, only a point —
# graph_from_place() fails on those. graph_from_point() sidesteps this
# entirely and also gives direct control over download size/speed.
#
# This MUST cover every source/destination you'll test. The original 3km
# circle around Koramangala only covered Koramangala — anything outside it
# (PES University, Banashankari, etc.) silently snapped to the nearest
# node it could find, which could be km away from where you actually meant.
# Centered to comfortably span Koramangala / Jayanagar / Banashankari /
# JP Nagar / BTM / PES University (~9km radius). Widen further (and
# force_refresh=True) if you test addresses further out than that.
CENTER_POINT = (12.9250, 77.5780)
RADIUS_M = 9000


def get_bengaluru_graph(
    center_point: tuple = CENTER_POINT,
    radius_m: int = RADIUS_M,
    force_refresh: bool = False,
):
    """Load the road network graph, using the local cache if available."""
    if not force_refresh and os.path.exists(GRAPH_CACHE_PATH):
        print(f"[graph_utils] Loading cached graph from {GRAPH_CACHE_PATH}")
        G = ox.load_graphml(GRAPH_CACHE_PATH)
        G = add_urban_speeds_and_times(G)
        return add_emergency_weight(G)

    print(f"[graph_utils] Downloading road network within {radius_m}m of {center_point}")
    G = ox.graph_from_point(center_point, dist=radius_m, network_type="drive")

    os.makedirs(os.path.dirname(GRAPH_CACHE_PATH), exist_ok=True)
    ox.save_graphml(G, GRAPH_CACHE_PATH)
    print(f"[graph_utils] Cached graph to {GRAPH_CACHE_PATH}")
    G = add_urban_speeds_and_times(G)
    G = add_emergency_weight(G)
    return G


# OSM's `maxspeed` tag (what ox.add_edge_speeds() used before) reflects the
# LEGAL/free-flow speed limit, not what's actually achievable in dense city
# traffic -- that's why the old formula gave ~40 km/h defaults. These are
# realistic urban travel speeds instead (15-20 km/h band), used in place of
# maxspeed-derived speeds entirely.


# Applied only to nodes OSM actually tags as a real traffic signal
# (highway=traffic_signals on the NODE, not every 3+-way junction). Most
# junctions in a route are uncontrolled crossings with no real wait --
# treating every one of them as a 20s red light is what over-inflated the
# ETA (a ~2.8km route can easily pass 25-30 geometric junctions, most of
# them minor, so flat-penalizing all of them added ~9 extra minutes).
# Realistic average travel speeds across Bengaluru road classes
URBAN_SPEED_KPH = {
    "motorway": 40, "motorway_link": 30,
    "trunk": 35, "trunk_link": 28,          # Ring Road, Hosur Road mainlines
    "primary": 30, "primary_link": 24,        # 80 Feet Rd, 100 Feet Rd
    "secondary": 24, "secondary_link": 20,    # Connecting avenues
    "tertiary": 20, "tertiary_link": 18,     # Major layout roads
    "unclassified": 16,
    "residential": 14,
    "living_street": 12,
    "service": 10,
}
DEFAULT_URBAN_SPEED_KPH = 16

# Reduced to 6s per OSM signal node. Because OSM splits a single intersection
# into 3-6 separate tagged nodes across dual carriageways, a 6s value sums to
# a realistic 20-35s total delay per actual physical junction.
SIGNAL_PENALTY_S = 6


def _is_signal_node(G, n):
    hwy = G.nodes[n].get("highway")
    if isinstance(hwy, list):
        return "traffic_signals" in hwy
    return hwy == "traffic_signals"


def add_urban_speeds_and_times(G):
    """Calculates realistic travel times per edge without runaway signal inflation."""
    signal_nodes = {n for n in G.nodes if _is_signal_node(G, n)}

    for u, v, k, data in G.edges(keys=True, data=True):
        hwy = data.get("highway", "unclassified")
        if isinstance(hwy, list):
            hwy = hwy[0]
        speed_kph = URBAN_SPEED_KPH.get(hwy, DEFAULT_URBAN_SPEED_KPH)
        length_m = data.get("length", 0)

        base_time_s = length_m / (speed_kph * 1000 / 3600)
        signal_delay_s = SIGNAL_PENALTY_S if v in signal_nodes else 0

        data["speed_kph"] = speed_kph
        data["travel_time"] = base_time_s + signal_delay_s

    return G


# This now multiplies the realistic travel_time above (urban speed +
# signal delay already baked in) to further bias path SELECTION toward
# arterials -- it's a routing preference on top of the physical estimate,
# not a separate physical estimate itself.
EMERGENCY_CLASS_PENALTY = {
    "motorway": 1.0,
    "motorway_link": 1.0,
    "trunk": 1.0,
    "trunk_link": 1.05,
    "primary": 1.0,
    "primary_link": 1.1,
    "secondary": 1.15,
    "secondary_link": 1.2,
    "tertiary": 1.35,
    "tertiary_link": 1.4,
    "unclassified": 6.0,
    "residential": 25.0,      # Strict barrier: emergency vehicles cannot take narrow residential cross-streets
    "living_street": 50.0,    # Strictly impassable back alleys / gullies
    "service": 50.0,          # Impassable parking/service roads
}
DEFAULT_CLASS_PENALTY = 8.0




def add_emergency_weight(G):
    """Adds an 'emergency_time' edge attribute = travel_time * class
    penalty. get_route() uses this by default so the planned route
    reflects what an emergency vehicle (and Google) would actually take,
    not just the geometrically/nominally fastest path on paper."""
    for u, v, k, data in G.edges(keys=True, data=True):
        hwy = data.get("highway", "unclassified")
        if isinstance(hwy, list):
            hwy = hwy[0]  # some edges carry multiple tags; use the first
        penalty = EMERGENCY_CLASS_PENALTY.get(hwy, DEFAULT_CLASS_PENALTY)
        data["emergency_time"] = data.get("travel_time", 0) * penalty
    return G


def _sum_route_attribute(G, route_nodes, attr):
    """Sum an edge attribute along a route, done manually so we don't
    depend on ox.utils_graph.get_route_edge_attributes (removed in
    OSMnx 2.x). Handles multigraphs by taking the min-weight parallel
    edge between each node pair, matching what shortest_path used."""
    total = 0.0
    for u, v in zip(route_nodes[:-1], route_nodes[1:]):
        edge_data = G.get_edge_data(u, v)
        # edge_data is {key: {attrs...}} for a MultiDiGraph
        values = [d.get(attr, 0) for d in edge_data.values()]
        total += min(values) if values else 0
    return total


def _haversine_m(p1, p2):
    lat1, lon1 = p1
    lat2, lon2 = p2
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _snap_to_graph(G, latlon, label, max_snap_distance_m=800):
    """Finds the nearest graph node, but FAILS LOUDLY if it's suspiciously
    far away -- that means latlon is outside the graph's coverage area
    entirely, and nearest_nodes() would otherwise silently return some
    unrelated node instead of erroring (which is exactly what produced a
    route nowhere near the real source/destination)."""
    node = ox.nearest_nodes(G, latlon[1], latlon[0])
    node_coords = (G.nodes[node]["y"], G.nodes[node]["x"])
    dist = _haversine_m(latlon, node_coords)
    if dist > max_snap_distance_m:
        raise ValueError(
            f"{label} at {latlon} is {dist/1000:.1f}km from the nearest road "
            f"in the currently loaded graph -- it's outside the graph's "
            f"coverage area (centered at {CENTER_POINT}, radius {RADIUS_M}m). "
            f"Widen RADIUS_M / recenter CENTER_POINT in graph_utils.py and "
            f"call get_bengaluru_graph(force_refresh=True)."
        )
    return node


def get_route(G, origin_latlon, dest_latlon, weight="emergency_time"):
    """
    origin_latlon / dest_latlon: (lat, lon) tuples
    weight: "emergency_time" (default) favors arterials the way a real
        ambulance route would; pass "travel_time" for the raw/naive
        fastest path (useful for comparison, but prone to the narrow-lane
        problem described in graph_utils.add_emergency_weight's docstring),
        or "length" for pure shortest-distance.
    Returns: list of node ids forming the shortest path, and the route as
    a list of (lat, lon) coordinates for plotting / simulation.

    Raises ValueError if origin/dest fall outside the loaded graph's
    coverage area (see _snap_to_graph) instead of silently routing between
    unrelated points.
    """
    orig_node = _snap_to_graph(G, origin_latlon, "Origin")
    dest_node = _snap_to_graph(G, dest_latlon, "Destination")

    route_nodes = nx.shortest_path(G, orig_node, dest_node, weight=weight)
    route_coords = [(G.nodes[n]["y"], G.nodes[n]["x"]) for n in route_nodes]

    route_length_m = _sum_route_attribute(G, route_nodes, "length")
    route_time_s = _sum_route_attribute(G, route_nodes, "travel_time")

    return {
        "nodes": route_nodes,
        "coords": route_coords,
        "length_m": route_length_m,
        "eta_s": route_time_s,
    }


if __name__ == "__main__":
    G = get_bengaluru_graph()
    # Example: Koramangala Sony World Junction -> Forum Mall (adjust to your area)
    origin = (12.9352, 77.6146)
    dest = (12.9345, 77.6101)
    route = get_route(G, origin, dest)
    print(f"Route: {len(route['nodes'])} nodes, {route['length_m']:.0f} m, "
          f"ETA {route['eta_s']/60:.1f} min")