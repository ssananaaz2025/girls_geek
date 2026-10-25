import sys
import os

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(__file__))

import graph_utils
import decision_engine

def test_decision_engine():
    print("--- Testing GeoAgent Decision Engine ---")
    G = graph_utils.get_bengaluru_graph()
    
    origin = (12.9352, 77.6146)
    dest = (12.9345, 77.6080)
    planned = graph_utils.get_route(G, origin, dest)

    # 1. Normal route check
    rec1 = decision_engine.analyze_incident_and_recommend(
        G, "AMB-104", origin, dest, planned["coords"]
    )
    print("\n[Test 1] On-route recommendation:")
    print(rec1.natural_language_explanation)

    # 2. Obstacle / Reroute check
    blocked_edge = [planned["coords"][0], planned["coords"][1]]
    rec2 = decision_engine.analyze_incident_and_recommend(
        G, "AMB-104", origin, dest, planned["coords"],
        blocked_edge=blocked_edge, incident_type="accident"
    )
    print("\n[Test 2] Obstacle & Reroute recommendation:")
    print(rec2.natural_language_explanation)
    print(f"\nBackup Dispatched: {rec2.should_dispatch_backup}")
    print(f"Confidence Score: {rec2.confidence_score}")

    assert rec2.has_deviated == True
    assert rec2.cause == "Road Accident / Collision"
    print("\nSUCCESS: GeoAgent Decision Engine test passed!")

if __name__ == "__main__":
    test_decision_engine()
