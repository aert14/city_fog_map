(async function () {
  const tg =
    window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  if (tg) {
    try {
      tg.ready();
    } catch (_) {}
  }

  async function getDebugSettings() {
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

  const hasInitData = !!(tg && tg.initData);
  const { noAuthMode, debugAuthMode, baseVisitResolution } =
    await getDebugSettings();

  if (!hasInitData && !noAuthMode) {
    document.getElementById("app").innerHTML = `
      <div style="display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100%; text-align: center; font-family: sans-serif; color: #ccc;">
        <h2 style="margin: 0; color: #eee;">Oops!</h2>
        <p style="margin: 8px 0 0;">Could not initialize the application.<br>Please make sure you are running this inside Telegram.</p>
      </div>
    `;
    return;
  }

  // --- UI & Config ---
  const openBtn = document.getElementById("openBtn");
  const leaderboardBtn = document.getElementById("leaderboardBtn");
  const leaderboardOverlay = document.getElementById("leaderboard-overlay");
  const leaderboardCloseBtn = document.getElementById("leaderboardCloseBtn");
  const leaderboardLevelSelect = document.getElementById("leaderboardLevel");
  const leaderboardPeriodSelect = document.getElementById("leaderboardPeriod");
  const leaderboardStatus = document.getElementById("leaderboardStatus");
  const leaderboardBody = document.getElementById("leaderboardBody");
  const toggleFogBtn = document.getElementById("toggleFogBtn");
  const countEl = document.getElementById("count");
  const statusEl = document.getElementById("status");
  const fogCanvas = document.getElementById("fog-canvas");
  const fogCtx = fogCanvas.getContext("2d");
  const DPR = window.devicePixelRatio || 1;

  const FOG_CONFIG = {
    shadowBlur: 55,
    revealBlur: 40,
    pulseAmplitude: 0.04,
    animationSpeed: 0.0015,
  };

  // --- Map Initialization ---
  const map = new maplibregl.Map({
    container: "map",
    style:
      "https://api.maptiler.com/maps/pastel/style.json?key=TFV5uV6DVVucu16gTZdi",
    center: [37.6173, 55.7558],
    zoom: 12,
    maxBounds: [
      [36.0, 55.0],
      [39.0, 56.5],
    ],
  });
  map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }));
  const geolocate = new maplibregl.GeolocateControl({
    positionOptions: { enableHighAccuracy: true },
    trackUserLocation: false,
    showUserHeading: true,
    showAccuracyCircle: false,
    fitBoundsOptions: { maxZoom: 22 },
  });
  try {
    if (geolocate && geolocate._updateCamera) {
      geolocate._updateCamera = function () {};
    }
  } catch (_) {}
  map.addControl(geolocate);

  // --- Application State ---
  const allKnownHexagons = new Set();
  let isFetching = false;
  let fogEnabled = true;
  let animationTime = 0;
  const defaultVisitResolution =
    baseVisitResolution || window.__CITY_FOG_BASE_RESOLUTION__ || 8;
  const BASE_DISTRICT_RESOLUTION = defaultVisitResolution;
  window.currentH3Resolution = defaultVisitResolution;
  let ignoreNextClick = false;
  const emptyFeatureCollection = { type: "FeatureCollection", features: [] };
  const ADMIN_FETCH_DEBOUNCE_MS = 360;
  const ADMIN_FETCH_IDLE_MS = 120;
  let adminUpdateTimer = null;
  let adminRequestSeq = 0;
  let adminFetchAbortController = null;
  let selectedDistrictId = null;
  let selectedDistrictName = "";
  let selectedDistrictFeature = null;
  let selectedDistrictAbortController = null;
  let selectedDistrictResView = null;
  const DEFAULT_STATUS_TEXT = "Select a district";
  const districtFeatureMap = new Map();
  const okrugFeatureMap = new Map();
  const districtCellsCache = new Map();
  let statusOverrideMessage = null;

  // --- NEW: Spatial Index for Hexagons ---
  const spatialIndex = new Map();
  const GRID_SIZE = 0.25; // Size of the grid cell in degrees. Tune if needed.

  function getGridKey(lat, lng) {
    const gridX = Math.floor(lng / GRID_SIZE);
    const gridY = Math.floor(lat / GRID_SIZE);
    return `${gridX}_${gridY}`;
  }

  function addToSpatialIndex(hexId) {
    if (!hexId) return;
    try {
      const [lat, lng] = h3.cellToLatLng(hexId);
      const key = getGridKey(lat, lng);
      if (!spatialIndex.has(key)) {
        spatialIndex.set(key, new Set());
      }
      spatialIndex.get(key).add(hexId);
    } catch (e) {
      console.warn(`Failed to add hex ${hexId} to spatial index`, e);
    }
  }

  function removeFromSpatialIndex(hexId) {
    if (!hexId) return;
    try {
      const [lat, lng] = h3.cellToLatLng(hexId);
      const key = getGridKey(lat, lng);
      if (spatialIndex.has(key)) {
        spatialIndex.get(key).delete(hexId);
        if (spatialIndex.get(key).size === 0) {
          spatialIndex.delete(key);
        }
      }
    } catch (e) {
      console.warn(`Failed to remove hex ${hexId} from spatial index`, e);
    }
  }
  // --- END: Spatial Index ---

  const leaderboardState = {
    isOpen: false,
    level: "district",
    period: "week",
    entries: [],
    loading: false,
    error: null,
  };
  let leaderboardAbortController = null;

  function showLeaderboard() {
    if (!leaderboardOverlay) return;
    leaderboardOverlay.classList.add("visible");
    leaderboardState.isOpen = true;
    leaderboardStatus.textContent = "";
    fetchLeaderboard();
  }

  function hideLeaderboard() {
    if (!leaderboardOverlay) return;
    leaderboardOverlay.classList.remove("visible");
    leaderboardState.isOpen = false;
    if (leaderboardAbortController) {
      leaderboardAbortController.abort();
      leaderboardAbortController = null;
    }
  }

  function renderLeaderboard(entries) {
    if (!leaderboardBody) return;
    leaderboardBody.innerHTML = "";
    if (!Array.isArray(entries) || entries.length === 0) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 6;
      cell.textContent = "No data yet. Explore the city to be the first!";
      cell.style.textAlign = "center";
      cell.style.padding = "20px";
      row.appendChild(cell);
      leaderboardBody.appendChild(row);
      return;
    }

    entries.forEach((entry) => {
      const row = document.createElement("tr");

      const rankCell = document.createElement("td");
      rankCell.textContent = entry.rank ?? "-";
      rankCell.className = "rank";
      row.appendChild(rankCell);

      const userCell = document.createElement("td");
      userCell.textContent = entry.username || `User ${entry.user_id ?? ""}`;
      userCell.className = "username";
      row.appendChild(userCell);

      const cellsCell = document.createElement("td");
      cellsCell.textContent =
        typeof entry.visited_cells === "number"
          ? entry.visited_cells.toLocaleString("ru-RU")
          : "-";
      row.appendChild(cellsCell);

      const weightCell = document.createElement("td");
      weightCell.textContent =
        typeof entry.visited_weight === "number"
          ? entry.visited_weight.toFixed(2)
          : "-";
      row.appendChild(weightCell);

      const percentCellsCell = document.createElement("td");
      percentCellsCell.textContent =
        typeof entry.percent_cells === "number"
          ? `${safeRound(entry.percent_cells)}%`
          : "-";
      row.appendChild(percentCellsCell);

      const percentWeightCell = document.createElement("td");
      percentWeightCell.textContent =
        typeof entry.percent_weight === "number"
          ? `${safeRound(entry.percent_weight)}%`
          : "-";
      row.appendChild(percentWeightCell);

      leaderboardBody.appendChild(row);
    });
  }

  function setLeaderboardLoading(isLoading) {
    leaderboardState.loading = isLoading;
    if (leaderboardStatus) {
      leaderboardStatus.textContent = isLoading
        ? "Loading…"
        : leaderboardState.error || "";
      leaderboardStatus.style.color = leaderboardState.error
        ? "#f87171"
        : "inherit";
    }
  }

  async function fetchLeaderboard() {
    if (!leaderboardState.isOpen) return;

    if (leaderboardAbortController) {
      leaderboardAbortController.abort();
    }
    const controller = new AbortController();
    leaderboardAbortController = controller;

    setLeaderboardLoading(true);
    leaderboardState.error = null;

    const params = new URLSearchParams({
      level: leaderboardState.level,
      period: leaderboardState.period,
    });

    try {
      const response = await fetch(`/api/v1/leaderboard?${params.toString()}`, {
        signal: controller.signal,
        headers: getAuthHeaders({ Accept: "application/json" }),
      });
      if (!response.ok) {
        throw new Error(`Request failed: ${response.status}`);
      }
      const payload = await response.json();
      if (!controller.signal.aborted) {
        leaderboardState.entries = Array.isArray(payload?.entries)
          ? payload.entries
          : [];
        renderLeaderboard(leaderboardState.entries);
        setLeaderboardLoading(false);
      }
    } catch (error) {
      if (controller.signal.aborted) return;
      console.warn("[leaderboard] Failed to fetch leaderboard:", error);
      leaderboardState.error = "Unable to load leaderboard.";
      renderLeaderboard([]);
      setLeaderboardLoading(false);
    }
  }

  function handleLeaderboardLevelChange(event) {
    const value = event?.target?.value;
    if (!value || !["district", "okrug"].includes(value)) return;
    leaderboardState.level = value;
    fetchLeaderboard();
  }

  function handleLeaderboardPeriodChange(event) {
    const value = event?.target?.value;
    if (!value || !["week", "season"].includes(value)) return;
    leaderboardState.period = value;
    fetchLeaderboard();
  }

  function handleLeaderboardKey(event) {
    if (event.key === "Escape" && leaderboardState.isOpen) {
      hideLeaderboard();
    }
  }

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

  function getAuthHeaders(custom = {}) {
    const headers = { ...custom };
    if (tg && tg.initData) {
      headers["X-Telegram-Init"] = tg.initData;
    }
    if (typeof window.currentH3Resolution === "number") {
      headers["X-H3-Resolution"] = String(window.currentH3Resolution);
    }
    return headers;
  }

  function cloneFeature(feature) {
    if (!feature) return null;
    try {
      return JSON.parse(JSON.stringify(feature));
    } catch (err) {
      console.warn("[admin] Failed to clone feature", err);
      return null;
    }
  }

  function safeRound(value) {
    return Math.max(0, Math.round(value));
  }

  function formatProgressSuffix(feature) {
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

  function ensureAdminSourcesAndLayers() {
    if (!map.getSource(ADMIN_SOURCES.okrugs)) {
      map.addSource(ADMIN_SOURCES.okrugs, {
        type: "geojson",
        data: emptyFeatureCollection,
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
        data: emptyFeatureCollection,
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
        data: emptyFeatureCollection,
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
        data: emptyFeatureCollection,
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

  function toFeatureCollection(features = []) {
    return { type: "FeatureCollection", features };
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
      return BASE_DISTRICT_RESOLUTION;
    }
    if (areaKm2 >= 3) {
      return Math.max(0, BASE_DISTRICT_RESOLUTION - 1);
    }
    return BASE_DISTRICT_RESOLUTION;
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
    const percentCells =
      raw.progress?.percent_cells ?? raw.progress?.percent ?? 0;
    const percentWeight = raw.progress?.percent_weight ?? 0;
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

  function refreshAdminLayers() {
    if (!map || !map.isStyleLoaded()) return;
    ensureAdminSourcesAndLayers();

    if (adminFetchAbortController) {
      adminFetchAbortController.abort();
    }

    const bounds = map.getBounds();
    const bbox = [
      bounds.getWest(),
      bounds.getSouth(),
      bounds.getEast(),
      bounds.getNorth(),
    ].join(",");

    const controller = new AbortController();
    adminFetchAbortController = controller;
    const currentSeq = ++adminRequestSeq;
    const requestOptions = {
      signal: controller.signal,
      headers: getAuthHeaders({ Accept: "application/json" }),
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

        if (controller.signal.aborted || currentSeq !== adminRequestSeq) return;

        const okrugFeatures = [];
        okrugFeatureMap.clear();
        okrugData.forEach((raw) => {
          const feature = mapDistrictApiFeature(raw);
          if (feature) {
            okrugFeatures.push(feature);
            okrugFeatureMap.set(raw.id, feature);
          }
        });

        const districtFeatures = [];
        districtFeatureMap.clear();
        districtData.forEach((raw) => {
          const feature = mapDistrictApiFeature(raw);
          if (feature) {
            getFeatureAreaKm2(feature); // Pre-calculate area
            feature.properties.overlay_suffix = formatProgressSuffix(feature);
            districtFeatures.push(feature);
            districtFeatureMap.set(raw.id, feature);
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

        if (selectedDistrictId != null) {
          const updatedFeature = districtFeatureMap.get(selectedDistrictId);
          if (updatedFeature) {
            selectedDistrictFeature = cloneFeature(updatedFeature);
            selectedDistrictName =
              selectedDistrictFeature.properties?.name || selectedDistrictName;
            updateSelectedDistrictHighlight();
            updateStatusForSelection();
          } else {
            clearDistrictSelection();
            updateDistrictHexLayer(emptyFeatureCollection);
            if (!statusOverrideMessage) setStatus(DEFAULT_STATUS_TEXT);
          }
        } else if (!statusOverrideMessage) {
          setStatus(DEFAULT_STATUS_TEXT);
        }
      })
      .catch((error) => {
        if (controller.signal.aborted) return;
        console.warn("[admin] Failed to refresh admin layers:", error);
      })
      .finally(() => {
        if (controller === adminFetchAbortController) {
          adminFetchAbortController = null;
        }
      });
  }

  function scheduleAdminRefresh(isMapMoving = false) {
    if (adminUpdateTimer) {
      clearTimeout(adminUpdateTimer);
      adminUpdateTimer = null;
    }
    const delay = isMapMoving ? ADMIN_FETCH_IDLE_MS : ADMIN_FETCH_DEBOUNCE_MS;
    adminUpdateTimer = setTimeout(() => {
      adminUpdateTimer = null;
      refreshAdminLayers();
    }, delay);
  }

  function updateSelectedDistrictHighlight() {
    const selectedSource = map.getSource(ADMIN_SOURCES.selected);
    if (!selectedSource) return;
    if (selectedDistrictFeature) {
      selectedSource.setData(toFeatureCollection([selectedDistrictFeature]));
    } else {
      selectedSource.setData(emptyFeatureCollection);
    }
  }

  function updateStatusForSelection() {
    if (!selectedDistrictId || !selectedDistrictFeature) {
      if (!statusOverrideMessage) setStatus(DEFAULT_STATUS_TEXT);
      return;
    }
    const suffix = formatProgressSuffix(selectedDistrictFeature);
    const label = suffix
      ? `${selectedDistrictName} • ${suffix}`
      : selectedDistrictName;
    setStatus(label || DEFAULT_STATUS_TEXT);
  }

  function handleDistrictSelection(feature) {
    if (!feature || !feature.properties) return;
    const districtId = Number(feature.properties.id);
    if (!Number.isFinite(districtId)) return;
    if (selectedDistrictId === districtId) {
      clearDistrictSelection();
      return;
    }

    selectedDistrictId = districtId;
    selectedDistrictFeature = cloneFeature(
      districtFeatureMap.get(districtId) || feature,
    );
    selectedDistrictName =
      selectedDistrictFeature?.properties?.name || `District ${districtId}`;

    updateSelectedDistrictHighlight();
    updateStatusForSelection();

    const effectiveResView =
      selectedDistrictResView != null
        ? selectedDistrictResView
        : pickResByArea(getFeatureAreaKm2(selectedDistrictFeature));
    const cacheKey = `${districtId}@${effectiveResView}`;
    const cached = districtCellsCache.get(cacheKey);
    if (cached) {
      updateDistrictHexLayer(cached.featureCollection);
      if (cached.meta?.district?.progress) {
        const progress = cached.meta.district.progress;
        const percentCells = progress.percent_cells ?? progress.percent;
        selectedDistrictFeature.properties.percent_cells =
          percentCells ?? selectedDistrictFeature.properties.percent_cells;
        selectedDistrictFeature.properties.percent_weight =
          progress.percent_weight ??
          selectedDistrictFeature.properties.percent_weight;
        updateStatusForSelection();
      }
      return;
    }

    const areaKm2 = getFeatureAreaKm2(selectedDistrictFeature);
    const desiredRes = pickResByArea(areaKm2);
    selectedDistrictResView = desiredRes;
    const targetCacheKey = `${districtId}@${desiredRes}`;
    const cachedForRes = districtCellsCache.get(targetCacheKey);
    if (cachedForRes) {
      updateDistrictHexLayer(cachedForRes.featureCollection);
      if (cachedForRes.meta?.district?.progress) {
        const progress = cachedForRes.meta.district.progress;
        const percentCells = progress.percent_cells ?? progress.percent;
        selectedDistrictFeature.properties.percent_cells =
          percentCells ?? selectedDistrictFeature.properties.percent_cells;
        selectedDistrictFeature.properties.percent_weight =
          progress.percent_weight ??
          selectedDistrictFeature.properties.percent_weight;
        updateStatusForSelection();
      }
      return;
    }

    fetchDistrictCells(districtId, desiredRes);
  }

  function clearDistrictSelection() {
    if (selectedDistrictAbortController) {
      selectedDistrictAbortController.abort();
      selectedDistrictAbortController = null;
    }
    const wasSelected = selectedDistrictId != null;
    const previousId = selectedDistrictId;
    selectedDistrictId = null;
    selectedDistrictName = "";
    selectedDistrictFeature = null;
    selectedDistrictResView = null;
    updateSelectedDistrictHighlight();
    updateDistrictHexLayer(emptyFeatureCollection);
    updateStatusForSelection();
    if (wasSelected && previousId != null) {
      const deleteKeys = [];
      districtCellsCache.forEach((_, key) => {
        if (typeof key === "string" && key.startsWith(`${previousId}@`)) {
          deleteKeys.push(key);
        }
      });
      deleteKeys.forEach((key) => districtCellsCache.delete(key));
    }
  }

  function fetchDistrictCells(districtId, resView = null) {
    if (selectedDistrictAbortController) {
      selectedDistrictAbortController.abort();
    }
    setStatus(`Loading ${selectedDistrictName}…`, { temporary: true });
    const controller = new AbortController();
    selectedDistrictAbortController = controller;

    fetchDistrictCellsRaw(districtId, resView, { signal: controller.signal })
      .then(({ payload, resValue, featureCollection }) => {
        if (controller.signal.aborted) return;
        if (selectedDistrictId === districtId) {
          selectedDistrictResView = resValue;
          if (payload?.district?.progress) {
            selectedDistrictFeature.properties.percent_cells =
              payload.district.progress.percent_cells ??
              payload.district.progress.percent ??
              selectedDistrictFeature.properties.percent_cells;
            selectedDistrictFeature.properties.percent_weight =
              payload.district.progress.percent_weight ??
              selectedDistrictFeature.properties.percent_weight;
            
            const existingFeature = districtFeatureMap.get(districtId);
            if (existingFeature) {
                existingFeature.properties.percent_cells = selectedDistrictFeature.properties.percent_cells;
                existingFeature.properties.percent_weight = selectedDistrictFeature.properties.percent_weight;
                existingFeature.properties.overlay_suffix = formatProgressSuffix(existingFeature);
            }
          }
          updateDistrictHexLayer(featureCollection);
          updateStatusForSelection();
        }
      })
      .catch((error) => {
        if (controller.signal.aborted) return;
        console.warn("[district] Failed to fetch district cells", error);
        if (selectedDistrictId === districtId) {
          setStatus("Failed to load district cells", {
            temporary: true,
            state: "error",
          });
        }
      })
      .finally(() => {
        if (selectedDistrictAbortController === controller) {
          selectedDistrictAbortController = null;
        }
      });
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

  function updateDistrictHexLayer(featureCollection) {
    const source = map.getSource(ADMIN_SOURCES.districtHex);
    if (!source) return;
    source.setData(featureCollection || emptyFeatureCollection);
  }

  async function fetchDistrictCellsRaw(
    districtId,
    resView = null,
    options = {},
  ) {
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
        : BASE_DISTRICT_RESOLUTION;
    const resValue = Math.min(effectiveRes, BASE_DISTRICT_RESOLUTION);
    const cacheKey = `${districtId}@${resValue}`;
    const featureCollection = buildHexFeatureCollection(
      payload.cells || [],
      districtId,
    );
    districtCellsCache.set(cacheKey, { featureCollection, meta: payload });
    return { payload, resValue, cacheKey, featureCollection };
  }

  function setStatus(message, opts = {}) {
    statusOverrideMessage = opts.temporary ? message : null;
    if (!statusEl) return;
    statusEl.textContent = message || DEFAULT_STATUS_TEXT;
    statusEl.dataset.state = opts.state || "";
  }

  setStatus(DEFAULT_STATUS_TEXT);

  if (noAuthMode || debugAuthMode) {
    toggleFogBtn.style.display = "inline-block";
  }

  let cloudPattern = null;

  // Initialize texture worker
  const textureWorker = new Worker('texture.worker.js');
  textureWorker.postMessage({ width: 512, height: 512 });

  textureWorker.onmessage = function(e) {
    if (e.data.bitmap) {
      // Create pattern from the generated ImageBitmap
      cloudPattern = fogCtx.createPattern(e.data.bitmap, "repeat");
      console.log('Cloud texture generated and pattern created');
    } else if (e.data.imageData) {
      // Fallback for environments without OffscreenCanvas support
      const tempCanvas = document.createElement('canvas');
      tempCanvas.width = 512;
      tempCanvas.height = 512;
      const tempCtx = tempCanvas.getContext('2d');
      tempCtx.putImageData(e.data.imageData, 0, 0);
      cloudPattern = fogCtx.createPattern(tempCanvas, "repeat");
      console.log('Cloud texture generated (fallback mode) and pattern created');
    }
  };

  function drawFogLoop() {
    if (!cloudPattern) return; // Wait for texture to be ready

    animationTime++;
    FogModule.drawFog(
      fogCtx,
      map,
      fogEnabled,
      spatialIndex, // Pass spatial index instead of all hexagons
      animationTime,
      FOG_CONFIG,
      DPR,
      cloudPattern,
      GRID_SIZE // Pass grid size to the module
    );
  }

  async function addVisitAt(lat, lng) {
    const response = await fetch("/api/v1/visit", {
      method: "POST",
      headers: getAuthHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ lat, lon: lng }),
    });
    if (!response.ok) {
      throw new Error(`Server error: ${response.statusText}`);
    }
    const result = await response.json();

    // Extract h3_geokey from response and immediately update UI
    const h3Geokey = result.h3_geokey;
    if (h3Geokey && !allKnownHexagons.has(h3Geokey)) {
      allKnownHexagons.add(h3Geokey);
      addToSpatialIndex(h3Geokey);
      map.triggerRepaint();
    }

    // Note: We don't update the count here since stats are updated asynchronously
    // The count will be updated when stats are refreshed from the server

    return result;
  }

  async function deleteHexAtPoint(point) {
    const lngLat = map.unproject(point);
    const h3Resolution = window.currentH3Resolution || defaultVisitResolution;
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
      map.triggerRepaint();
      console.log("Deleted hexagon:", targetHexId);
    } else {
      console.warn("Delete command sent, but server reported 0 deleted.", {
        geokey: targetHexId,
      });
    }
  }

  // --- Rest of the code ---
  const loader = document.getElementById("loader");
  async function updateHexagonsFromServer() {
    if (isFetching) return;
    isFetching = true;
    const loaderTimeout = setTimeout(() => {
      if (loader) loader.style.display = "flex";
    }, 500);
    try {
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
      const textData = await response.text();
      const receivedHexagons = textData ? textData.split(' ') : [];

      // Expand aggregated hexagons back to base resolution
      const expandedHexagons = new Set();
      receivedHexagons.forEach((hexId) => {
        if (!hexId) return; // Skip empty strings
        const resolution = h3.getResolution(hexId);
        if (resolution === defaultVisitResolution) {
          // Base resolution, add directly
          expandedHexagons.add(hexId);
        } else if (resolution < defaultVisitResolution) {
          // Aggregated parent, expand to children
          try {
            const children = h3.cellToChildren(hexId, defaultVisitResolution);
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
      map.triggerRepaint();
    } catch (error) {
      console.error("[fog] Failed to fetch hexagons:", error);
    } finally {
      isFetching = false;
      clearTimeout(loaderTimeout);
      if (loader) loader.style.display = "none";
    }
  }

  async function revealEntireDistrict(districtId) {
    // Always fetch cells with the server's base resolution to get all cells
    const serverBaseResolution = window.__CITY_FOG_BASE_RESOLUTION__ || 9;
    const detailed = await fetchDistrictCellsRaw(
      districtId,
      serverBaseResolution,
    );
    const meta = detailed.payload || detailed.meta;
    if (!meta || !Array.isArray(meta.cells) || meta.cells.length === 0) {
      throw new Error("No cells available for district");
    }

    const revealCells = meta.cells;

    await revealDistrictViaVisits(revealCells);

    // Refresh the display after revealing
    const areaKm2 = getFeatureAreaKm2(selectedDistrictFeature);
    const desiredRes = pickResByArea(areaKm2);
    await Promise.all([
      updateHexagonsFromServer(),
      fetchDistrictCells(districtId, desiredRes),
    ]);
    refreshAdminLayers();
  }

  async function revealDistrictViaVisits(cells) {
    if (!Array.isArray(cells) || cells.length === 0) return;
    for (let i = 0; i < cells.length; i++) {
      const cell = cells[i];
      if (!cell || !cell.h3) continue;
      if (allKnownHexagons.has(cell.h3)) continue;
      const [lat, lng] = h3.cellToLatLng(cell.h3);
      try {
        const response = await fetch("/api/v1/visit", {
          method: "POST",
          headers: getAuthHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({ lat, lon: lng }),
        });
        if (response.ok) {
          const result = await response.json();
          if (result.added > 0) {
            allKnownHexagons.add(cell.h3);
            addToSpatialIndex(cell.h3);
            const total =
              result.stats && typeof result.stats.total_circles === "number"
                ? result.stats.total_circles
                : allKnownHexagons.size;
            countEl.textContent = Number(total).toLocaleString();
            map.triggerRepaint();
          }
        }
      } catch (err) {
        console.warn("[debug] reveal visit failed", { cell: cell.h3, err });
      }
    }
  }

  map.on("load", () => {
    const mapContainer = document.getElementById("map-container");
    const controls = mapContainer.querySelector(
      ".maplibregl-control-container",
    );
    if (controls) {
      mapContainer.appendChild(controls);
    }
    const resizeObserver = new ResizeObserver(() => {
      const cssW = mapContainer.clientWidth;
      const cssH = mapContainer.clientHeight;
      fogCanvas.style.width = cssW + "px";
      fogCanvas.style.height = cssH + "px";
      fogCanvas.width = Math.max(1, Math.floor(cssW * DPR));
      fogCanvas.height = Math.max(1, Math.floor(cssH * DPR));
      fogCtx.setTransform(DPR, 0, 0, DPR, 0, 0);
    });
    resizeObserver.observe(mapContainer);
    updateHexagonsFromServer();
    refreshAdminLayers();
    try {
      geolocate.trigger();
    } catch (e) {
      console.error(e);
    }

    map.on("render", drawFogLoop);
  });

  map.on("moveend", () => {
    updateHexagonsFromServer();
    scheduleAdminRefresh();
  });
  map.on("move", () => scheduleAdminRefresh(true));
  map.on("zoomend", () => scheduleAdminRefresh());
  map.on("movestart", () => {
    ignoreNextClick = true;
  });
  map.on("moveend", () => {
    setTimeout(() => {
      ignoreNextClick = false;
    }, 120);
  });

  let lastKnownPosition = null;
  const TARGET_GEO_ZOOM = 17;
  openBtn.disabled = true;
  openBtn.textContent = "Locating...";

  geolocate.on("geolocate", (pos) => {
    lastKnownPosition = pos.coords;
    const zoom = Math.max(map.getZoom(), TARGET_GEO_ZOOM);
    map.flyTo({ center: [pos.coords.longitude, pos.coords.latitude], zoom });
    openBtn.disabled = false;
    openBtn.textContent = "Explore 50m Around";
  });

  geolocate.on("error", () => {
    if (noAuthMode) {
      openBtn.textContent = "Click map to add points";
      openBtn.disabled = false;
    } else {
      openBtn.textContent = "Geolocation failed";
    }
  });

  openBtn.addEventListener("click", async () => {
    if (!lastKnownPosition) {
      if (noAuthMode) {
        alert("Click on the map to add points.");
      } else {
        alert("Location not determined.");
      }
      return;
    }
    openBtn.disabled = true;
    try {
      const response = await fetch("/api/v1/visit", {
        method: "POST",
        headers: getAuthHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          lat: lastKnownPosition.latitude,
          lon: lastKnownPosition.longitude,
        }),
      });
      if (!response.ok) throw new Error(`Server error: ${response.statusText}`);
      const result = await response.json();
      if (result.added > 0) {
        const h3Resolution =
          window.currentH3Resolution || defaultVisitResolution;
        const hexId = h3.latLngToCell(
          lastKnownPosition.latitude,
          lastKnownPosition.longitude,
          h3Resolution,
        );
        if(!allKnownHexagons.has(hexId)) {
            allKnownHexagons.add(hexId);
            addToSpatialIndex(hexId);
        }
        countEl.textContent =
          result.stats && typeof result.stats.total_circles === "number"
            ? result.stats.total_circles.toLocaleString()
            : allKnownHexagons.size.toLocaleString();
        map.triggerRepaint();
      }
    } catch (error) {
      console.error("[visit] Failed to visit area:", error);
    } finally {
      openBtn.disabled = !lastKnownPosition;
    }
  });

  toggleFogBtn.addEventListener("click", () => {
    fogEnabled = !fogEnabled;
    toggleFogBtn.textContent = fogEnabled ? "Hide Fog" : "Show Fog";
    map.triggerRepaint();
  });

  // Debug UI
  const deleteModeBtn = document.getElementById("deleteModeBtn");
  const clearDbBtn = document.getElementById("clearDbBtn");
  const debugPanel = document.getElementById("debugPanel");
  const revealDistrictBtn = document.getElementById("revealDistrictBtn");
  let deleteMode = false;
  let selectionEnabled = true;

  function setDeleteMode(on) {
    deleteMode = !!on;
    if (deleteModeBtn) {
      deleteModeBtn.textContent = deleteMode ? "Delete: On" : "Delete: Off";
      deleteModeBtn.style.background = deleteMode ? "#b91c1c" : "#ef4444";
    }
  }

  function setSelectionEnabled(on) {
    selectionEnabled = !!on;
    const selectionToggleBtn = document.getElementById("selectionToggleBtn");
    if (selectionToggleBtn) {
      selectionToggleBtn.textContent = selectionEnabled
        ? "Select: On"
        : "Select: Off";
      selectionToggleBtn.style.background = selectionEnabled
        ? "#0ea5e9"
        : "#475569";
    }
  }

  if (noAuthMode || debugAuthMode) {
    if (debugPanel) debugPanel.style.display = "flex";
  }

  if (deleteModeBtn) {
    deleteModeBtn.addEventListener("click", () => setDeleteMode(!deleteMode));
  }

  const selectionToggleBtn = document.getElementById("selectionToggleBtn");
  setSelectionEnabled(true);
  if (selectionToggleBtn) {
    selectionToggleBtn.addEventListener("click", () => {
      setSelectionEnabled(!selectionEnabled);
    });
  }

  if (leaderboardBtn) {
    leaderboardBtn.addEventListener("click", () => {
      leaderboardLevelSelect.value = leaderboardState.level;
      leaderboardPeriodSelect.value = leaderboardState.period;
      showLeaderboard();
    });
  }

  if (leaderboardCloseBtn) {
    leaderboardCloseBtn.addEventListener("click", hideLeaderboard);
  }

  if (leaderboardOverlay) {
    leaderboardOverlay.addEventListener("click", (event) => {
      if (event.target === leaderboardOverlay) hideLeaderboard();
    });
  }

  if (leaderboardLevelSelect) {
    leaderboardLevelSelect.addEventListener(
      "change",
      handleLeaderboardLevelChange,
    );
  }

  if (leaderboardPeriodSelect) {
    leaderboardPeriodSelect.addEventListener(
      "change",
      handleLeaderboardPeriodChange,
    );
  }

  document.addEventListener("keydown", handleLeaderboardKey);

  if (revealDistrictBtn) {
    revealDistrictBtn.addEventListener("click", async () => {
      if (!selectedDistrictId) {
        alert("Select a district first.");
        return;
      }
      revealDistrictBtn.disabled = true;
      revealDistrictBtn.textContent = "Revealing…";
      try {
        await revealEntireDistrict(selectedDistrictId);
      } catch (err) {
        console.warn("[debug] reveal district failed", err);
        alert("Failed to reveal district");
      } finally {
        revealDistrictBtn.disabled = false;
        revealDistrictBtn.textContent = "Reveal District";
      }
    });
  }

  if (clearDbBtn) {
    clearDbBtn.addEventListener("click", async () => {
      if (!confirm("Clear the entire database? This action is irreversible."))
        return;
      try {
        const res = await fetch("/api/v1/dev/clear-db", { method: "POST" });
        if (!res.ok) throw new Error("clear-db failed");
        const data = await res.json().catch(() => ({}));
        allKnownHexagons.clear();
        spatialIndex.clear(); // Clear the index too
        countEl.textContent = "0";
        map.triggerRepaint();
        alert(
          `DB cleared. circles=${data.cleared_circles ?? "?"}, users=${data.cleared_users ?? "?"}`,
        );
      } catch (e) {
        alert("Error clearing database");
        console.warn("[dev] clear-db error", e);
      }
    });
  }

  map.on("click", (e) => {
    if (ignoreNextClick) return;

    const districtFeatures = map.queryRenderedFeatures(e.point, {
      layers: [ADMIN_LAYERS.districtHitArea],
    });
    if (districtFeatures && districtFeatures.length > 0) {
      if (selectionEnabled) {
        handleDistrictSelection(districtFeatures[0]);
        return;
      }
    }

    if ((noAuthMode || debugAuthMode) && !deleteMode) {
      const lngLat = map.unproject(e.point);
      addVisitAt(lngLat.lat, lngLat.lng)
        .then(() => {
          scheduleAdminRefresh();
        })
        .catch((error) => {
          console.error("[visit] Failed to visit area:", error);
        });
      return;
    }

    if (deleteMode) {
      deleteHexAtPoint(e.point);
    }
  });
})();