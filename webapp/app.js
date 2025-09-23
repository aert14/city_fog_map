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
  const DPR = window.devicePixelRatio || 1;
  // Fog configuration
  const FOG_CONFIG = {
    // Base fog layer
    baseAlpha: 1.0, // Fully opaque base to completely cover the map
    baseColor: 'rgb(12, 12, 12)', // Very dark base color

    // Noise properties
    noiseScale: 0.015, // Larger, softer features
    noiseIntensity: 0.25, // Strength of shading when multiplying
    driftSpeed: 0.00002, // Slow drift

    // Hexagon reveal properties
    blurAmount: 4,
    animationSpeed: 0.001,
    pulseAmplitude: 0.05
  };

  // Animation state
  let animationTime = 0;
  let animationFrameId = null;
  // --- FPS Throttling ---
  let then = 0;
  const FPS = 25; // Target FPS
  const FPS_INTERVAL = 1000 / FPS;

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
    showAccuracyCircle: false, // <--- УБЕДИТЕСЬ, ЧТО ЭТА СТРОКА ЕСТЬ И У НЕЁ ЗНАЧЕНИЕ 'false'
    fitBoundsOptions: {
      maxZoom: 22 // Установите желаемый максимальный уровень масштабирования
  }
  });
  // Отключаем внутренние перемещения камеры у GeolocateControl
  try {
    if (geolocate && geolocate._updateCamera) {
      geolocate._updateCamera = function(){};
    }
  } catch (_) {}
  map.addControl(geolocate);

  // Keep fog canvas synchronized with MapLibre's CSS transform during zoom/pan animations
  fogCanvas.style.transformOrigin = '0 0';
  map.on('render', () => {
    const mapCanvas = map.getCanvas();
    if (mapCanvas && mapCanvas.style && fogCanvas.style) {
      fogCanvas.style.transform = mapCanvas.style.transform || '';
    }
  });
  // --- State ---
  const allKnownHexagons = new Set();
  let isFetching = false;
  let fogEnabled = true;
  let noAuthMode = isNoAuthMode; // Use the value from the initial check

  // Initialize H3 resolution (will be updated by radius slider)
  window.currentH3Resolution = 9; // Larger default hexagons

  // Show toggle fog button in no-auth mode
  if (noAuthMode) {
    toggleFogBtn.style.display = 'inline-block';
  }

  // --- Core Drawing Logic ---

  /**
   * Creates a canvas with a seamless, tileable noise pattern.
   * Uses OffscreenCanvas if available with a DOM Canvas fallback.
   * The scale parameter controls feature size: smaller values -> larger blobs.
   * @param {number} width
   * @param {number} height
   * @param {number} scale
   * @returns {Canvas}
   */
  function createNoisePattern(width, height, scale) {
    const useOffscreen = typeof OffscreenCanvas === 'function';
    const canvas = useOffscreen ? new OffscreenCanvas(width, height) : document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext('2d');

    const imageData = ctx.createImageData(width, height);
    const data = imageData.data;

    // Base frequency controls feature size
    const baseFreq = Math.max(0.002, Math.min(0.1, scale || 0.02));

    // Periodic value noise with bilinear interpolation + fBm (multi-octave)
    function hash2(ix, iy) {
      const s = Math.sin(ix * 127.1 + iy * 311.7) * 43758.5453123;
      return s - Math.floor(s);
    }
    function smoothstep(t) { return t * t * (3 - 2 * t); }

    // To ensure seamless tiling, wrap grid indices with a fixed period
    const GRID_PERIOD = 64; // grid cells per tile on base octave

    function valueNoise(u, v, freq) {
      const uu = u * freq;
      const vv = v * freq;
      const i0 = Math.floor(uu);
      const j0 = Math.floor(vv);
      const fx = smoothstep(uu - i0);
      const fy = smoothstep(vv - j0);
      const ix0 = ((i0 % GRID_PERIOD) + GRID_PERIOD) % GRID_PERIOD;
      const iy0 = ((j0 % GRID_PERIOD) + GRID_PERIOD) % GRID_PERIOD;
      const ix1 = (ix0 + 1) % GRID_PERIOD;
      const iy1 = (iy0 + 1) % GRID_PERIOD;
      const v00 = hash2(ix0, iy0);
      const v10 = hash2(ix1, iy0);
      const v01 = hash2(ix0, iy1);
      const v11 = hash2(ix1, iy1);
      const vx0 = v00 * (1 - fx) + v10 * fx;
      const vx1 = v01 * (1 - fx) + v11 * fx;
      return vx0 * (1 - fy) + vx1 * fy;
    }

    const octaves = 4;
    const persistence = 0.5; // amplitude per octave
    const lacunarity = 2.0;  // frequency multiplier per octave

    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) {
        const u = x / width;
        const v = y / height;
        let amp = 1.0;
        let freq = baseFreq * GRID_PERIOD; // scale to grid period
        let sum = 0.0;
        let norm = 0.0;
        for (let o = 0; o < octaves; o++) {
          sum += valueNoise(u * GRID_PERIOD, v * GRID_PERIOD, freq) * amp;
          norm += amp;
          amp *= persistence;
          freq *= lacunarity;
        }
        const value = Math.floor((sum / norm) * 255);
        const index = (y * width + x) * 4;
        data[index] = value;
        data[index + 1] = value;
        data[index + 2] = value;
        data[index + 3] = 255;
      }
    }
    ctx.putImageData(imageData, 0, 0);
    return canvas;
  }

  const NOISE_TILE_SIZE = 256;
  const noisePattern = createNoisePattern(NOISE_TILE_SIZE, NOISE_TILE_SIZE, FOG_CONFIG.noiseScale);


  function drawFog() {
    if (!fogEnabled) {
      fogCtx.clearRect(0, 0, fogCanvas.width, fogCanvas.height);
      return;
    }

    // Work in CSS pixel space to match MapLibre and our DPR transform
    const width = fogCanvas.clientWidth || fogCanvas.width / DPR;
    const height = fogCanvas.clientHeight || fogCanvas.height / DPR;

    // 1. Base fog layer
    fogCtx.globalCompositeOperation = 'source-over';
    fogCtx.fillStyle = FOG_CONFIG.baseColor;
    fogCtx.globalAlpha = FOG_CONFIG.baseAlpha;
    fogCtx.fillRect(0, 0, width, height);
    fogCtx.globalAlpha = 1.0; // Reset alpha

    // 2. Shade fog with noise (no transparency) for visuals only
    const pattern = fogCtx.createPattern(noisePattern, 'repeat');
    fogCtx.globalCompositeOperation = 'multiply';
    fogCtx.fillStyle = pattern;

    // Animate the drift
    const dx = (animationTime * FOG_CONFIG.driftSpeed * width) % noisePattern.width;
    const dy = (animationTime * FOG_CONFIG.driftSpeed * height) % noisePattern.height;
    fogCtx.save();
    fogCtx.translate(dx, dy);
    fogCtx.globalAlpha = FOG_CONFIG.noiseIntensity;
    const prevFilter = fogCtx.filter;
    fogCtx.filter = 'blur(0.6px)';
    fogCtx.fillRect(-dx, -dy, width, height);
    fogCtx.filter = prevFilter || 'none';
    fogCtx.restore();

    // 3. Clear areas where fog has been "dispelled" with hexagon shapes
    fogCtx.globalCompositeOperation = 'destination-out';
    allKnownHexagons.forEach(hexId => {
      try {
        const boundary = h3.cellToBoundary(hexId);
        const center = h3.cellToLatLng(hexId);

        // Convert hexagon boundary to screen coordinates
        const screenPoints = boundary.map(([lat, lng]) => map.project([lng, lat]));

        // Calculate hexagon center on screen
        const centerPixels = map.project([center[1], center[0]]);

        // Add slight pulsing to hexagons for more dynamic effect
        const hexPulse = 1 + Math.sin(animationTime * FOG_CONFIG.animationSpeed + center[0] * 10 + center[1] * 10) * FOG_CONFIG.pulseAmplitude;

        // Create hexagon path
        fogCtx.beginPath();
        fogCtx.moveTo(screenPoints[0].x, screenPoints[0].y);
        for (let i = 1; i < screenPoints.length; i++) {
          fogCtx.lineTo(screenPoints[i].x, screenPoints[i].y);
        }
        fogCtx.closePath();

        // Two-phase reveal: 1) clear hole 2) draw subtle rim in multiply
        // 1) Clear hole fully
        fogCtx.fillStyle = 'rgba(255,255,255,1)';
        fogCtx.fill();

        // 2) Subtle rim for visible boundaries
        const bounds = fogCtx.getPathBounds ? fogCtx.getPathBounds() : {
          left: Math.min(...screenPoints.map(p => p.x)),
          right: Math.max(...screenPoints.map(p => p.x)),
          top: Math.min(...screenPoints.map(p => p.y)),
          bottom: Math.max(...screenPoints.map(p => p.y))
        };
        const hexagonRadius = Math.max(bounds.right - bounds.left, bounds.bottom - bounds.top) * hexPulse / 2;
        const rimRadius = hexagonRadius * 1.08;
        const gradient = fogCtx.createRadialGradient(
          centerPixels.x, centerPixels.y, hexagonRadius * 0.9,
          centerPixels.x, centerPixels.y, rimRadius
        );
        gradient.addColorStop(0.0, 'rgba(0,0,0,0)');
        gradient.addColorStop(1.0, 'rgba(0,0,0,0.7)');
        fogCtx.save();
        fogCtx.globalCompositeOperation = 'multiply';
        fogCtx.fillStyle = gradient;
        fogCtx.fill();
        fogCtx.restore();
      } catch (error) {
        console.warn('[fog] Error drawing hexagon:', hexId, error);
      }
    });
    fogCtx.globalCompositeOperation = 'source-over';
  }

  // Animation loop
  function animateFog() {
    // The loop is controlled by start/stop functions.
    // We request the next frame first.
    animationFrameId = requestAnimationFrame(animateFog);

    // Check if enough time has passed to draw the next frame.
    const now = performance.now();
    const elapsed = now - then;

    if (elapsed > FPS_INTERVAL) {
      // Adjust 'then' to maintain a consistent framerate, preventing drift.
      then = now - (elapsed % FPS_INTERVAL);

      // Run the core animation and drawing logic.
      animationTime++;
      drawFog();
    }
  }

  function startFogAnimation() {
    if (fogEnabled && !animationFrameId) {
      then = performance.now(); // Reset timer.
      animateFog();
    }
  }

  function stopFogAnimation() {
    if (animationFrameId) {
      cancelAnimationFrame(animationFrameId);
      animationFrameId = null;
    }
  }

  // --- Data Fetching Logic ---
  const loader = document.getElementById('loader');

  async function updateHexagonsFromServer() {
    if (isFetching) return;
    isFetching = true;

    const loaderTimeout = setTimeout(() => {
      if (loader) loader.style.display = 'flex';
    }, 500); // Only show loader if request takes > 300ms

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
      // Fog will be updated automatically by animation loop
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

    // --- РЕШЕНИЕ ПРОБЛЕМЫ ---
    // 1. Находим контейнер с кнопками, который создал MapLibre.
    const controls = mapContainer.querySelector('.maplibregl-control-container');
    if (controls) {
      // 2. "Вынимаем" его из карты и вставляем как прямой дочерний элемент #map-container.
      // Теперь он является "соседом" карты и холста, а не их "потомком".
      mapContainer.appendChild(controls);
    }
    // -------------------------

    const resizeObserver = new ResizeObserver(() => {
      const cssW = mapContainer.clientWidth;
      const cssH = mapContainer.clientHeight;
      // Style size in CSS pixels
      fogCanvas.style.width = cssW + 'px';
      fogCanvas.style.height = cssH + 'px';
      // Backing store size in device pixels
      fogCanvas.width = Math.max(1, Math.floor(cssW * DPR));
      fogCanvas.height = Math.max(1, Math.floor(cssH * DPR));
      // Scale context so that drawing uses CSS pixel coordinates
      fogCtx.setTransform(DPR, 0, 0, DPR, 0, 0);
      // Fog will be redrawn automatically by animation loop
    });
    resizeObserver.observe(mapContainer);

    updateHexagonsFromServer();

    try { geolocate.trigger(); } catch (e) { console.error(e); }

    // Start fog animation if enabled
    startFogAnimation();
  });

  // Map events - fog animation handles redrawing automatically
  map.on('moveend', updateHexagonsFromServer);
  map.on('zoomend', updateHexagonsFromServer);
  
  function tryLocateCenter() {
    if (!navigator.geolocation) return;
    navigator.geolocation.getCurrentPosition(
      (pos) => map.flyTo({ center: [pos.coords.longitude, pos.coords.latitude], zoom: 15 }),
      (err) => console.warn('[geo] getCurrentPosition error:', err.message)
    );
  }
  let lastKnownPosition = null;
  // При нажатии кнопки геолокации приближать минимум до целевого зума
  const TARGET_GEO_ZOOM = 17;

  openBtn.disabled = true; // Disable by default
  openBtn.textContent = 'Определение...';

  geolocate.on('geolocate', (pos) => {
    lastKnownPosition = pos.coords;
    const zoom = Math.max(map.getZoom(), TARGET_GEO_ZOOM);
    map.flyTo({ center: [pos.coords.longitude, pos.coords.latitude], zoom });
    openBtn.disabled = false;
    openBtn.textContent = 'Открыть 50 м вокруг';
  });

  geolocate.on('error', () => {
    if (noAuthMode) {
      openBtn.textContent = 'Кликните на карту для добавления точки';
      openBtn.disabled = false;
    } else {
      openBtn.textContent = 'Геолокация не удалась';
    }
    // Maybe show a message to the user here
  });

  openBtn.addEventListener('click', async () => {
    if (!lastKnownPosition) {
      // This should not happen if the button is disabled
      if (noAuthMode) {
        alert('Используйте клик на карту для добавления точек.');
      } else {
        alert('Местоположение не определено.');
      }
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
        // Convert the visited location to hexagon ID
        const h3Resolution = window.currentH3Resolution || 11; // Use current resolution or default
        const hexId = h3.latLngToCell(lastKnownPosition.latitude, lastKnownPosition.longitude, h3Resolution);
        allKnownHexagons.add(hexId);
        countEl.textContent = allKnownHexagons.size.toLocaleString();
        // Fog will be updated automatically by animation loop
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
    if (fogEnabled) {
      startFogAnimation();
    } else {
      stopFogAnimation();
      fogCtx.clearRect(0, 0, fogCanvas.width, fogCanvas.height);
    }
  });

  // --- Debug UI: radius slider + delete mode ---
  const radiusSlider = document.getElementById('radiusSlider');
  const radiusValue = document.getElementById('radiusValue');
  const deleteModeBtn = document.getElementById('deleteModeBtn');
  const clearDbBtn = document.getElementById('clearDbBtn');
  const debugPanel = document.getElementById('debugPanel');
  let deleteMode = false;

  function setDeleteMode(on) {
    deleteMode = !!on;
    if (deleteModeBtn) {
      deleteModeBtn.textContent = deleteMode ? 'Удаление: вкл' : 'Удаление: выкл';
      deleteModeBtn.style.background = deleteMode ? '#b91c1c' : '#ef4444';
    }
  }

  // Show debug panel only in noAuthMode
  if (isNoAuthMode) {
    if (debugPanel) debugPanel.style.display = 'flex';
  }

  if (deleteModeBtn) {
    deleteModeBtn.addEventListener('click', () => setDeleteMode(!deleteMode));
  }

  if (radiusSlider && radiusValue) {
    radiusValue.textContent = radiusSlider.value;
    radiusSlider.addEventListener('input', async () => {
      radiusValue.textContent = radiusSlider.value;
      const radiusValue = parseInt(radiusSlider.value, 10);

      // Map radius to H3 resolution (same logic as backend)
      let h3Resolution;
      if (radiusValue <= 30) {
        h3Resolution = 13;  // Small hexagons (~100m)
      } else if (radiusValue <= 70) {
        h3Resolution = 12;  // Medium-small hexagons (~200m)
      } else if (radiusValue <= 150) {
        h3Resolution = 11;  // Medium hexagons (~400m)
      } else {
        h3Resolution = 10;  // Large hexagons (~800m)
      }

      try {
        const response = await fetch('/api/v1/radius', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Telegram-Init': tg ? tg.initData : '' },
          body: JSON.stringify({ radius_m: radiusValue })
        });
        if (!response.ok) throw new Error('radius update failed');

        const result = await response.json();

        // Store current H3 resolution for use in hexagon operations
        window.currentH3Resolution = h3Resolution;

        // If resolution changed, backend cleared all data, so we need to clear local cache too
        if (result.resolution_changed) {
          allKnownHexagons.clear();
          console.log('H3 resolution changed, cleared local hexagon cache');
        }

        // Refresh data from server
        updateHexagonsFromServer();
      } catch (e) {
        console.warn('[debug] radius update error', e);
      }
    });
  }

  // Debug: clear DB button (available only in dev/no-auth modes from backend)
  if (clearDbBtn) {
    clearDbBtn.addEventListener('click', async () => {
      if (!confirm('Очистить всю БД? Это действие необратимо.')) return;
      try {
        const res = await fetch('/api/v1/dev/clear-db', { method: 'POST' });
        if (!res.ok) throw new Error('clear-db failed');
        const data = await res.json().catch(() => ({}));
        allKnownHexagons.clear();
        countEl.textContent = '0';
        // немедленно перерисуем туман
        drawFog();
        alert(`БД очищена. circles=${data.cleared_circles ?? '?'}, users=${data.cleared_users ?? '?'}`);
      } catch (e) {
        alert('Ошибка очистки БД');
        console.warn('[dev] clear-db error', e);
      }
    });
  }

  // Добавление точки по клику в no-auth режиме (если геолокация недоступна)
  map.on('click', async (e) => {
    // Режим добавления точек в no-auth режиме без геолокации
    if (noAuthMode && !lastKnownPosition && !deleteMode) {
      const lngLat = map.unproject(e.point);
      const lat = lngLat.lat;
      const lng = lngLat.lng;

      try {
        const response = await fetch('/api/v1/visit', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Telegram-Init': tg ? tg.initData : '' },
          body: JSON.stringify({ lat: lat, lon: lng })
        });
        if (!response.ok) throw new Error(`Server error: ${response.statusText}`);
        const result = await response.json();
        if (result.added > 0) {
          // Convert the visited location to hexagon ID
          const h3Resolution = window.currentH3Resolution || 11; // Use current resolution or default
          const hexId = h3.latLngToCell(lat, lng, h3Resolution);
          allKnownHexagons.add(hexId);
          countEl.textContent = allKnownHexagons.size.toLocaleString();
          // Fog will be updated automatically by animation loop
        }
      } catch (error) {
        console.error('[visit] Failed to visit area by click:', error);
      }
      return; // Не продолжаем с логикой удаления
    }

    if (!deleteMode) return;

    // Convert click coordinates to lat/lng
    const lngLat = map.unproject(e.point);
    const lat = lngLat.lat;
    const lng = lngLat.lng;

    // Find hexagon that contains this point
    let targetHexId = null;
    allKnownHexagons.forEach(hexId => {
      try {
        // Check if the point is in this hexagon
        const h3Resolution = window.currentH3Resolution || 11; // Use current resolution or default
        const pointCell = h3.latLngToCell(lat, lng, h3Resolution);
        if (pointCell === hexId) {
          targetHexId = hexId;
        }
      } catch (error) {
        // Ignore invalid hexagons
      }
    });

    if (!targetHexId) return; // No hexagon found at click location

    // Get center coordinates of the hexagon for deletion
    const center = h3.cellToLatLng(targetHexId);
    try {
      const response = await fetch(`/api/v1/circle?lat=${center[0]}&lon=${center[1]}`, {
        method: 'DELETE',
        headers: { 'X-Telegram-Init': tg ? tg.initData : '' }
      });
      if (!response.ok) throw new Error('delete failed');
      const res = await response.json();
      if (res.deleted > 0) {
        allKnownHexagons.delete(targetHexId);
        countEl.textContent = allKnownHexagons.size.toLocaleString();
        // Fog will be updated automatically by animation loop
      }
    } catch (err) {
      console.warn('[debug] delete error', err);
    }
  });
})();