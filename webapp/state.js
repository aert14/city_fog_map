// State module - manages application state and state-related utilities
export const state = {
  // Map and hexagons
  allKnownHexagons: new Set(),
  isFetching: false,

  // Fog
  fogEnabled: true,
  animationTime: 0,

  // Resolutions
  defaultVisitResolution: 10,
  BASE_DISTRICT_RESOLUTION: 10,

  // Spatial index
  spatialIndex: new Map(),
  GRID_SIZE: 0.25, // Size of the grid cell in degrees. Tune if needed.
  fogDataChanged: false, // Flag to track changes in fog data

  // Admin/district state
  selectedDistrictId: null,
  selectedDistrictName: "",
  selectedDistrictFeature: null,
  selectedDistrictAbortController: null,
  selectedDistrictResView: null,
  districtFeatureMap: new Map(),
  districtCellsCache: new Map(),
  okrugFeatureMap: new Map(),
  adminUpdateTimer: null,
  adminRequestSeq: 0,
  adminFetchAbortController: null,

  // UI state
  leaderboardState: {
    isOpen: false,
    level: "district",
    period: "week",
    entries: [],
    loading: false,
    error: null,
  },
  leaderboardAbortController: null,

  // Constants
  emptyFeatureCollection: { type: "FeatureCollection", features: [] },
  ADMIN_FETCH_DEBOUNCE_MS: 360,
  ADMIN_FETCH_IDLE_MS: 120,
};

// Initialize resolution from global config
if (window.__CITY_FOG_BASE_RESOLUTION__) {
  state.defaultVisitResolution = window.__CITY_FOG_BASE_RESOLUTION__;
  state.BASE_DISTRICT_RESOLUTION = window.__CITY_FOG_BASE_RESOLUTION__;
}
window.currentH3Resolution = state.defaultVisitResolution;

export function getGridKey(lat, lng) {
  const gridX = Math.floor(lng / state.GRID_SIZE);
  const gridY = Math.floor(lat / state.GRID_SIZE);
  return `${gridX}_${gridY}`;
}

export function addToSpatialIndex(hexId) {
  if (!hexId) return;
  try {
    const [lat, lng] = h3.cellToLatLng(hexId);
    const key = getGridKey(lat, lng);
    if (!state.spatialIndex.has(key)) {
      state.spatialIndex.set(key, new Set());
    }
    state.spatialIndex.get(key).add(hexId);
    state.fogDataChanged = true; // Mark data as changed
  } catch (e) {
    console.warn(`Failed to add hex ${hexId} to spatial index`, e);
  }
}

export function removeFromSpatialIndex(hexId) {
  if (!hexId) return;
  try {
    const [lat, lng] = h3.cellToLatLng(hexId);
    const key = getGridKey(lat, lng);
    if (state.spatialIndex.has(key)) {
      state.spatialIndex.get(key).delete(hexId);
      if (state.spatialIndex.get(key).size === 0) {
        state.spatialIndex.delete(key);
      }
      state.fogDataChanged = true; // Mark data as changed
    }
  } catch (e) {
    console.warn(`Failed to remove hex ${hexId} from spatial index`, e);
  }
}

export function cloneFeature(feature) {
  if (!feature) return null;
  try {
    return JSON.parse(JSON.stringify(feature));
  } catch (err) {
    console.warn("[admin] Failed to clone feature", err);
    return null;
  }
}

export function safeRound(value) {
  return Math.max(0, Math.round(value));
}

export function formatProgressSuffix(feature) {
  const props = feature?.properties;
  if (!props) return null;

  const cells = props.percent_cells;
  const weight = props.percent_weight;
  const cellsLabel =
    typeof cells === "number" && !Number.isNaN(cells)
      ? `${safeRound(cells)}%`
      : null;

  if (cellsLabel) return cellsLabel;

  const weightLabel =
    typeof weight === "number" && !Number.isNaN(weight)
      ? `${safeRound(weight)}% weight`
      : null;

  return weightLabel;
}

export function updateDistrictProgress(districtStats, okrugStats, { map }) {
  let needsRedraw = false;

  // Update district stats
  if (districtStats && typeof districtStats.id === "number") {
    const districtId = districtStats.id;
    const districtFeature = state.districtFeatureMap.get(districtId);
    if (districtFeature) {
      // Update progress properties
      if (typeof districtStats.visited_cells === "number") {
        districtFeature.properties.visited_cells = districtStats.visited_cells;
      }
      if (typeof districtStats.visited_weight === "number") {
        districtFeature.properties.visited_weight = districtStats.visited_weight;
      }

      // Calculate percentages if we have the data
      const totalCells = districtFeature.properties.total_cells;
      const totalWeight = districtFeature.properties.total_weight;
      if (typeof totalCells === "number" && totalCells > 0) {
        districtFeature.properties.percent_cells = Math.min(100, (districtStats.visited_cells / totalCells) * 100);
      }
      if (typeof totalWeight === "number" && totalWeight > 0) {
        districtFeature.properties.percent_weight = Math.min(100, (districtStats.visited_weight / totalWeight) * 100);
      }

      // Update overlay suffix for map display
      districtFeature.properties.overlay_suffix = formatProgressSuffix(districtFeature);
      needsRedraw = true;
    }
  }

  // Update okrug stats
  if (okrugStats && typeof okrugStats.id === "number") {
    const okrugId = okrugStats.id;
    const okrugFeature = state.okrugFeatureMap.get(okrugId);
    if (okrugFeature) {
      // Update progress properties
      if (typeof okrugStats.visited_cells === "number") {
        okrugFeature.properties.visited_cells = okrugStats.visited_cells;
      }
      if (typeof okrugStats.visited_weight === "number") {
        okrugFeature.properties.visited_weight = okrugStats.visited_weight;
      }

      // Calculate percentages if we have the data
      const totalCells = okrugFeature.properties.total_cells;
      const totalWeight = okrugFeature.properties.total_weight;
      if (typeof totalCells === "number" && totalCells > 0) {
        okrugFeature.properties.percent_cells = Math.min(100, (okrugStats.visited_cells / totalCells) * 100);
      }
      if (typeof totalWeight === "number" && totalWeight > 0) {
        okrugFeature.properties.percent_weight = Math.min(100, (okrugStats.visited_weight / totalWeight) * 100);
      }

      // Note: Okrugs don't have overlay_suffix in the current implementation
      needsRedraw = true;
    }
  }

  // Redraw map if any updates were made
  if (needsRedraw) {
    const districtSource = map.getSource('admin-districts');
    if (districtSource) {
      districtSource.setData(toFeatureCollection(Array.from(state.districtFeatureMap.values())));
    }

    const okrugSource = map.getSource('admin-okrugs');
    if (okrugSource) {
      okrugSource.setData(toFeatureCollection(Array.from(state.okrugFeatureMap.values())));
    }

    // Update status if currently selected district was affected
    if (state.selectedDistrictId != null) {
      const updatedFeature = state.districtFeatureMap.get(state.selectedDistrictId);
      if (updatedFeature) {
        state.selectedDistrictFeature = cloneFeature(updatedFeature);
        state.selectedDistrictName = state.selectedDistrictFeature.properties?.name || state.selectedDistrictName;
        // updateStatusForSelection(); // This will be handled in UI module
      }
    }
  }
}

export function toFeatureCollection(features = []) {
  return { type: "FeatureCollection", features };
}
