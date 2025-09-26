
// Caches to reduce repeated H3 geometry computations
const __boundaryCache = new Map();
const __centerCache = new Map();
let __lastRenderMs = 0;
let __lastRenderState = null;

// --- UPDATED drawFog function ---
function drawFog(fogCtx, map, fogEnabled, spatialIndex, animationTime, FOG_CONFIG, DPR, cloudPattern, GRID_SIZE, fogDataChanged) {
  const width = fogCtx.canvas.clientWidth || fogCtx.canvas.width / DPR;
  const height = fogCtx.canvas.clientHeight || fogCtx.canvas.height / DPR;

  const now = performance.now();
  const isMoving = map.isMoving() || map.isZooming() || map.isRotating();

  // Improved frame throttling: skip rendering if map hasn't changed significantly
  const center = map.getCenter();
  const zoom = map.getZoom();

  if (
    !isMoving &&
    !fogDataChanged &&
    __lastRenderState &&
    Math.abs(center.lng - __lastRenderState.lng) < 0.00001 &&
    Math.abs(center.lat - __lastRenderState.lat) < 0.00001 &&
    Math.abs(zoom - __lastRenderState.zoom) < 0.01
  ) {
    return; // Nothing has changed significantly, skip this frame
  }

  __lastRenderMs = now;

  fogCtx.clearRect(0, 0, width, height);
  if (!fogEnabled) return;

  fogCtx.globalCompositeOperation = 'source-over';
  const scale = Math.pow(2, zoom - 12);
  const mapOffset = map.project([0, 0]);

  const patternMatrix = new DOMMatrix();
  patternMatrix.a = scale; patternMatrix.d = scale;
  patternMatrix.e = mapOffset.x; patternMatrix.f = mapOffset.y;
  cloudPattern.setTransform(patternMatrix);

  fogCtx.save();
  fogCtx.globalAlpha = 1;
  fogCtx.fillStyle = cloudPattern;
  fogCtx.fillRect(0, 0, width, height);
  fogCtx.fillStyle = 'rgba(250, 252, 255, 0.85)';
  fogCtx.fillRect(0, 0, width, height);
  fogCtx.fillStyle = 'rgba(210, 220, 234, 0.35)';
  fogCtx.fillRect(0, 0, width, height);
  fogCtx.restore();

  if (spatialIndex.size === 0) return;

  // --- NEW: Get visible hexagons from the spatial index ---
  const bounds = map.getBounds();
  const sw = bounds.getSouthWest();
  const ne = bounds.getNorthEast();

  const visibleHexSet = new Set();
  const minGridX = Math.floor(sw.lng / GRID_SIZE);
  const maxGridX = Math.floor(ne.lng / GRID_SIZE);
  const minGridY = Math.floor(sw.lat / GRID_SIZE);
  const maxGridY = Math.floor(ne.lat / GRID_SIZE);

  for (let x = minGridX; x <= maxGridX; x++) {
      for (let y = minGridY; y <= maxGridY; y++) {
          const key = `${x}_${y}`;
          if (spatialIndex.has(key)) {
              spatialIndex.get(key).forEach(hexId => {
                  visibleHexSet.add(hexId);
              });
          }
      }
  }
  const visibleHexagons = Array.from(visibleHexSet);
  // --- END NEW ---

  if (visibleHexagons.length === 0) return;

  // --- OPTIMIZED: Single composite fog rendering ---
  const combinedHexPath = new Path2D();

  // Build single path for all visible hexagons
  for (const hexId of visibleHexagons) {
    try {
      let boundary = __boundaryCache.get(hexId);
      if (!boundary) { boundary = h3.cellToBoundary(hexId); __boundaryCache.set(hexId, boundary); }

      const first = boundary[0];
      const firstProjected = map.project([first[1], first[0]]);
      combinedHexPath.moveTo(firstProjected.x, firstProjected.y);

      for (let j = 1; j < boundary.length; j++) {
        const ll = boundary[j];
        const projected = map.project([ll[1], ll[0]]);
        combinedHexPath.lineTo(projected.x, projected.y);
      }
      combinedHexPath.closePath();
    } catch (e) { /* ignore errors for this hex */ }
  }

  // --- OPTIMIZED: Single composite rendering with holes ---
  // Set destination-out to create "holes" in the fog
  fogCtx.globalCompositeOperation = 'destination-out';

  // Create soft shadow effect for the holes
  fogCtx.filter = 'blur(55px)';
  fogCtx.fillStyle = 'rgba(0, 0, 0, 0.85)';
  fogCtx.fill(combinedHexPath);

  // Create clearer inner reveal
  fogCtx.filter = 'blur(40px)';
  fogCtx.fillStyle = 'white';
  fogCtx.fill(combinedHexPath);

  fogCtx.filter = 'none';
  fogCtx.globalCompositeOperation = 'source-over';

  // Save current render state for next frame's throttling decision
  __lastRenderState = { lng: center.lng, lat: center.lat, zoom: zoom };
}

window.FogModule = { drawFog };