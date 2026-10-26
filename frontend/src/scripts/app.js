const API_BASE = ""; // Uses Vite proxy to http://127.0.0.1:8000
const MAX_SAFETY_PRIORITY = "Critical / Code Red";

let mainMap = null;
let driverMap = null;

let mainRouteLine = null;
let mainRerouteLine = null;
let mainVehicleMarker = null;
let mainEndpointMarkers = [];
let mainObstacleMarkers = [];
let animTimer = null;

let driverRouteLine = null;
let driverVehicleMarker = null;
let driverEndpointMarkers = [];

let currentMission = null;
let pingIndex = 0;
let currentVehicleId = "AMB-104";
let activeRecommendation = null;
let activeBlockedEdge = null;  // The road segment blocked by the current obstacle

function getVehicleIcon(vehicleId = "AMB-104") {
  const vid = String(vehicleId).toUpperCase();
  const iconHtml = vid.startsWith("FIRE") ? "🚒" : (vid.startsWith("POL") ? "🚓" : "🚑");
  const className = vid.startsWith("FIRE") ? "fire-icon" : (vid.startsWith("POL") ? "police-icon" : "ambulance-icon");
  return L.divIcon({ html: iconHtml, className: className, iconSize: [28, 28] });
}

function clearDriverMapLayers({ keepVehicleMarker = false } = {}) {
  if (!driverMap) return;
  if (driverRouteLine) {
    try { driverMap.removeLayer(driverRouteLine); } catch (_) { }
    driverRouteLine = null;
  }
  driverEndpointMarkers.forEach(m => {
    try { driverMap.removeLayer(m); } catch (_) { }
  });
  driverEndpointMarkers = [];

  if (!keepVehicleMarker && driverVehicleMarker) {
    try { driverMap.removeLayer(driverVehicleMarker); } catch (_) { }
    driverVehicleMarker = null;
  }

  // Ensure no duplicate or orphaned polylines / markers remain on driverMap
  driverMap.eachLayer(layer => {
    if (layer instanceof L.Polyline) {
      if (layer !== driverRouteLine) {
        try { driverMap.removeLayer(layer); } catch (_) { }
      }
    } else if (layer instanceof L.Marker) {
      if (keepVehicleMarker && layer === driverVehicleMarker) {
        return;
      }
      if (!driverEndpointMarkers.includes(layer) && layer !== driverVehicleMarker) {
        try { driverMap.removeLayer(layer); } catch (_) { }
      }
    }
  });
}

function updateDriverVehicleMarker(lat, lng, vehicleId = currentVehicleId) {
  if (!driverMap || vehicleId !== currentVehicleId) return;

  if (driverVehicleMarker) {
    driverVehicleMarker.setLatLng([lat, lng]);
    driverVehicleMarker.setZIndexOffset(1000);
  } else {
    const icon = getVehicleIcon(vehicleId);
    driverVehicleMarker = L.marker([lat, lng], {
      icon,
      zIndexOffset: 1000,
      title: vehicleId
    }).addTo(driverMap).bindPopup(`<b>${vehicleId}</b><br>Status: EN ROUTE`);
  }
}

function initMaps() {
  mainMap = L.map("mainMap").setView([12.9352, 77.6146], 14);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap"
  }).addTo(mainMap);

  driverMap = L.map("driverMap").setView([12.9352, 77.6146], 15);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap"
  }).addTo(driverMap);
}

function switchTab(tab) {
  document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
  document.querySelectorAll(".dashboard-view").forEach(v => v.classList.remove("active"));

  if (tab === "controlRoom") {
    document.getElementById("tabControlRoom").classList.add("active");
    document.getElementById("controlRoomView").classList.add("active");
    setTimeout(() => {
      mainMap.invalidateSize();
      driverMap.invalidateSize();
    }, 100);
    loadDriverHUD();
  } else if (tab === "admin") {
    document.getElementById("tabAdmin").classList.add("active");
    document.getElementById("adminView").classList.add("active");
    refreshAdminData();
  }
}

function clearMainMap() {
  if (mainRouteLine) { mainMap.removeLayer(mainRouteLine); mainRouteLine = null; }
  if (mainRerouteLine) { mainMap.removeLayer(mainRerouteLine); mainRerouteLine = null; }
  if (mainVehicleMarker) { mainMap.removeLayer(mainVehicleMarker); mainVehicleMarker = null; }
  mainEndpointMarkers.forEach(m => mainMap.removeLayer(m));
  mainEndpointMarkers = [];
  mainObstacleMarkers.forEach(m => mainMap.removeLayer(m));
  mainObstacleMarkers = [];
  if (animTimer) { clearInterval(animTimer); animTimer = null; }
}

async function resetMissionAndSystem() {
  if (animTimer) { clearInterval(animTimer); animTimer = null; }
  clearMainMap();

  clearDriverMapLayers({ keepVehicleMarker: false });

  currentMission = null;
  activeRecommendation = null;
  activeBlockedEdge = null;
  pingIndex = 0;

  document.getElementById("statsCard").style.display = "none";
  document.getElementById("agentCard").style.display = "none";
  document.getElementById("decisionActions").style.display = "none";
  document.getElementById("delayCell").style.display = "none";
  document.getElementById("causeCell").style.display = "none";
  document.getElementById("obstacleSimBtn").disabled = true;
  document.getElementById("dispatchBtn").disabled = false;
  // Restore full dispatch form
  document.getElementById("dispatchCard").classList.remove("dispatch-card-compact");
  // Restore right panel idle state
  const aiPlaceholder = document.getElementById("agentIdlePlaceholder");
  if (aiPlaceholder) aiPlaceholder.style.display = "block";
  const aiRightBadge = document.getElementById("agentBadge");
  if (aiRightBadge) { aiRightBadge.className = "badge badge-info"; aiRightBadge.textContent = "STANDBY"; }
  const aiRightPanel = document.getElementById("aiRightPanel");
  if (aiRightPanel) aiRightPanel.scrollTop = 0;

  const srcInput = document.getElementById("sourceAddr");
  if (srcInput) srcInput.value = "";
  const destInput = document.getElementById("destAddr");
  if (destInput) destInput.value = "";

  const driverStatus = document.getElementById("driverMissionStatus");
  if (driverStatus) { driverStatus.className = "badge badge-warning"; driverStatus.textContent = "STANDBY"; }
  const dMissionId = document.getElementById("dMissionId");
  if (dMissionId) dMissionId.textContent = "NONE";
  const dPriority = document.getElementById("dPriority");
  if (dPriority) dPriority.textContent = MAX_SAFETY_PRIORITY;
  const dOrigin = document.getElementById("dOrigin");
  if (dOrigin) dOrigin.textContent = "Station Base";
  const dDest = document.getElementById("dDest");
  if (dDest) dDest.textContent = "Standby Zone";
  const dEta = document.getElementById("dEta");
  if (dEta) dEta.textContent = "0.0 MIN";

  try { await fetch(`${API_BASE}/api/system/reset`, { method: "POST" }); } catch (e) { console.error("Reset API call:", e); }

  await refreshTimeline();
  loadDriverHUD();
  refreshAdminData();

  const statusText = document.getElementById("sysStatusText");
  if (statusText) {
    const orig = statusText.textContent;
    statusText.textContent = "System Reset ✓";
    setTimeout(() => { statusText.textContent = orig; }, 3000);
  }
}

function fillDemoAddresses() {
  document.getElementById("sourceAddr").value = "Koramangala Sony World Junction, Bengaluru";
  document.getElementById("destAddr").value = "Forum Mall, Koramangala, Bengaluru";
}

async function planAndDispatch() {
  const vehicleId = document.getElementById("vehicleSelect").value;
  const source = document.getElementById("sourceAddr").value.trim();
  const destination = document.getElementById("destAddr").value.trim();

  if (!source || !destination) { alert("Please enter both source and destination addresses."); return; }

  clearMainMap();
  currentVehicleId = vehicleId;
  document.getElementById("driverVehicleLabel").textContent = vehicleId;
  clearDriverMapLayers({ keepVehicleMarker: false });
  document.getElementById("dispatchBtn").disabled = true;

  try {
    const res = await fetch(`${API_BASE}/api/route`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source,
        destination,
        vehicle_id: vehicleId,
        priority: MAX_SAFETY_PRIORITY,
      }),
    });

    if (!res.ok) { const err = await res.json().catch(() => ({})); throw new Error(err.detail || "Dispatch failed"); }

    const data = await res.json();
    currentMission = data;
    pingIndex = 0;

    const latlngs = data.planned_route.coords.map(c => [c[0], c[1]]);
    mainRouteLine = L.polyline(latlngs, { color: "#3d8bff", weight: 5, opacity: 0.9 }).addTo(mainMap);
    mainMap.fitBounds(mainRouteLine.getBounds(), { padding: [40, 40] });

    mainEndpointMarkers.push(L.marker(data.source_coords, { title: "Origin" }).addTo(mainMap));
    mainEndpointMarkers.push(L.marker(data.dest_coords, { title: "Destination" }).addTo(mainMap));

    document.getElementById("statVehicle").textContent = vehicleId;
    document.getElementById("statDist").textContent = `${(data.planned_route.length_m / 1000).toFixed(2)} km`;
    document.getElementById("statEta").textContent = `${(data.planned_route.eta_s / 60).toFixed(1)} min`;
    document.getElementById("statsCard").style.display = "block";
    document.getElementById("obstacleSimBtn").disabled = false;

    // Collapse dispatch form to compact mode to free sidebar space
    document.getElementById("compactVehicle").textContent = `${vehicleId} · ${source.split(',')[0]}`;
    document.getElementById("compactPriority").textContent = "● CRITICAL / CODE RED";
    document.getElementById("dispatchCard").classList.add("dispatch-card-compact");

    fetch(`${API_BASE}/api/trip/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ vehicle_id: vehicleId, planned_route_coords: data.planned_route.coords, destination_lat: data.dest_coords[0], destination_lon: data.dest_coords[1] }),
    }).catch(() => { });

    fetchAgentRecommendation(data.source_coords);
    animateVehicle(data.pings);
    loadDriverHUD();
    refreshTimeline();
  } catch (e) {
    alert(`Error: ${e.message}`);
  } finally {
    document.getElementById("dispatchBtn").disabled = false;
  }
}

function animateVehicle(pings) {
  if (!pings || pings.length === 0) return;
  const icon = getVehicleIcon(currentVehicleId);
  mainVehicleMarker = L.marker([pings[0].lat, pings[0].lon], { icon, zIndexOffset: 1000 }).addTo(mainMap);

  // Initialize/update driver vehicle marker on driverMap
  updateDriverVehicleMarker(pings[0].lat, pings[0].lon, currentVehicleId);

  animTimer = setInterval(() => {
    if (pingIndex >= pings.length) { clearInterval(animTimer); animTimer = null; return; }
    const p = pings[pingIndex];
    mainVehicleMarker.setLatLng([p.lat, p.lon]);
    updateDriverVehicleMarker(p.lat, p.lon, currentVehicleId);
    fetch(`${API_BASE}/api/trip/ping`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ vehicle_id: currentVehicleId, lat: p.lat, lon: p.lon }) }).catch(() => { });
    pingIndex++;
  }, 500);
}

async function fetchAgentRecommendation(currentPos, blockedEdge = null, incidentType = "accident") {
  if (!currentMission) return;
  const agentCardEl = document.getElementById("agentCard");
  const placeholder = document.getElementById("agentIdlePlaceholder");
  const rightPanel = document.getElementById("aiRightPanel");

  // Show agent card, hide idle placeholder
  if (placeholder) placeholder.style.display = "none";
  agentCardEl.style.display = "block";

  // Scroll right panel to top so full content is visible
  if (rightPanel) rightPanel.scrollTop = 0;

  document.getElementById("agentBadge").className = "badge badge-warning";
  document.getElementById("agentBadge").textContent = "EVALUATING";

  try {
    const res = await fetch(`${API_BASE}/api/agent/recommend`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        vehicle_id: currentVehicleId,
        current_lat: currentPos[0], current_lon: currentPos[1],
        destination_lat: currentMission.dest_coords[0], destination_lon: currentMission.dest_coords[1],
        planned_route_coords: currentMission.planned_route.coords,
        blocked_edge: blockedEdge, incident_type: incidentType,
      }),
    });

    if (!res.ok) return;
    const data = await res.json();
    activeRecommendation = data;

    const confPct = Math.round(data.confidence_score * 100);
    document.getElementById("confVal").textContent = `${confPct}%`;
    const fill = document.getElementById("confidenceFill");
    if (fill) fill.style.width = `${confPct}%`;

    if (data.has_deviated) {
      document.getElementById("agentBadge").className = "badge badge-danger";
      document.getElementById("agentBadge").textContent = "DEVIATION ALERT";
      document.getElementById("qDevStatus").innerHTML = `<span style="color:var(--red);font-weight:700;">⚠️ DEVIATED</span> near obstacle`;
    } else {
      document.getElementById("agentBadge").className = "badge badge-success";
      document.getElementById("agentBadge").textContent = "ON TRAJECTORY";
      document.getElementById("qDevStatus").innerHTML = `<span style="color:var(--green);">✅ ON ROUTE</span> (Arterial Path)`;
    }

    document.getElementById("qCause").textContent = `${data.cause} — ${data.cause_details}`;
    document.getElementById("qDelay").textContent = data.delay_expected_s > 0 ? `+${(data.delay_expected_s / 60).toFixed(1)} min delay` : "On Schedule";
    document.getElementById("qReroute").textContent = data.best_alternative_route && data.best_alternative_route.is_rerouted ? "Green Bypass Calculated" : "Maintain Primary Route";
    document.getElementById("qBackup").textContent = data.should_dispatch_backup ? `⚠️ DISPATCH ${data.recommended_backup_vehicle_id}` : "Keep Primary Vehicle";
    document.getElementById("agentNlp").innerHTML = data.natural_language_explanation.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');

    if (blockedEdge || data.has_deviated || data.should_dispatch_backup) {
      const decisionBox = document.getElementById("decisionActions");
      decisionBox.style.display = "block";
      document.getElementById("proposalDesc").textContent = data.should_dispatch_backup
        ? `Agent recommends dispatching backup vehicle ${data.recommended_backup_vehicle_id} from ${data.backup_station_name} (ETA: ${(data.backup_eta_s / 60).toFixed(1)}m).`
        : `Agent computed green arterial bypass around blockage (+${(data.delay_expected_s / 60).toFixed(1)}m delay).`;
      // Scroll right panel to bottom so decision buttons are fully visible
      const rightPanel = document.getElementById("aiRightPanel");
      if (rightPanel) setTimeout(() => { rightPanel.scrollTop = rightPanel.scrollHeight; }, 150);
    }

    refreshTimeline();
  } catch (e) { console.error(e); }
}

function haversineM(p1, p2) {
  const R = 6371000;
  const toRad = x => (x * Math.PI) / 180;
  const dLat = toRad(p2[0] - p1[0]);
  const dLon = toRad(p2[1] - p1[1]);
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(p1[0])) * Math.cos(toRad(p2[0])) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

function animateAlongCoords(coordsList) {
  if (!coordsList || coordsList.length === 0 || !mainVehicleMarker) return;
  if (animTimer) clearInterval(animTimer);
  let i = 0;
  animTimer = setInterval(() => {
    if (i >= coordsList.length) {
      clearInterval(animTimer); animTimer = null;
      const badge = document.getElementById("activeMissionBadge");
      if (badge) { badge.className = "badge badge-success"; badge.textContent = "ARRIVED (REROUTED)"; }
      return;
    }
    const pt = coordsList[i];
    mainVehicleMarker.setLatLng(pt);
    updateDriverVehicleMarker(pt[0], pt[1], currentVehicleId);
    fetch(`${API_BASE}/api/trip/ping`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ vehicle_id: currentVehicleId, lat: pt[0], lon: pt[1] }) }).catch(() => { });
    i++;
  }, 500);
}

async function respondDecision(decision) {
  if (!currentMission || !activeRecommendation) {
    console.error("respondDecision: missing currentMission or activeRecommendation", { currentMission, activeRecommendation });
    showToast("⚠️ No active recommendation to act on. Please dispatch a mission first.", "warning");
    return;
  }

  document.querySelectorAll("#decisionActions .btn").forEach(b => { b.disabled = true; });

  if (decision === "reject") {
    // Reject: just log and close
    try {
      await fetch(`${API_BASE}/api/missions/decision`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          mission_id: currentMission.mission_id,
          decision: "reject",
          action_type: "reroute",
        }),
      });
    } catch (_) { }
    document.getElementById("decisionActions").style.display = "none";
    showToast("❌ Reroute rejected. Vehicle instructed to hold.", "danger");
    document.querySelectorAll("#decisionActions .btn").forEach(b => { b.disabled = false; });
    refreshTimeline();
    return;
  }

  // ── APPROVE: compute real new route from vehicle's current position ──
  if (!mainVehicleMarker) {
    showToast("⚠️ Cannot reroute: vehicle position unknown.", "warning");
    document.querySelectorAll("#decisionActions .btn").forEach(b => { b.disabled = false; });
    return;
  }

  const currentLatLng = mainVehicleMarker.getLatLng();
  const [destLat, destLon] = currentMission.dest_coords;

  showToast("🗺️ Computing real reroute from live vehicle position…", "info");

  try {
    // Call /api/route/coords — takes current GPS position as source, same destination
    const res = await fetch(`${API_BASE}/api/route/coords`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source_lat: currentLatLng.lat,
        source_lon: currentLatLng.lng,
        dest_lat: destLat,
        dest_lon: destLon,
        vehicle_id: currentVehicleId,
        priority: MAX_SAFETY_PRIORITY,
        blocked_edge: activeBlockedEdge || null,
      }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || "Reroute computation failed");
    }

    const data = await res.json();
    const newCoords = data.route.coords; // [[lat,lon], ...]

    // Remove old reroute preview line if any
    if (mainRerouteLine) { mainMap.removeLayer(mainRerouteLine); mainRerouteLine = null; }

    // Draw the real new route in solid green
    mainRerouteLine = L.polyline(newCoords, {
      color: "#07834f",
      weight: 6,
      opacity: 0.95,
    }).addTo(mainMap);
    mainMap.fitBounds(mainRerouteLine.getBounds(), { padding: [40, 40] });

    // Update Driver Dashboard route line without removing driverVehicleMarker
    if (driverMap) {
      if (driverRouteLine) {
        try { driverMap.removeLayer(driverRouteLine); } catch (_) { }
        driverRouteLine = null;
      }
      driverRouteLine = L.polyline(newCoords, {
        color: "#07834f",
        weight: 6,
        opacity: 0.95,
      }).addTo(driverMap);

      if (driverVehicleMarker) {
        driverVehicleMarker.setZIndexOffset(1000);
      }
      const dEta = document.getElementById("dEta");
      if (dEta) dEta.textContent = `${(data.route.eta_s / 60).toFixed(1)} MIN (REROUTED)`;
    }

    // Update telemetry panel
    const newEtaMin = (data.route.eta_s / 60).toFixed(1);
    document.getElementById("statEta").textContent = `${newEtaMin} min (rerouted)`;
    document.getElementById("statDist").textContent = `${(data.route.length_m / 1000).toFixed(2)} km`;
    document.getElementById("delayCell").style.display = "none";
    document.getElementById("causeCell").style.display = "none";

    // Update monitoring with new route
    fetch(`${API_BASE}/api/trip/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        vehicle_id: currentVehicleId,
        planned_route_coords: newCoords,
        destination_lat: destLat,
        destination_lon: destLon,
      }),
    }).catch(() => { });

    // Log the decision
    await fetch(`${API_BASE}/api/missions/decision`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        mission_id: currentMission.mission_id,
        decision: "approve",
        action_type: "reroute",
        new_route_coords: newCoords,
        new_eta_s: data.route.eta_s,
      }),
    }).catch(() => { });

    // Update currentMission so future agent calls use new route
    currentMission.planned_route = data.route;
    currentMission.eta_s = data.route.eta_s;

    // Animate vehicle along the real new route
    animateAlongCoords(newCoords);

    document.getElementById("decisionActions").style.display = "none";
    showToast(`✅ Reroute approved! New route computed. ETA: ${newEtaMin} min.`, "success");
    refreshTimeline();

  } catch (e) {
    showToast(`⚠️ Reroute failed: ${e.message}`, "danger");
    console.error("respondDecision reroute error:", e);
  } finally {
    document.querySelectorAll("#decisionActions .btn").forEach(b => { b.disabled = false; });
  }
}

function showToast(msg, type = "info") {
  const colors = { success: "#07834f", danger: "#ff4444", warning: "#ffaa00", info: "#2563eb" };
  const toast = document.createElement("div");
  toast.style.cssText = `position:fixed;bottom:24px;right:24px;z-index:9999;padding:12px 20px;border-radius:10px;font-size:13px;font-weight:600;color:#fff;background:${colors[type] || colors.info};box-shadow:0 8px 32px rgba(0,0,0,0.4);max-width:380px;line-height:1.4;animation:fadeInUp 0.3s ease;`;
  toast.textContent = msg;
  document.body.appendChild(toast);
  setTimeout(() => { toast.style.opacity = "0"; toast.style.transition = "opacity 0.4s"; setTimeout(() => toast.remove(), 400); }, 4000);
}

async function simulateObstacleControl() {
  if (!currentMission || !mainVehicleMarker) return;

  // Stop the current animation so ambulance freezes at its current position
  if (animTimer) { clearInterval(animTimer); animTimer = null; }

  const currentLatLng = mainVehicleMarker.getLatLng();
  const currentPos = [currentLatLng.lat, currentLatLng.lng];
  const routeCoords = currentMission.planned_route.coords;

  // Find the point ~250m ahead on the route as the obstacle location
  let nearestIdx = 0, bestDist = Infinity;
  for (let idx = 0; idx < routeCoords.length; idx++) {
    const d = haversineM(routeCoords[idx], currentPos);
    if (d < bestDist) { bestDist = d; nearestIdx = idx; }
  }
  const OBSTACLE_AHEAD_M = 250;
  let aheadIdx = nearestIdx, accumulatedM = 0;
  while (aheadIdx < routeCoords.length - 1 && accumulatedM < OBSTACLE_AHEAD_M) {
    accumulatedM += haversineM(routeCoords[aheadIdx], routeCoords[aheadIdx + 1]);
    aheadIdx++;
  }
  const obstacleCoord = routeCoords[aheadIdx];

  // Place obstacle marker on map
  const obstacleIcon = L.divIcon({ html: "🚧", className: "obstacle-icon", iconSize: [28, 28] });
  mainObstacleMarkers.push(
    L.marker(obstacleCoord, { icon: obstacleIcon })
      .addTo(mainMap)
      .bindPopup("<b>🚧 Road Blocked Ahead</b><br>Vehicle stopped. Awaiting reroute approval.")
      .openPopup()
  );

  document.getElementById("obstacleSimBtn").disabled = true;
  showToast("🚧 Obstacle detected! AI Engine computing reroute…", "warning");

  // Let the AI engine analyse and propose a reroute
  // Build blocked_edge for agent context
  const routeNodes = currentMission.planned_route.nodes || [];
  const segStart = Math.max(nearestIdx, aheadIdx - 1);
  const segEnd = Math.min(routeCoords.length - 1, segStart + 1);
  // Save blocked edge globally so respondDecision can pass it to backend
  activeBlockedEdge = null; // reset first
  if (routeNodes.length > segEnd) activeBlockedEdge = [routeNodes[segStart], routeNodes[segEnd]];
  else activeBlockedEdge = [routeCoords[segStart], routeCoords[segEnd]];
  const blockedEdge = activeBlockedEdge;

  fetchAgentRecommendation(currentPos, blockedEdge, "accident");
}

async function refreshTimeline() {
  try {
    const res = await fetch(`${API_BASE}/api/timeline`);
    if (!res.ok) return;
    const data = await res.json();
    const list = document.getElementById("timelineList");
    list.innerHTML = data.timeline.map(e => `
      <div class="timeline-item ${e.severity}">
        <div class="tl-header"><span class="tl-title">${e.title}</span><span class="tl-time">${e.time_str}</span></div>
        <div class="tl-msg">${e.message}</div>
      </div>
    `).join("");
  } catch (e) { }
}

async function loadDriverHUD() {
  const vid = currentVehicleId;
  const vehicleLabel = document.getElementById("driverVehicleLabel");
  if (vehicleLabel) vehicleLabel.textContent = vid;

  const statusEl = document.getElementById("driverMissionStatus");
  const missionIdEl = document.getElementById("dMissionId");
  const priorityEl = document.getElementById("dPriority");
  const originEl = document.getElementById("dOrigin");
  const destEl = document.getElementById("dDest");
  const etaEl = document.getElementById("dEta");

  // Clear previous route and endpoint markers; will recreate vehicle marker for this vehicle
  clearDriverMapLayers({ keepVehicleMarker: false });

  try {
    const res = await fetch(
      `${API_BASE}/api/missions/active/${encodeURIComponent(vid)}`
    );

    if (!res.ok) {
      throw new Error(`Mission API returned HTTP ${res.status}`);
    }

    const data = await res.json();
    const m = data.mission;

    // Validate [latitude, longitude] coordinates.
    const validPoint = p =>
      Array.isArray(p) &&
      p.length >= 2 &&
      Number.isFinite(Number(p[0])) &&
      Number.isFinite(Number(p[1])) &&
      Math.abs(Number(p[0])) <= 90 &&
      Math.abs(Number(p[1])) <= 180;

    // Robust resolution of vehicle coordinates without assuming fixed field names
    const resolveVehicleCoords = (vehicleObj, fallbackSource, fallbackRoute) => {
      // 1. Live position if vehicle is currently animating in active session
      if (vid === currentVehicleId && mainVehicleMarker) {
        const liveLL = mainVehicleMarker.getLatLng();
        if (validPoint([liveLL.lat, liveLL.lng])) {
          return [liveLL.lat, liveLL.lng];
        }
      }
      // 2. Vehicle location from backend
      if (vehicleObj) {
        if (validPoint(vehicleObj.location)) {
          return [Number(vehicleObj.location[0]), Number(vehicleObj.location[1])];
        }
        if (validPoint([vehicleObj.lat, vehicleObj.lng])) {
          return [Number(vehicleObj.lat), Number(vehicleObj.lng)];
        }
        if (validPoint([vehicleObj.latitude, vehicleObj.longitude])) {
          return [Number(vehicleObj.latitude), Number(vehicleObj.longitude)];
        }
      }
      // 3. Fallback: mission source starting coordinate
      if (fallbackSource && validPoint(fallbackSource)) {
        return fallbackSource;
      }
      // 4. Fallback: first coordinate of active route
      if (fallbackRoute && fallbackRoute.length > 0 && validPoint(fallbackRoute[0])) {
        return fallbackRoute[0];
      }
      return null;
    };

    if (!data.has_active_mission || !m) {
      statusEl.className = "badge badge-warning";
      statusEl.textContent = "STANDBY";
      missionIdEl.textContent = "NONE";
      priorityEl.textContent = MAX_SAFETY_PRIORITY;
      originEl.textContent = "No active mission";
      destEl.textContent = "No active mission";
      etaEl.textContent = "0.0 MIN";

      const vehiclePos = resolveVehicleCoords(data.vehicle, null, null);
      if (vehiclePos) {
        const icon = getVehicleIcon(vid);
        driverVehicleMarker = L.marker(vehiclePos, {
          icon,
          zIndexOffset: 1000,
          title: vid
        }).addTo(driverMap).bindPopup(`<b>${vid}</b><br>Status: STANDBY`);
        driverMap.setView(vehiclePos, 15);
      }
      setTimeout(() => driverMap.invalidateSize(), 150);
      return;
    }

    // Update mission information.
    statusEl.className = "badge badge-success";
    statusEl.textContent = m.status || "EN ROUTE";
    missionIdEl.textContent = m.id || "—";
    priorityEl.textContent = MAX_SAFETY_PRIORITY;
    originEl.textContent = m.source || "Source unavailable";
    destEl.textContent = m.destination || "Destination unavailable";
    etaEl.textContent =
      `${(Number(m.eta_s || 0) / 60).toFixed(1)} MIN`;

    const source = validPoint(m.source_coords)
      ? [Number(m.source_coords[0]), Number(m.source_coords[1])]
      : null;

    const destination = validPoint(m.dest_coords)
      ? [Number(m.dest_coords[0]), Number(m.dest_coords[1])]
      : null;

    const route = Array.isArray(m.active_route_coords)
      ? m.active_route_coords
        .filter(validPoint)
        .map(p => [Number(p[0]), Number(p[1])])
      : [];

    // Draw source and destination markers.
    if (source) {
      const srcM = L.marker(source)
        .addTo(driverMap)
        .bindPopup(`<b>Source</b><br>${m.source || "Start point"}`);
      driverEndpointMarkers.push(srcM);
    }

    if (destination) {
      const dstM = L.marker(destination)
        .addTo(driverMap)
        .bindPopup(
          `<b>Destination</b><br>${m.destination || "End point"}`
        );
      driverEndpointMarkers.push(dstM);
    }

    // Draw the actual route only when usable route geometry exists.
    if (route.length >= 2) {
      driverRouteLine = L.polyline(route, {
        color: "#1677ff",
        weight: 6,
        opacity: 0.95
      }).addTo(driverMap);

      // Fit the map to show the complete route.
      driverMap.fitBounds(driverRouteLine.getBounds(), {
        padding: [35, 35],
        maxZoom: 16
      });
    } else if (source && destination) {
      // Show both endpoints while clearly identifying missing road geometry.
      driverMap.fitBounds([source, destination], {
        padding: [35, 35],
        maxZoom: 16
      });

      console.warn(
        "Driver HUD: mission exists, but active_route_coords is empty or invalid.",
        m
      );
    } else if (source) {
      driverMap.setView(source, 15);
    } else if (destination) {
      driverMap.setView(destination, 15);
    } else {
      console.error(
        "Driver HUD: mission has no valid source or destination coordinates.",
        m
      );
    }

    // Add dedicated driverVehicleMarker at current position or fallback to starting coordinate
    const vehiclePos = resolveVehicleCoords(data.vehicle, source, route);
    if (vehiclePos) {
      const icon = getVehicleIcon(vid);
      driverVehicleMarker = L.marker(vehiclePos, {
        icon,
        zIndexOffset: 1000,
        title: vid
      }).addTo(driverMap).bindPopup(`<b>${vid}</b><br>Status: ${m.status || "EN ROUTE"}`);
    }

    // Correct map dimensions after opening the Driver tab.
    setTimeout(() => driverMap.invalidateSize(), 150);

  } catch (error) {
    console.error("Failed to load Driver HUD:", error);
    statusEl.className = "badge badge-warning";
    statusEl.textContent = "LOAD ERROR";
  }
}
async function refreshAdminData() {
  try {
    const res = await fetch(`${API_BASE}/api/admin/overview`);
    if (!res.ok) return;
    const data = await res.json();
    document.getElementById("admStatus").textContent = data.status;
    document.getElementById("admUptime").textContent = data.uptime_formatted;
    document.getElementById("admFleetCount").textContent = data.fleet_summary.total_vehicles;
    document.getElementById("admGraphNodes").textContent = data.road_network.nodes_count.toLocaleString();
    document.getElementById("admMissionsCount").textContent = data.total_missions;

    const fleetRes = await fetch(`${API_BASE}/api/fleet`);
    if (fleetRes.ok) {
      const fleetData = await fleetRes.json();
      const tbody = document.getElementById("adminFleetTable");
      tbody.innerHTML = fleetData.vehicles.map(v => `
        <tr>
          <td><b>${v.id}</b></td>
          <td>${v.type.toUpperCase()}</td>
          <td>${v.callsign}</td>
          <td>${v.driver_name} (${v.driver_contact})</td>
          <td><span class="badge ${v.status === 'En Route' ? 'badge-success' : (v.status === 'Deviated' ? 'badge-danger' : 'badge-info')}">${v.status}</span></td>
          <td><code>${v.location[0].toFixed(4)}, ${v.location[1].toFixed(4)}</code></td>
          <td>&#x26A1; ${v.battery_fuel_pct}%</td>
        </tr>
      `).join("");
    }
  } catch (e) { }
}

window.addEventListener("DOMContentLoaded", () => {
  initMaps();
  loadDriverHUD();
  setInterval(refreshTimeline, 5000);
});