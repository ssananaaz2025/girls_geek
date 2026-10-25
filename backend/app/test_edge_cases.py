import sys
import os
import json

sys.path.insert(0, os.path.dirname(__file__))

import graph_utils
import reroute
import networkx as nx

def test_edge_cases():
    print("--- Testing Routing & Rerouting Edge Cases ---")
    G = graph_utils.get_bengaluru_graph()
    
    origin = (12.9352, 77.6146)
    dest   = (12.9345, 77.6080)
    
    # 1. Test coordinate segment input to reroute_around_blockage
    print("\n1. Testing coordinate segment blocked_edge input...")
    seg_blocked = [[12.9354, 77.6144], [12.9352, 77.6141]]
    try:
        res1 = reroute.reroute_around_blockage(G, origin, dest, seg_blocked)
        json_str = json.dumps(res1)
        print("SUCCESS: Coordinate segment reroute succeeded and is JSON serializable.")
        print(f"   Rerouted: {res1['rerouted']}, Has Bypass: {res1.get('has_bypass')}, Delay added: {res1['delay_added_s']:.2f}s")
    except Exception as e:
        print(f"FAILED: Coordinate segment reroute failed: {type(e).__name__}: {e}")

    # 2. Test degenerate blocked edge input [[lat, lon], [lat, lon]] (same point A and B)
    print("\n2. Testing degenerate identical-coordinate blocked_edge input...")
    degen_blocked = [[12.9352, 77.6146], [12.9352, 77.6146]]
    try:
        res2 = reroute.reroute_around_blockage(G, origin, dest, degen_blocked)
        json_str = json.dumps(res2)
        print("SUCCESS: Degenerate blocked_edge handled safely without self-loop crash.")
        print(f"   Rerouted: {res2['rerouted']}, Has Bypass: {res2.get('has_bypass')}")
    except Exception as e:
        print(f"FAILED: Degenerate blocked edge failed: {type(e).__name__}: {e}")

    # 3. Test disconnected graph / NoPath scenario (blocking all outgoing edges from origin)
    print("\n3. Testing disconnected path (no alternative route available)...")
    orig_node = graph_utils._snap_to_graph(G, origin, "Origin")
    G_isolated = G.copy()
    for neighbor in list(G_isolated.successors(orig_node)):
        reroute.block_edge(G_isolated, orig_node, neighbor)

    try:
        # Check if reroute_around_blockage handles no-bypass gracefully
        first_neighbor = list(G.successors(orig_node))[0]
        res3 = reroute.reroute_around_blockage(G_isolated, origin, dest, [orig_node, first_neighbor])
        json_str = json.dumps(res3)
        print(f"SUCCESS: Disconnected graph handled cleanly. Rerouted: {res3['rerouted']}, Has Bypass: {res3.get('has_bypass')}")
        print(f"   Cause reported: {res3['cause']}")
        assert res3['rerouted'] == False
        assert res3['has_bypass'] == False
    except nx.NetworkXNoPath:
        print("INFO: nx.NetworkXNoPath raised directly when graph is disconnected.")
    except Exception as e:
        print(f"Exception during isolated path test: {type(e).__name__}: {e}")

if __name__ == "__main__":
    test_edge_cases()
