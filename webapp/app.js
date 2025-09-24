  (async function(){
  const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  if (tg) { try { tg.ready(); } catch (_) {} }

  async function getDebugSettings() {
    try {
      const response = await fetch('/api/v1/debug-mode');
      if (!response.ok) return { noAuthMode: false, debugAuthMode: false };
      const data = await response.json();
      return {
        noAuthMode: !!data.no_auth_mode,
        debugAuthMode: !!data.debug_auth_mode,
        baseVisitResolution: typeof data.base_visit_resolution === 'number' ? data.base_visit_resolution : undefined
      };
    } catch (error) {
      console.warn('[auth] Failed to check debug mode:', error);
      return { noAuthMode: false, debugAuthMode: false };
    }
  }

  const hasInitData = !!(tg && tg.initData);
  const { noAuthMode, debugAuthMode, baseVisitResolution } = await getDebugSettings();

  if (!hasInitData && !noAuthMode) {
    document.getElementById('app').innerHTML = `
      <div style="display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100%; text-align: center; font-family: sans-serif; color: #ccc;">
        <h2 style="margin: 0; color: #eee;">Oops!</h2>
        <p style="margin: 8px 0 0;">Could not initialize the application.<br>Please make sure you are running this inside Telegram.</p>
      </div>
    `;
    return;
  }

  // --- UI & Config ---
  const openBtn = document.getElementById('openBtn');
  const toggleFogBtn = document.getElementById('toggleFogBtn');
  const countEl = document.getElementById('count');
  const statusEl = document.getElementById('status');
  const fogCanvas = document.getElementById('fog-canvas');
  const fogCtx = fogCanvas.getContext('2d');
  const DPR = window.devicePixelRatio || 1;

  const FOG_CONFIG = {
    shadowBlur: 55,
    revealBlur: 40,
    pulseAmplitude: 0.04,
    animationSpeed: 0.0015,
  };

  // --- Map Initialization ---
  const map = new maplibregl.Map({
    container: 'map',
    style: 'https://api.maptiler.com/maps/pastel/style.json?key=TFV5uV6DVVucu16gTZdi',
    center: [37.6173, 55.7558],
    zoom: 12,
    maxBounds: [[36.0, 55.0], [39.0, 56.5]]
  });
  map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }));
  const geolocate = new maplibregl.GeolocateControl({
    positionOptions: { enableHighAccuracy: true },
    trackUserLocation: false,
    showUserHeading: true,
    showAccuracyCircle: false,
    fitBoundsOptions: { maxZoom: 22 }
  });
  try { if (geolocate && geolocate._updateCamera) { geolocate._updateCamera = function(){}; } } catch (_) {}
  map.addControl(geolocate);

  // --- Application State ---
  const allKnownHexagons = new Set();
  let isFetching = false;
  let fogEnabled = true;
  let animationTime = 0;
  const defaultVisitResolution = baseVisitResolution || window.__CITY_FOG_BASE_RESOLUTION__ || 8;
  window.currentH3Resolution = defaultVisitResolution;
  // Prevent accidental clicks after dragging/zooming the map (phantom circles)
  let ignoreNextClick = false;
  const emptyFeatureCollection = { type: 'FeatureCollection', features: [] };
  const ADMIN_FETCH_DEBOUNCE_MS = 360;
  const ADMIN_FETCH_IDLE_MS = 120;
  let adminUpdateTimer = null;
  let adminRequestSeq = 0;
  let adminFetchAbortController = null;
  let selectedDistrictId = null;
  let selectedDistrictName = '';
  let selectedDistrictFeature = null;
  let selectedDistrictAbortController = null;
  const DEFAULT_STATUS_TEXT = 'Select a district';
  const districtFeatureMap = new Map();
  const okrugFeatureMap = new Map();
  const districtCellsCache = new Map();
  let statusOverrideMessage = null;
  const labelOverlayEl = document.getElementById('label-overlay');
  const overlayLabels = new Map();

  const ADMIN_SOURCES = {
    districts: 'admin-districts',
    okrugs: 'admin-okrugs',
    selected: 'selected-district',
    districtHex: 'district-hex-grid'
  };

  const ADMIN_LAYERS = {
    districtFill: 'district-fill',
    districtBorders: 'district-borders',
    districtLabels: 'district-labels',
    districtHitArea: 'district-hit-area',
    okrugBorders: 'okrug-borders',
    okrugFill: 'okrug-fill',
    selectedFill: 'selected-district-fill',
    selectedOutline: 'selected-district-outline',
    hexFill: 'selected-district-hex-fill',
    hexOutline: 'selected-district-hex-outline'
  };

  function getAuthHeaders(custom = {}) {
    const headers = { ...custom };
    if (tg && tg.initData) {
      headers['X-Telegram-Init'] = tg.initData;
    }
    if (typeof window.currentH3Resolution === 'number') {
      headers['X-H3-Resolution'] = String(window.currentH3Resolution);
    }
    return headers;
  }

  function cloneFeature(feature) {
    if (!feature) return null;
    try {
      return JSON.parse(JSON.stringify(feature));
    } catch (err) {
      console.warn('[admin] Failed to clone feature', err);
      return null;
    }
  }

  function featureProgressPercent(feature) {
    const percent = feature?.properties?.progress_percent;
    return typeof percent === 'number' && !Number.isNaN(percent) ? percent : null;
  }

  function formatProgressPercent(percent) {
    if (typeof percent !== 'number' || Number.isNaN(percent)) return null;
    return `${Math.round(percent)}%`;
  }

  function ensureAdminSourcesAndLayers() {
    if (!map.getSource(ADMIN_SOURCES.okrugs)) {
      map.addSource(ADMIN_SOURCES.okrugs, {
        type: 'geojson',
        data: emptyFeatureCollection
      });
    }

    if (!map.getLayer(ADMIN_LAYERS.okrugFill)) {
      map.addLayer({
        id: ADMIN_LAYERS.okrugFill,
        type: 'fill',
        source: ADMIN_SOURCES.okrugs,
        paint: {
          'fill-color': '#0ea5e9',
          'fill-opacity': 0.05
        },
        maxzoom: 12
      });
    }

    if (!map.getLayer(ADMIN_LAYERS.okrugBorders)) {
      map.addLayer({
        id: ADMIN_LAYERS.okrugBorders,
        type: 'line',
        source: ADMIN_SOURCES.okrugs,
        paint: {
          'line-color': '#0f172a',
          'line-width': [
            'interpolate', ['linear'], ['zoom'],
            8, 3.5,
            14, 4.5
          ],
          'line-dasharray': [3, 2],
          'line-opacity': 0.9
        }
      });
    }

    if (!map.getSource(ADMIN_SOURCES.districts)) {
      map.addSource(ADMIN_SOURCES.districts, {
        type: 'geojson',
        data: emptyFeatureCollection
      });
    }

    if (!map.getLayer(ADMIN_LAYERS.districtFill)) {
      map.addLayer({
        id: ADMIN_LAYERS.districtFill,
        type: 'fill',
        source: ADMIN_SOURCES.districts,
        paint: {
          'fill-color': '#38bdf8',
          'fill-opacity': 0.08
        },
        maxzoom: 12,
        layout: {
          visibility: 'visible'
        }
      });
    }

    if (!map.getLayer(ADMIN_LAYERS.districtHitArea)) {
      map.addLayer({
        id: ADMIN_LAYERS.districtHitArea,
        type: 'fill',
        source: ADMIN_SOURCES.districts,
        paint: {
          'fill-opacity': 0
        },
        filter: ['==', ['get', 'level'], 'district']
      });
    }

    if (!map.getLayer(ADMIN_LAYERS.districtLabels)) {
      map.addLayer({
        id: ADMIN_LAYERS.districtLabels,
        type: 'symbol',
        source: ADMIN_SOURCES.districts,
        layout: {
          visibility: 'none'
        }
      });
    }

    if (!map.getLayer(ADMIN_LAYERS.districtBorders)) {
      map.addLayer({
        id: ADMIN_LAYERS.districtBorders,
        type: 'line',
        source: ADMIN_SOURCES.districts,
        paint: {
          'line-color': '#1f2937',
          'line-width': [
            'interpolate', ['linear'], ['zoom'],
            8, 2.5,
            14, 3.5
          ],
          'line-blur': 0.5,
          'line-opacity': 0.95
        }
      });
    }

    if (!map.getSource(ADMIN_SOURCES.selected)) {
      map.addSource(ADMIN_SOURCES.selected, {
        type: 'geojson',
        data: emptyFeatureCollection
      });
    }

    if (!map.getLayer(ADMIN_LAYERS.selectedFill)) {
      map.addLayer({
        id: ADMIN_LAYERS.selectedFill,
        type: 'fill',
        source: ADMIN_SOURCES.selected,
        paint: {
          'fill-color': '#f97316',
          'fill-opacity': 0.12
        }
      });
    }

    if (!map.getLayer(ADMIN_LAYERS.selectedOutline)) {
      map.addLayer({
        id: ADMIN_LAYERS.selectedOutline,
        type: 'line',
        source: ADMIN_SOURCES.selected,
        paint: {
          'line-color': '#fb923c',
          'line-width': [
            'interpolate', ['linear'], ['zoom'],
            8, 3.2,
            14, 5
          ],
          'line-opacity': 0.9
        }
      });
    }

    if (!map.getSource(ADMIN_SOURCES.districtHex)) {
      map.addSource(ADMIN_SOURCES.districtHex, {
        type: 'geojson',
        data: emptyFeatureCollection
      });
    }

    if (!map.getLayer(ADMIN_LAYERS.hexFill)) {
      map.addLayer({
        id: ADMIN_LAYERS.hexFill,
        type: 'fill',
        source: ADMIN_SOURCES.districtHex,
        paint: {
          'fill-color': [
            'case',
            ['boolean', ['get', 'visited'], false],
            '#22c55e',
            '#94a3b8'
          ],
          'fill-opacity': [
            'case',
            ['boolean', ['get', 'visited'], false],
            0.55,
            [
              'interpolate', ['linear'],
              ['coalesce', ['get', 'coverage'], 0],
              0, 0.06,
              1, 0.35
            ]
          ]
        }
      });
    }

    if (!map.getLayer(ADMIN_LAYERS.hexOutline)) {
      map.addLayer({
        id: ADMIN_LAYERS.hexOutline,
        type: 'line',
        source: ADMIN_SOURCES.districtHex,
        paint: {
          'line-color': '#1f2937',
          'line-width': 0.65,
          'line-opacity': 0.5
        }
      });
    }
  }

  function toFeatureCollection(features = []) {
    return { type: 'FeatureCollection', features };
  }

  function mapDistrictApiFeature(raw) {
    if (!raw || !raw.geom) return null;
    const feature = {
      type: 'Feature',
      geometry: raw.geom,
      properties: {
        id: raw.id,
        name: raw.name,
        level: raw.level,
        parent_id: raw.parent_id,
        progress_percent: raw.progress?.percent ?? 0,
        visited_cells: raw.progress?.visited_cells ?? 0,
        total_cells: raw.progress?.total_cells ?? 0,
        bbox: raw.bbox ?? null
      }
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
      bounds.getNorth()
    ].join(',');

    const controller = new AbortController();
    adminFetchAbortController = controller;
    const currentSeq = ++adminRequestSeq;
    const requestOptions = {
      signal: controller.signal,
      headers: getAuthHeaders({ Accept: 'application/json' })
    };

    const okrugPromise = fetch(`/api/v1/districts?bbox=${bbox}&level=okrug`, requestOptions);
    const districtPromise = fetch(`/api/v1/districts?bbox=${bbox}&level=district`, requestOptions);

    Promise.all([okrugPromise, districtPromise])
      .then(async ([okrugRes, districtRes]) => {
        if (controller.signal.aborted) return;
        if (!okrugRes.ok) throw new Error(`okrug fetch failed: ${okrugRes.status}`);
        if (!districtRes.ok) throw new Error(`district fetch failed: ${districtRes.status}`);

        const [okrugData, districtData] = await Promise.all([
          okrugRes.json(),
          districtRes.json()
        ]);

        if (controller.signal.aborted || currentSeq !== adminRequestSeq) return;

        const okrugFeatures = [];
        okrugFeatureMap.clear();
        okrugData.forEach(raw => {
          const feature = mapDistrictApiFeature(raw);
          if (feature) {
            okrugFeatures.push(feature);
            okrugFeatureMap.set(raw.id, feature);
          }
        });

        const districtFeatures = [];
        districtFeatureMap.clear();
        districtData.forEach(raw => {
          const feature = mapDistrictApiFeature(raw);
          if (feature) {
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
        updateOverlayLabels(districtFeatures);

        if (selectedDistrictId != null) {
          const updatedFeature = districtFeatureMap.get(selectedDistrictId);
          if (updatedFeature) {
            selectedDistrictFeature = cloneFeature(updatedFeature);
            selectedDistrictName = selectedDistrictFeature.properties?.name || selectedDistrictName;
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
      .catch(error => {
        if (controller.signal.aborted) return;
        console.warn('[admin] Failed to refresh admin layers:', error);
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
    const percent = featureProgressPercent(selectedDistrictFeature);
    const suffix = formatProgressPercent(percent);
    const label = suffix ? `${selectedDistrictName} • ${suffix}` : selectedDistrictName;
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
    selectedDistrictFeature = cloneFeature(districtFeatureMap.get(districtId) || feature);
    selectedDistrictName = selectedDistrictFeature?.properties?.name || `District ${districtId}`;

    updateSelectedDistrictHighlight();
    updateStatusForSelection();

    const cached = districtCellsCache.get(districtId);
    if (cached) {
      updateDistrictHexLayer(cached.featureCollection);
      if (cached.meta?.district?.progress) {
        const progress = cached.meta.district.progress;
        selectedDistrictFeature.properties.progress_percent = progress.percent ?? selectedDistrictFeature.properties.progress_percent;
        selectedDistrictFeature.properties.visited_cells = progress.visited_cells ?? selectedDistrictFeature.properties.visited_cells;
        selectedDistrictFeature.properties.total_cells = progress.total_cells ?? selectedDistrictFeature.properties.total_cells;
        updateStatusForSelection();
      }
      return;
    }

    fetchDistrictCells(districtId);
  }

  function clearDistrictSelection() {
    if (selectedDistrictAbortController) {
      selectedDistrictAbortController.abort();
      selectedDistrictAbortController = null;
    }
    const wasSelected = selectedDistrictId != null;
    const previousId = selectedDistrictId;
    selectedDistrictId = null;
    selectedDistrictName = '';
    selectedDistrictFeature = null;
    updateSelectedDistrictHighlight();
    updateDistrictHexLayer(emptyFeatureCollection);
    updateStatusForSelection();
    if (wasSelected && previousId != null) {
      districtCellsCache.delete(previousId);
    }
  }

  function fetchDistrictCells(districtId) {
    if (selectedDistrictAbortController) {
      selectedDistrictAbortController.abort();
    }
    setStatus(`Loading ${selectedDistrictName}…`, { temporary: true });
    const controller = new AbortController();
    selectedDistrictAbortController = controller;

    fetch(`/api/v1/district/${districtId}/cells`, {
      signal: controller.signal,
      headers: getAuthHeaders({ Accept: 'application/json' })
    })
      .then(response => {
        if (!response.ok) {
          throw new Error(`Failed to fetch district cells: ${response.status}`);
        }
        return response.json();
      })
      .then(payload => {
        if (controller.signal.aborted) return;
        const featureCollection = buildHexFeatureCollection(payload.cells || [], districtId);
        districtCellsCache.set(districtId, { featureCollection, meta: payload });
        if (selectedDistrictId === districtId) {
          if (payload?.district?.progress) {
            selectedDistrictFeature.properties.progress_percent = payload.district.progress.percent ?? selectedDistrictFeature.properties.progress_percent;
            selectedDistrictFeature.properties.visited_cells = payload.district.progress.visited_cells ?? selectedDistrictFeature.properties.visited_cells;
            selectedDistrictFeature.properties.total_cells = payload.district.progress.total_cells ?? selectedDistrictFeature.properties.total_cells;
            const existingFeature = districtFeatureMap.get(districtId);
            if (existingFeature) {
              existingFeature.properties = {
                ...existingFeature.properties,
                progress_percent: selectedDistrictFeature.properties.progress_percent,
                visited_cells: selectedDistrictFeature.properties.visited_cells,
                total_cells: selectedDistrictFeature.properties.total_cells
              };
            }
          }
          updateDistrictHexLayer(featureCollection);
          updateStatusForSelection();
        }
      })
      .catch(error => {
        if (controller.signal.aborted) return;
        console.warn('[district] Failed to fetch district cells', error);
        if (selectedDistrictId === districtId) {
          setStatus('Failed to load district cells', { temporary: true, state: 'error' });
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
      cells.forEach(cell => {
        if (!cell || !cell.h3) return;
        try {
          const boundary = h3.cellToBoundary(cell.h3, true);
          if (!Array.isArray(boundary) || boundary.length === 0) return;
          const coordinates = boundary.map(([lat, lng]) => [lng, lat]);
          if (coordinates.length > 0) {
            coordinates.push(coordinates[0]);
          }
          features.push({
            type: 'Feature',
            geometry: {
              type: 'Polygon',
              coordinates: [coordinates]
            },
            properties: {
              h3: cell.h3,
              coverage: typeof cell.coverage === 'number' ? cell.coverage : 0,
              visited: !!cell.visited,
              visited_children: cell.visited_children ?? null,
              total_children: cell.total_children ?? null,
              visited_fraction: cell.visited_fraction ?? null,
              district_id: districtId
            }
          });
        } catch (err) {
          console.warn('[h3] Failed to build hex geometry', cell?.h3, err);
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

  function updateOverlayLabels(features) {
    if (!labelOverlayEl) return;
    const seenIds = new Set();
    features.forEach(feature => {
      const props = feature.properties || {};
      const id = props.id;
      if (id == null) return;
      seenIds.add(id);
      let el = overlayLabels.get(id);
      if (!el) {
        el = document.createElement('div');
        el.className = 'district-label';
        overlayLabels.set(id, el);
        labelOverlayEl.appendChild(el);
      }
      const percent = featureProgressPercent(feature);
      const text = percent != null ? `${props.name || id}\n${Math.round(percent)}%` : `${props.name || id}`;
      el.textContent = text;
      el.dataset.labelId = `${id}`;
      el.dataset.progress = percent != null ? `${percent}` : '';
    });

    overlayLabels.forEach((el, id) => {
      if (!seenIds.has(id)) {
        el.remove();
        overlayLabels.delete(id);
      }
    });
  }

  function renderDistrictLabels() {
    if (!labelOverlayEl || overlayLabels.size === 0) return;
    const zoom = map.getZoom();
    const hideAll = zoom > 12.8;
    overlayLabels.forEach((el, id) => {
      if (hideAll) {
        el.style.display = 'none';
        return;
      }
      el.style.display = 'block';
      const feature = districtFeatureMap.get(id);
      if (!feature || !feature.geometry) {
        el.style.display = 'none';
        return;
      }
      let center;
      try {
        center = turf.center(feature).geometry.coordinates;
      } catch (_) {
        const bbox = turf.bbox(feature);
        center = [(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2];
      }
      const point = map.project(center);
      el.style.transform = `translate(-50%, -50%) translate(${point.x}px, ${point.y}px)`;
    });
  }

  function setStatus(message, opts = {}) {
    statusOverrideMessage = opts.temporary ? message : null;
    if (!statusEl) return;
    statusEl.textContent = message || DEFAULT_STATUS_TEXT;
    statusEl.dataset.state = opts.state || '';
  }

  setStatus(DEFAULT_STATUS_TEXT);

  if (noAuthMode || debugAuthMode) {
    toggleFogBtn.style.display = 'inline-block';
  }

  const cloudTexture = FogModule.createCloudTexture(512, 512);
  const cloudPattern = fogCtx.createPattern(cloudTexture, 'repeat');

  function drawFogLoop() {
    animationTime++;
    FogModule.drawFog(fogCtx, map, fogEnabled, allKnownHexagons, animationTime, FOG_CONFIG, DPR, cloudPattern);
    renderDistrictLabels();
  }

  async function addVisitAt(lat, lng) {
    const response = await fetch('/api/v1/visit', {
      method: 'POST',
      headers: getAuthHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ lat, lon: lng })
    });
    if (!response.ok) {
      throw new Error(`Server error: ${response.statusText}`);
    }
    const result = await response.json();
    if (result.added > 0) {
      const h3Resolution = window.currentH3Resolution || defaultVisitResolution;
      const hexId = h3.latLngToCell(lat, lng, h3Resolution);
      allKnownHexagons.add(hexId);
      const total = (result.stats && typeof result.stats.total_circles === 'number')
        ? result.stats.total_circles
        : allKnownHexagons.size;
      countEl.textContent = Number(total).toLocaleString();
      map.triggerRepaint();
    }
    return result;
  }

  async function deleteHexAtPoint(point) {
    const lngLat = map.unproject(point);
    const h3Resolution = window.currentH3Resolution || defaultVisitResolution;
    const targetHexId = h3.latLngToCell(lngLat.lat, lngLat.lng, h3Resolution);

    if (!allKnownHexagons.has(targetHexId)) {
      console.log('Clicked on a cell that is not a known hexagon:', targetHexId);
      return;
    }

    const response = await fetch('/api/v1/circle', {
      method: 'DELETE',
      headers: getAuthHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ geokey: targetHexId })
    });
    if (!response.ok) {
      const errText = await response.text();
      throw new Error(`Delete failed with status ${response.status}: ${errText}`);
    }
    const res = await response.json();
    if (res.deleted > 0) {
      allKnownHexagons.delete(targetHexId);
      countEl.textContent = allKnownHexagons.size.toLocaleString();
      map.triggerRepaint();
      console.log('Deleted hexagon:', targetHexId);
    } else {
      console.warn('Delete command sent, but server reported 0 deleted.', { geokey: targetHexId });
    }
  }

  // --- Rest of the code ---
  const loader = document.getElementById('loader');
  async function updateHexagonsFromServer() {
    if (isFetching) return;
    isFetching = true;
    const loaderTimeout = setTimeout(() => { if (loader) loader.style.display = 'flex'; }, 500);
    try {
      const bounds = map.getBounds();
      const bbox = [bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth()].join(',');
    const response = await fetch(`/api/v1/circles?bbox=${bbox}`, { headers: getAuthHeaders() });
      if (!response.ok) throw new Error(`Network error: ${response.statusText}`);
      const data = await response.json();
      let newHexagons = 0;
      data.hexagons.forEach(hexId => {
        if (!allKnownHexagons.has(hexId)) {
          allKnownHexagons.add(hexId);
          newHexagons++;
        }
      });
      if (newHexagons > 0) {
      countEl.textContent = allKnownHexagons.size.toLocaleString();
      }
      map.triggerRepaint();
    } catch (error) {
      console.error('[fog] Failed to fetch hexagons:', error);
    } finally {
      isFetching = false;
      clearTimeout(loaderTimeout);
      if (loader) loader.style.display = 'none';
    }
  }

  async function revealEntireDistrict(districtId) {
    const cached = districtCellsCache.get(districtId);
    if (!cached) {
      await fetchDistrictCells(districtId);
    }
    const meta = districtCellsCache.get(districtId)?.meta;
    if (!meta || !Array.isArray(meta.cells) || meta.cells.length === 0) {
      throw new Error('No cells cached for district');
    }

    await revealDistrictViaVisits(meta.cells);

    await Promise.all([
      updateHexagonsFromServer(),
      fetchDistrictCells(districtId)
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
        const response = await fetch('/api/v1/visit', {
          method: 'POST',
          headers: getAuthHeaders({ 'Content-Type': 'application/json' }),
          body: JSON.stringify({ lat, lon: lng })
        });
        enforceResolution(defaultVisitResolution);
        if (response.ok) {
          const result = await response.json();
          if (result.added > 0) {
            allKnownHexagons.add(cell.h3);
            const total = (result.stats && typeof result.stats.total_circles === 'number')
              ? result.stats.total_circles
              : allKnownHexagons.size;
            countEl.textContent = Number(total).toLocaleString();
            map.triggerRepaint();
          }
        }
      } catch (err) {
        console.warn('[debug] reveal visit failed', { cell: cell.h3, err });
      }
    }
  }

  map.on('load', () => {
    const mapContainer = document.getElementById('map-container');
    const controls = mapContainer.querySelector('.maplibregl-control-container');
    if (controls) {
      mapContainer.appendChild(controls);
    }
    const resizeObserver = new ResizeObserver(() => {
      const cssW = mapContainer.clientWidth;
      const cssH = mapContainer.clientHeight;
      fogCanvas.style.width = cssW + 'px';
      fogCanvas.style.height = cssH + 'px';
      fogCanvas.width = Math.max(1, Math.floor(cssW * DPR));
      fogCanvas.height = Math.max(1, Math.floor(cssH * DPR));
      fogCtx.setTransform(DPR, 0, 0, DPR, 0, 0);
    });
    resizeObserver.observe(mapContainer);
    updateHexagonsFromServer();
    refreshAdminLayers();
    try { geolocate.trigger(); } catch (e) { console.error(e); }

    map.on('render', drawFogLoop);
  });

  map.on('moveend', () => {
    updateHexagonsFromServer();
    scheduleAdminRefresh();
  });
  map.on('move', () => scheduleAdminRefresh(true));
  map.on('zoomend', () => scheduleAdminRefresh());
  // Mark that a move has occurred so the next click is ignored
  map.on('movestart', () => { ignoreNextClick = true; });
  map.on('moveend', () => { setTimeout(() => { ignoreNextClick = false; }, 120); });
  
  let lastKnownPosition = null;
  const TARGET_GEO_ZOOM = 17;
  openBtn.disabled = true;
  openBtn.textContent = 'Locating...';

  geolocate.on('geolocate', (pos) => {
    lastKnownPosition = pos.coords;
    const zoom = Math.max(map.getZoom(), TARGET_GEO_ZOOM);
    map.flyTo({ center: [pos.coords.longitude, pos.coords.latitude], zoom });
    openBtn.disabled = false;
    openBtn.textContent = 'Explore 50m Around';
  });

  geolocate.on('error', () => {
    if (noAuthMode) {
      openBtn.textContent = 'Click map to add points';
      openBtn.disabled = false;
    } else {
      openBtn.textContent = 'Geolocation failed';
    }
  });

  openBtn.addEventListener('click', async () => {
    if (!lastKnownPosition) {
      if (noAuthMode) { alert('Click on the map to add points.'); }
      else { alert('Location not determined.'); }
      return;
    }
    openBtn.disabled = true;
    try {
      const response = await fetch('/api/v1/visit', {
        method: 'POST',
        headers: getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ lat: lastKnownPosition.latitude, lon: lastKnownPosition.longitude })
      });
      if (!response.ok) throw new Error(`Server error: ${response.statusText}`);
      const result = await response.json();
      if (result.added > 0) {
        const h3Resolution = window.currentH3Resolution || defaultVisitResolution;
        const hexId = h3.latLngToCell(lastKnownPosition.latitude, lastKnownPosition.longitude, h3Resolution);
        allKnownHexagons.add(hexId);
        countEl.textContent = (result.stats && typeof result.stats.total_circles === 'number') ? result.stats.total_circles.toLocaleString() : allKnownHexagons.size.toLocaleString();
        map.triggerRepaint();
      }
    } catch (error) {
      console.error('[visit] Failed to visit area:', error);
    } finally {
      openBtn.disabled = !lastKnownPosition;
    }
  });

  toggleFogBtn.addEventListener('click', () => {
    fogEnabled = !fogEnabled;
    toggleFogBtn.textContent = fogEnabled ? 'Hide Fog' : 'Show Fog';
    map.triggerRepaint();
  });

  // Debug UI
  const deleteModeBtn = document.getElementById('deleteModeBtn');
  const clearDbBtn = document.getElementById('clearDbBtn');
  const debugPanel = document.getElementById('debugPanel');
  const revealDistrictBtn = document.getElementById('revealDistrictBtn');
  let deleteMode = false;
  let selectionEnabled = true;

  function setDeleteMode(on) {
    deleteMode = !!on;
    if (deleteModeBtn) {
      deleteModeBtn.textContent = deleteMode ? 'Delete: On' : 'Delete: Off';
      deleteModeBtn.style.background = deleteMode ? '#b91c1c' : '#ef4444';
    }
  }

  function setSelectionEnabled(on) {
    selectionEnabled = !!on;
    const selectionToggleBtn = document.getElementById('selectionToggleBtn');
    if (selectionToggleBtn) {
      selectionToggleBtn.textContent = selectionEnabled ? 'Select: On' : 'Select: Off';
      selectionToggleBtn.style.background = selectionEnabled ? '#0ea5e9' : '#475569';
    }
  }

  if (noAuthMode || debugAuthMode) {
    if (debugPanel) debugPanel.style.display = 'flex';
  }

  if (deleteModeBtn) {
    deleteModeBtn.addEventListener('click', () => setDeleteMode(!deleteMode));
  }
 
  const selectionToggleBtn = document.getElementById('selectionToggleBtn');
  setSelectionEnabled(true);
  if (selectionToggleBtn) {
    selectionToggleBtn.addEventListener('click', () => {
      setSelectionEnabled(!selectionEnabled);
    });
  }

  if (revealDistrictBtn) {
    revealDistrictBtn.addEventListener('click', async () => {
      if (!selectedDistrictId) {
        alert('Select a district first.');
        return;
      }
      revealDistrictBtn.disabled = true;
      revealDistrictBtn.textContent = 'Revealing…';
      try {
        await revealEntireDistrict(selectedDistrictId);
      } catch (err) {
        console.warn('[debug] reveal district failed', err);
        alert('Failed to reveal district');
      } finally {
        revealDistrictBtn.disabled = false;
        revealDistrictBtn.textContent = 'Reveal District';
      }
    });
  }

  if (clearDbBtn) {
    clearDbBtn.addEventListener('click', async () => {
      if (!confirm('Clear the entire database? This action is irreversible.')) return;
      try {
        const res = await fetch('/api/v1/dev/clear-db', { method: 'POST' });
        if (!res.ok) throw new Error('clear-db failed');
        const data = await res.json().catch(() => ({}));
        allKnownHexagons.clear();
        countEl.textContent = '0';
        map.triggerRepaint();
        alert(`DB cleared. circles=${data.cleared_circles ?? '?'}, users=${data.cleared_users ?? '?'}`);
      } catch (e) {
        alert('Error clearing database');
        console.warn('[dev] clear-db error', e);
      }
    });
  }

  map.on('click', (e) => {
    if (ignoreNextClick) return;

    // First, try to detect district feature under cursor
    const districtFeatures = map.queryRenderedFeatures(e.point, {
      layers: [ADMIN_LAYERS.districtHitArea]
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
        .catch(error => {
          console.error('[visit] Failed to visit area:', error);
        });
      return;
    }

    if (deleteMode) {
      deleteHexAtPoint(e.point);
    }
  });
})();