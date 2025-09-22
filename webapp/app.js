(async function(){
  const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  if (tg) { try { tg.ready(); } catch (_) {} }

  async function checkNoAuthMode() {
    try {
      const response = await fetch('/api/v1/debug-mode');
      if (!response.ok) return false;
      const data = await response.json();
      return !!data.no_auth_mode;
    } catch (error) {
      console.warn('[auth] Failed to check debug mode:', error);
      return false;
    }
  }

  const hasInitData = !!(tg && tg.initData);
  const isNoAuthMode = await checkNoAuthMode();

  if (!hasInitData && !isNoAuthMode) {
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
  const FOG_CONFIG = {
    baseColor: 'rgba(42, 42, 42, 0.95)',
    blurAmount: 2,
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
  try {
    if (geolocate && geolocate._updateCamera) {
      geolocate._updateCamera = function(){};
    }
  } catch (_) {}
  map.addControl(geolocate);

  // --- State ---
  const allKnownHexagons = new Set();
  let isFetching = false;
  let fogEnabled = true;
  let noAuthMode = isNoAuthMode;

  if (noAuthMode) {
    toggleFogBtn.style.display = 'inline-block';
  }

  // --- Core Drawing Logic ---
  function drawFog() {
    if (!fogEnabled) {
      fogCtx.clearRect(0, 0, fogCanvas.width, fogCanvas.height);
      return;
    }

    fogCtx.fillStyle = FOG_CONFIG.baseColor;
    fogCtx.fillRect(0, 0, fogCanvas.width, fogCanvas.height);

    if (FOG_CONFIG.blurAmount > 0) {
      fogCtx.filter = `blur(${FOG_CONFIG.blurAmount}px)`;
      fogCtx.drawImage(fogCanvas, 0, 0);
      fogCtx.filter = 'none';
    }

    fogCtx.globalCompositeOperation = 'destination-out';
    fogCtx.fillStyle = 'white'; // Color doesn't matter with destination-out, but good practice

    allKnownHexagons.forEach(h3Index => {
      try {
        const boundary = h3.h3ToGeoBoundary(h3Index);
        const projectedBoundary = boundary.map(p => map.project([p[1], p[0]]));

        fogCtx.beginPath();
        fogCtx.moveTo(projectedBoundary[0].x, projectedBoundary[0].y);
        for (let i = 1; i < projectedBoundary.length; i++) {
          fogCtx.lineTo(projectedBoundary[i].x, projectedBoundary[i].y);
        }
        fogCtx.closePath();
        fogCtx.fill();
      } catch (e) {
        console.warn(`[h3] Invalid H3 index: ${h3Index}`, e);
      }
    });
    fogCtx.globalCompositeOperation = 'source-over';
  }

  let needsRedraw = false;
  function scheduleRedraw() {
      needsRedraw = true;
  }

  function animationLoop() {
      if (needsRedraw) {
          drawFog();
          needsRedraw = false;
      }
      requestAnimationFrame(animationLoop);
  }


  // --- Data Fetching Logic ---
  const loader = document.getElementById('loader');

  async function updateHexagonsFromServer() {
    if (isFetching) return;
    isFetching = true;

    const loaderTimeout = setTimeout(() => {
      if (loader) loader.style.display = 'flex';
    }, 300);

    try {
      // Bbox is removed, we fetch all hexagons for the user
      const response = await fetch(`/api/v1/hexagons`, { headers: { 'X-Telegram-Init': tg ? tg.initData : '' } });
      if (!response.ok) throw new Error(`Network error: ${response.statusText}`);
      const data = await response.json();

      let newHexagons = 0;
      data.hexagons.forEach(h3Index => {
        if (!allKnownHexagons.has(h3Index)) {
          allKnownHexagons.add(h3Index);
          newHexagons++;
        }
      });

      if (newHexagons > 0) {
        scheduleRedraw();
      }
      countEl.textContent = allKnownHexagons.size.toLocaleString();

    } catch (error) {
      console.error('[fog] Failed to fetch hexagons:', error);
    } finally {
      isFetching = false;
      clearTimeout(loaderTimeout);
      if (loader) loader.style.display = 'none';
    }
  }

  // --- Event Handlers ---
  map.on('load', () => {
    const mapContainer = document.getElementById('map-container');
    const controls = mapContainer.querySelector('.maplibregl-control-container');
    if (controls) {
      mapContainer.appendChild(controls);
    }

    const resizeObserver = new ResizeObserver(() => {
      fogCanvas.width = mapContainer.clientWidth;
      fogCanvas.height = mapContainer.clientHeight;
      scheduleRedraw();
    });
    resizeObserver.observe(mapContainer);

    updateHexagonsFromServer();
    try { geolocate.trigger(); } catch (e) { console.error(e); }

    // Start animation loop
    requestAnimationFrame(animationLoop);
  });
  
  // Redraw on map move/zoom
  map.on('move', scheduleRedraw);
  map.on('zoom', scheduleRedraw);
  // Also update from server when move ends
  map.on('moveend', updateHexagonsFromServer);


  let lastKnownPosition = null;
  const TARGET_GEO_ZOOM = 17;

  openBtn.disabled = true;
  openBtn.textContent = 'Определение...';

  geolocate.on('geolocate', (pos) => {
    lastKnownPosition = pos.coords;
    const zoom = Math.max(map.getZoom(), TARGET_GEO_ZOOM);
    map.flyTo({ center: [pos.coords.longitude, pos.coords.latitude], zoom });
    openBtn.disabled = false;
    openBtn.textContent = 'Открыть вокруг';
  });

  geolocate.on('error', () => {
    openBtn.textContent = 'Геолокация не удалась';
  });

  openBtn.addEventListener('click', async () => {
    if (!lastKnownPosition) {
      alert('Местоположение не определено.');
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

      // Update total count from server response
      if (result.stats && result.stats.total_hexagons) {
          countEl.textContent = result.stats.total_hexagons.toLocaleString();
      }

      // If a new hexagon was added, we need to find out which one
      // For simplicity, we just refetch all hexagons
      if (result.added > 0) {
        await updateHexagonsFromServer();
      }

    } catch (error) {
      console.error('[visit] Failed to visit area:', error);
    } finally {
      openBtn.disabled = !lastKnownPosition;
    }
  });

  // --- Fog Toggle Handler ---
  toggleFogBtn.addEventListener('click', () => {
    fogEnabled = !fogEnabled;
    toggleFogBtn.textContent = fogEnabled ? 'Скрыть туман' : 'Показать туман';
    if (!fogEnabled) {
        fogCtx.clearRect(0, 0, fogCanvas.width, fogCanvas.height);
    }
    scheduleRedraw();
  });

  // --- Remove Debug UI ---
  const debugPanel = document.getElementById('debugPanel');
  if (debugPanel) {
    debugPanel.remove();
  }
})();