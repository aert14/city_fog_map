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
  // Fog configuration
  const FOG_CONFIG = {
    baseColor: '#2a2a2a',
    gradientColors: ['rgba(42, 42, 42, 0.9)', 'rgba(32, 32, 32, 0.7)', 'rgba(22, 22, 22, 0.5)', 'rgba(15, 15, 15, 0.3)'],
    blurAmount: 2,
    animationSpeed: 0.001,
    pulseAmplitude: 0.1
  };

  // Animation state
  let animationTime = 0;
  let animationFrameId = null;

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
  // --- State ---
  const allKnownCircles = new Map();
  let isFetching = false;
  let fogEnabled = true;
  let noAuthMode = isNoAuthMode; // Use the value from the initial check

  // Show toggle fog button in no-auth mode
  if (noAuthMode) {
    toggleFogBtn.style.display = 'inline-block';
  }

  // --- Core Drawing Logic ---
  function drawFog() {
    if (!fogEnabled) {
      fogCtx.clearRect(0, 0, fogCanvas.width, fogCanvas.height);
      return;
    }

    // Create layered fog effect with animation
    const layers = FOG_CONFIG.gradientColors.length;
    const layerHeight = fogCanvas.height / layers;
    const pulse = 1 + Math.sin(animationTime * FOG_CONFIG.animationSpeed) * FOG_CONFIG.pulseAmplitude;

    for (let i = 0; i < layers; i++) {
      const animatedLayerHeight = layerHeight * pulse;
      const yOffset = i * layerHeight + (layerHeight - animatedLayerHeight) / 2;

      const gradient = fogCtx.createLinearGradient(0, yOffset, 0, yOffset + animatedLayerHeight);
      gradient.addColorStop(0, FOG_CONFIG.gradientColors[i]);
      gradient.addColorStop(1, FOG_CONFIG.gradientColors[Math.min(i + 1, layers - 1)]);

      fogCtx.fillStyle = gradient;
      fogCtx.fillRect(0, yOffset, fogCanvas.width, animatedLayerHeight);
    }

    // Apply blur for fog effect
    if (FOG_CONFIG.blurAmount > 0) {
      fogCtx.filter = `blur(${FOG_CONFIG.blurAmount}px)`;
      fogCtx.drawImage(fogCanvas, 0, 0);
      fogCtx.filter = 'none';
    }

    // Clear areas where fog has been "dispelled" with smooth edges
    fogCtx.globalCompositeOperation = 'destination-out';
    allKnownCircles.forEach(circle => {
      const centerPixels = map.project([circle.lon, circle.lat]);
      const edgeLonLat = [circle.lon + 0.001, circle.lat];
      const edgePixels = map.project(edgeLonLat);
      const pixelsPerLonDegree = Math.abs(edgePixels.x - centerPixels.x) / 0.001;
      const metersPerDegree = 111320 * Math.cos(circle.lat * Math.PI / 180);
      const radiusPixels = (circle.radius_m / metersPerDegree) * pixelsPerLonDegree;

      // Add slight pulsing to circles for more dynamic effect
      const circlePulse = 1 + Math.sin(animationTime * FOG_CONFIG.animationSpeed + circle.lat * 10 + circle.lon * 10) * 0.05;
      const animatedRadius = radiusPixels * circlePulse;

      // Create radial gradient for smooth edges
      const gradient = fogCtx.createRadialGradient(
        centerPixels.x, centerPixels.y, 0,
        centerPixels.x, centerPixels.y, animatedRadius
      );
      gradient.addColorStop(0, 'rgba(255, 255, 255, 1)');
      gradient.addColorStop(0.7, 'rgba(255, 255, 255, 0.8)');
      gradient.addColorStop(1, 'rgba(255, 255, 255, 0)');

      fogCtx.beginPath();
      fogCtx.arc(centerPixels.x, centerPixels.y, animatedRadius, 0, Math.PI * 2);
      fogCtx.fillStyle = gradient;
      fogCtx.fill();
    });
    fogCtx.globalCompositeOperation = 'source-over';
  }

  // Animation loop
  function animateFog() {
    if (fogEnabled) {
      animationTime += 1;
      drawFog();
      animationFrameId = requestAnimationFrame(animateFog);
    }
  }

  function startFogAnimation() {
    if (fogEnabled && !animationFrameId) {
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

  async function updateCirclesFromServer() {
    if (isFetching) return;
    isFetching = true;

    const loaderTimeout = setTimeout(() => {
      if (loader) loader.style.display = 'flex';
    }, 300); // Only show loader if request takes > 300ms

    try {
      const bounds = map.getBounds();
      const bbox = [bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth()].join(',');
      const response = await fetch(`/api/v1/circles?bbox=${bbox}`, { headers: { 'X-Telegram-Init': tg ? tg.initData : '' } });
      if (!response.ok) throw new Error(`Network error: ${response.statusText}`);
      const data = await response.json();
      let newCircles = 0;
      data.circles.forEach(c => {
        const id = `${c.lat},${c.lon}`;
        if (!allKnownCircles.has(id)) {
          allKnownCircles.set(id, c);
          newCircles++;
        }
      });
      // Fog will be updated automatically by animation loop
      countEl.textContent = allKnownCircles.size.toLocaleString();
    } catch (error) {
      console.error('[fog] Failed to fetch circles:', error);
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
      fogCanvas.width = mapContainer.clientWidth;
      fogCanvas.height = mapContainer.clientHeight;
      // Fog will be redrawn automatically by animation loop
    });
    resizeObserver.observe(mapContainer);

    updateCirclesFromServer();

    try { geolocate.trigger(); } catch (e) { console.error(e); }

    // Start fog animation if enabled
    startFogAnimation();
  });

  // Map events - fog animation handles redrawing automatically
  map.on('moveend', updateCirclesFromServer);
  map.on('zoomend', updateCirclesFromServer);
  
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
    openBtn.textContent = 'Геолокация не удалась';
    // Maybe show a message to the user here
  });

  openBtn.addEventListener('click', async () => {
    if (!lastKnownPosition) {
      // This should not happen if the button is disabled
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
      if (result.added > 0) {
        const c = result.circle;
        allKnownCircles.set(`${c.lat},${c.lon}`, {lat: c.lat, lon: c.lon, radius_m: c.radius_m});
        countEl.textContent = allKnownCircles.size.toLocaleString();
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
      try {
        const response = await fetch('/api/v1/radius', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Telegram-Init': tg ? tg.initData : '' },
          body: JSON.stringify({ radius_m: parseInt(radiusSlider.value, 10) })
        });
        if (!response.ok) throw new Error('radius update failed');
        // обновим локально радиусы для визуальной мгновенной обратной связи
        const newR = parseInt(radiusSlider.value, 10);
        allKnownCircles.forEach((c, id) => { c.radius_m = newR; allKnownCircles.set(id, c); });
        // Fog will be updated automatically by animation loop
      } catch (e) {
        console.warn('[debug] radius update error', e);
      }
    });
  }

  // Удаление ближайшей точки по клику в режиме удаления
  map.on('click', async (e) => {
    if (!deleteMode) return;
    let bestId = null;
    let bestDist = Infinity;
    allKnownCircles.forEach((c, id) => {
      const p = map.project([c.lon, c.lat]);
      const d = Math.hypot(p.x - e.point.x, p.y - e.point.y);
      if (d < bestDist) { bestDist = d; bestId = id; }
    });
    if (!bestId || bestDist > 30) return; // слишком далеко от клика
    const c = allKnownCircles.get(bestId);
    try {
      const response = await fetch(`/api/v1/circle?lat=${c.lat}&lon=${c.lon}`, {
        method: 'DELETE',
        headers: { 'X-Telegram-Init': tg ? tg.initData : '' }
      });
      if (!response.ok) throw new Error('delete failed');
      const res = await response.json();
      if (res.deleted > 0) {
        allKnownCircles.delete(bestId);
        countEl.textContent = allKnownCircles.size.toLocaleString();
        // Fog will be updated automatically by animation loop
      }
    } catch (err) {
      console.warn('[debug] delete error', err);
    }
  });
})();