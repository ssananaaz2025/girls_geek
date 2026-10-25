"""
api.py
FastAPI backend for the GeoAgentic Control Room, Driver HUD, and Admin Dashboards.

Supports:
    - Multi-vehicle fleet tracking (Ambulance, Fire Engine, Police)
    - Route planning and instant GPS stream synthesis
    - Dynamic rerouting around road closures/obstacles
    - Live deviation detection with debounce protection
    - GeoAgent automated reasoning for 5 control-room questions
    - Dispatcher decision approval/rejection workflow
    - Driver deviation/obstacle reporting
    - Operational timeline audit log
    - Admin system health & fleet status inspection
"""

import time
from dataclasses import asdict
from typing import Optional, List, Dict, Any, Tuple

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import sys
from pathlib import Path

# Ensure backend/app directory is in sys.path
_app_dir = str(Path(__file__).resolve().parent)
if _app_dir not in sys.path:
    sys.path.insert(0, _app_dir)

import networkx as nx
import graph_utils
from graph_utils import get_bengaluru_graph, get_route
from dispatch import geocode_incident
from simulator import simulate_trip
from reroute import reroute_around_blockage
import monitoring
import decision_engine
import fleet_manager


app = FastAPI(title="GeoAgentic Multi-Role Dashboard API")

# Dev CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_graph = None
_start_time = time.time()


@app.on_event("startup")
def load_graph():
    global _graph
    print("[api] Loading road network graph (cached if available)...")
    _graph = get_bengaluru_graph()
    print("[api] Graph ready.")


class RouteRequest(BaseModel):
    source: str
    destination: str
    vehicle_id: str = "AMB-104"
    priority: str = "Critical / Code Red"


@app.post("/api/route")
def plan_route(req: RouteRequest):
    if _graph is None:
        raise HTTPException(status_code=503, detail="Graph still loading, try again shortly.")

    try:
        source_coords = geocode_incident(req.source)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Could not geocode source address: {req.source!r}")

    try:
        dest_coords = geocode_incident(req.destination)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Could not geocode destination address: {req.destination!r}")

    try:
        route = get_route(_graph, source_coords, dest_coords)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Could not compute a route between those points: {e}")

    pings = list(simulate_trip(
        vehicle_id=req.vehicle_id,
        planned_route_coords=route["coords"],
        speed_kmh=40.0,
        interval_s=2.0,
        realtime=False,
    ))

    # Register mission in fleet_manager
    try:
        mission = fleet_manager.create_mission(
            vehicle_id=req.vehicle_id,
            source=req.source,
            destination=req.destination,
            source_coords=source_coords,
            dest_coords=dest_coords,
            route_coords=route["coords"],
            eta_s=route["eta_s"],
            priority=req.priority,
        )
    except fleet_manager.ActiveMissionConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {
        "mission_id": mission.id,
        "vehicle_id": req.vehicle_id,
        "source_coords": source_coords,
        "dest_coords": dest_coords,
        "planned_route": {
            "nodes": route["nodes"],
            "coords": route["coords"],
            "length_m": route["length_m"],
            "eta_s": route["eta_s"],
        },
        "pings": [asdict(p) for p in pings],
    }


class RouteFromCoordsRequest(BaseModel):
    source_lat: float
    source_lon: float
    dest_lat: float
    dest_lon: float
    vehicle_id: str = "AMB-104"
    priority: str = "Critical / Code Red"
    blocked_edge: Optional[list] = None   # [[lat,lon],[lat,lon]] or [node_id, node_id]


@app.post("/api/route/coords")
def plan_route_from_coords(req: RouteFromCoordsRequest):
    """
    Compute a route directly from lat/lon coordinates (no geocoding).
    If blocked_edge is provided, those road edges are temporarily removed
    from the graph so the returned route is forced to avoid them.
    """
    if _graph is None:
        raise HTTPException(status_code=503, detail="Graph still loading, try again shortly.")

    source_coords = (req.source_lat, req.source_lon)
    dest_coords = (req.dest_lat, req.dest_lon)

    # ── Temporarily remove blocked edges ─────────────────────────────────────
    removed_edges: list[tuple] = []
    if req.blocked_edge and len(req.blocked_edge) == 2:
        be = req.blocked_edge
        # Resolve: be can be [node_id, node_id] ints or [[lat,lon],[lat,lon]] pairs
        def _resolve_node(val):
            if isinstance(val, (int, str)) and val in _graph.nodes:
                return val
            # It's a [lat, lon] pair — snap to nearest node
            if isinstance(val, (list, tuple)) and len(val) == 2:
                try:
                    import osmnx as ox
                    return ox.nearest_nodes(_graph, val[1], val[0])
                except Exception:
                    return None
            return None

        u_node = _resolve_node(be[0])
        v_node = _resolve_node(be[1])

        if u_node is not None and v_node is not None:
            # Remove all parallel edges between u↔v in both directions
            for (a, b) in [(u_node, v_node), (v_node, u_node)]:
                if _graph.has_edge(a, b):
                    edge_data_map = dict(_graph[a][b])   # {key: attrs}
                    for k, data in edge_data_map.items():
                        _graph.remove_edge(a, b, key=k)
                        removed_edges.append((a, b, k, data))

    try:
        route = get_route(_graph, source_coords, dest_coords)
    except nx.NetworkXNoPath:
        raise HTTPException(status_code=404, detail="No alternative route found around the blockage.")
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Could not compute a route: {e}")
    finally:
        # ── Always restore removed edges ──────────────────────────────────────
        for (a, b, k, data) in removed_edges:
            _graph.add_edge(a, b, key=k, **data)

    fleet_manager.log_event(
        event_type="REROUTE_COMPUTED",
        vehicle_id=req.vehicle_id,
        title="Live Reroute Computed",
        message=f"{req.vehicle_id} rerouted from current position (blocked edge excluded). New ETA: {route['eta_s']/60:.1f} min.",
        severity="info",
    )
    fleet_manager.save_route_option_for_vehicle(
        req.vehicle_id,
        route["coords"],
        route["eta_s"],
        label="RECOVERY",
    )

    return {
        "vehicle_id": req.vehicle_id,
        "source_coords": list(source_coords),
        "dest_coords": list(dest_coords),
        "route": {
            "nodes": route["nodes"],
            "coords": route["coords"],
            "length_m": route["length_m"],
            "eta_s": route["eta_s"],
        },
    }




class RerouteRequest(BaseModel):
    current_lat: float
    current_lon: float
    destination_lat: float
    destination_lon: float
    blocked_edge: list
    vehicle_id: str = "AMB-104"


@app.post("/api/reroute")
def reroute(req: RerouteRequest):
    if _graph is None:
        raise HTTPException(status_code=503, detail="Graph still loading, try again shortly.")

    try:
        result = reroute_around_blockage(
            _graph,
            (req.current_lat, req.current_lon),
            (req.destination_lat, req.destination_lon),
            tuple(req.blocked_edge),
        )
    except nx.NetworkXNoPath:
        raise HTTPException(
            status_code=404,
            detail="No alternate route exists around the blocked location on the current road network."
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Could not compute a reroute: {e}")

    fleet_manager.log_event(
        event_type="OBSTACLE_DETECTED",
        vehicle_id=req.vehicle_id,
        title="Obstacle Encountered Ahead",
        message=f"{req.vehicle_id} detected blockage. Bypass computed (+{result.get('delay_added_s', 0)/60:.1f} min delay).",
        severity="warning",
    )
    fleet_manager.save_route_option_for_vehicle(
        req.vehicle_id,
        [tuple(point) for point in result["new_route_coords"]],
        result["new_eta_s"],
        label="RECOVERY",
    )

    return result


class StartTripRequest(BaseModel):
    vehicle_id: str
    planned_route_coords: list
    destination_lat: float
    destination_lon: float


@app.post("/api/trip/start")
def trip_start(req: StartTripRequest):
    monitoring.start_trip(
        req.vehicle_id,
        [tuple(c) for c in req.planned_route_coords],
        (req.destination_lat, req.destination_lon),
    )
    return {"vehicle_id": req.vehicle_id, "status": "monitoring_started"}


class PingRequest(BaseModel):
    vehicle_id: str
    lat: float
    lon: float


@app.post("/api/trip/ping")
def trip_ping(req: PingRequest):
    try:
        result = monitoring.process_ping(req.vehicle_id, req.lat, req.lon)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    fleet_manager.update_vehicle_position(req.vehicle_id, req.lat, req.lon)

    if result.get("alert"):
        fleet_manager.log_event(
            event_type="DEVIATION_ALERT",
            vehicle_id=req.vehicle_id,
            title="Deviation Confirmed",
            message=result["alert"]["message"],
            severity="danger",
        )

    return result


@app.get("/api/trip/alerts")
def trip_alerts(vehicle_id: Optional[str] = None):
    return {"alerts": monitoring.list_alerts(vehicle_id)}


class EndTripRequest(BaseModel):
    vehicle_id: str


@app.post("/api/trip/end")
def trip_end(req: EndTripRequest):
    ended = monitoring.end_trip(req.vehicle_id)
    v = fleet_manager.get_vehicle(req.vehicle_id)
    if v and v.status not in ["Available", "Standby"]:
        fleet_manager.complete_active_mission(req.vehicle_id)
    return {"vehicle_id": req.vehicle_id, "ended": ended}


@app.get("/api/health")
def health():
    return {"status": "ok", "graph_loaded": _graph is not None}


class AgentRecommendRequest(BaseModel):
    vehicle_id: str = "AMB-104"
    current_lat: float
    current_lon: float
    destination_lat: float
    destination_lon: float
    planned_route_coords: list = []
    blocked_edge: Optional[list] = None
    incident_type: str = "accident"
    current_speed_kmh: float = 0.0


@app.post("/api/agent/recommend")
def agent_recommend(req: AgentRecommendRequest):
    if _graph is None:
        raise HTTPException(status_code=503, detail="Graph still loading, try again shortly.")

    try:
        rec = decision_engine.analyze_incident_and_recommend(
            _graph,
            req.vehicle_id,
            (req.current_lat, req.current_lon),
            (req.destination_lat, req.destination_lon),
            [tuple(c) for c in req.planned_route_coords],
            blocked_edge=tuple(req.blocked_edge) if req.blocked_edge else None,
            incident_type=req.incident_type,
            current_speed_kmh=req.current_speed_kmh,
        )
        rec_dict = rec.to_dict()

        fleet_manager.save_recommendation(
            req.vehicle_id,
            req.incident_type,
            req.current_lat,
            req.current_lon,
            rec_dict,
        )

        fleet_manager.log_event(
            event_type="AGENT_RECOMMENDATION",
            vehicle_id=req.vehicle_id,
            title=f"AI Agent Evaluation ({int(rec.confidence_score * 100)}% Conf)",
            message=f"Cause: {rec.cause}. Delay: +{rec.delay_expected_s/60:.1f}m. Backup dispatch: {'YES' if rec.should_dispatch_backup else 'NO'}.",
            severity="warning" if rec.should_dispatch_backup or rec.has_deviated else "info",
        )

        return rec_dict
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Decision engine evaluation failed: {e}")


# =========================================================================
# Multi-Role Support Endpoints (Control Room, Driver HUD, Admin Overview)
# =========================================================================

@app.get("/api/fleet")
def get_fleet():
    """Returns the multi-vehicle fleet registry (Ambulance, Fire, Police)."""
    return {"vehicles": fleet_manager.list_vehicles()}


@app.get("/api/missions")
def get_missions():
    """Returns all missions."""
    return {"missions": fleet_manager.list_missions()}


@app.get("/api/missions/active/{vehicle_id}")
def get_active_mission(vehicle_id: str):
    """Driver view: returns the active assigned mission for this vehicle."""
    m = fleet_manager.get_active_mission_for_vehicle(vehicle_id)
    v = fleet_manager.get_vehicle(vehicle_id)
    if not m:
        return {
            "vehicle": asdict(v) if v else None,
            "has_active_mission": False,
            "mission": None,
        }
    return {
        "vehicle": asdict(v) if v else None,
        "has_active_mission": True,
        "mission": asdict(m),
    }


class MissionDecisionRequest(BaseModel):
    mission_id: str
    decision: str  # "approve" | "reject"
    action_type: str = "reroute"  # "reroute" | "backup"
    new_route_coords: Optional[list] = None
    new_eta_s: Optional[float] = None
    backup_vehicle_id: Optional[str] = None


@app.post("/api/missions/decision")
def mission_decision(req: MissionDecisionRequest):
    """
    Dispatcher workflow: approves or rejects a proposed rerouting or backup dispatch.
    """
    try:
        updated_mission = fleet_manager.apply_decision(
            mission_id=req.mission_id,
            decision=req.decision,
            new_route_coords=[tuple(c) for c in req.new_route_coords] if req.new_route_coords else None,
            new_eta_s=req.new_eta_s,
            backup_vehicle_id=req.backup_vehicle_id,
        )
        return {"status": "success", "mission": updated_mission}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


class DriverReportRequest(BaseModel):
    vehicle_id: str
    reason: str
    details: str
    lat: Optional[float] = None
    lon: Optional[float] = None


@app.post("/api/driver/report")
def driver_report(req: DriverReportRequest):
    """
    Driver view: emergency vehicle driver reports an on-scene route deviation / blockage.
    """
    pos = (req.lat, req.lon) if req.lat is not None and req.lon is not None else None
    res = fleet_manager.driver_report_deviation(
        vehicle_id=req.vehicle_id,
        reason=req.reason,
        details=req.details,
        location=pos,
    )
    return res


@app.get("/api/timeline")
def get_timeline():
    """Control Room operational audit timeline."""
    return {"timeline": fleet_manager.list_timeline()}


@app.get("/api/admin/overview")
def admin_overview():
    """
    Admin Dashboard: system telemetry, graph metrics, fleet breakdown, session states.
    """
    vehicles = fleet_manager.list_vehicles()
    status_counts = {}
    type_counts = {}
    for v in vehicles:
        status_counts[v["status"]] = status_counts.get(v["status"], 0) + 1
        type_counts[v["type"]] = type_counts.get(v["type"], 0) + 1

    graph_nodes = len(_graph.nodes) if _graph else 0
    graph_edges = len(_graph.edges) if _graph else 0
    uptime_s = int(time.time() - _start_time)

    return {
        "status": "HEALTHY",
        "uptime_seconds": uptime_s,
        "uptime_formatted": f"{uptime_s // 3600}h {(uptime_s % 3600) // 60}m {uptime_s % 60}s",
        "road_network": {
            "loaded": _graph is not None,
            "region": "Bengaluru Urban Center (9km coverage)",
            "nodes_count": graph_nodes,
            "edges_count": graph_edges,
            "weighting_model": "Emergency Arterial Penalty Matrix",
        },
        "fleet_summary": {
            "total_vehicles": len(vehicles),
            "by_type": type_counts,
            "by_status": status_counts,
        },
        "active_monitoring_sessions": len(monitoring._sessions),
        "total_missions": len(fleet_manager.list_missions()),
        "timeline_events_recorded": len(fleet_manager.list_timeline()),
    }


@app.post("/api/system/reset")
def system_reset():
    """Resets fleet vehicles, ends all monitoring sessions, and clears active missions."""
    global _graph
    if _graph is not None:
        graph_utils.add_emergency_weight(_graph)
    monitoring._sessions.clear()
    res = fleet_manager.reset_system_state()
    return res


@app.post("/api/system/reload_weights")
def reload_weights():
    """Recomputes road hierarchy emergency weights on the graph in-place."""
    global _graph
    if _graph is not None:
        graph_utils.add_emergency_weight(_graph)
    return {"status": "weights_reloaded"}
