from .graph_utils import get_bengaluru_graph, get_route
from .simulator import simulate_trip
from .detector import DeviationDetector
from .visualize import build_map

G = get_bengaluru_graph()  # cached after first run

origin = (12.9352, 77.6146)   # pick real coordinates in your chosen area
dest   = (12.9330, 77.6080)
detour_via = (12.9390, 77.6140)  # a point that forces a different path

planned   = get_route(G, origin, dest)
deviation = get_route(G, detour_via, dest)  # simulate "got forced onto this path"

detector = DeviationDetector(planned["coords"], threshold_m=60, consecutive_needed=3)
actual_path, dev_point = [], None

for ping in simulate_trip("AMB-104", planned["coords"],
                           deviation_route_coords=deviation["coords"],
                           deviation_at_fraction=0.4):
    actual_path.append((ping.lat, ping.lon))
    state = detector.process_ping(ping.lat, ping.lon)
    if state.is_deviated and dev_point is None:
        dev_point = state.deviation_point
        print(f"Deviation detected at {dev_point}")

build_map(planned["coords"], actual_path, dev_point, deviation["coords"],
          out_path="output/bengaluru_demo.html")