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

  const map = new maplibregl.Map({
    container: 'map',
    style: 'https://demotiles.maplibre.org/style.json',
    center: [37.6173, 55.7558], // Moscow fallback
    zoom: 12
  });

  map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }));

  const circles = { type: 'FeatureCollection', features: [] };

  function toFeature(lon, lat, radius) {
    return {
      type: 'Feature',
      properties: { radius_m: radius },
      geometry: { type: 'Polygon', coordinates: [circleCoordinates(lon, lat, radius, 64)] }
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
  }

  map.on('load', () => {
    map.addSource('circles', { type: 'geojson', data: circles });
    map.addLayer({ id: 'circles-fill', type: 'fill', source: 'circles', paint: { 'fill-color': '#4f46e5', 'fill-opacity': 0.25 } });
    map.addLayer({ id: 'circles-outline', type: 'line', source: 'circles', paint: { 'line-color': '#4f46e5', 'line-width': 2 } });

    tryLocateCenter();
    fetchVisible();
  });

  function setStatus(msg) { statusEl.textContent = msg || ''; }

  function tryLocateCenter() {
    if (!navigator.geolocation) return;
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const { latitude, longitude } = pos.coords;
        map.flyTo({ center: [longitude, latitude], zoom: 15 });
      },
      () => {},
      { enableHighAccuracy: true, maximumAge: 2000, timeout: 8000 }
    );
  }

  async function fetchVisible() {
    const b = map.getBounds();
    const bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].join(',');
    try {
      const res = await fetch(`/api/v1/circles?bbox=${encodeURIComponent(bbox)}`, { headers: getHeaders() });
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
      setStatus('Отправляем на сервер…');
      try {
        const res = await fetch('/api/v1/visit', {
          method: 'POST',
          headers: getHeaders(),
          body: JSON.stringify({ lat: latitude, lon: longitude })
        });
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
    }, { enableHighAccuracy: true, maximumAge: 2000, timeout: 10000 });
  });
})();


