// Map module - handles MapLibre map initialization and all map-related functionality
import { state, updateDistrictProgress, toFeatureCollection, cloneFeature, formatProgressSuffix, safeRound, addToSpatialIndex } from './state.js';
import { updateHexagonsFromServer, fetchDistrictCellsRaw } from './api.js';

let map = null;
const geolocate = null;

const ADMIN_SOURCES = {
  districts: "admin-districts",
  okrugs: "admin-okrugs",
  selected: "selected-district",
  districtHex: "district-hex-grid",
};

const ADMIN_LAYERS = {
  districtFill: "district-fill",
  districtBorders: "district-borders",
  districtLabels: "district-labels",
  districtHitArea: "district-hit-area",
  okrugBorders: "okrug-borders",
  okrugFill: "okrug-fill",
  selectedFill: "selected-district-fill",
  selectedOutline: "selected-district-outline",
  hexFill: "selected-district-hex-fill",
  hexOutline: "selected-district-hex-outline",
};

export function initializeMap() {
  map = new maplibregl.Map({
    container: "map",
    style:
      "https://api.maptiler.com/maps/01999189-4baa-7e39-a599-8526afdd67ae/style.json?key=APpVE6JSa2fSgJJvdPyv",
    center: [37.6173, 55.7558],
    zoom: 12,
    maxBounds: [
      [36.0, 55.0],
      [39.0, 56.5],
    ],
  });
  map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }));

  const geolocateControl = new maplibregl.GeolocateControl({
    positionOptions: { enableHighAccuracy: true },
    trackUserLocation: false,
    showUserHeading: true,
    showAccuracyCircle: false,
    fitBoundsOptions: { maxZoom: 22 },
  });

  try {
    if (geolocateControl && geolocateControl._updateCamera) {
      geolocateControl._updateCamera = function () {};
    }
  } catch (_) {}

  map.addControl(geolocateControl);

  return map;
}

export function getMap() {
  return map;
}

function calculateFeatureAreaKm2(feature) {
  if (!feature?.geometry) return null;
  try {
    const areaSqMeters = turf.area(feature);
    if (typeof areaSqMeters !== "number" || Number.isNaN(areaSqMeters)) {
      return null;
    }
    return areaSqMeters / 1_000_000;
  } catch (err) {
    console.warn("[area] Failed to compute feature area", err);
    return null;
  }
}

function pickResByArea(areaKm2) {
  if (typeof areaKm2 !== "number" || Number.isNaN(areaKm2)) {
    return state.BASE_DISTRICT_RESOLUTION;
  }
  if (areaKm2 >= 3) {
    return Math.max(0, state.BASE_DISTRICT_RESOLUTION - 1);
  }
  return state.BASE_DISTRICT_RESOLUTION;
}

function getFeatureAreaKm2(feature) {
  if (!feature) return null;
  const stored = feature?.properties?.area_km2;
  if (typeof stored === "number" && !Number.isNaN(stored)) {
    return stored;
  }
  const computed = calculateFeatureAreaKm2(feature);
  if (feature?.properties) {
    feature.properties.area_km2 = computed;
  }
  return computed;
}

function mapDistrictApiFeature(raw) {
  if (!raw || !raw.geom) return null;
  const percentCells = Math.min(100,
    raw.progress?.percent_cells ?? raw.progress?.percent ?? 0);
  const percentWeight = Math.min(100,
    raw.progress?.percent_weight ?? 0);
  const feature = {
    type: "Feature",
    geometry: raw.geom,
    properties: {
      id: raw.id,
      name: raw.name,
      level: raw.level,
      parent_id: raw.parent_id,
      percent_cells: percentCells,
      percent_weight: percentWeight,
      visited_cells: raw.progress?.visited_cells ?? 0,
      total_cells: raw.progress?.total_cells ?? 0,
      visited_weight: raw.progress?.visited_weight ?? 0,
      total_weight: raw.progress?.total_weight ?? 0,
      bbox: raw.bbox ?? null,
    },
  };
  return feature;
}

export function ensureAdminSourcesAndLayers() {
  if (!map.getSource(ADMIN_SOURCES.okrugs)) {
    map.addSource(ADMIN_SOURCES.okrugs, {
      type: "geojson",
      data: state.emptyFeatureCollection,
    });
  }

  if (!map.getLayer(ADMIN_LAYERS.okrugFill)) {
    map.addLayer({
      id: ADMIN_LAYERS.okrugFill,
      type: "fill",
      source: ADMIN_SOURCES.okrugs,
      paint: {
        "fill-color": "#10b981",
        "fill-opacity": 0.05,
      },
      minzoom: 7,
      maxzoom: 10,
    });
  }

  if (!map.getLayer(ADMIN_LAYERS.okrugBorders)) {
    map.addLayer({
      id: ADMIN_LAYERS.okrugBorders,
      type: "line",
      source: ADMIN_SOURCES.okrugs,
      paint: {
        "line-color": "#0f172a",
        "line-width": ["interpolate", ["linear"], ["zoom"], 8, 3.5, 14, 4.5],
        "line-dasharray": [3, 2],
        "line-opacity": 0.9,
      },
    });
  }

  if (!map.getSource(ADMIN_SOURCES.districts)) {
    map.addSource(ADMIN_SOURCES.districts, {
      type: "geojson",
      data: state.emptyFeatureCollection,
    });
  }

  if (!map.getLayer(ADMIN_LAYERS.districtFill)) {
    map.addLayer({
      id: ADMIN_LAYERS.districtFill,
      type: "fill",
      source: ADMIN_SOURCES.districts,
      paint: {
        "fill-color": "#38bdf8",
        "fill-opacity": 0.08,
      },
      minzoom: 9,
      maxzoom: 11,
      layout: {
        visibility: "visible",
      },
    });
  }

  if (!map.getLayer(ADMIN_LAYERS.districtHitArea)) {
    map.addLayer({
      id: ADMIN_LAYERS.districtHitArea,
      type: "fill",
      source: ADMIN_SOURCES.districts,
      paint: {
        "fill-opacity": 0,
      },
      filter: ["==", ["get", "level"], "district"],
    });
  }

  // --- UPDATED: NATIVE MAPLIBRE LABELS ---
  if (!map.getLayer(ADMIN_LAYERS.districtLabels)) {
    map.addLayer({
      id: ADMIN_LAYERS.districtLabels,
      type: "symbol",
      source: ADMIN_SOURCES.districts,
      minzoom: 11.5,
      layout: {
        "visibility": "visible",
        "text-field": [
          "format",
          ["get", "name"],
          { "font-scale": 1.0, "text-font": ["literal", ["Inter Bold", "Arial Unicode MS Bold"]] },
          "\n",
          {},
          [
            "case",
            ["has", "overlay_suffix"],
            ["get", "overlay_suffix"],
            "",
          ],
          { "font-scale": 0.85, "text-font": ["literal", ["Inter Regular", "Arial Unicode MS Regular"]] },
        ],
        "text-size": 13,
        "text-allow-overlap": false,
        "text-ignore-placement": false,
      },
      paint: {
        "text-color": "#f8fafc",
        "text-halo-color": "#1e293b",
        "text-halo-width": 1.5,
        "text-halo-blur": 1,
      },
    });
  }
  // --- END UPDATED ---

  if (!map.getLayer(ADMIN_LAYERS.districtBorders)) {
    map.addLayer({
      id: ADMIN_LAYERS.districtBorders,
      type: "line",
      source: ADMIN_SOURCES.districts,
      paint: {
        "line-color": "#1f2937",
        "line-width": ["interpolate", ["linear"], ["zoom"], 8, 2.5, 14, 3.5],
        "line-blur": 0.5,
        "line-opacity": 0.95,
      },
    });
  }

  if (!map.getSource(ADMIN_SOURCES.selected)) {
    map.addSource(ADMIN_SOURCES.selected, {
      type: "geojson",
      data: state.emptyFeatureCollection,
    });
  }

  if (!map.getLayer(ADMIN_LAYERS.selectedFill)) {
    map.addLayer({
      id: ADMIN_LAYERS.selectedFill,
      type: "fill",
      source: ADMIN_SOURCES.selected,
      paint: {
        "fill-color": "#f97316",
        "fill-opacity": 0.12,
      },
    });
  }

  if (!map.getLayer(ADMIN_LAYERS.selectedOutline)) {
    map.addLayer({
      id: ADMIN_LAYERS.selectedOutline,
      type: "line",
      source: ADMIN_SOURCES.selected,
      paint: {
        "line-color": "#fb923c",
        "line-width": ["interpolate", ["linear"], ["zoom"], 8, 3.2, 14, 5],
        "line-opacity": 0.9,
      },
    });
  }

  if (!map.getSource(ADMIN_SOURCES.districtHex)) {
    map.addSource(ADMIN_SOURCES.districtHex, {
      type: "geojson",
      data: state.emptyFeatureCollection,
    });
  }

  if (!map.getLayer(ADMIN_LAYERS.hexFill)) {
    map.addLayer({
      id: ADMIN_LAYERS.hexFill,
      type: "fill",
      source: ADMIN_SOURCES.districtHex,
      paint: {
        "fill-color": [
          "case",
          ["boolean", ["get", "visited"], false],
          "#22c55e",
          "#94a3b8",
        ],
        "fill-opacity": [
          "case",
          ["boolean", ["get", "visited"], false],
          0.55,
          [
            "interpolate",
            ["linear"],
            ["coalesce", ["get", "coverage"], 0],
            0,
            0.06,
            1,
            0.35,
          ],
        ],
      },
    });
  }

  if (!map.getLayer(ADMIN_LAYERS.hexOutline)) {
    map.addLayer({
      id: ADMIN_LAYERS.hexOutline,
      type: "line",
      source: ADMIN_SOURCES.districtHex,
      paint: {
        "line-color": "#1f2937",
        "line-width": 0.65,
        "line-opacity": 0.5,
      },
    });
  }
}

export function loadAllDistricts() {
  if (!map || !map.isStyleLoaded()) return;
  ensureAdminSourcesAndLayers();

  if (state.adminFetchAbortController) {
    state.adminFetchAbortController.abort();
  }

  const controller = new AbortController();
  state.adminFetchAbortController = controller;
  const currentSeq = ++state.adminRequestSeq;

  fetch(`/api/v1/districts/all`, {
    signal: controller.signal,
    headers: { Accept: "application/json" }, // getAuthHeaders will be called from api module
  })
    .then(async (response) => {
      if (controller.signal.aborted) return;
      if (!response.ok)
        throw new Error(`districts fetch failed: ${response.status}`);

      const allDistrictsData = await response.json();

      if (controller.signal.aborted || currentSeq !== state.adminRequestSeq) return;

      const okrugFeatures = [];
      state.okrugFeatureMap.clear();
      const districtFeatures = [];
      state.districtFeatureMap.clear();

      allDistrictsData.forEach((raw) => {
        const feature = mapDistrictApiFeature(raw);
        if (feature) {
          if (raw.level === 'okrug') {
            okrugFeatures.push(feature);
            state.okrugFeatureMap.set(raw.id, feature);
          } else if (raw.level === 'district') {
            getFeatureAreaKm2(feature); // Pre-calculate area
            feature.properties.overlay_suffix = formatProgressSuffix(feature);
            districtFeatures.push(feature);
            state.districtFeatureMap.set(raw.id, feature);
          }
        }
      });

      const okrugSource = map.getSource(ADMIN_SOURCES.okrugs);
      if (okrugSource) {
        okrugSource.setData(toFeatureCollection(okrugFeatures));
      }

      const districtSource = map.getSource(ADMIN_SOURCES.districts);
      if (districtSource) {
        districtSource.setData(toFeatureCollection(districtFeatures));
      }

      if (state.selectedDistrictId != null) {
        const updatedFeature = state.districtFeatureMap.get(state.selectedDistrictId);
        if (updatedFeature) {
          state.selectedDistrictFeature = cloneFeature(updatedFeature);
          state.selectedDistrictName =
            state.selectedDistrictFeature.properties?.name || state.selectedDistrictName;
          updateSelectedDistrictHighlight();
          // updateStatusForSelection(); // Will be handled in UI
        } else {
          clearDistrictSelection();
          updateDistrictHexLayer(state.emptyFeatureCollection);
        }
      }
    })
    .catch((error) => {
      if (controller.signal.aborted) return;
      console.warn("[admin] Failed to load all districts:", error);
    })
    .finally(() => {
      if (controller === state.adminFetchAbortController) {
        state.adminFetchAbortController = null;
      }
    });
}

export function refreshAdminLayers() {
  if (!map || !map.isStyleLoaded()) return;
  ensureAdminSourcesAndLayers();

  if (state.adminFetchAbortController) {
    state.adminFetchAbortController.abort();
  }

  const bounds = map.getBounds();
  const bbox = [
    bounds.getWest(),
    bounds.getSouth(),
    bounds.getEast(),
    bounds.getNorth(),
  ].join(",");

  const controller = new AbortController();
  state.adminFetchAbortController = controller;
  const currentSeq = ++state.adminRequestSeq;

  // This will need to be updated to use proper auth headers from api module
  const requestOptions = {
    signal: controller.signal,
    headers: { Accept: "application/json" },
  };

  const okrugPromise = fetch(
    `/api/v1/districts?bbox=${bbox}&level=okrug`,
    requestOptions,
  );
  const districtPromise = fetch(
    `/api/v1/districts?bbox=${bbox}&level=district`,
    requestOptions,
  );

  Promise.all([okrugPromise, districtPromise])
    .then(async ([okrugRes, districtRes]) => {
      if (controller.signal.aborted) return;
      if (!okrugRes.ok)
        throw new Error(`okrug fetch failed: ${okrugRes.status}`);
      if (!districtRes.ok)
        throw new Error(`district fetch failed: ${districtRes.status}`);

      const [okrugData, districtData] = await Promise.all([
        okrugRes.json(),
        districtRes.json(),
      ]);

      if (controller.signal.aborted || currentSeq !== state.adminRequestSeq) return;

      const okrugFeatures = [];
      state.okrugFeatureMap.clear();
      okrugData.forEach((raw) => {
        const feature = mapDistrictApiFeature(raw);
        if (feature) {
          okrugFeatures.push(feature);
          state.okrugFeatureMap.set(raw.id, feature);
        }
      });

      const districtFeatures = [];
      state.districtFeatureMap.clear();
      districtData.forEach((raw) => {
        const feature = mapDistrictApiFeature(raw);
        if (feature) {
          getFeatureAreaKm2(feature); // Pre-calculate area
          feature.properties.overlay_suffix = formatProgressSuffix(feature);
          districtFeatures.push(feature);
          state.districtFeatureMap.set(raw.id, feature);
        }
      });

      const okrugSource = map.getSource(ADMIN_SOURCES.okrugs);
      if (okrugSource) {
        okrugSource.setData(toFeatureCollection(okrugFeatures));
      }

      const districtSource = map.getSource(ADMIN_SOURCES.districts);
      if (districtSource) {
        districtSource.setData(toFeatureCollection(districtFeatures));
      }

      if (state.selectedDistrictId != null) {
        const updatedFeature = state.districtFeatureMap.get(state.selectedDistrictId);
        if (updatedFeature) {
          state.selectedDistrictFeature = cloneFeature(updatedFeature);
          state.selectedDistrictName =
            state.selectedDistrictFeature.properties?.name || state.selectedDistrictName;
          updateSelectedDistrictHighlight();
          // updateStatusForSelection(); // Will be handled in UI
        } else {
          clearDistrictSelection();
          updateDistrictHexLayer(state.emptyFeatureCollection);
        }
      }
    })
    .catch((error) => {
      if (controller.signal.aborted) return;
      console.warn("[admin] Failed to refresh admin layers:", error);
    })
    .finally(() => {
      if (controller === state.adminFetchAbortController) {
        state.adminFetchAbortController = null;
      }
    });
}

export function scheduleAdminRefresh(isMapMoving = false) {
  if (state.adminUpdateTimer) {
    clearTimeout(state.adminUpdateTimer);
    state.adminUpdateTimer = null;
  }
  const delay = isMapMoving ? state.ADMIN_FETCH_IDLE_MS : state.ADMIN_FETCH_DEBOUNCE_MS;
  state.adminUpdateTimer = setTimeout(() => {
    state.adminUpdateTimer = null;
    refreshAdminLayers();
  }, delay);
}

function updateSelectedDistrictHighlight() {
  const selectedSource = map.getSource(ADMIN_SOURCES.selected);
  if (!selectedSource) return;
  if (state.selectedDistrictFeature) {
    selectedSource.setData(toFeatureCollection([state.selectedDistrictFeature]));
  } else {
    selectedSource.setData(state.emptyFeatureCollection);
  }
}

function updateDistrictHexLayer(featureCollection) {
  const source = map.getSource(ADMIN_SOURCES.districtHex);
  if (!source) return;
  source.setData(featureCollection || state.emptyFeatureCollection);
}

function buildHexFeatureCollection(cells, districtId) {
  const features = [];
  if (Array.isArray(cells)) {
    cells.forEach((cell) => {
      if (!cell || !cell.h3) return;
      try {
        const boundary = h3.cellToBoundary(cell.h3, true);
        if (!Array.isArray(boundary) || boundary.length === 0) return;
        const coordinates = boundary.map(([lat, lng]) => [lng, lat]);
        if (coordinates.length > 0) {
          coordinates.push(coordinates[0]);
        }
        features.push({
          type: "Feature",
          geometry: {
            type: "Polygon",
            coordinates: [coordinates],
          },
          properties: {
            h3: cell.h3,
            coverage: typeof cell.coverage === "number" ? cell.coverage : 0,
            visited: !!cell.visited,
            visited_children: cell.visited_children ?? null,
            total_children: cell.total_children ?? null,
            visited_fraction: cell.visited_fraction ?? null,
            district_id: districtId,
          },
        });
      } catch (err) {
        console.warn("[h3] Failed to build hex geometry", cell?.h3, err);
      }
    });
  }
  return toFeatureCollection(features);
}

export function handleDistrictSelection(feature) {
  if (!feature || !feature.properties) return;
  const districtId = Number(feature.properties.id);
  if (!Number.isFinite(districtId)) return;
  if (state.selectedDistrictId === districtId) {
    clearDistrictSelection();
    return;
  }

  state.selectedDistrictId = districtId;
  state.selectedDistrictFeature = cloneFeature(
    state.districtFeatureMap.get(districtId) || feature,
  );
  state.selectedDistrictName =
    state.selectedDistrictFeature?.properties?.name || `District ${districtId}`;

  updateSelectedDistrictHighlight();
  // updateStatusForSelection(); // Will be handled in UI

  const effectiveResView =
    state.selectedDistrictResView != null
      ? state.selectedDistrictResView
      : pickResByArea(getFeatureAreaKm2(state.selectedDistrictFeature));
  const cacheKey = `${districtId}@${effectiveResView}`;
  const cached = state.districtCellsCache.get(cacheKey);
  if (cached) {
    updateDistrictHexLayer(cached.featureCollection);
    if (cached.meta?.district?.progress) {
      const progress = cached.meta.district.progress;
      const percentCells = progress.percent_cells ?? progress.percent;
      state.selectedDistrictFeature.properties.percent_cells = Math.min(100,
        percentCells ?? state.selectedDistrictFeature.properties.percent_cells);
      state.selectedDistrictFeature.properties.percent_weight = Math.min(100,
        progress.percent_weight ??
        state.selectedDistrictFeature.properties.percent_weight);
      // updateStatusForSelection(); // Will be handled in UI
    }
    return;
  }

  const areaKm2 = getFeatureAreaKm2(state.selectedDistrictFeature);
  const desiredRes = pickResByArea(areaKm2);
  state.selectedDistrictResView = desiredRes;
  const targetCacheKey = `${districtId}@${desiredRes}`;
  const cachedForRes = state.districtCellsCache.get(targetCacheKey);
  if (cachedForRes) {
    updateDistrictHexLayer(cachedForRes.featureCollection);
    if (cachedForRes.meta?.district?.progress) {
      const progress = cachedForRes.meta.district.progress;
      const percentCells = progress.percent_cells ?? progress.percent;
      state.selectedDistrictFeature.properties.percent_cells = Math.min(100,
        percentCells ?? state.selectedDistrictFeature.properties.percent_cells);
      state.selectedDistrictFeature.properties.percent_weight = Math.min(100,
        progress.percent_weight ??
        state.selectedDistrictFeature.properties.percent_weight);
      // updateStatusForSelection(); // Will be handled in UI
    }
    return;
  }

  fetchDistrictCells(districtId, desiredRes);
}

function clearDistrictSelection() {
  if (state.selectedDistrictAbortController) {
    state.selectedDistrictAbortController.abort();
    state.selectedDistrictAbortController = null;
  }
  const wasSelected = state.selectedDistrictId != null;
  const previousId = state.selectedDistrictId;
  state.selectedDistrictId = null;
  state.selectedDistrictName = "";
  state.selectedDistrictFeature = null;
  state.selectedDistrictResView = null;
  updateSelectedDistrictHighlight();
  updateDistrictHexLayer(state.emptyFeatureCollection);
  // updateStatusForSelection(); // Will be handled in UI
  if (wasSelected && previousId != null) {
    const deleteKeys = [];
    state.districtCellsCache.forEach((_, key) => {
      if (typeof key === "string" && key.startsWith(`${previousId}@`)) {
        deleteKeys.push(key);
      }
    });
    deleteKeys.forEach((key) => state.districtCellsCache.delete(key));
  }
}

function fetchDistrictCells(districtId, resView = null) {
  if (state.selectedDistrictAbortController) {
    state.selectedDistrictAbortController.abort();
  }
  const controller = new AbortController();
  state.selectedDistrictAbortController = controller;

  fetchDistrictCellsRaw(districtId, resView, { signal: controller.signal })
    .then(({ payload, resValue, featureCollection }) => {
      if (controller.signal.aborted) return;
      if (state.selectedDistrictId === districtId) {
        state.selectedDistrictResView = resValue;
        const actualFeatureCollection = featureCollection || buildHexFeatureCollection(
          payload.cells || [],
          districtId,
        );
        state.districtCellsCache.set(`${districtId}@${resValue}`, { featureCollection: actualFeatureCollection, meta: payload });

        if (payload?.district?.progress) {
          state.selectedDistrictFeature.properties.percent_cells = Math.min(100,
            payload.district.progress.percent_cells ??
            payload.district.progress.percent ??
            state.selectedDistrictFeature.properties.percent_cells);
          state.selectedDistrictFeature.properties.percent_weight = Math.min(100,
            payload.district.progress.percent_weight ??
            state.selectedDistrictFeature.properties.percent_weight);

          const existingFeature = state.districtFeatureMap.get(districtId);
          if (existingFeature) {
              existingFeature.properties.percent_cells = state.selectedDistrictFeature.properties.percent_cells;
              existingFeature.properties.percent_weight = state.selectedDistrictFeature.properties.percent_weight;
              existingFeature.properties.overlay_suffix = formatProgressSuffix(existingFeature);
          }
        }
        updateDistrictHexLayer(actualFeatureCollection);
        // updateStatusForSelection(); // Will be handled in UI
      }
    })
    .catch((error) => {
      if (controller.signal.aborted) return;
      console.warn("[district] Failed to fetch district cells", error);
    })
    .finally(() => {
      if (state.selectedDistrictAbortController === controller) {
        state.selectedDistrictAbortController = null;
      }
    });
}

export async function revealEntireDistrict(districtId, { updateHexagonsFromServer, addToSpatialIndex, updateDistrictProgress, countEl, forceFogRedraw, allKnownHexagons }) {
  // Always fetch cells with the server's base resolution to get all cells
  const serverBaseResolution = window.__CITY_FOG_BASE_RESOLUTION__ || 10;
  const detailed = await fetchDistrictCellsRaw(
    districtId,
    serverBaseResolution,
  );
  const meta = detailed.payload || detailed.meta;
  if (!meta || !Array.isArray(meta.cells) || meta.cells.length === 0) {
    throw new Error("No cells available for district");
  }

  const revealCells = meta.cells;

  await revealDistrictViaVisits(revealCells, {
    addToSpatialIndex,
    updateDistrictProgress,
    countEl,
    forceFogRedraw,
    allKnownHexagons
  });

  // Refresh the display after revealing
  const areaKm2 = getFeatureAreaKm2(state.selectedDistrictFeature);
  const desiredRes = pickResByArea(areaKm2);
  await Promise.all([
    updateHexagonsFromServer(),
    fetchDistrictCells(districtId, desiredRes),
  ]);
  // Note: District layers are now loaded statically and don't need refresh
}

async function revealDistrictViaVisits(cells, { addToSpatialIndex, updateDistrictProgress, countEl, forceFogRedraw, allKnownHexagons }) {
  // Note: map is available in module scope
  if (!Array.isArray(cells) || cells.length === 0) return;
  let hasChanges = false;
  for (let i = 0; i < cells.length; i++) {
    const cell = cells[i];
    if (!cell || !cell.h3) continue;
    if (allKnownHexagons.has(cell.h3)) continue;
    const [lat, lng] = h3.cellToLatLng(cell.h3);
    try {
      const response = await fetch("/api/v1/visit", {
        method: "POST",
        headers: { "Content-Type": "application/json" }, // getAuthHeaders will be called from api module
        body: JSON.stringify({ lat, lon: lng }),
      });
      if (response.ok) {
        const result = await response.json();

        // Add to known hexagons if not already there (sync with server state)
        if (!allKnownHexagons.has(cell.h3)) {
          allKnownHexagons.add(cell.h3);
          addToSpatialIndex(cell.h3);
          hasChanges = true;
        }

        if (result.added > 0) {
          const total =
            result.stats && typeof result.stats.total_circles === "number"
              ? result.stats.total_circles
              : allKnownHexagons.size;
          countEl.textContent = Number(total).toLocaleString();
        }

        // Update district progress with stats from response (always, since stats may change)
        if (result.stats) {
          updateDistrictProgress(result.stats.district, result.stats.okrug);
        }
      }
    } catch (err) {
      console.warn("[debug] reveal visit failed", { cell: cell.h3, err });
    }
  }
  if (hasChanges) {
    forceFogRedraw();
    if (map) map.triggerRepaint();
  }
}
