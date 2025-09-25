// texture.worker.js - Web Worker for generating cloud textures

self.onmessage = function(e) {
  const { width, height } = e.data;

  // Use OffscreenCanvas if available, fallback to regular canvas
  const useOffscreen = typeof OffscreenCanvas === 'function';
  const canvas = useOffscreen ? new OffscreenCanvas(width, height) : new ImageData(width, height);
  const ctx = useOffscreen ? canvas.getContext('2d') : null;
  const imageData = useOffscreen ? ctx.createImageData(width, height) : canvas;
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

  if (useOffscreen) {
    ctx.putImageData(imageData, 0, 0);
    // Create ImageBitmap from canvas and transfer to main thread
    createImageBitmap(canvas).then(bitmap => {
      self.postMessage({ bitmap }, [bitmap]);
    });
  } else {
    // Fallback for environments without OffscreenCanvas
    self.postMessage({ imageData });
  }
};
