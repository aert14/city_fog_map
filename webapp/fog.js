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

               const base_scale = 0.008;    // Large scale for masses and gaps
               const detail_scale = 0.075;  // More detail for volume
               const height_scale = 0.02;   // Scale for height and depth

               const v_base = sample(i, j, base_scale, 6);  // More octaves for density
               const v_detail = sample(i, j, detail_scale, 10); // Even more octaves for detail
               const v_height = sample(i, j, height_scale, 8); // More octaves for depth

               const idx = (j * width + i) * 4;

               // --- Final Formula ---
               const base_alpha = 0.995; // Near solid fog

               // Denser clouds - fewer gaps and more depth
               let density = smoothstep(0.12, 0.88, v_base);
               density = Math.pow(density, 0.75); // Push towards 1 for overall density
               const height_factor = smoothstep(0.08, 0.92, v_height); // Height affects density
               const final_density = Math.min(1.0, density + height_factor * 0.45); // More height influence for depth
               const final_alpha = base_alpha + (1 - base_alpha) * final_density;

               // Create a sense of depth through several color gradients (very white, voluminous)
               const shadow_color = [220, 225, 230];   // Slightly darker shadows for depth
               const mid_color = [250, 251, 252];      // Almost pure white midtones
               const peak_color = [255, 255, 255];     // Pure white peaks

               // Increase volume - more influence from detail and height
               const volume_factor = v_detail * 0.55 + height_factor * 0.45;
               let color_intensity = smoothstep(0.05, 0.95, volume_factor);
               // Increase local contrast to remove blandness
               color_intensity = Math.min(1, Math.max(0, 0.5 + (color_intensity - 0.5) * 1.35));

               // Minimal atmospheric effect to preserve whiteness
               const atmosphere_factor = smoothstep(0.4, 0.8, height_factor);
               const atmosphere_darkening = 1 - atmosphere_factor * 0.03;

               // Interpolation between colors to create depth
               let r, g, b;
               if (color_intensity < 0.5) {
                   // From shadows to midtones
                   const t = color_intensity * 2;
                   r = lerp(t, shadow_color[0], mid_color[0]);
                   g = lerp(t, shadow_color[1], mid_color[1]);
                   b = lerp(t, shadow_color[2], mid_color[2]);
               } else {
                   // From midtones to peaks
                   const t = (color_intensity - 0.5) * 2;
                   r = lerp(t, mid_color[0], peak_color[0]);
                   g = lerp(t, mid_color[1], peak_color[1]);
                   b = lerp(t, mid_color[2], peak_color[2]);
               }

               // Simple lighting based on height and detail: enhance light and shadow
               const lightFactor = smoothstep(0.3, 0.8, v_height * 0.6 + v_detail * 0.4);
               const highlight = lightFactor * 0.18;
               const shadow = (1 - lightFactor) * 0.18;
               r = r * (1 - shadow) + 255 * highlight;
               g = g * (1 - shadow) + 255 * highlight;
               b = b * (1 - shadow) + 255 * highlight;

               // Apply atmospheric effect
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
const __boundaryCache = new Map(); // hexId -> [[lat,lng],...]
const __centerCache = new Map();   // hexId -> [lat,lng]
const __bboxCache = new Map();     // hexId -> {minLat,maxLat,minLng,maxLng}
let __lastRenderMs = 0;            // throttle draw to ~30 FPS

function drawFog(fogCtx, map, fogEnabled, allKnownHexagons, animationTime, FOG_CONFIG, DPR, cloudPattern) {
    const width = fogCtx.canvas.clientWidth || fogCtx.canvas.width / DPR;
    const height = fogCtx.canvas.clientHeight || fogCtx.canvas.height / DPR;

    // Throttle to ~30 FPS only when map is idle to keep sync during movement
    const now = performance.now();
    const isMoving = (typeof map.isMoving === 'function' && map.isMoving()) ||
                     (typeof map.isZooming === 'function' && map.isZooming()) ||
                     (typeof map.isRotating === 'function' && map.isRotating());
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

    if (allKnownHexagons.size === 0) return;

    // --- Optimized hexagon filtering ---
    const bounds = map.getBounds();
    const sw = bounds.getSouthWest();
    const ne = bounds.getNorthEast();
    const h3Res = window.currentH3Resolution || 8;

    // Add some padding to the bounding box to reveal hexagons at the edges
    const paddingLat = (ne.lat - sw.lat) * 0.15;
    const paddingLng = (ne.lng - sw.lng) * 0.15;

    const visibleHexagons = [];
    const crossesDateline = sw.lng > ne.lng;

    if (crossesDateline) {
        // Handle dateline crossing by creating two polygons
        const poly1 = [
            [ne.lat + paddingLat, sw.lng - paddingLng],
            [ne.lat + paddingLat, 180],
            [sw.lat - paddingLat, 180],
            [sw.lat - paddingLat, sw.lng - paddingLng],
        ];
        const poly2 = [
            [ne.lat + paddingLat, -180],
            [ne.lat + paddingLat, ne.lng + paddingLng],
            [sw.lat - paddingLat, ne.lng + paddingLng],
            [sw.lat - paddingLat, -180],
        ];

        const potentialHexes = new Set([
            ...h3.polygonToCells(poly1, h3Res),
            ...h3.polygonToCells(poly2, h3Res),
        ]);

        potentialHexes.forEach(hexId => {
            if (allKnownHexagons.has(hexId)) {
                visibleHexagons.push(hexId);
            }
        });
    } else {
        const viewPolygon = [
            [ne.lat + paddingLat, sw.lng - paddingLng], // Top-left
            [ne.lat + paddingLat, ne.lng + paddingLng], // Top-right
            [sw.lat - paddingLat, ne.lng + paddingLng], // Bottom-right
            [sw.lat - paddingLat, sw.lng - paddingLng], // Bottom-left
        ];

        const potentialHexes = h3.polygonToCells(viewPolygon, h3Res);
        potentialHexes.forEach(hexId => {
            if (allKnownHexagons.has(hexId)) {
                visibleHexagons.push(hexId);
            }
        });
    }

    if (visibleHexagons.length === 0) return;

    const heavyLoad = visibleHexagons.length > 180;
    const extremeLoad = visibleHexagons.length > 320;
    const pulseEnabled = !heavyLoad && visibleHexagons.length <= 120;
    const blurScale = heavyLoad ? (extremeLoad ? 0 : 0.55) : 1;
    const shadowBlurPx = blurScale > 0 ? Math.max(8, FOG_CONFIG.shadowBlur * blurScale) : 0;
    const revealBlurPx = blurScale > 0 ? Math.max(6, FOG_CONFIG.revealBlur * blurScale) : 0;

    const frameGeometries = new Map();

    if (heavyLoad) {
      // Simplified rendering: draw soft circles instead of full hex paths
      fogCtx.globalCompositeOperation = 'destination-out';
      fogCtx.filter = shadowBlurPx > 0 ? `blur(${shadowBlurPx}px)` : 'none';
      fogCtx.fillStyle = 'rgba(0, 0, 0, 0.75)';

      for (let i = 0; i < visibleHexagons.length; i++) {
        const hexId = visibleHexagons[i];
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

          fogCtx.beginPath();
          fogCtx.arc(centerPixels.x, centerPixels.y, radius, 0, Math.PI * 2);
          fogCtx.fill();
        } catch (e) {}
      }

      fogCtx.filter = revealBlurPx > 0 ? `blur(${revealBlurPx}px)` : 'none';
      fogCtx.fillStyle = 'white';
      for (let i = 0; i < visibleHexagons.length; i++) {
        const hexId = visibleHexagons[i];
        const geom = frameGeometries.get(hexId);
        if (!geom) continue;
        fogCtx.beginPath();
        fogCtx.arc(geom.centerPixels.x, geom.centerPixels.y, geom.radius * 0.92, 0, Math.PI * 2);
        fogCtx.fill();
      }
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
