"""
fleet_manager.py
Database-backed management for multi-vehicle fleet, active dispatch missions,
and operational timeline audit logging across Control Room, Driver, and Admin.
"""

import time
from dataclasses import asdict, dataclass
from math import asin, cos, isfinite, radians, sin, sqrt
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from sqlalchemy import select

try:
    from app.db import (
        Base,
        IncidentModel,
        MissionModel,
        RecommendationModel,
        RouteOptionModel,
        SessionLocal,
        TimelineEventModel,
        VehicleModel,
        engine,
    )
except ImportError:  # pragma: no cover - fallback for direct script execution
    from db import (
        Base,
        IncidentModel,
        MissionModel,
        RecommendationModel,
        RouteOptionModel,
        SessionLocal,
        TimelineEventModel,
        VehicleModel,
        engine,
    )


@dataclass
class FleetVehicle:
    id: str
    type: str
    callsign: str
    driver_name: str
    driver_contact: str
    status: str
    location: Tuple[float, float]
    battery_fuel_pct: int
    active_mission_id: Optional[str] = None


@dataclass
class TimelineEvent:
    id: str
    timestamp: float
    time_str: str
    event_type: str
    vehicle_id: str
    title: str
    message: str
    severity: str


@dataclass
class Mission:
    id: str
    vehicle_id: str
    vehicle_type: str
    priority: str
    source: str
    destination: str
    source_coords: Tuple[float, float]
    dest_coords: Tuple[float, float]
    status: str
    created_at: float
    eta_s: float
    delay_s: float
    active_route_coords: List[Tuple[float, float]]
    latest_recommendation: Optional[Dict[str, Any]] = None
    decision_status: str = "none"
    driver_reported_incident: Optional[str] = None


_DEFAULT_VEHICLES = [
    {
        "id": "AMB-104",
        "type": "ambulance",
        "callsign": "Advanced Life Support 104",
        "driver_name": "Rajesh Kumar",
        "driver_contact": "+91 98450 12044",
        "status": "Available",
        "location": (12.9352, 77.6146),
        "battery_fuel_pct": 88,
    },
    {
        "id": "AMB-108",
        "type": "ambulance",
        "callsign": "Rapid Paramedic Unit 108",
        "driver_name": "Anita Sharma",
        "driver_contact": "+91 98450 12088",
        "status": "Available",
        "location": (12.9279, 77.6271),
        "battery_fuel_pct": 94,
    },
    {
        "id": "FIRE-201",
        "type": "fire_engine",
        "callsign": "Heavy Water Tender 201",
        "driver_name": "Vikram Singh",
        "driver_contact": "+91 98450 22011",
        "status": "Available",
        "location": (12.9250, 77.5850),
        "battery_fuel_pct": 79,
    },
    {
        "id": "FIRE-202",
        "type": "fire_engine",
        "callsign": "Rapid Rescue Tender 202",
        "driver_name": "Karthik R.",
        "driver_contact": "+91 98450 22022",
        "status": "Available",
        "location": (12.9165, 77.6101),
        "battery_fuel_pct": 85,
    },
    {
        "id": "POL-301",
        "type": "police",
        "callsign": "Traffic Interceptor 301",
        "driver_name": "Insp. Suresh Patel",
        "driver_contact": "+91 98450 33011",
        "status": "Available",
        "location": (12.9220, 77.6180),
        "battery_fuel_pct": 92,
    },
    {
        "id": "POL-302",
        "type": "police",
        "callsign": "Emergency Rapid Patrol 302",
        "driver_name": "SI Priya Mukherjee",
        "driver_contact": "+91 98450 33022",
        "status": "Available",
        "location": (12.9345, 77.6101),
        "battery_fuel_pct": 76,
    },
]

_mission_counter = 100


class ActiveMissionConflict(ValueError):
    """Raised when dispatch is attempted for a vehicle already on an active mission."""


def initialize() -> None:
    """Create required tables and seed the default fleet if missing."""
    global _mission_counter
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as session:
        if session.query(VehicleModel).count() == 0:
            for raw in _DEFAULT_VEHICLES:
                session.add(
                    VehicleModel(
                        id=raw["id"],
                        callsign=raw["callsign"],
                        vehicle_type=raw["type"],
                        status=raw["status"],
                        lat=raw["location"][0],
                        lng=raw["location"][1],
                        speed_kmh=45,
                        base_speed_kmh=45,
                        created_at=time.time(),
                        driver_name=raw["driver_name"],
                        driver_contact=raw["driver_contact"],
                        battery_fuel_pct=raw["battery_fuel_pct"],
                    )
                )
        else:
            for raw in _DEFAULT_VEHICLES:
                vehicle = session.query(VehicleModel).filter_by(id=raw["id"]).first()
                if vehicle:
                    vehicle.driver_name = raw["driver_name"]
                    vehicle.driver_contact = raw["driver_contact"]
                    vehicle.battery_fuel_pct = raw["battery_fuel_pct"]

        if session.query(TimelineEventModel).count() == 0:
            session.add(
                TimelineEventModel(
                    id="evt-system-boot",
                    vehicle_id=None,
                    event_type="SYSTEM_BOOT",
                    title="Control System Operational",
                    message="GeoAgentic Multi-Role Dashboard initialized with Bengaluru OSM network.",
                    timestamp=time.time(),
                    time_str=time.strftime("%H:%M:%S", time.localtime()),
                    severity="info",
                )
            )

        session.commit()

    _mission_counter = max(_mission_counter, _next_mission_number())


def _next_mission_number() -> int:
    with SessionLocal() as session:
        rows = session.execute(
            select(MissionModel.id).where(MissionModel.id.like("MSN-%"))
        ).all()
    if not rows:
        return 100

    highest = 0
    for (mission_id,) in rows:
        try:
            highest = max(highest, int(mission_id.split("-")[-1]))
        except (TypeError, ValueError):
            continue
    return highest


def _vehicle_from_model(model: VehicleModel) -> FleetVehicle:
    return FleetVehicle(
        id=model.id,
        type=model.vehicle_type,
        callsign=model.callsign,
        driver_name=model.driver_name,
        driver_contact=model.driver_contact,
        status=model.status,
        location=(float(model.lat), float(model.lng)),
        battery_fuel_pct=int(model.battery_fuel_pct),
        active_mission_id=model.current_mission_id,
    )


def _mission_from_model(model: MissionModel) -> Mission:
    source_coords = tuple(model.source_coords) if isinstance(model.source_coords, (list, tuple)) else (0.0, 0.0)
    dest_coords = tuple(model.dest_coords) if isinstance(model.dest_coords, (list, tuple)) else (0.0, 0.0)
    route_coords = model.active_route_coords or []
    if isinstance(route_coords, list):
        active_route_coords = [tuple(point) for point in route_coords]
    else:
        active_route_coords = []

    return Mission(
        id=model.id,
        vehicle_id=model.vehicle_id,
        vehicle_type=model.vehicle_type or "ambulance",
        priority=model.priority,
        source=model.source or "",
        destination=model.destination or "",
        source_coords=source_coords,
        dest_coords=dest_coords,
        status=model.status,
        created_at=float(model.created_at),
        eta_s=float(model.eta_s),
        delay_s=float(model.delay_s),
        active_route_coords=active_route_coords,
        latest_recommendation=model.latest_recommendation,
        decision_status=model.decision_status,
        driver_reported_incident=model.driver_reported_incident,
    )


def _timeline_from_model(model: TimelineEventModel) -> TimelineEvent:
    return TimelineEvent(
        id=model.id,
        timestamp=float(model.timestamp),
        time_str=model.time_str,
        event_type=model.event_type,
        vehicle_id=model.vehicle_id or "SYSTEM",
        title=model.title,
        message=model.message,
        severity=model.severity,
    )


def log_event(
    event_type: str,
    vehicle_id: str,
    title: str,
    message: str,
    severity: str = "info",
    mission_id: Optional[str] = None,
) -> TimelineEvent:
    now = time.time()
    t_str = time.strftime("%H:%M:%S", time.localtime(now))
    event_id = f"evt-{uuid4().hex}"

    with SessionLocal() as session:
        vehicle = session.query(VehicleModel).filter_by(id=vehicle_id).first()
        mission = session.query(MissionModel).filter_by(id=mission_id).first() if mission_id else None
        if mission is None and vehicle and vehicle.current_mission_id:
            mission = session.query(MissionModel).filter_by(id=vehicle.current_mission_id).first()
        if mission is None and vehicle:
            mission = (
                session.query(MissionModel).filter_by(id=vehicle.current_mission_id).first()
                if vehicle.current_mission_id
                else None
            )
            if mission is None:
                mission = (
                    session.query(MissionModel)
                    .filter_by(vehicle_id=vehicle_id)
                    .filter(MissionModel.status.in_(["Dispatched", "En Route", "Deviated", "Rerouted", "Backup Dispatched"]))
                    .order_by(MissionModel.created_at.desc())
                    .first()
                )
        record = TimelineEventModel(
            id=event_id,
            mission_id=mission.id if mission else None,
            vehicle_id=vehicle.id if vehicle else None,
            event_type=event_type,
            message=message,
            timestamp=now,
            title=title,
            time_str=t_str,
            severity=severity,
        )
        session.add(record)
        session.commit()

    return TimelineEvent(
        id=event_id,
        timestamp=now,
        time_str=t_str,
        event_type=event_type,
        vehicle_id=vehicle_id,
        title=title,
        message=message,
        severity=severity,
    )


def list_vehicles() -> List[Dict[str, Any]]:
    with SessionLocal() as session:
        rows = session.query(VehicleModel).all()

    out = []
    for row in rows:
        vehicle = _vehicle_from_model(row)
        data = asdict(vehicle)
        data["location"] = list(vehicle.location)
        out.append(data)
    return out


def get_vehicle(vehicle_id: str) -> Optional[FleetVehicle]:
    with SessionLocal() as session:
        row = session.query(VehicleModel).filter_by(id=vehicle_id).first()
    if row is None:
        return None
    return _vehicle_from_model(row)


def update_vehicle_position(vehicle_id: str, lat: float, lon: float):
    with SessionLocal() as session:
        row = session.query(VehicleModel).filter_by(id=vehicle_id).first()
        if row is None:
            return
        row.lat = float(lat)
        row.lng = float(lon)
        session.commit()


def update_vehicle_status(vehicle_id: str, status: str, active_mission_id: Optional[str] = None) -> None:
    with SessionLocal() as session:
        vehicle = session.query(VehicleModel).filter_by(id=vehicle_id).first()
        if vehicle is None:
            return
        vehicle.status = status
        vehicle.current_mission_id = active_mission_id
        if active_mission_id is None:
            vehicle.active_route_id = None
        session.commit()


def complete_active_mission(vehicle_id: str) -> None:
    now = time.time()
    with SessionLocal() as session:
        vehicle = session.query(VehicleModel).filter_by(id=vehicle_id).first()
        if vehicle is None:
            return
        mission = (
            session.query(MissionModel).filter_by(id=vehicle.current_mission_id).first()
            if vehicle.current_mission_id
            else None
        )
        if mission is None:
            mission = (
                session.query(MissionModel)
                .filter_by(vehicle_id=vehicle_id)
                .filter(MissionModel.status.in_(["Dispatched", "En Route", "Deviated", "Rerouted", "Backup Dispatched"]))
                .order_by(MissionModel.created_at.desc())
                .first()
            )
        if mission:
            mission.status = "completed"
            original_vehicle = session.query(VehicleModel).filter_by(id=mission.vehicle_id).first()
            if original_vehicle:
                original_vehicle.status = "Available"
                original_vehicle.current_mission_id = None
                original_vehicle.active_route_id = None
            if vehicle.active_route_id:
                active_route = session.query(RouteOptionModel).filter_by(id=vehicle.active_route_id).first()
                if active_route:
                    active_route.status = "available"
            session.add(
                TimelineEventModel(
                    id=f"evt-{uuid4().hex}",
                    mission_id=mission.id,
                    vehicle_id=vehicle_id,
                    event_type="ARRIVAL",
                    title="Trip Completed",
                    message=f"{vehicle_id} completed mission {mission.id}.",
                    timestamp=now,
                    time_str=time.strftime("%H:%M:%S", time.localtime(now)),
                    severity="success",
                )
            )
        vehicle.status = "Available"
        vehicle.current_mission_id = None
        vehicle.active_route_id = None
        session.commit()


def _distance_km(polyline: List[Tuple[float, float]]) -> float:
    distance = 0.0
    for first, second in zip(polyline, polyline[1:]):
        lat1, lon1 = map(radians, first)
        lat2, lon2 = map(radians, second)
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        value = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
        distance += 6371.0 * 2 * asin(sqrt(value))
    return distance


def _save_route_option(
    session,
    mission: MissionModel,
    vehicle: Optional[VehicleModel],
    route_coords: List[Tuple[float, float]],
    eta_s: float,
    label: str,
    is_mock: bool = False,
    activate: bool = True,
) -> RouteOptionModel:
    if activate and vehicle and vehicle.active_route_id:
        previous = session.query(RouteOptionModel).filter_by(id=vehicle.active_route_id).first()
        if previous:
            previous.status = "available"

    polyline = [[float(lat), float(lon)] for lat, lon in route_coords]
    option = RouteOptionModel(
        id=f"route-{uuid4().hex}",
        mission_id=mission.id,
        label=label,
        polyline=polyline,
        distance_km=_distance_km(route_coords),
        base_eta_min=max(0.0, float(eta_s)) / 60.0,
        status="active" if activate else "available",
        is_mock=is_mock,
    )
    session.add(option)
    if activate:
        mission.active_route_coords = polyline
        mission.eta_s = float(eta_s)
    if activate and vehicle:
        vehicle.active_route_id = option.id
    return option


def save_route_option_for_vehicle(
    vehicle_id: str,
    route_coords: List[Tuple[float, float]],
    eta_s: float,
    label: str = "RECOVERY",
    is_mock: bool = False,
) -> Optional[str]:
    with SessionLocal() as session:
        vehicle = session.query(VehicleModel).filter_by(id=vehicle_id).first()
        if vehicle is None:
            return None
        mission = (
            session.query(MissionModel).filter_by(id=vehicle.current_mission_id).first()
            if vehicle.current_mission_id
            else None
        )
        if mission is None:
            mission = (
                session.query(MissionModel)
                .filter_by(vehicle_id=vehicle_id)
                .filter(MissionModel.status.in_(["Dispatched", "En Route", "Deviated", "Rerouted", "Backup Dispatched"]))
                .order_by(MissionModel.created_at.desc())
                .first()
            )
        if mission is None:
            return None
        option = _save_route_option(
            session, mission, vehicle, route_coords, eta_s, label, is_mock, activate=False
        )
        session.commit()
        return option.id


def create_mission(
    vehicle_id: str,
    source: str,
    destination: str,
    source_coords: Tuple[float, float],
    dest_coords: Tuple[float, float],
    route_coords: List[Tuple[float, float]],
    eta_s: float,
    priority: str = "Critical / Code Red",
) -> Mission:
    global _mission_counter
    _mission_counter += 1
    mission_id = f"MSN-{_mission_counter}"

    with SessionLocal() as session:
        vehicle = session.query(VehicleModel).filter_by(id=vehicle_id).first()
        if vehicle is None:
            raise ValueError(f"Vehicle {vehicle_id} not found")
        if vehicle.current_mission_id:
            current_mission = session.query(MissionModel).filter_by(id=vehicle.current_mission_id).first()
            if current_mission and current_mission.status in {
                "Dispatched", "En Route", "Deviated", "Rerouted", "Backup Dispatched"
            }:
                raise ActiveMissionConflict(f"Vehicle {vehicle_id} already has an active mission")
        v_type = vehicle.vehicle_type

        mission = MissionModel(
            id=mission_id,
            vehicle_id=vehicle_id,
            mission_type="emergency_response",
            origin_lat=float(source_coords[0]),
            origin_lng=float(source_coords[1]),
            dest_lat=float(dest_coords[0]),
            dest_lng=float(dest_coords[1]),
            vehicle_type=v_type,
            priority=priority,
            source=source,
            destination=destination,
            source_coords=[float(source_coords[0]), float(source_coords[1])],
            dest_coords=[float(dest_coords[0]), float(dest_coords[1])],
            status="En Route",
            created_at=time.time(),
            eta_s=float(eta_s),
            delay_s=0.0,
            active_route_coords=[[float(lat), float(lon)] for lat, lon in route_coords],
            latest_recommendation=None,
            decision_status="none",
            driver_reported_incident=None,
        )
        session.add(mission)
        session.flush()
        _save_route_option(
            session,
            mission,
            vehicle,
            route_coords,
            eta_s,
            "A",
            is_mock=False,
        )

        vehicle.status = "En Route"
        vehicle.current_mission_id = mission_id

        session.commit()

    mission_obj = Mission(
        id=mission_id,
        vehicle_id=vehicle_id,
        vehicle_type=v_type,
        priority=priority,
        source=source,
        destination=destination,
        source_coords=source_coords,
        dest_coords=dest_coords,
        status="En Route",
        created_at=time.time(),
        eta_s=float(eta_s),
        delay_s=0.0,
        active_route_coords=list(route_coords),
        latest_recommendation=None,
        decision_status="none",
        driver_reported_incident=None,
    )
    log_event(
        event_type="MISSION_CREATED",
        vehicle_id=vehicle_id,
        title=f"Mission {mission_id} Dispatched",
        message=f"{vehicle_id} ({v_type.capitalize()}) dispatched from {source} to {destination}. ETA: {eta_s/60:.1f} min.",
        severity="info",
    )
    return mission_obj


def get_mission(mission_id: str) -> Optional[Mission]:
    with SessionLocal() as session:
        row = session.query(MissionModel).filter_by(id=mission_id).first()
    if row is None:
        return None
    return _mission_from_model(row)


def get_active_mission_for_vehicle(vehicle_id: str) -> Optional[Mission]:
    with SessionLocal() as session:
        vehicle = session.query(VehicleModel).filter_by(id=vehicle_id).first()
        if vehicle and vehicle.current_mission_id:
            current_mission = session.query(MissionModel).filter_by(id=vehicle.current_mission_id).first()
            if current_mission and current_mission.status in {
                "Dispatched", "En Route", "Deviated", "Rerouted", "Backup Dispatched"
            }:
                return _mission_from_model(current_mission)
        row = (
            session.query(MissionModel)
            .filter_by(vehicle_id=vehicle_id)
            .filter(MissionModel.status.in_(["Dispatched", "En Route", "Deviated", "Rerouted", "Backup Dispatched"]))
            .order_by(MissionModel.created_at.desc())
            .first()
        )
    if row is None:
        return None
    return _mission_from_model(row)


def list_missions() -> List[Dict[str, Any]]:
    with SessionLocal() as session:
        rows = session.query(MissionModel).order_by(MissionModel.created_at.desc()).all()

    out = []
    for row in rows:
        mission = _mission_from_model(row)
        data = asdict(mission)
        data["source_coords"] = list(mission.source_coords)
        data["dest_coords"] = list(mission.dest_coords)
        data["active_route_coords"] = [list(coord) for coord in mission.active_route_coords]
        out.append(data)
    return out


def list_timeline() -> List[Dict[str, Any]]:
    with SessionLocal() as session:
        rows = session.query(TimelineEventModel).order_by(TimelineEventModel.timestamp.desc()).all()
    return [asdict(_timeline_from_model(row)) for row in rows]


def apply_decision(
    mission_id: str,
    decision: str,
    new_route_coords: Optional[List[Tuple[float, float]]] = None,
    backup_vehicle_id: Optional[str] = None,
    new_eta_s: Optional[float] = None,
) -> Dict[str, Any]:
    if decision not in {"approve", "reject"}:
        raise ValueError("Decision must be 'approve' or 'reject'")
    if new_eta_s is not None and (not isfinite(new_eta_s) or new_eta_s < 0):
        raise ValueError("ETA must be a finite, non-negative number")

    event_details = None
    with SessionLocal() as session:
        mission_row = session.query(MissionModel).filter_by(id=mission_id).first()
        if mission_row is None:
            raise ValueError(f"Mission {mission_id} not found")

        vehicle_row = session.query(VehicleModel).filter_by(id=mission_row.vehicle_id).first()
        recommendation = (
            session.query(RecommendationModel)
            .filter_by(mission_id=mission_id, status="pending")
            .order_by(RecommendationModel.created_at.desc())
            .first()
        )

        if decision == "approve":
            mission_row.decision_status = "approved"
            if recommendation:
                recommendation.status = "applied"
            if new_route_coords:
                mission_row.status = "Rerouted"
                if vehicle_row:
                    vehicle_row.status = "Rerouted"
                route_option = _save_route_option(
                    session,
                    mission_row,
                    vehicle_row,
                    new_route_coords,
                    mission_row.eta_s if new_eta_s is None else new_eta_s,
                    "RECOVERY",
                    is_mock=False,
                )
                session.flush()
                if recommendation:
                    recommendation.recommended_route_id = route_option.id
                event_details = (
                    mission_row.vehicle_id,
                    "Reroute Plan Approved",
                    f"Dispatcher approved alternative route for {mission_row.vehicle_id}. Active trajectory updated.",
                    "success",
                )
            elif backup_vehicle_id:
                backup_row = session.query(VehicleModel).filter_by(id=backup_vehicle_id).first()
                if backup_row is None:
                    raise ValueError(f"Backup vehicle {backup_vehicle_id} not found")
                mission_row.status = "Backup Dispatched"
                backup_row.status = "En Route"
                backup_row.current_mission_id = mission_id
                backup_row.active_route_id = vehicle_row.active_route_id if vehicle_row else None
                event_details = (
                    backup_vehicle_id,
                    f"Backup Dispatch Approved ({backup_vehicle_id})",
                    f"Dispatcher approved backup vehicle {backup_vehicle_id} to relieve {mission_row.vehicle_id}.",
                    "warning",
                )
        else:
            mission_row.decision_status = "rejected"
            if recommendation:
                recommendation.status = "rejected"
            event_details = (
                mission_row.vehicle_id,
                "Recommendation Rejected",
                f"Dispatcher rejected proposed change for {mission_row.vehicle_id}. Vehicle instructed to hold primary route.",
                "danger",
            )

        session.commit()
        result = asdict(_mission_from_model(mission_row))

    event_type = "DECISION_APPROVED" if decision == "approve" else "DECISION_REJECTED"
    if event_details:
        log_event(event_type, *event_details, mission_id=mission_id)
    return result


def driver_report_deviation(
    vehicle_id: str,
    reason: str,
    details: str,
    location: Optional[Tuple[float, float]] = None,
) -> Dict[str, Any]:
    with SessionLocal() as session:
        vehicle = session.query(VehicleModel).filter_by(id=vehicle_id).first()
        if vehicle:
            vehicle.status = "Deviated"
            if location is not None:
                vehicle.lat = float(location[0])
                vehicle.lng = float(location[1])

        mission_row = (
            session.query(MissionModel).filter_by(id=vehicle.current_mission_id).first()
            if vehicle and vehicle.current_mission_id
            else None
        )
        if mission_row is None:
            mission_row = (
                session.query(MissionModel)
                .filter_by(vehicle_id=vehicle_id)
                .filter(MissionModel.status.in_(["Dispatched", "En Route", "Deviated", "Rerouted", "Backup Dispatched"]))
                .order_by(MissionModel.created_at.desc())
                .first()
            )
        if mission_row:
            mission_row.status = "Deviated"
            mission_row.driver_reported_incident = f"{reason}: {details}"

        incident = IncidentModel(
            id=f"incident-{uuid4().hex}",
            mission_id=mission_row.id if mission_row else None,
            incident_type=reason.strip().lower().replace(" ", "_"),
            lat=float(location[0]) if location else (float(vehicle.lat) if vehicle else None),
            lng=float(location[1]) if location else (float(vehicle.lng) if vehicle else None),
            severity="high",
            status="active",
            affected_route_id=vehicle.active_route_id if vehicle else None,
            created_at=time.time(),
        )
        session.add(incident)

        event = TimelineEventModel(
            id=f"evt-{uuid4().hex}",
            mission_id=mission_row.id if mission_row else None,
            vehicle_id=vehicle.id if vehicle else None,
            event_type="DRIVER_DEVIATION_REPORT",
            title=f"Driver Alert: {reason}",
            message=f"Driver of {vehicle_id} ({vehicle.callsign if vehicle else 'Driver'}) reported obstacle: {details}",
            timestamp=time.time(),
            time_str=time.strftime("%H:%M:%S", time.localtime()),
            severity="danger",
        )
        session.add(event)
        session.commit()

    return {"status": "reported", "event": asdict(_timeline_from_model(event))}


def save_recommendation(
    vehicle_id: str,
    incident_type: str,
    lat: float,
    lng: float,
    recommendation: Dict[str, Any],
) -> Optional[str]:
    with SessionLocal() as session:
        vehicle = session.query(VehicleModel).filter_by(id=vehicle_id).first()
        if vehicle is None:
            return None
        mission = (
            session.query(MissionModel).filter_by(id=vehicle.current_mission_id).first()
            if vehicle.current_mission_id
            else None
        )
        if mission is None:
            mission = (
                session.query(MissionModel)
                .filter_by(vehicle_id=vehicle_id)
                .filter(MissionModel.status.in_(["Dispatched", "En Route", "Deviated", "Rerouted", "Backup Dispatched"]))
                .order_by(MissionModel.created_at.desc())
                .first()
            )
        if mission is None:
            return None

        incident = IncidentModel(
            id=f"incident-{uuid4().hex}",
            mission_id=mission.id,
            incident_type=incident_type.strip().lower().replace(" ", "_"),
            lat=float(lat),
            lng=float(lng),
            severity="high",
            status="active",
            affected_route_id=vehicle.active_route_id,
            created_at=time.time(),
        )
        session.add(incident)
        session.flush()

        alternative = recommendation.get("best_alternative_route")
        action = (
            "dispatch_backup"
            if recommendation.get("should_dispatch_backup")
            else "reroute"
            if alternative
            else "hold"
        )
        recommended_route_id = None
        if alternative and alternative.get("coords"):
            route = _save_route_option(
                session,
                mission,
                vehicle,
                [tuple(point) for point in alternative["coords"]],
                float(alternative.get("eta_s", mission.eta_s)),
                "RECOVERY",
                is_mock=False,
                activate=False,
            )
            session.flush()
            recommended_route_id = route.id

        session.add(
            RecommendationModel(
                id=f"recommendation-{uuid4().hex}",
                mission_id=mission.id,
                incident_id=incident.id,
                recommended_route_id=recommended_route_id,
                action=action,
                reason=str(recommendation.get("natural_language_explanation", "")),
                confidence=float(recommendation.get("confidence_score", 0.7)),
                extra=recommendation,
                status="pending",
                created_at=time.time(),
            )
        )
        mission.latest_recommendation = recommendation
        mission.decision_status = "pending"
        session.commit()
        return incident.id


def reset_system_state() -> Dict[str, Any]:
    """Resets all vehicles to Available, clears active missions and timeline."""
    global _mission_counter
    with SessionLocal() as session:
        for vehicle in session.query(VehicleModel).all():
            vehicle.status = "Available"
            vehicle.current_mission_id = None
            vehicle.active_route_id = None
        session.flush()
        session.query(RecommendationModel).delete()
        session.query(IncidentModel).delete()
        session.query(RouteOptionModel).delete()
        session.query(TimelineEventModel).delete()
        session.query(MissionModel).delete()
        session.commit()

    _mission_counter = 100
    log_event(
        event_type="SYSTEM_RESET",
        vehicle_id="SYSTEM",
        title="System Reset Executed",
        message="All fleet vehicles returned to Available. Active missions and sessions cleared.",
        severity="info",
    )
    return {"status": "system_reset_complete"}


initialize()
