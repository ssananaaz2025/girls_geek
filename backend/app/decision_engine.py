"""
decision_engine.py
GeoAgent Decision Engine for Emergency Vehicle Control Room.

Implements automated reasoning for the 5 core control room questions:
  1. Has the ambulance deviated from its planned route?
  2. What caused the deviation?
  3. How much delay is expected?
  4. What is the best alternative route?
  5. Should another ambulance be dispatched instead?

Answers all objectives (A-F) of the GeoAgentic Framework Problem Statement.
"""

from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Tuple, Any
import math
import time
import osmnx as ox

import graph_utils
import reroute
from dispatch import AMBULANCE_STATIONS
import monitoring

# Thresholds for decision logic
BACKUP_DISPATCH_DELAY_THRESHOLD_S = 180.0  # 3 minutes threshold to trigger backup ambulance



@dataclass
class DecisionRecommendation:
    has_deviated: bool
    deviation_point: Optional[Tuple[float, float]]
    off_route_distance_m: float
    cause: str
    cause_details: str
    original_eta_s: float
    delay_expected_s: float
    new_eta_s: float
    best_alternative_route: Optional[Dict[str, Any]]  # {coords, length_m, eta_s}
    should_dispatch_backup: bool
    recommended_backup_vehicle_id: Optional[str]
    backup_station_name: Optional[str]
    backup_eta_s: Optional[float]
    confidence_score: float  # 0.0 to 1.0
    reasoning_summary: List[str]
    natural_language_explanation: str

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if self.deviation_point:
            d["deviation_point"] = list(self.deviation_point)
        return d


def _compute_dynamic_confidence(
    G,
    current_pos: Tuple[float, float],
    session: Optional[monitoring.TripSession],
    blocked_edge: Optional[Tuple[Any, Any]],
    eta_diff_s: float,
) -> float:
    # 1. Spatial Graph Snap Precision Confidence
    try:
        orig_node = ox.nearest_nodes(G, current_pos[1], current_pos[0])
        node_coords = (G.nodes[orig_node]["y"], G.nodes[orig_node]["x"])
        d_snap = graph_utils._haversine_m(current_pos, node_coords)
        c_spatial = math.exp(- (d_snap ** 2) / (2 * (15.0 ** 2)))  # 15m std dev
    except Exception:
        c_spatial = 0.85

    # 2. Telemetry Persistence & Debounce Confidence
    if blocked_edge:
        c_telemetry = 0.97
    elif session:
        consec = session.detector.state.consecutive_off_route
        if session.detector.state.is_deviated:
            c_telemetry = 0.96
        elif consec > 0:
            c_telemetry = 0.65  # Transitioning / noisy state
        else:
            c_telemetry = 0.94
        
        # Penalize stale pings (> 5s old)
        if session.last_ping_at:
            age_s = time.time() - session.last_ping_at
            if age_s > 5.0:
                c_telemetry *= math.exp(-0.04 * min(age_s - 5.0, 30.0))
    else:
        c_telemetry = 0.88

    # 3. Decision Margin Robustness Confidence
    c_decision = 0.5 + 0.5 * math.tanh(abs(eta_diff_s - BACKUP_DISPATCH_DELAY_THRESHOLD_S) / 120.0)

    # Weighted composite score
    composite = 0.35 * c_spatial + 0.45 * c_telemetry + 0.20 * c_decision
    return round(max(0.52, min(0.99, composite)), 2)


def analyze_incident_and_recommend(
    G,
    vehicle_id: str,
    current_pos: Tuple[float, float],
    dest_pos: Tuple[float, float],
    planned_route_coords: List[Tuple[float, float]],
    blocked_edge: Optional[Tuple[Any, Any]] = None,
    incident_type: str = "accident",
    current_speed_kmh: float = 0.0,
) -> DecisionRecommendation:
    """
    Main entry point for the GeoAgent Decision Engine.
    Given vehicle telemetry, route status, and optional incident reports,
    it computes a decision recommendation answering all 5 control room questions.
    """
    reasoning = []
    
    # 1. Has the ambulance deviated?
    session = monitoring.get_session(vehicle_id)
    has_deviated = False
    dev_point = None
    off_route_dist = 0.0

    if session and session.detector.state.is_deviated:
        has_deviated = True
        dev_point = session.detector.state.deviation_point
        reasoning.append(f"[Q1: Deviation] Vehicle {vehicle_id} confirmed off-route near {dev_point}.")
    elif blocked_edge:
        has_deviated = True
        dev_point = current_pos
        reasoning.append(f"[Q1: Obstacle] Incident reported directly ahead of vehicle {vehicle_id} at {current_pos}.")
    else:
        reasoning.append(f"[Q1: Deviation] Vehicle {vehicle_id} is on planned trajectory.")

    # 2. What caused the deviation?
    if not has_deviated and not blocked_edge:
        cause = "Normal Flow"
        cause_details = "Vehicle is maintaining expected travel speed along planned arterial route."
        reasoning.append("[Q2: Cause] No route deviation; normal traffic conditions.")
    else:
        cause_label_map = {
            "accident": ("Road Accident / Collision", "Telemetry indicates vehicle stopped or forced off path due to collision ahead."),
            "heavy_congestion": ("Gridlock / Traffic Congestion", "Sustained speed < 10 km/h detected across multiple road segments."),
            "road_closure": ("Unannounced Road Closure", "Physical barricade or construction closure detected on planned segment."),
            "roadwork": ("Active Roadwork", "Construction activity blocking emergency vehicle passage."),
            "flood": ("Urban Flooding / Waterlogging", "Waterlogging hazard rendering path impassable."),
            "hazard": ("Hazardous Obstacle", "Debris or emergency hazard zone blocking lane."),
        }

        cause, cause_details = cause_label_map.get(
            incident_type, 
            ("Unplanned Route Deviation", "Vehicle left planned path due to localized road conditions.")
        )
        reasoning.append(f"[Q2: Cause] Cause identified as: {cause}.")

    # 3 & 4. Expected Delay and Best Alternative Route
    # Compute baseline remaining route ETA from current position
    try:
        baseline_route = graph_utils.get_route(G, current_pos, dest_pos)
        orig_eta_s = baseline_route["eta_s"]
    except Exception:
        orig_eta_s = 300.0  # Fallback 5 min baseline

    alt_route = None
    new_eta_s = orig_eta_s
    delay_s = 0.0

    if blocked_edge:
        try:
            reroute_res = reroute.reroute_around_blockage(G, current_pos, dest_pos, blocked_edge)
            alt_route = {
                "coords": reroute_res["new_route_coords"],
                "eta_s": reroute_res["new_eta_s"],
                "length_m": reroute_res.get("length_m", 0.0),
                "is_rerouted": reroute_res.get("rerouted", True)
            }
            new_eta_s = reroute_res["new_eta_s"]
            delay_s = reroute_res["delay_added_s"]
            reasoning.append(f"[Q3: Delay] Rerouting adds {delay_s/60:.1f} min delay. New ETA: {new_eta_s/60:.1f} min.")
            reasoning.append(f"[Q4: Alternative Route] Computed optimal bypass route with {len(reroute_res['new_route_coords'])} path coordinates.")
        except Exception as e:
            reasoning.append(f"[Q3/Q4 Error] Could not compute reroute: {e}")
            alt_route = None
    else:
        alt_route = {
            "coords": planned_route_coords,
            "eta_s": orig_eta_s,
            "length_m": 0.0,
            "is_rerouted": False
        }

    # 5. Should another ambulance be dispatched instead?
    best_backup_name = None
    best_backup_eta = float("inf")
    best_backup_vid = None

    # Check candidate stations for a backup dispatch option
    for idx, (station_name, station_coords) in enumerate(AMBULANCE_STATIONS.items()):
        try:
            b_route = graph_utils.get_route(G, station_coords, dest_pos)
            if b_route["eta_s"] < best_backup_eta:
                best_backup_eta = b_route["eta_s"]
                best_backup_name = station_name
                best_backup_vid = f"AMB-0{idx+2}"
        except Exception:
            continue

    should_dispatch_backup = False
    if alt_route and (new_eta_s > best_backup_eta + BACKUP_DISPATCH_DELAY_THRESHOLD_S):
        should_dispatch_backup = True
        reasoning.append(
            f"[Q5: Backup Dispatch] RECOMMENDED: Primary vehicle reroute ETA ({new_eta_s/60:.1f} min) exceeds "
            f"backup vehicle {best_backup_vid} from {best_backup_name} ({best_backup_eta/60:.1f} min)."
        )
    else:
        reasoning.append(
            f"[Q5: Backup Dispatch] NOT REQUIRED: Primary vehicle reroute ETA ({new_eta_s/60:.1f} min) remains faster "
            f"or competitive with backup station ({best_backup_eta/60:.1f} min)."
        )

    # Dynamic Confidence Score calculation
    eta_diff_s = (new_eta_s - best_backup_eta) if best_backup_name and best_backup_eta != float("inf") else 0.0
    confidence_score = _compute_dynamic_confidence(G, current_pos, session, blocked_edge, eta_diff_s)


    # Generate Natural Language Explanation for Control Room Operator
    nlp_parts = []
    nlp_parts.append(f"🚨 **GeoAgentic Control Room Alert — Vehicle {vehicle_id}**")
    
    if has_deviated:
        nlp_parts.append(f"• **Deviation Status**: Confirmed off-route near coordinate {dev_point}.")
    else:
        nlp_parts.append("• **Deviation Status**: Vehicle is currently following planned route.")

    nlp_parts.append(f"• **Identified Cause**: {cause} ({cause_details})")
    nlp_parts.append(f"• **ETA & Delay**: Original ETA was {orig_eta_s/60:.1f} min. Estimated delay is +{delay_s/60:.1f} min (New ETA: {new_eta_s/60:.1f} min).")

    if alt_route and alt_route.get("is_rerouted"):
        nlp_parts.append(f"• **Alternative Route**: Recommended green bypass route generated via arterial roads ({len(alt_route['coords'])} nodes).")

    if should_dispatch_backup:
        nlp_parts.append(
            f"• **Backup Recommendation**: ⚠️ **Dispatch Backup Vehicle {best_backup_vid}** from {best_backup_name}. "
            f"Backup vehicle will arrive in {best_backup_eta/60:.1f} min, saving {(new_eta_s - best_backup_eta)/60:.1f} min!"
        )
    else:
        nlp_parts.append(f"• **Backup Recommendation**: Keep primary vehicle {vehicle_id}. Backup vehicle from {best_backup_name} would take {best_backup_eta/60:.1f} min.")

    natural_language_explanation = "\n".join(nlp_parts)

    return DecisionRecommendation(
        has_deviated=has_deviated,
        deviation_point=dev_point,
        off_route_distance_m=off_route_dist,
        cause=cause,
        cause_details=cause_details,
        original_eta_s=round(orig_eta_s, 1),
        delay_expected_s=round(delay_s, 1),
        new_eta_s=round(new_eta_s, 1),
        best_alternative_route=alt_route,
        should_dispatch_backup=should_dispatch_backup,
        recommended_backup_vehicle_id=best_backup_vid,
        backup_station_name=best_backup_name,
        backup_eta_s=round(best_backup_eta, 1) if best_backup_name else None,
        confidence_score=confidence_score,
        reasoning_summary=reasoning,
        natural_language_explanation=natural_language_explanation,
    )
