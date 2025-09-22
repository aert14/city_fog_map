(function(){
  const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  if (tg) {
    try { tg.ready(); } catch (_) {}
  }

  const initData = tg ? tg.initData : null;

  // Show message if not in Telegram Mini App
  if (!tg) {
    document.getElementById('status').textContent = 'Открой через Telegram Mini App в боте';
    document.getElementById('openBtn').disabled = true;
    return;
  }

  function getHeaders() {
    const tgw = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
    const id = tgw && typeof tgw.initData === 'string' ? tgw.initData : '';
    return { 'X-Telegram-Init': id, 'Content-Type': 'application/json' };
  }

  const statusEl = document.getElementById('status');
  const countEl = document.getElementById('count');
  const openBtn = document.getElementById('openBtn');
  const fogToggle = document.getElementById('fogToggle');
  let fogEnabled = true;

  // CARTO Positron GL (без политических акцентов)
  const primaryStyle = 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json';

  const map = new maplibregl.Map({
    container: 'map',
    style: primaryStyle,
    center: [37.6173, 55.7558], // Moscow fallback
    zoom: 12
  });

  map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }));

  // Геолокация: кнопка + маркер текущего положения
  const geolocate = new maplibregl.GeolocateControl({
    positionOptions: { enableHighAccuracy: true },
    trackUserLocation: true,
    showUserHeading: true
  });
  map.addControl(geolocate);

  const circles = { type: 'FeatureCollection', features: [] };

  function toFeature(lon, lat, radius) {
    return {
      type: 'Feature',
      properties: { radius_m: radius },
      geometry: { type: 'Polygon', coordinates: [circleCoordinates(lon, lat, radius, 64)] }
    };
  }

  function signedArea(ring) {
    // Shoelace formula in lon/lat plane (sufficient for orientation)
    let sum = 0;
    for (let i = 0, n = ring.length - 1; i < n; i++) {
      const [x1, y1] = ring[i];
      const [x2, y2] = ring[i + 1];
      sum += (x1 * y2 - x2 * y1);
    }
    return sum; // >0 → CCW, <0 → CW
  }

  function ensureOrientation(ring, wantCCW) {
    if (!Array.isArray(ring) || ring.length < 4) return ring;
    const closed = (ring[0][0] === ring[ring.length - 1][0] && ring[0][1] === ring[ring.length - 1][1]);
    const r = closed ? ring.slice(0, -1) : ring.slice();
    const isCCW = signedArea(r) > 0;
    const oriented = (wantCCW === isCCW) ? r : r.reverse();
    oriented.push(oriented[0]);
    return oriented;
  }

  function buildFogFromCircles() {
    const outer = [
      [-179.999, -85.0],
      [179.999, -85.0],
      [179.999, 85.0],
      [-179.999, 85.0],
      [-179.999, -85.0]
    ];
  // MapLibre right-hand rule: outer CW, holes CCW
  const outerOriented = ensureOrientation(outer, false); // CW
  const holes = circles.features
    .map(f => (f && f.geometry && f.geometry.type === 'Polygon' && f.geometry.coordinates[0]) ? ensureOrientation(f.geometry.coordinates[0], true) : null) // CCW
    .filter(Boolean);
  try {
    const outerArea = (function(){
      const r = outerOriented.slice(0, -1);
      let s = 0; for (let i=0;i<r.length-1;i++){ const [x1,y1]=r[i], [x2,y2]=r[i+1]; s += x1*y2 - x2*y1; }
      return s;
    })();
    const firstHoleArea = holes[0] ? (function(){ const rr = holes[0].slice(0, -1); let s=0; for (let i=0;i<rr.length-1;i++){ const [x1,y1]=rr[i], [x2,y2]=rr[i+1]; s += x1*y2 - x2*y1; } return s; })() : null;
    console.log('[fog] build holes', { count: holes.length, outerCW: outerArea < 0, firstHoleCCW: firstHoleArea != null ? firstHoleArea > 0 : null });
  } catch(_) {}
    return {
      type: 'FeatureCollection',
      features: [{ type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [outerOriented, ...holes] } }]
    };
  }

  function circleCoordinates(lon, lat, radiusMeters, steps) {
    const coords = [];
    const R = 6371000; // meters
    const latRad = lat * Math.PI / 180;
    const lonRad = lon * Math.PI / 180;
    for (let i = 0; i <= steps; i++) {
      const bearing = 2 * Math.PI * i / steps;
      const angDist = radiusMeters / R;
      const lat2 = Math.asin(Math.sin(latRad) * Math.sin(angDist) + Math.cos(latRad) * Math.cos(angDist) * Math.cos(bearing));
      const lon2 = lonRad + Math.atan2(Math.sin(bearing) * Math.sin(angDist) * Math.cos(latRad), Math.cos(angDist) - Math.sin(latRad) * Math.sin(lat2));
      coords.push([lon2 * 180 / Math.PI, lat2 * 180 / Math.PI]);
    }
    return coords;
  }

  function updateCirclesSource() {
    const src = map.getSource('circles');
    if (src) src.setData(circles);
    const fsrc = map.getSource('fog');
    if (fsrc) {
      const fogData = buildFogFromCircles();
      try {
        const holesCount = (fogData && fogData.features && fogData.features[0] && fogData.features[0].geometry && fogData.features[0].geometry.coordinates) ? (fogData.features[0].geometry.coordinates.length - 1) : 0;
        console.log('[fog] update setData holes', holesCount);
      } catch(_) {}
      fsrc.setData(fogData);
    }
  }

  // Нет отката стиля: используем только primaryStyle

  map.on('load', () => {
    map.addSource('circles', { type: 'geojson', data: circles });

    // Fog of war overlay (world polygon with holes for visited circles)
    map.addSource('fog', { type: 'geojson', data: buildFogFromCircles() });
    // Полупрозрачная дымка без паттерна
    map.addLayer({ id: 'fog-bg', type: 'fill', source: 'fog', paint: { 'fill-color': '#0b1220', 'fill-opacity': 0.65, 'fill-antialias': true }, layout: { 'visibility': fogEnabled ? 'visible' : 'none' } });

    map.addLayer({ id: 'circles-outline', type: 'line', source: 'circles', paint: { 'line-color': '#94a3b8', 'line-width': 1.5 } });

    // Стартуем автоопределение местоположения и ставим маркер
    try { geolocate.trigger(); } catch (_) {}
    tryLocateCenter();
    hideBoundaries();
    fetchVisible();
    updateFogUi();
  });

  // Убираем границы стран/административные линии
  function hideBoundaries() {
    try {
      const style = map.getStyle && map.getStyle();
      if (!style || !style.layers) return;
      for (const layer of style.layers) {
        const id = layer && layer.id ? String(layer.id) : '';
        const srcLayer = layer && layer['source-layer'] ? String(layer['source-layer']) : '';
        const type = layer && layer.type ? String(layer.type) : '';
        const name = (id + ' ' + srcLayer).toLowerCase();
        if (
          name.includes('boundary') ||
          name.includes('admin') ||
          name.includes('country') ||
          name.includes('state') ||
          name.includes('province')
        ) {
          try { map.setLayoutProperty(id, 'visibility', 'none'); } catch (_) {}
          if (type === 'line') {
            try { map.setPaintProperty(id, 'line-opacity', 0); } catch (_) {}
          }
        }
      }
    } catch (_) {}
  }

  // При смене стиля (например, откат на резервный) — повторно скрыть границы
  map.on('styledata', () => { hideBoundaries(); });

  function updateFogUi() {
    if (!fogToggle) return;
    fogToggle.textContent = `Туман: ${fogEnabled ? 'вкл' : 'выкл'}`;
  }

  function applyFogVisibility() {
    try { map.setLayoutProperty('fog-bg', 'visibility', fogEnabled ? 'visible' : 'none'); } catch(_) {}
    updateFogUi();
  }

  if (fogToggle) {
    fogToggle.addEventListener('click', () => { fogEnabled = !fogEnabled; applyFogVisibility(); });
  }

  function setStatus(msg) { statusEl.textContent = msg || ''; }

  function tryLocateCenter() {
    if (!navigator.geolocation) return;
    console.log('[geo] getCurrentPosition: requesting…');
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const { latitude, longitude } = pos.coords;
        console.log('[geo] getCurrentPosition: ok', { latitude, longitude, accuracy: pos.coords.accuracy });
        map.flyTo({ center: [longitude, latitude], zoom: 15 });
      },
      (err) => {
        console.warn('[geo] getCurrentPosition: error', err && { code: err.code, message: err.message });
      },
      { enableHighAccuracy: true, maximumAge: 10000, timeout: 20000 }
    );
  }

  async function fetchVisible() {
    const b = map.getBounds();
    const bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].join(',');
    try {
      const hdr = getHeaders();
      console.log('[net] GET /api/v1/circles', { bbox, initLen: (hdr['X-Telegram-Init'] || '').length });
      const res = await fetch(`/api/v1/circles?bbox=${encodeURIComponent(bbox)}`, { headers: hdr });
      if (res.status === 401) {
        setStatus('Открой через бота (нет initData)');
        return;
      }
      if (!res.ok) throw new Error('Circles error');
      const data = await res.json();
      circles.features = data.circles.map(c => toFeature(c.lon, c.lat, c.radius_m));
      updateCirclesSource();
    } catch (e) {
      console.error(e);
      setStatus('Ошибка загрузки кругов');
    }
  }

  map.on('moveend', fetchVisible);

  openBtn.addEventListener('click', async () => {
    if (!navigator.geolocation) {
      setStatus('Нет доступа к геолокации');
      return;
    }
    openBtn.disabled = true;
    setStatus('Определяем местоположение…');
    navigator.geolocation.getCurrentPosition(async (pos) => {
      const { latitude, longitude } = pos.coords;
      console.log('[geo] visit position', { latitude, longitude, accuracy: pos.coords.accuracy });
      setStatus('Отправляем на сервер…');
      try {
        const hdr = getHeaders();
        console.log('[net] POST /api/v1/visit', { initLen: (hdr['X-Telegram-Init'] || '').length, body: { lat: latitude, lon: longitude } });
        const res = await fetch('/api/v1/visit', {
          method: 'POST',
          headers: hdr,
          body: JSON.stringify({ lat: latitude, lon: longitude })
        });
        console.log('[net] /api/v1/visit response', { status: res.status });
        if (res.status === 401) { setStatus('Открой через бота'); return; }
        if (!res.ok) throw new Error('Visit error');
        const data = await res.json();
        if (data.added === 1) {
          circles.features.push(toFeature(data.circle.lon, data.circle.lat, data.circle.radius_m));
          updateCirclesSource();
        }
        if (data.stats && typeof data.stats.total_circles === 'number') {
          countEl.textContent = String(data.stats.total_circles);
        }
        map.flyTo({ center: [longitude, latitude], zoom: Math.max(map.getZoom(), 15) });
        setStatus('Готово');
      } catch (e) {
        console.error(e);
        setStatus('Ошибка запроса');
      } finally {
        openBtn.disabled = false;
      }
    }, (err) => {
      console.warn(err);
      setStatus('Дай доступ к геолокации в Telegram');
      openBtn.disabled = false;
    }, { enableHighAccuracy: true, maximumAge: 10000, timeout: 20000 });
  });
})();


