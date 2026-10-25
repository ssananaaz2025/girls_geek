import sys
import os

# Add backend/app to path
sys.path.insert(0, os.path.dirname(__file__))

import graph_utils
import reroute
import incident_simulator
import detector
import simulator

def test_routing_system():
    print("--- 1. Testing Graph Loading ---")
    try:
        G = graph_utils.get_bengaluru_graph()
        print(f"SUCCESS: Graph loaded with {len(G.nodes)} nodes and {len(G.edges)} edges.")
    except Exception as e:
        print(f"FAILED: Graph loading failed: {e}")
        return

    print("\n--- 2. Testing Route Planning ---")
    origin = (12.9352, 77.6146)   # Koramangala Sony World Junction
    dest   = (12.9345, 77.6080)   # Near Forum Mall

    try:
        route = graph_utils.get_route(G, origin, dest)
        print(f"SUCCESS: Planned route found with {len(route['nodes'])} nodes.")
        print(f"   Length: {route['length_m']:.2f} meters")
        print(f"   ETA: {route['eta_s'] / 60:.2f} minutes ({route['eta_s']:.1f} seconds)")
        print(f"   First 3 coords: {route['coords'][:3]}")
    except Exception as e:
        print(f"FAILED: Route planning failed: {e}")
        return

    print("\n--- 3. Testing Edge Rerouting ---")
    try:
        if len(route['nodes']) >= 2:
            blocked_edge = (route['nodes'][0], route['nodes'][1])
        else:
            blocked_edge = [(12.9352, 77.6146), (12.9348, 77.6120)]

        reroute_res = reroute.reroute_around_blockage(G, origin, dest, blocked_edge)
        print(f"SUCCESS: Reroute calculated around blocked edge {blocked_edge}.")
        print(f"   Rerouted: {reroute_res['rerouted']}")
        print(f"   New ETA: {reroute_res['new_eta_s'] / 60:.2f} minutes")
        print(f"   Delay Added: {reroute_res['delay_added_s'] / 60:.2f} minutes")
        print(f"   Cause: {reroute_res['cause']}")
    except Exception as e:
        print(f"FAILED: Edge rerouting failed: {e}")

    print("\n--- 4. Testing Incident Simulator & Reroute ---")
    try:
        incident, dev_route = incident_simulator.simulate_incident_and_reroute(G, route, dest)
        print(f"SUCCESS: Incident simulated.")
        print(f"   Type: {incident.type}")
        print(f"   Location: {incident.location}")
        print(f"   Blocked Edge: {incident.blocked_edge}")
        print(f"   Deviated Route Nodes: {len(dev_route['nodes'])}")
        print(f"   Deviated Route ETA: {dev_route['eta_s'] / 60:.2f} minutes")
    except Exception as e:
        print(f"FAILED: Incident simulation failed: {e}")

    print("\n--- 5. Testing GPS Ping Simulation & Deviation Detector ---")
    try:
        dev_detector = detector.DeviationDetector(route['coords'], threshold_m=50.0, consecutive_needed=3)
        pings = list(simulator.simulate_trip("AMB-104", route['coords'], realtime=False))
        print(f"SUCCESS: Simulated {len(pings)} pings for trip.")
        
        off_route_count = 0
        for p in pings:
            st = dev_detector.process_ping(p.lat, p.lon)
            if st.is_deviated:
                off_route_count += 1
        print(f"   Detector evaluated {len(pings)} pings. Confirmed deviated count: {off_route_count}")
    except Exception as e:
        print(f"FAILED: GPS Ping & Deviation Detector test failed: {e}")

if __name__ == "__main__":
    test_routing_system()
