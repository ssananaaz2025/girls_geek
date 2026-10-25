"""
reroute.py
Dynamic rerouting when an obstacle (road closure / incident) is detected
along the active route.

Never touches the shared/cached graph `G` -- every call works on a
throwaway copy, so one incident (or a resolved one) can't leave stale
blockages affecting every other request that reuses the same graph object.
"""

from typing import Tuple, Union, Optional

from graph_utils import get_route, _snap_to_graph
import google_router


# A large FINITE penalty, not float('inf'):
#   1. float('inf') breaks JSON serialization -- FastAPI/Starlette's
#      encoder raises on non-finite floats ("Out of range float values
#      are not JSON compliant"), so an actual inf would crash the response
#      the moment any downstream value touched it.
#   2. A finite-but-huge cost still guarantees shortest_path avoids the
#      edge whenever ANY alternative exists, while still letting the
#      algorithm fall back to it (a usable, if very slow, route) in the
#      rare case it's genuinely the only way through -- instead of the
#      request failing outright.
BLOCKED_EDGE_PENALTY = 1e7


def _resolve_blocked_edge(G, blocked_edge) -> Tuple[int, int]:
    """
    Accepts EITHER:
      - (u, v) node ids directly, or
      - ((lat1, lon1), (lat2, lon2)) a road coordinate segment, which
        gets snapped to the nearest graph nodes.
    Returns (u, v) node ids.
    """
    if len(blocked_edge) != 2:
        raise ValueError(
            "blocked_edge must have exactly 2 elements: either [u_node_id, v_node_id] "
            f"or [[lat1, lon1], [lat2, lon2]]. Got {len(blocked_edge)} element(s): {blocked_edge!r}"
        )
    a, b = blocked_edge

    try:
        if a in G.nodes and b in G.nodes:
            u, v = a, b
        else:
            u = _snap_to_graph(G, a, "Blocked edge point A")
            v = _snap_to_graph(G, b, "Blocked edge point B")
    except TypeError:
        u = _snap_to_graph(G, a, "Blocked edge point A")
        v = _snap_to_graph(G, b, "Blocked edge point B")

    # Safeguard against degenerate self-loop (u == v): select adjacent node
    if u == v:
        neighbors = list(G.successors(u)) or list(G.predecessors(u)) or list(G.neighbors(u))
        if neighbors:
            v = neighbors[0]

    return u, v


def block_edge(H, u, v):
    # Block forward direction (all parallel lanes/keys)
    if H.has_edge(u, v):
        for k in H[u][v]:
            H[u][v][k]['travel_time'] = BLOCKED_EDGE_PENALTY
            H[u][v][k]['emergency_time'] = BLOCKED_EDGE_PENALTY
            
    # Block reverse direction (if present)
    if H.has_edge(v, u):
        for k in H[v][u]:
            H[v][u][k]['travel_time'] = BLOCKED_EDGE_PENALTY
            H[v][u][k]['emergency_time'] = BLOCKED_EDGE_PENALTY


def block_incident_corridor(H, u, v, stretch_steps: int = 6):
    """
    Blocks the primary edge (u, v) plus downstream edges along the same
    road segment for stretch_steps (~300-450m).
    This models a real-world arterial closure where the full corridor stretch
    is blocked, preventing the routing engine from cheating by cutting
    through narrow 50m residential gullies/cross-streets just to re-enter
    the blocked road 50 meters down.
    """
    block_edge(H, u, v)

    curr = v
    for _ in range(stretch_steps):
        next_nodes = list(H.successors(curr))
        if not next_nodes:
            break
        nxt = next_nodes[0]
        block_edge(H, curr, nxt)
        curr = nxt



def reroute_around_blockage(
    G,
    current_vehicle_latlon: Tuple[float, float],
    destination_latlon: Tuple[float, float],
    blocked_edge: Union[Tuple[int, int], Tuple[Tuple[float, float], Tuple[float, float]]],
):
    """
    Main entry point.

    blocked_edge: (u, v) node ids, OR a coordinate segment
        ((lat1,lon1), (lat2,lon2)) -- both are accepted and resolved to
        graph nodes.

    Returns a dict matching the response contract:
        rerouted, current_position, new_route_coords, new_eta_s,
        delay_added_s, cause, has_bypass
    """
    # 1. Resolve blocked edge nodes and position
    u, v = _resolve_blocked_edge(G, blocked_edge)
    obstacle_latlon = None
    if isinstance(blocked_edge[0], (tuple, list)):
        obstacle_latlon = (float(blocked_edge[0][0]), float(blocked_edge[0][1]))
    elif u in G.nodes:
        obstacle_latlon = (G.nodes[u]["y"], G.nodes[u]["x"])

    # 2. Try Google Routes API with obstacle avoidance filter first
    google_res = google_router.compute_google_reroute(
        current_vehicle_latlon,
        destination_latlon,
        obstacle_pos=obstacle_latlon,
    )
    if google_res:
        baseline_remaining = get_route(G, current_vehicle_latlon, destination_latlon, weight="emergency_time")
        delay_added = max(0.0, google_res["eta_s"] - baseline_remaining["eta_s"])
        return {
            "rerouted": True,
            "has_bypass": True,
            "current_position": [current_vehicle_latlon[0], current_vehicle_latlon[1]],
            "new_route_coords": [[c[0], c[1]] for c in google_res["coords"]],
            "new_eta_s": google_res["eta_s"],
            "delay_added_s": round(delay_added, 1),
            "cause": f"Road closure ahead — Bypassed via {google_res.get('description', 'Arterial alternative')} ({google_res.get('source')})",
        }

    # 3. Fallback to in-house NetworkX graph copy with corridor block
    G_blocked = G.copy()
    block_incident_corridor(G_blocked, u, v, stretch_steps=6)

    # 4. Recompute the forced alternate route on the blocked graph
    new_route = get_route(G_blocked, current_vehicle_latlon, destination_latlon, weight="emergency_time")


    # 3. baseline: what the ORIGINAL (unblocked) graph says this remaining leg should take
    baseline_remaining = get_route(G, current_vehicle_latlon, destination_latlon, weight="emergency_time")

    # 4. Check if the resulting path was forced across the blocked edge (no bypass available)
    nodes = new_route["nodes"]
    traversed_blocked_edge = False
    for p1, p2 in zip(nodes[:-1], nodes[1:]):
        if (p1 == u and p2 == v) or (p1 == v and p2 == u):
            traversed_blocked_edge = True
            break

    delay_added_s = new_route["eta_s"] - baseline_remaining["eta_s"]

    # If delay exceeds 24 hours (1e7 penalty paid) or blocked edge was crossed, no alternate route exists
    if traversed_blocked_edge or delay_added_s >= 86400:
        return {
            "rerouted": False,
            "has_bypass": False,
            "current_position": [current_vehicle_latlon[0], current_vehicle_latlon[1]],
            "new_route_coords": [[c[0], c[1]] for c in baseline_remaining["coords"]],
            "new_eta_s": baseline_remaining["eta_s"],
            "delay_added_s": 0.0,
            "cause": "No alternate bypass route available; road network disconnected around blockage",
        }

    return {
        "rerouted": True,
        "has_bypass": True,
        "current_position": [current_vehicle_latlon[0], current_vehicle_latlon[1]],
        "new_route_coords": [[c[0], c[1]] for c in new_route["coords"]],
        "new_eta_s": new_route["eta_s"],
        "delay_added_s": max(0.0, delay_added_s),
        "cause": "Road closure / dynamic incident detected ahead",
    }