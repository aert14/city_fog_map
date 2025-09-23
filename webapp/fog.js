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
               const base_alpha = 0.88; // Almost solid clouds like in Civ5

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

              data[idx] = r; data[idx+1] = g; data[idx+2] = b;
              data[idx+3] = Math.floor(final_alpha * 255);
      }
    }
    ctx.putImageData(imageData, 0, 0);
    return canvas;
  }

function drawFog(fogCtx, map, fogEnabled, allKnownHexagons, animationTime, FOG_CONFIG, DPR, cloudPattern) {
    const width = fogCtx.canvas.clientWidth || fogCtx.canvas.width / DPR;
    const height = fogCtx.canvas.clientHeight || fogCtx.canvas.height / DPR;

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

    fogCtx.fillStyle = cloudPattern;
    fogCtx.fillRect(0, 0, width, height);

    if (allKnownHexagons.size === 0) return;

    // --- OPTIMIZATION: Filter hexagons to only those visible on screen ---
    const bounds = map.getBounds();
    // Add a small padding so hexagons at the edge don't disappear abruptly
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
    // --- END OPTIMIZATION ---

    // Step A: Shadow under the clouds
    fogCtx.globalCompositeOperation = 'destination-out';
    fogCtx.filter = `blur(${FOG_CONFIG.shadowBlur}px)`;
    // Use the filtered visibleHexagons array instead of allKnownHexagons
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

    // Step B: Hole in the clouds
    fogCtx.filter = `blur(${FOG_CONFIG.revealBlur}px)`;
    // Use the filtered visibleHexagons array here as well
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

window.FogModule = { createCloudTexture, drawFog };
