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
  const fogCanvas = document.getElementById('fog-canvas');
  const fogCtx = fogCanvas.getContext('2d');
  const DPR = window.devicePixelRatio || 1;

  const FOG_CONFIG = {
    shadowBlur: 55,
    revealBlur: 40,
    pulseAmplitude: 0.04,
    animationSpeed: 0.0015,
  };

  // --- Инициализация карты ---
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

  // --- Состояние приложения ---
  const allKnownHexagons = new Set();
  let isFetching = false;
  let fogEnabled = true;
  let noAuthMode = isNoAuthMode;
  let animationTime = 0;
  window.currentH3Resolution = 9;

  if (noAuthMode) {
    toggleFogBtn.style.display = 'inline-block';
  }

  // --- ФИНАЛЬНАЯ ЛОГИКА ГЕНЕРАЦИИ ОБЛАКОВ ---

  function createCloudTexture(width, height) {
    const useOffscreen = typeof OffscreenCanvas === 'function';
    const canvas = useOffscreen ? new OffscreenCanvas(width, height) : document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext('2d');
    const imageData = ctx.createImageData(width, height);
    const data = imageData.data;

      // Генератор шума
      let seed = Math.random(); function random() { const x = Math.sin(seed++) * 10000; return x - Math.floor(x); }
      const p = new Uint8Array(512);
      for (let i = 0; i < 256; i++) p[i] = i;
      for (let i = 255; i > 0; i--) { const j = Math.floor(random() * (i + 1)); [p[i], p[j]] = [p[j], p[i]]; }
      for (let i = 0; i < 256; i++) p[i + 256] = p[i];
      function fade(t) { return t * t * t * (t * (t * 6 - 15) + 10); }
      function lerp(t, a, b) { return a + t * (b - a); }
      function grad(hash, x, y) { const h = hash & 7; const u = h < 4 ? x : y; const v = h < 4 ? y : x; return ((h & 1) ? -u : u) + ((h & 2) ? -2 * v : 2 * v); }
      function noise(x, y) {
          const X = Math.floor(x) & 255, Y = Math.floor(y) & 255; x -= Math.floor(x); y -= Math.floor(y);
          const u = fade(x), v = fade(y);
          const A = p[X] + Y, B = p[X + 1] + Y;
          return lerp(v, lerp(u, grad(p[A], x, y), grad(p[B], x - 1, y)), lerp(u, grad(p[A + 1], x, y - 1), grad(p[B + 1], x - 1, y - 1)));
      }
      function fBm(x, y, octaves) {
          let total = 0, frequency = 1, amplitude = 1, maxValue = 0;
          for (let i = 0; i < octaves; i++) {
              total += noise(x * frequency, y * frequency) * amplitude;
              maxValue += amplitude; amplitude *= 0.5; frequency *= 2.0;
          }
          return (total / maxValue + 1) / 2;
      }
      function smoothstep(edge0, edge1, x) {
          const t = Math.max(0, Math.min(1, (x - edge0) / (edge1 - edge0)));
          return t * t * (3 - 2 * t);
      }

      const sample = (x, y, scale, oct) => {
        const nx = x/width, ny = y/height;
        return lerp(smoothstep(0,1,ny), lerp(smoothstep(0,1,nx), fBm(x*scale,y*scale,oct), fBm((x-width)*scale,y*scale,oct)), lerp(smoothstep(0,1,nx), fBm(x*scale,(y-height)*scale,oct), fBm((x-width)*scale,(y-height)*scale,oct)));
      };

      for (let j = 0; j < height; j++) {
          for (let i = 0; i < width; i++) {
              
               const base_scale = 0.008;    // Крупный масштаб для масс и просветов
               const detail_scale = 0.075;  // Больше деталей для объёма
               const height_scale = 0.02;   // Масштаб для высоты и глубины

               const v_base = sample(i, j, base_scale, 6);  // Больше октав для плотности
               const v_detail = sample(i, j, detail_scale, 10); // Ещё больше октав для деталей
               const v_height = sample(i, j, height_scale, 8); // Больше октав для глубины

               const idx = (j * width + i) * 4;

               // --- Финальная формула ---
               const base_alpha = 0.88; // Почти сплошные облака как в Civ5

               // Более плотные облака - меньше просветов и больше глубины
               let density = smoothstep(0.12, 0.88, v_base);
               density = Math.pow(density, 0.75); // поджимаем к единице для общей плотности
               const height_factor = smoothstep(0.08, 0.92, v_height); // Высота влияет на плотность
               const final_density = Math.min(1.0, density + height_factor * 0.45); // Больше влияния высоты для глубины
               const final_alpha = base_alpha + (1 - base_alpha) * final_density;

               // Создаём ощущение глубины через несколько цветовых градиентов (очень белые, объёмные)
               const shadow_color = [220, 225, 230];   // Чуть темнее тени для глубины
               const mid_color = [250, 251, 252];      // Почти чисто белые средние тона
               const peak_color = [255, 255, 255];     // Чисто белые вершины

               // Увеличиваем объёмность - больше влияния деталей и высоты
               const volume_factor = v_detail * 0.55 + height_factor * 0.45;
               let color_intensity = smoothstep(0.05, 0.95, volume_factor);
               // Повышаем локальный контраст, чтобы убрать блеклость
               color_intensity = Math.min(1, Math.max(0, 0.5 + (color_intensity - 0.5) * 1.35));

               // Минимальный атмосферный эффект для сохранения белизны
               const atmosphere_factor = smoothstep(0.4, 0.8, height_factor);
               const atmosphere_darkening = 1 - atmosphere_factor * 0.03;

               // Интерполяция между цветами для создания глубины
               let r, g, b;
               if (color_intensity < 0.5) {
                   // От теней к средним тонам
                   const t = color_intensity * 2;
                   r = lerp(t, shadow_color[0], mid_color[0]);
                   g = lerp(t, shadow_color[1], mid_color[1]);
                   b = lerp(t, shadow_color[2], mid_color[2]);
               } else {
                   // От средних тонов к вершинам
                   const t = (color_intensity - 0.5) * 2;
                   r = lerp(t, mid_color[0], peak_color[0]);
                   g = lerp(t, mid_color[1], peak_color[1]);
                   b = lerp(t, mid_color[2], peak_color[2]);
               }

               // Простое освещение на основе высоты и деталей: усиливаем свет и тени
               const lightFactor = smoothstep(0.3, 0.8, v_height * 0.6 + v_detail * 0.4);
               const highlight = lightFactor * 0.18;
               const shadow = (1 - lightFactor) * 0.18;
               r = r * (1 - shadow) + 255 * highlight;
               g = g * (1 - shadow) + 255 * highlight;
               b = b * (1 - shadow) + 255 * highlight;

               // Применяем атмосферный эффект
               r *= atmosphere_darkening;
               g *= atmosphere_darkening;
               b *= atmosphere_darkening;

              data[idx] = r; data[idx+1] = g; data[idx+2] = b;
              data[idx+3] = Math.floor(final_alpha * 255);
      }
    }
    ctx.putImageData(imageData, 0, 0);
    return canvas;
  }

  const cloudTexture = createCloudTexture(512, 512);
  const cloudPattern = fogCtx.createPattern(cloudTexture, 'repeat');
  const patternMatrix = new DOMMatrix();

  function drawFog() {
    animationTime++;
    const width = fogCanvas.clientWidth || fogCanvas.width / DPR;
    const height = fogCanvas.clientHeight || fogCanvas.height / DPR;

    fogCtx.clearRect(0, 0, width, height);
    if (!fogEnabled) return;
    
    fogCtx.globalCompositeOperation = 'source-over';
    const zoom = map.getZoom();
    const scale = Math.pow(2, zoom - 12);
    const mapOffset = map.project([0, 0]);
    
    patternMatrix.a = scale; patternMatrix.d = scale;
    patternMatrix.e = mapOffset.x; patternMatrix.f = mapOffset.y;
    cloudPattern.setTransform(patternMatrix);
    
    fogCtx.fillStyle = cloudPattern;
    fogCtx.fillRect(0, 0, width, height);

    if (allKnownHexagons.size === 0) return;

    // --- ОПТИМИЗАЦИЯ: Фильтруем гексагоны, оставляя только видимые на экране ---
    const bounds = map.getBounds();
    // Добавляем небольшой отступ, чтобы гексагоны на границе не исчезали резко
    const sw = bounds.getSouthWest();
    const ne = bounds.getNorthEast();
    const paddingLat = (ne.lat - sw.lat) * 0.1;
    const paddingLng = (ne.lng - sw.lng) * 0.1;
    
    const visibleHexagons = [];
    allKnownHexagons.forEach(hexId => {
      try {
        const center = h3.cellToLatLng(hexId); // [lat, lng]
        if (center[0] > sw.lat - paddingLat && center[0] < ne.lat + paddingLat &&
            center[1] > sw.lng - paddingLng && center[1] < ne.lng + paddingLng) {
          visibleHexagons.push(hexId);
        }
      } catch (e) {}
    });
    // --- КОНЕЦ ОПТИМИЗАЦИИ ---

    // Этап А: Тень под облаками
    fogCtx.globalCompositeOperation = 'destination-out';
    fogCtx.filter = `blur(${FOG_CONFIG.shadowBlur}px)`;
    // Используем отфильтрованный массив visibleHexagons вместо allKnownHexagons
    visibleHexagons.forEach(hexId => {
      try {
        const boundary = h3.cellToBoundary(hexId);
        const screenPoints = boundary.map(([lat, lng]) => map.project([lng, lat]));
        fogCtx.beginPath();
        fogCtx.moveTo(screenPoints[0].x, screenPoints[0].y);
        for (let i = 1; i < screenPoints.length; i++) fogCtx.lineTo(screenPoints[i].x, screenPoints[i].y);
        fogCtx.closePath();
        fogCtx.fillStyle = 'rgba(0, 0, 0, 0.85)';
        fogCtx.fill();
      } catch (e) {}
    });

    // Этап Б: Дыра в облаках
    fogCtx.filter = `blur(${FOG_CONFIG.revealBlur}px)`;
    // Используем отфильтрованный массив visibleHexagons и здесь
    visibleHexagons.forEach(hexId => {
        try {
            const boundary = h3.cellToBoundary(hexId);
            const screenPoints = boundary.map(([lat, lng]) => map.project([lng, lat]));
            const center = h3.cellToLatLng(hexId);
            const pulse = 1 + Math.sin(animationTime * FOG_CONFIG.animationSpeed + center[0] + center[1]) * FOG_CONFIG.pulseAmplitude;

            fogCtx.save();
            const centerPixels = map.project([center[1], center[0]]);
            fogCtx.translate(centerPixels.x, centerPixels.y);
            fogCtx.scale(pulse, pulse);
            fogCtx.translate(-centerPixels.x, -centerPixels.y);

            fogCtx.beginPath();
            fogCtx.moveTo(screenPoints[0].x, screenPoints[0].y);
            for (let i = 1; i < screenPoints.length; i++) fogCtx.lineTo(screenPoints[i].x, screenPoints[i].y);
            fogCtx.closePath();
            fogCtx.fillStyle = 'white';
            fogCtx.fill();
            fogCtx.restore();
        } catch (e) {}
    });

    fogCtx.filter = 'none';
    fogCtx.globalCompositeOperation = 'source-over';
  }

  // --- Остальной код ---
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

    map.on('render', drawFog);
  });

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
    openBtn.textContent = 'Открыть 50 м вокруг';
  });

  geolocate.on('error', () => {
    if (noAuthMode) {
      openBtn.textContent = 'Кликните на карту для добавления точки';
      openBtn.disabled = false;
    } else {
      openBtn.textContent = 'Геолокация не удалась';
    }
  });

  openBtn.addEventListener('click', async () => {
    if (!lastKnownPosition) {
      if (noAuthMode) { alert('Используйте клик на карту для добавления точек.'); } 
      else { alert('Местоположение не определено.'); }
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
    toggleFogBtn.textContent = fogEnabled ? 'Скрыть туман' : 'Показать туман';
    map.triggerRepaint();
  });

  // UI для отладки
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
      const radiusValueNum = parseInt(radiusSlider.value, 10);
      let h3Resolution;
      if (radiusValueNum <= 30) h3Resolution = 13;
      else if (radiusValueNum <= 70) h3Resolution = 12;
      else if (radiusValueNum <= 150) h3Resolution = 11;
      else h3Resolution = 10;
      try {
        const response = await fetch('/api/v1/radius', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Telegram-Init': tg ? tg.initData : '' },
          body: JSON.stringify({ radius_m: radiusValueNum })
        });
        if (!response.ok) throw new Error('radius update failed');
        const result = await response.json();
        window.currentH3Resolution = h3Resolution;
        if (result.resolution_changed) {
          allKnownHexagons.clear();
          console.log('H3 resolution changed, cleared local hexagon cache');
        }
        updateHexagonsFromServer();
      } catch (e) {
        console.warn('[debug] radius update error', e);
      }
    });
  }

  if (clearDbBtn) {
    clearDbBtn.addEventListener('click', async () => {
      if (!confirm('Очистить всю БД? Это действие необратимо.')) return;
      try {
        const res = await fetch('/api/v1/dev/clear-db', { method: 'POST' });
        if (!res.ok) throw new Error('clear-db failed');
        const data = await res.json().catch(() => ({}));
        allKnownHexagons.clear();
        countEl.textContent = '0';
        map.triggerRepaint();
        alert(`БД очищена. circles=${data.cleared_circles ?? '?'}, users=${data.cleared_users ?? '?'}`);
      } catch (e) {
        alert('Ошибка очистки БД');
        console.warn('[dev] clear-db error', e);
      }
    });
  }

  map.on('click', async (e) => {
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