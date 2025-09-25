function createCloudTexture(width, height) {
  const useOffscreen = typeof OffscreenCanvas === 'function';
  const canvas = useOffscreen ? new OffscreenCanvas(width, height) : document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext('2d');
  const imageData = ctx.createImageData(width, height);
  const data = imageData.data;

    // Noise generator
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

             const base_scale = 0.008;
             const detail_scale = 0.075;
             const height_scale = 0.02;

             const v_base = sample(i, j, base_scale, 6);
             const v_detail = sample(i, j, detail_scale, 10);
             const v_height = sample(i, j, height_scale, 8);

             const idx = (j * width + i) * 4;

             const base_alpha = 0.995;

             let density = smoothstep(0.12, 0.88, v_base);
             density = Math.pow(density, 0.75);
             const height_factor = smoothstep(0.08, 0.92, v_height);
             const final_density = Math.min(1.0, density + height_factor * 0.45);
             const final_alpha = base_alpha + (1 - base_alpha) * final_density;

             const shadow_color = [220, 225, 230];
             const mid_color = [250, 251, 252];
             const peak_color = [255, 255, 255];

             const volume_factor = v_detail * 0.55 + height_factor * 0.45;
             let color_intensity = smoothstep(0.05, 0.95, volume_factor);
             color_intensity = Math.min(1, Math.max(0, 0.5 + (color_intensity - 0.5) * 1.35));

             const atmosphere_factor = smoothstep(0.4, 0.8, height_factor);
             const atmosphere_darkening = 1 - atmosphere_factor * 0.03;

             let r, g, b;
             if (color_intensity < 0.5) {
                 const t = color_intensity * 2;
                 r = lerp(t, shadow_color[0], mid_color[0]);
                 g = lerp(t, shadow_color[1], mid_color[1]);
                 b = lerp(t, shadow_color[2], mid_color[2]);
             } else {
                 const t = (color_intensity - 0.5) * 2;
                 r = lerp(t, mid_color[0], peak_color[0]);
                 g = lerp(t, mid_color[1], peak_color[1]);
                 b = lerp(t, mid_color[2], peak_color[2]);
             }

             const lightFactor = smoothstep(0.3, 0.8, v_height * 0.6 + v_detail * 0.4);
             const highlight = lightFactor * 0.18;
             const shadow = (1 - lightFactor) * 0.18;
             r = r * (1 - shadow) + 255 * highlight;
             g = g * (1 - shadow) + 255 * highlight;
             b = b * (1 - shadow) + 255 * highlight;

             r *= atmosphere_darkening;
             g *= atmosphere_darkening;
             b *= atmosphere_darkening;

            const whitenessBoost = 0.7;
            r = r + (255 - r) * whitenessBoost;
            g = g + (255 - g) * whitenessBoost;
            b = b + (255 - b) * whitenessBoost;

            data[idx] = r; data[idx+1] = g; data[idx+2] = b;
            data[idx+3] = Math.floor(final_alpha * 255);
    }
  }
  ctx.putImageData(imageData, 0, 0);
  return canvas;
}

// Caches to reduce repeated H3 geometry computations
const __boundaryCache = new Map();
const __centerCache = new Map();
let __lastRenderMs = 0;

// --- UPDATED drawFog function ---
function drawFog(fogCtx, map, fogEnabled, spatialIndex, animationTime, FOG_CONFIG, DPR, cloudPattern, GRID_SIZE) {
  const width = fogCtx.canvas.clientWidth || fogCtx.canvas.width / DPR;
  const height = fogCtx.canvas.clientHeight || fogCtx.canvas.height / DPR;

  const now = performance.now();
  const isMoving = map.isMoving() || map.isZooming() || map.isRotating();
  if (!isMoving && now - __lastRenderMs < 33) return;
  __lastRenderMs = now;

  fogCtx.clearRect(0, 0, width, height);
  if (!fogEnabled) return;

  fogCtx.globalCompositeOperation = 'source-over';
  const zoom = map.getZoom();
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
}

window.FogModule = { createCloudTexture, drawFog };