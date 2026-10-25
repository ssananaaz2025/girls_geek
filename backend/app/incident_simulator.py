"""
incident_simulator.py
Replaces the earlier "jitter a random waypoint" approach to generating a
deviation (dispatch.random_deviation_route, now removed) with something
causally real:

  1. Pick an actual edge (road segment) somewhere along the planned route.
  2. Simulate an incident there — accident / roadwork / closure / heavy
     congestion — by either removing that edge or heavily penalizing its
     travel time in a COPY of the graph.
  3. Recompute the shortest path from the point just before the blockage
     to the destination, on the now-blocked graph.

The ambulance has no choice but to take this new path — it's a real
consequence of a real blocked road, not an arbitrary detour. This also
gives you the incident's type and location for free, which is exactly
what a "cause identification" step (objective C) needs — no separate
guessing/matching required.
"""

import random
from dataclasses import dataclass
from typing import Tuple, Optional

from graph_utils import get_route

INCIDENT_TYPES = ["accident", "roadwork", "road_closure", "heavy_congestion"]

# None = treat as fully blocked (edge removed). A number = travel time
# multiplier (still passable, just much slower).
SEVERITY_MULTIPLIER = {
    "accident": None,
    "roadwork": None,
    "road_closure": None,
    "heavy_congestion": 6.0,
}


@dataclass
class Incident:
    type: str
    location: Tuple[float, float]   # lat, lon of the blocked edge's midpoint
    blocked_edge: Tuple[int, int]   # (u, v) node ids on the graph


def _edge_midpoint(G, u, v):
    return (
        (G.nodes[u]["y"] + G.nodes[v]["y"]) / 2,
        (G.nodes[u]["x"] + G.nodes[v]["x"]) / 2,
    )


def simulate_incident_and_reroute(
    G,
    planned_route: dict,
    dest_coords: Tuple[float, float],
    incident_at_fraction: Optional[float] = None,
    incident_type: Optional[str] = None,
):
    """
    planned_route: the dict returned by graph_utils.get_route()
    dest_coords: same destination the planned route was heading to

    Returns: (Incident, deviation_route) where deviation_route has the
    same shape as get_route()'s return value — {nodes, coords, length_m, eta_s}.
    """
    nodes = planned_route["nodes"]
    if incident_at_fraction is None:
        # avoid the very start (nothing to detect yet) and very end
        # (too late to matter) of the trip
        incident_at_fraction = random.uniform(0.2, 0.7)
    if incident_type is None:
        incident_type = random.choice(INCIDENT_TYPES)

    idx = max(1, min(len(nodes) - 2, int(len(nodes) * incident_at_fraction)))
    u, v = nodes[idx], nodes[idx + 1]

    incident = Incident(
        type=incident_type,
        location=_edge_midpoint(G, u, v),
        blocked_edge=(u, v),
    )

    # work on a copy so the shared cached graph isn't permanently mutated
    G_blocked = G.copy()
    multiplier = SEVERITY_MULTIPLIER[incident_type]
    edge_data = G_blocked.get_edge_data(u, v)
    if multiplier is None:
        G_blocked.remove_edge(u, v)
    else:
        for key in edge_data:
            G_blocked[u][v][key]["travel_time"] *= multiplier

    # reroute from the node just BEFORE the blockage to the destination —
    # this is what the driver is actually forced into in real life
    from_coords = (G.nodes[u]["y"], G.nodes[u]["x"])
    deviation_route = get_route(G_blocked, from_coords, dest_coords)

    return incident, deviation_route


if __name__ == "__main__":
    from graph_utils import get_bengaluru_graph
    from dispatch import plan_dispatch

    G = get_bengaluru_graph()
    plan = plan_dispatch(G, "Sony World Signal, Koramangala, Bengaluru")

    incident, deviation_route = simulate_incident_and_reroute(
        G, plan["planned_route"], plan["incident_coords"]
    )

    planned_eta = plan["planned_route"]["eta_s"]
    deviated_eta = deviation_route["eta_s"]

    print(f"Simulated incident: {incident.type} at {incident.location}")
    print(f"Planned ETA:  {planned_eta/60:.1f} min")
    print(f"Deviated ETA: {deviated_eta/60:.1f} min "
          f"({'+' if deviated_eta >= planned_eta else ''}{(deviated_eta - planned_eta)/60:.1f} min)")