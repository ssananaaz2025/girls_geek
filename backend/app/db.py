import os
import time
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import (
    Boolean,
    Column,
    Float,
    ForeignKey,
    Index,
    JSON,
    String,
    create_engine,
    event,
    inspect,
)
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.schema import CreateIndex, CreateTable

backend_dir = Path(__file__).resolve().parent.parent
load_dotenv(backend_dir / ".env")

Base = declarative_base()


def _resolve_database_url() -> str:
    configured = os.getenv("DATABASE_URL")
    if configured:
        if configured.startswith("sqlite:///") and not configured.startswith("sqlite:////"):
            path = Path(configured.replace("sqlite:///", "", 1))
            if not path.is_absolute():
                return f"sqlite:///{(backend_dir / path).as_posix()}"
        return configured
    return f"sqlite:///{(backend_dir / 'geoagentic.db').as_posix()}"


DATABASE_URL = _resolve_database_url()
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
    future=True,
)
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


class VehicleModel(Base):
    __tablename__ = "vehicles"

    id = Column(String, primary_key=True)
    callsign = Column(String, unique=True, nullable=False)
    vehicle_type = Column(String, nullable=False)
    status = Column(String, nullable=False, default="idle")
    lat = Column(Float, nullable=False)
    lng = Column(Float, nullable=False)
    heading_deg = Column(Float, default=0)
    speed_kmh = Column(Float, default=45)
    base_speed_kmh = Column(Float, default=45)
    current_mission_id = Column(String, ForeignKey("missions.id"), nullable=True)
    active_route_id = Column(String, ForeignKey("route_options.id"), nullable=True)
    route_progress = Column(Float, default=0)
    off_route = Column(Boolean, default=False)
    deviation_m = Column(Float, default=0)
    created_at = Column(Float, default=time.time)
    driver_name = Column(String, nullable=False, default="")
    driver_contact = Column(String, nullable=False, default="")
    battery_fuel_pct = Column(Float, nullable=False, default=100)


class MissionModel(Base):
    __tablename__ = "missions"

    id = Column(String, primary_key=True)
    vehicle_id = Column(String, ForeignKey("vehicles.id"), nullable=False)
    mission_type = Column(String, default="emergency_response")
    origin_lat = Column(Float, nullable=False)
    origin_lng = Column(Float, nullable=False)
    dest_lat = Column(Float, nullable=False)
    dest_lng = Column(Float, nullable=False)
    status = Column(String, default="active")
    created_at = Column(Float, default=time.time)
    vehicle_type = Column(String, nullable=True)
    priority = Column(String, default="Critical / Code Red")
    source = Column(String, nullable=True)
    destination = Column(String, nullable=True)
    source_coords = Column(JSON, nullable=True)
    dest_coords = Column(JSON, nullable=True)
    active_route_coords = Column(JSON, nullable=True)
    eta_s = Column(Float, default=0)
    delay_s = Column(Float, default=0)
    latest_recommendation = Column(JSON, nullable=True)
    decision_status = Column(String, default="none")
    driver_reported_incident = Column(String, nullable=True)


class RouteOptionModel(Base):
    __tablename__ = "route_options"

    id = Column(String, primary_key=True)
    mission_id = Column(String, ForeignKey("missions.id"), nullable=False)
    label = Column(String, nullable=False)
    polyline = Column(JSON, nullable=False)
    distance_km = Column(Float, nullable=False)
    base_eta_min = Column(Float, nullable=False)
    delay_min = Column(Float, default=0)
    risk_score = Column(Float, default=0)
    status = Column(String, default="available")
    is_mock = Column(Boolean, default=True)


class IncidentModel(Base):
    __tablename__ = "incidents"

    id = Column(String, primary_key=True)
    mission_id = Column(String, ForeignKey("missions.id"), nullable=True)
    incident_type = Column(String, nullable=False)
    lat = Column(Float, nullable=True)
    lng = Column(Float, nullable=True)
    radius_m = Column(Float, default=250)
    severity = Column(String, default="high")
    status = Column(String, default="active")
    affected_route_id = Column(String, ForeignKey("route_options.id"), nullable=True)
    created_at = Column(Float, default=time.time)


class RecommendationModel(Base):
    __tablename__ = "recommendations"

    id = Column(String, primary_key=True)
    mission_id = Column(String, ForeignKey("missions.id"), nullable=False)
    incident_id = Column(String, ForeignKey("incidents.id"), nullable=True)
    recommended_route_id = Column(String, ForeignKey("route_options.id"), nullable=True)
    action = Column(String, nullable=False)
    reason = Column(String, nullable=False)
    confidence = Column(Float, default=0.7)
    extra = Column(JSON, nullable=True)
    status = Column(String, default="pending")
    created_at = Column(Float, default=time.time)


class TimelineEventModel(Base):
    __tablename__ = "timeline_events"

    id = Column(String, primary_key=True)
    mission_id = Column(String, ForeignKey("missions.id"), nullable=True)
    vehicle_id = Column(String, ForeignKey("vehicles.id"), nullable=True)
    event_type = Column(String, nullable=False)
    message = Column(String, nullable=False)
    timestamp = Column(Float, default=time.time)
    title = Column(String, default="")
    time_str = Column(String, default="")
    severity = Column(String, default="info")


Index("idx_routes_mission", RouteOptionModel.mission_id)
Index("idx_incidents_mission", IncidentModel.mission_id)
Index("idx_recommendations_mission", RecommendationModel.mission_id)
Index("idx_timeline_mission", TimelineEventModel.mission_id)
Index("idx_timeline_timestamp", TimelineEventModel.timestamp)

_SCHEMA_TABLE_ORDER = [
    VehicleModel.__table__,
    MissionModel.__table__,
    RouteOptionModel.__table__,
    IncidentModel.__table__,
    RecommendationModel.__table__,
    TimelineEventModel.__table__,
]

def _migrate_sqlite_foreign_keys() -> bool:
    if not DATABASE_URL.startswith("sqlite") or engine.url.database in {None, ":memory:"}:
        return False

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    app_tables = set(Base.metadata.tables)
    if not existing_tables.intersection(app_tables):
        return False

    expected_foreign_keys = {
        "vehicles": {"current_mission_id", "active_route_id"},
        "missions": {"vehicle_id"},
        "route_options": {"mission_id"},
        "incidents": {"mission_id", "affected_route_id"},
        "recommendations": {"mission_id", "incident_id", "recommended_route_id"},
        "timeline_events": {"mission_id", "vehicle_id"},
    }
    for table_name, expected_columns in expected_foreign_keys.items():
        if table_name not in existing_tables:
            continue
        actual_columns = {
            column
            for foreign_key in inspector.get_foreign_keys(table_name)
            for column in foreign_key["constrained_columns"]
        }
        if not expected_columns.issubset(actual_columns):
            break
    else:
        return False

    raw_connection = engine.raw_connection()
    cursor = raw_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=OFF")
        cursor.execute("BEGIN IMMEDIATE")
        preserved_rows = {}
        for table_name in existing_tables.intersection(app_tables):
            cursor.execute(f'SELECT * FROM "{table_name}"')
            names = [description[0] for description in cursor.description]
            preserved_rows[table_name] = [
                dict(zip(names, row)) for row in cursor.fetchall()
            ]

        for table_name in existing_tables.intersection(app_tables):
            cursor.execute(f'DROP TABLE "{table_name}"')

        for table in _SCHEMA_TABLE_ORDER:
            cursor.execute(str(CreateTable(table).compile(dialect=engine.dialect)))
        for table in Base.metadata.tables.values():
            for index in table.indexes:
                cursor.execute(str(CreateIndex(index).compile(dialect=engine.dialect)))

        for table in _SCHEMA_TABLE_ORDER:
            for row in preserved_rows.get(table.name, []):
                values = {column.name: row[column.name] for column in table.columns if column.name in row}
                if table.name == "timeline_events" and values.get("vehicle_id") == "SYSTEM":
                    values["vehicle_id"] = None
                if table.name == "vehicles":
                    for column in ("current_mission_id", "active_route_id"):
                        if values.get(column) and not any(
                            saved.get("id") == values[column]
                            for saved in preserved_rows.get(
                                "missions" if column == "current_mission_id" else "route_options", []
                            )
                        ):
                            values[column] = None
                if table.name == "timeline_events" and values.get("vehicle_id"):
                    if not any(
                        saved.get("id") == values["vehicle_id"]
                        for saved in preserved_rows.get("vehicles", [])
                    ):
                        values["vehicle_id"] = None
                columns = list(values)
                column_sql = ", ".join(f'"{column}"' for column in columns)
                placeholders = ", ".join("?" for _ in columns)
                cursor.execute(
                    f'INSERT INTO "{table.name}" ({column_sql}) VALUES ({placeholders})',
                    [values[column] for column in columns],
                )

        violations = cursor.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"SQLite schema migration found invalid foreign keys: {violations}")
        raw_connection.commit()
    except Exception:
        raw_connection.rollback()
        raise
    finally:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
        raw_connection.close()
        engine.dispose()

    return True


if not _migrate_sqlite_foreign_keys():
    Base.metadata.create_all(bind=engine)
