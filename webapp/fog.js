
// Caches to reduce repeated H3 geometry computations
const __boundaryCache = new Map();
const __centerCache = new Map();
let __lastRenderMs = 0;
let __lastRenderState = null;

// --- UPDATED drawFog function ---
function drawFog(fogCtx, map, fogEnabled, spatialIndex, animationTime, FOG_CONFIG, DPR, cloudPattern, GRID_SIZE) {
  const width = fogCtx.canvas.clientWidth || fogCtx.canvas.width / DPR;
  const height = fogCtx.canvas.clientHeight || fogCtx.canvas.height / DPR;

  const now = performance.now();
  const isMoving = map.isMoving() || map.isZooming() || map.isRotating();

  // Improved frame throttling: skip rendering if map hasn't changed significantly
  const center = map.getCenter();
  const zoom = map.getZoom();

  if (
    !isMoving &&
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

  // --- UPDATED: Adjusted performance thresholds ---
  const heavyLoad = visibleHexagons.length > 120;
  const extremeLoad = visibleHexagons.length > 250;
  const pulseEnabled = !heavyLoad && visibleHexagons.length <= 100;
  const blurScale = heavyLoad ? (extremeLoad ? 0 : 0.55) : 1;
  const shadowBlurPx = blurScale > 0 ? Math.max(8, FOG_CONFIG.shadowBlur * blurScale) : 0;
  const revealBlurPx = blurScale > 0 ? Math.max(6, FOG_CONFIG.revealBlur * blurScale) : 0;

  const frameGeometries = new Map();

  if (heavyLoad) {
    // EVEN MORE SIMPLIFIED rendering: batch-draw soft circles
    fogCtx.globalCompositeOperation = 'destination-out';
    
    // Step 1: Collect all circle geometries needed for this frame
    for (const hexId of visibleHexagons) {
      try {
        let boundary = __boundaryCache.get(hexId);
        if (!boundary) { boundary = h3.cellToBoundary(hexId); __boundaryCache.set(hexId, boundary); }
        let center = __centerCache.get(hexId);
        if (!center) { center = h3.cellToLatLng(hexId); __centerCache.set(hexId, center); }

        const centerPixels = map.project([center[1], center[0]]);
        const sample = boundary[0] || center;
        const samplePixels = map.project([sample[1], sample[0]]);
        const radius = Math.max(4, Math.hypot(samplePixels.x - centerPixels.x, samplePixels.y - centerPixels.y) * 1.65);

        frameGeometries.set(hexId, { center, centerPixels, radius });
      } catch (e) { /* ignore errors for this hex */ }
    }

    // Step 2: Batch-draw all shadows in a single operation
    fogCtx.filter = shadowBlurPx > 0 ? `blur(${shadowBlurPx}px)` : 'none';
    fogCtx.fillStyle = 'rgba(0, 0, 0, 0.75)';
    const shadowPath = new Path2D();
    for (const geom of frameGeometries.values()) {
        shadowPath.moveTo(geom.centerPixels.x + geom.radius, geom.centerPixels.y);
        shadowPath.arc(geom.centerPixels.x, geom.centerPixels.y, geom.radius, 0, Math.PI * 2);
    }
    fogCtx.fill(shadowPath);


    // Step 3: Batch-draw all reveals in a single operation
    fogCtx.filter = revealBlurPx > 0 ? `blur(${revealBlurPx}px)` : 'none';
    fogCtx.fillStyle = 'white';
    const revealPath = new Path2D();
    for (const geom of frameGeometries.values()) {
        const revealRadius = geom.radius * 0.92;
        revealPath.moveTo(geom.centerPixels.x + revealRadius, geom.centerPixels.y);
        revealPath.arc(geom.centerPixels.x, geom.centerPixels.y, revealRadius, 0, Math.PI * 2);
    }
    fogCtx.fill(revealPath);

  } else {
    // Detailed rendering with cached paths
    fogCtx.globalCompositeOperation = 'destination-out';
    fogCtx.filter = `blur(${shadowBlurPx}px)`;

    const shadowPath = new Path2D();
    for (let i = 0; i < visibleHexagons.length; i++) {
      const hexId = visibleHexagons[i];
      try {
        let boundary = __boundaryCache.get(hexId);
        if (!boundary) { boundary = h3.cellToBoundary(hexId); __boundaryCache.set(hexId, boundary); }
        let center = __centerCache.get(hexId);
        if (!center) { center = h3.cellToLatLng(hexId); __centerCache.set(hexId, center); }

        const path = new Path2D();
        const first = boundary[0];
        const firstProjected = map.project([first[1], first[0]]);
        path.moveTo(firstProjected.x, firstProjected.y);
        for (let j = 1; j < boundary.length; j++) {
          const ll = boundary[j];
          const projected = map.project([ll[1], ll[0]]);
          path.lineTo(projected.x, projected.y);
        }
        path.closePath();
        shadowPath.addPath(path);

        const centerPixels = map.project([center[1], center[0]]);
        frameGeometries.set(hexId, { path, center, centerPixels });
      } catch (e) {}
    }
    fogCtx.fillStyle = 'rgba(0, 0, 0, 0.85)';
    fogCtx.fill(shadowPath);

    fogCtx.filter = `blur(${revealBlurPx}px)`;
    if (pulseEnabled) {
      for (let i = 0; i < visibleHexagons.length; i++) {
        const hexId = visibleHexagons[i];
        const geom = frameGeometries.get(hexId);
        if (!geom) continue;
        try {
          const pulse = 1 + Math.sin(animationTime * FOG_CONFIG.animationSpeed + geom.center[0] + geom.center[1]) * FOG_CONFIG.pulseAmplitude;

          fogCtx.save();
          const cp = geom.centerPixels;
          fogCtx.translate(cp.x, cp.y);
          fogCtx.scale(pulse, pulse);
          fogCtx.translate(-cp.x, -cp.y);

          fogCtx.fillStyle = 'white';
          fogCtx.fill(geom.path);
          fogCtx.restore();
        } catch (e) {}
      }
    } else {
      const revealPath = new Path2D();
      for (let i = 0; i < visibleHexagons.length; i++) {
        const hexId = visibleHexagons[i];
        const geom = frameGeometries.get(hexId);
        if (!geom) continue;
        revealPath.addPath(geom.path);
      }
      fogCtx.fillStyle = 'white';
      fogCtx.fill(revealPath);
    }
  }

  fogCtx.filter = 'none';
  fogCtx.globalCompositeOperation = 'source-over';

  // Save current render state for next frame's throttling decision
  __lastRenderState = { lng: center.lng, lat: center.lat, zoom: zoom };
}

window.FogModule = { drawFog };