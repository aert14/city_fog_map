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
        debugAuthMode: !!data.debug_auth_mode
      };
    } catch (error) {
      console.warn('[auth] Failed to check debug mode:', error);
      return { noAuthMode: false, debugAuthMode: false };
    }
  }

  const hasInitData = !!(tg && tg.initData);
  const { noAuthMode, debugAuthMode } = await getDebugSettings();

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
    style: 'https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json',
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
  window.currentH3Resolution = 9;
  // Prevent accidental clicks after dragging/zooming the map (phantom circles)
  let ignoreNextClick = false;

  if (noAuthMode || debugAuthMode) {
    toggleFogBtn.style.display = 'inline-block';
  }

  const cloudTexture = FogModule.createCloudTexture(512, 512);
  const cloudPattern = fogCtx.createPattern(cloudTexture, 'repeat');

  function drawFogLoop() {
    animationTime++;
    FogModule.drawFog(fogCtx, map, fogEnabled, allKnownHexagons, animationTime, FOG_CONFIG, DPR, cloudPattern);
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
      const response = await fetch(`/api/v1/circles?bbox=${bbox}`, { headers: { 'X-Telegram-Init': tg ? tg.initData : '' } });
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
    try { geolocate.trigger(); } catch (e) { console.error(e); }

    map.on('render', drawFogLoop);
  });

  map.on('moveend', updateHexagonsFromServer);
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
        headers: { 'Content-Type': 'application/json', 'X-Telegram-Init': tg ? tg.initData : '' },
        body: JSON.stringify({ lat: lastKnownPosition.latitude, lon: lastKnownPosition.longitude })
      });
      if (!response.ok) throw new Error(`Server error: ${response.statusText}`);
      const result = await response.json();
      if (result.added > 0) {
        const h3Resolution = window.currentH3Resolution || 11;
        const hexId = h3.latLngToCell(lastKnownPosition.latitude, lastKnownPosition.longitude, h3Resolution);
        allKnownHexagons.add(hexId);
        countEl.textContent = allKnownHexagons.size.toLocaleString();
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
  const radiusSlider = document.getElementById('radiusSlider');
  const radiusValue = document.getElementById('radiusValue');
  const deleteModeBtn = document.getElementById('deleteModeBtn');
  const clearDbBtn = document.getElementById('clearDbBtn');
  const debugPanel = document.getElementById('debugPanel');
  let deleteMode = false;

  function setDeleteMode(on) {
    deleteMode = !!on;
    if (deleteModeBtn) {
      deleteModeBtn.textContent = deleteMode ? 'Delete: On' : 'Delete: Off';
      deleteModeBtn.style.background = deleteMode ? '#b91c1c' : '#ef4444';
    }
  }

  if (noAuthMode || debugAuthMode) {
    if (debugPanel) debugPanel.style.display = 'flex';
  }

  if (deleteModeBtn) {
    deleteModeBtn.addEventListener('click', () => setDeleteMode(!deleteMode));
  }

  if (radiusSlider && radiusValue) {
    radiusValue.textContent = radiusSlider.value;
    radiusSlider.addEventListener('input', async () => {
      radiusValue.textContent = radiusSlider.value;
      const radiusValueNum = parseInt(radiusSlider.value, 10);
      try {
        const response = await fetch('/api/v1/radius', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Telegram-Init': tg ? tg.initData : '' },
          body: JSON.stringify({ radius_m: radiusValueNum })
        });
        if (!response.ok) throw new Error('radius update failed');
        const result = await response.json();
        window.currentH3Resolution = result.h3_resolution;
        if (result.resolution_changed) {
          allKnownHexagons.clear();
          console.log('H3 resolution changed, cleared local hexagon cache');
          updateHexagonsFromServer();
        }
      } catch (e) {
        console.warn('[debug] radius update error', e);
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

  map.on('click', async (e) => {
    if (ignoreNextClick) return;
    if (noAuthMode && !lastKnownPosition && !deleteMode) {
      const lngLat = map.unproject(e.point);
      try {
        const response = await fetch('/api/v1/visit', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Telegram-Init': tg ? tg.initData : '' },
          body: JSON.stringify({ lat: lngLat.lat, lon: lngLat.lng })
        });
        if (!response.ok) throw new Error(`Server error: ${response.statusText}`);
        const result = await response.json();
        if (result.added > 0) {
          const h3Resolution = window.currentH3Resolution || 11;
          const hexId = h3.latLngToCell(lngLat.lat, lngLat.lng, h3Resolution);
          allKnownHexagons.add(hexId);
          countEl.textContent = allKnownHexagons.size.toLocaleString();
          map.triggerRepaint();
        }
      } catch (error) {
        console.error('[visit] Failed to visit area by click:', error);
      }
      return;
    }

    if (!deleteMode) return;

    const lngLat = map.unproject(e.point);
    const h3Resolution = window.currentH3Resolution || 11;
    const targetHexId = h3.latLngToCell(lngLat.lat, lngLat.lng, h3Resolution);

    if (!allKnownHexagons.has(targetHexId)) {
      console.log("Clicked on a cell that is not a known hexagon:", targetHexId);
      return;
    }

    try {
      const response = await fetch('/api/v1/circle', {
        method: 'DELETE',
        headers: {
          'Content-Type': 'application/json',
          'X-Telegram-Init': tg ? tg.initData : ''
        },
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
        console.log("Deleted hexagon:", targetHexId);
      } else {
        console.warn("Delete command sent, but server reported 0 deleted.", {geokey: targetHexId});
      }
    } catch (err) {
      console.warn('[debug] delete error', err);
    }
  });
})();