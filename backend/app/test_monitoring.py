import time
import threading
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

import monitoring

def test_monitoring_thread_safety_and_ttl():
    print("--- 1. Testing Session Creation & Thread Safety ---")
    route = [(12.9352, 77.6146), (12.9345, 77.6101), (12.9330, 77.6080)]
    dest = (12.9330, 77.6080)

    # Spawn 10 concurrent threads starting trips and sending pings
    def worker(vehicle_num):
        vid = f"AMB-{vehicle_num}"
        monitoring.start_trip(vid, route, dest)
        for p in route:
            monitoring.process_ping(vid, p[0], p[1])
        monitoring.list_alerts(vid)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    print(f"SUCCESS: Concurrent session operations completed. Active sessions count: {len(monitoring._sessions)}")

    print("\n--- 2. Testing Stale Session Purge (TTL) ---")
    # Manually create a simulated old session
    s_old = monitoring.start_trip("AMB-OLD", route, dest)
    s_old.last_ping_at = time.time() - 1000.0  # > 900 seconds ago

    s_fresh = monitoring.start_trip("AMB-FRESH", route, dest)

    print(f"Sessions before cleanup: {list(monitoring._sessions.keys())}")
    purged = monitoring.cleanup_stale_sessions(ttl_seconds=900)
    print(f"Purged {purged} stale session(s).")
    remaining = list(monitoring._sessions.keys())
    print(f"Remaining sessions: {remaining}")

    assert "AMB-OLD" not in remaining, "AMB-OLD should have been purged"
    assert "AMB-FRESH" in remaining, "AMB-FRESH should still be active"
    print("SUCCESS: TTL cleanup verification passed!")

if __name__ == "__main__":
    test_monitoring_thread_safety_and_ttl()
