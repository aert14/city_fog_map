// API module - handles all backend API interactions
export async function getDebugSettings() {
  try {
    const response = await fetch("/api/v1/debug-mode");
    if (!response.ok) return { noAuthMode: false, debugAuthMode: false };
    const data = await response.json();
    return {
      noAuthMode: !!data.no_auth_mode,
      debugAuthMode: !!data.debug_auth_mode,
      baseVisitResolution:
        typeof data.base_visit_resolution === "number"
          ? data.base_visit_resolution
          : undefined,
    };
  } catch (error) {
    console.warn("[auth] Failed to check debug mode:", error);
    return { noAuthMode: false, debugAuthMode: false };
  }
}

export function getAuthHeaders(custom = {}) {
  const headers = { ...custom };
  if (window.tg && window.tg.initData) {
    headers["X-Telegram-Init"] = window.tg.initData;
  }
  if (typeof window.currentH3Resolution === "number") {
    headers["X-H3-Resolution"] = String(window.currentH3Resolution);
  }
  return headers;
}

export async function addVisitAt(lat, lng, { allKnownHexagons, addToSpatialIndex, updateDistrictProgress, countEl, forceFogRedraw, map }) {
  const response = await fetch("/api/v1/visit", {
    method: "POST",
    headers: getAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ lat, lon: lng }),
  });
  if (!response.ok) {
    throw new Error(`Server error: ${response.statusText}`);
  }
  // 1. Calculate H3 geokey locally from the coordinates
  const h3Resolution = window.currentH3Resolution || window.defaultVisitResolution;
  const h3Geokey = h3.latLngToCell(lat, lng, h3Resolution);
  // 2. Send request to server (API call remains the same)
  const result = await response.json(); // <-- `result` is now used only for stats update, not for `h3_geokey`
  if (h3Geokey && !allKnownHexagons.has(h3Geokey)) {
    allKnownHexagons.add(h3Geokey);
    addToSpatialIndex(h3Geokey);
  }

  // Update district progress with stats from response
  if (result.stats) {
    updateDistrictProgress(result.stats.district, result.stats.okrug);

    // Update the main counter with server stats
    countEl.textContent =
      result.stats && typeof result.stats.total_circles === "number"
        ? result.stats.total_circles.toLocaleString()
        : allKnownHexagons.size.toLocaleString();
  }

  forceFogRedraw();
  map.triggerRepaint();

  return result;
}

export async function deleteHexAtPoint(point, { allKnownHexagons, removeFromSpatialIndex, countEl, forceFogRedraw, map }) {
  const lngLat = map.unproject(point);
  const h3Resolution = window.currentH3Resolution || window.defaultVisitResolution;
  const targetHexId = h3.latLngToCell(lngLat.lat, lngLat.lng, h3Resolution);

  if (!allKnownHexagons.has(targetHexId)) {
    console.log(
      "Clicked on a cell that is not a known hexagon:",
      targetHexId,
    );
    return;
  }

  const response = await fetch("/api/v1/circle", {
    method: "DELETE",
    headers: getAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ geokey: targetHexId }),
  });
  if (!response.ok) {
    const errText = await response.text();
    throw new Error(
      `Delete failed with status ${response.status}: ${errText}`,
    );
  }
  const res = await response.json();
  if (res.deleted > 0) {
    allKnownHexagons.delete(targetHexId);
    removeFromSpatialIndex(targetHexId);
    countEl.textContent = allKnownHexagons.size.toLocaleString();
    forceFogRedraw();
    map.triggerRepaint();
    console.log("Deleted hexagon:", targetHexId);
  } else {
    console.warn("Delete command sent, but server reported 0 deleted.", {
      geokey: targetHexId,
    });
  }
}

export async function fetchLeaderboard({ level, period }, abortController) {
  const params = new URLSearchParams({
    level: level,
    period: period,
  });

  const response = await fetch(`/api/v1/leaderboard?${params.toString()}`, {
    signal: abortController?.signal,
    headers: getAuthHeaders({ Accept: "application/json" }),
  });
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }
  const payload = await response.json();
  return Array.isArray(payload?.entries) ? payload.entries : [];
}

export async function updateHexagonsFromServer({ map, allKnownHexagons, addToSpatialIndex, countEl, forceFogRedraw, loader }) {
  // This function needs to be refactored to work with the module system
  // For now, keeping it simple
  const bounds = map.getBounds();
  const bbox = [
    bounds.getWest(),
    bounds.getSouth(),
    bounds.getEast(),
    bounds.getNorth(),
  ].join(",");

  const response = await fetch(`/api/v1/circles?bbox=${bbox}`, {
    headers: getAuthHeaders(),
  });
  if (!response.ok)
    throw new Error(`Network error: ${response.statusText}`);
  const jsonData = await response.json();
  const receivedHexagons = jsonData.hexagons || [];

  // Expand aggregated hexagons back to base resolution
  const expandedHexagons = new Set();
  receivedHexagons.forEach((hexId) => {
    if (!hexId) return; // Skip empty strings
    const resolution = h3.getResolution(hexId);
    if (resolution === window.defaultVisitResolution) {
      // Base resolution, add directly
      expandedHexagons.add(hexId);
    } else if (resolution < window.defaultVisitResolution) {
      // Aggregated parent, expand to children
      try {
        const children = h3.cellToChildren(hexId, window.defaultVisitResolution);
        children.forEach(childHex => expandedHexagons.add(childHex));
      } catch (e) {
        console.warn(`Failed to expand hexagon ${hexId}:`, e);
        // Fallback: add the parent as-is if expansion fails
        expandedHexagons.add(hexId);
      }
    } else {
      // Higher resolution than base (unexpected), add as-is
      expandedHexagons.add(hexId);
    }
  });

  let newHexagons = 0;
  expandedHexagons.forEach((hexId) => {
    if (!allKnownHexagons.has(hexId)) {
      allKnownHexagons.add(hexId);
      addToSpatialIndex(hexId);
      newHexagons++;
    }
  });
  if (newHexagons > 0) {
    countEl.textContent = allKnownHexagons.size.toLocaleString();
  }
  forceFogRedraw();
  map.triggerRepaint();
}

export async function fetchDistrictCellsRaw(districtId, resView = null, options = {}) {
  const { signal } = options;
  const resParam = typeof resView === "number" ? `?res_view=${resView}` : "";
  const response = await fetch(
    `/api/v1/district/${districtId}/cells${resParam}`,
    {
      signal,
      headers: getAuthHeaders({ Accept: "application/json" }),
    },
  );
  if (!response.ok) {
    throw new Error(`Failed to fetch district cells: ${response.status}`);
  }
  const payload = await response.json();
  const effectiveRes =
    typeof payload?.resolution === "number"
      ? payload.resolution
      : window.BASE_DISTRICT_RESOLUTION;
  const resValue = Math.min(effectiveRes, window.BASE_DISTRICT_RESOLUTION);
  return { payload, resValue, cacheKey: `${districtId}@${resValue}`, featureCollection: null }; // featureCollection will be built in map module
}

export async function fetchAndRenderAchievements({ achievementsList }) {
  if (!achievementsList) return;
  achievementsList.innerHTML = '<div class="loader-sm"></div>'; // Показываем загрузчик

  try {
    const response = await fetch('/api/v1/me/achievements', { headers: getAuthHeaders() });
    if (!response.ok) throw new Error('Failed to load achievements');

    const achievements = await response.json();
    achievementsList.innerHTML = ''; // Очищаем

    if (achievements.length === 0) {
      achievementsList.textContent = 'Достижений пока нет.';
      return;
    }

    achievements.forEach(ach => {
      const item = document.createElement('div');
      item.className = 'achievement-item';
      if (ach.unlocked) {
        item.classList.add('unlocked');
      }

      // TODO: Иконки можно будет добавить позже, пока плейсхолдер
      item.innerHTML = `
        <div class="achievement-icon" style="background: #334155;"></div>
        <div class="achievement-details">
          <h4>${ach.name}</h4>
          <p>${ach.description}</p>
        </div>
      `;
      achievementsList.appendChild(item);
    });

  } catch (error) {
    console.error('Achievement fetch error:', error);
    achievementsList.innerHTML = '<p style="color: #f87171;">Не удалось загрузить достижения.</p>';
  }
}
