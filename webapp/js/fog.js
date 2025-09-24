/**
 * @module FogModule
 * @description Handles the "Fog of War" effect on the map canvas.
 *
 * This module is responsible for two main tasks:
 * 1. Procedurally generating a cloudy texture using Perlin noise.
 * 2. Drawing the fog layer on the map canvas on each frame, revealing explored areas.
 *
 * It includes performance optimizations such as frame throttling, viewport culling,
 * and switching between detailed (hexagon) and simplified (circle) rendering
 * based on the number of visible items.
 */

// Caches to reduce repeated H3 geometry computations, which can be expensive.
const boundaryCache = new Map(); // Cache for hexagon boundary coordinates
const centerCache = new Map();   // Cache for hexagon center coordinates
let lastRenderMs = 0;            // Used for throttling the render loop

/**
 * Creates a procedural, tileable cloud texture using Perlin noise.
 *
 * This function generates a complex, natural-looking cloud pattern by combining
 * multiple layers of Perlin noise (a technique called Fractional Brownian Motion, or fBm).
 * The result is a canvas that can be used as a repeating pattern for the fog.
 *
 * @param {number} width - The width of the texture to generate.
 * @param {number} height - The height of the texture to generate.
 * @returns {HTMLCanvasElement | OffscreenCanvas} A canvas element containing the generated texture.
 */
function createCloudTexture(width, height) {
    const useOffscreen = typeof OffscreenCanvas === 'function';
    const canvas = useOffscreen ? new OffscreenCanvas(width, height) : document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext('2d');
    const imageData = ctx.createImageData(width, height);
    const data = imageData.data;

    // --- Perlin Noise Generator ---
    // A seeded random number generator for creating a permutation table.
    let seed = Math.random();
    function random() { const x = Math.sin(seed++) * 10000; return x - Math.floor(x); }
    const p = new Uint8Array(512);
    for (let i = 0; i < 256; i++) p[i] = i;
    for (let i = 255; i > 0; i--) { const j = Math.floor(random() * (i + 1));[p[i], p[j]] = [p[j], p[i]]; }
    for (let i = 0; i < 256; i++) p[i + 256] = p[i];

    const fade = (t) => t * t * t * (t * (t * 6 - 15) + 10);
    const lerp = (t, a, b) => a + t * (b - a);
    const grad = (hash, x, y) => {
        const h = hash & 7;
        const u = h < 4 ? x : y;
        const v = h < 4 ? y : x;
        return ((h & 1) ? -u : u) + ((h & 2) ? -2 * v : 2 * v);
    };
    const noise = (x, y) => {
        const X = Math.floor(x) & 255, Y = Math.floor(y) & 255;
        x -= Math.floor(x); y -= Math.floor(y);
        const u = fade(x), v = fade(y);
        const A = p[X] + Y, B = p[X + 1] + Y;
        return lerp(v, lerp(u, grad(p[A], x, y), grad(p[B], x - 1, y)), lerp(u, grad(p[A + 1], x, y - 1), grad(p[B + 1], x - 1, y - 1)));
    };
    // Fractional Brownian Motion: combines multiple layers (octaves) of noise.
    const fBm = (x, y, octaves) => {
        let total = 0, frequency = 1, amplitude = 1, maxValue = 0;
        for (let i = 0; i < octaves; i++) {
            total += noise(x * frequency, y * frequency) * amplitude;
            maxValue += amplitude; amplitude *= 0.5; frequency *= 2.0;
        }
        return (total / maxValue + 1) / 2;
    };
    const smoothstep = (edge0, edge1, x) => {
        const t = Math.max(0, Math.min(1, (x - edge0) / (edge1 - edge0)));
        return t * t * (3 - 2 * t);
    };
    // Samples the noise function, ensuring the texture is tileable.
    const sample = (x, y, scale, oct) => {
        const nx = x / width, ny = y / height;
        return lerp(smoothstep(0, 1, ny), lerp(smoothstep(0, 1, nx), fBm(x * scale, y * scale, oct), fBm((x - width) * scale, y * scale, oct)), lerp(smoothstep(0, 1, nx), fBm(x * scale, (y - height) * scale, oct), fBm((x - width) * scale, (y - height) * scale, oct)));
    };

    // --- Texture Generation ---
    // Iterate over each pixel to calculate its color and alpha based on noise.
    for (let j = 0; j < height; j++) {
        for (let i = 0; i < width; i++) {
            // Combine multiple noise samples at different scales for a more complex look.
            const v_base = sample(i, j, 0.008, 6);   // Base cloud shapes
            const v_detail = sample(i, j, 0.075, 10); // Fine details
            const v_height = sample(i, j, 0.02, 8);  // Depth simulation

            const idx = (j * width + i) * 4;

            // --- Final Color & Alpha Calculation ---
            // These values are fine-tuned to produce a visually appealing cloud effect.
            const base_alpha = 0.88;
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

            const atmosphere_darkening = 1 - smoothstep(0.4, 0.8, height_factor) * 0.03;

            let r, g, b;
            if (color_intensity < 0.5) {
                const t = color_intensity * 2;
                r = lerp(t, shadow_color[0], mid_color[0]); g = lerp(t, shadow_color[1], mid_color[1]); b = lerp(t, shadow_color[2], mid_color[2]);
            } else {
                const t = (color_intensity - 0.5) * 2;
                r = lerp(t, mid_color[0], peak_color[0]); g = lerp(t, mid_color[1], peak_color[1]); b = lerp(t, mid_color[2], peak_color[2]);
            }

            const lightFactor = smoothstep(0.3, 0.8, v_height * 0.6 + v_detail * 0.4);
            const highlight = lightFactor * 0.18;
            const shadow = (1 - lightFactor) * 0.18;
            r = r * (1 - shadow) + 255 * highlight; g = g * (1 - shadow) + 255 * highlight; b = b * (1 - shadow) + 255 * highlight;

            r *= atmosphere_darkening; g *= atmosphere_darkening; b *= atmosphere_darkening;

            data[idx] = r; data[idx + 1] = g; data[idx + 2] = b;
            data[idx + 3] = Math.floor(final_alpha * 255);
        }
    }
    ctx.putImageData(imageData, 0, 0);
    return canvas;
}

/**
 * Draws the fog layer onto the canvas for the current map view.
 *
 * This function is designed to be called on every map render frame. It first
 * draws the cloud texture over the entire canvas, then "cuts out" the areas
 * corresponding to explored hexagons.
 *
 * @param {CanvasRenderingContext2D} fogCtx - The 2D context of the fog canvas.
 * @param {maplibregl.Map} map - The MapLibre map instance.
 * @param {boolean} fogEnabled - Whether to draw the fog or clear the canvas.
 * @param {Set<string>} allKnownHexagons - A Set containing the H3 IDs of all explored hexagons.
 * @param {number} animationTime - A frame counter used for animations.
 * @param {object} FOG_CONFIG - Configuration object for fog appearance.
 * @param {number} DPR - The device pixel ratio.
 * @param {CanvasPattern} cloudPattern - The pre-rendered cloud pattern.
 */
function drawFog(fogCtx, map, fogEnabled, allKnownHexagons, animationTime, FOG_CONFIG, DPR, cloudPattern) {
    const width = fogCtx.canvas.clientWidth || fogCtx.canvas.width / DPR;
    const height = fogCtx.canvas.clientHeight || fogCtx.canvas.height / DPR;

    // Throttle rendering to ~30 FPS when the map is idle to save CPU.
    const now = performance.now();
    const isMoving = map.isMoving() || map.isZooming() || map.isRotating();
    if (!isMoving && now - lastRenderMs < 33) return;
    lastRenderMs = now;

    fogCtx.clearRect(0, 0, width, height);
    if (!fogEnabled) return;

    // Step 1: Draw the cloud pattern over the entire canvas.
    fogCtx.globalCompositeOperation = 'source-over';
    const zoom = map.getZoom();
    const scale = Math.pow(2, zoom - 12);
    const mapOffset = map.project([0, 0]);
    const patternMatrix = new DOMMatrix().translate(mapOffset.x, mapOffset.y).scale(scale, scale);
    cloudPattern.setTransform(patternMatrix);
    fogCtx.fillStyle = cloudPattern;
    fogCtx.fillRect(0, 0, width, height);

    if (allKnownHexagons.size === 0) return;

    // Step 2: Cull hexagons to only those visible on screen (+ a buffer).
    const bounds = map.getBounds();
    const sw = bounds.getSouthWest();
    const ne = bounds.getNorthEast();
    const paddingLat = (ne.lat - sw.lat) * 0.1;
    const paddingLng = (ne.lng - sw.lng) * 0.1;

    const visibleHexagons = [];
    allKnownHexagons.forEach(hexId => {
        try {
            let center = centerCache.get(hexId);
            if (!center) { center = h3.cellToLatLng(hexId); centerCache.set(hexId, center); }
            if (center[0] > sw.lat - paddingLat && center[0] < ne.lat + paddingLat &&
                center[1] > sw.lng - paddingLng && center[1] < ne.lng + paddingLng) {
                visibleHexagons.push(hexId);
            }
        } catch (e) { /* Ignore invalid hexIDs */ }
    });

    if (visibleHexagons.length === 0) return;

    // Step 3: Adapt rendering strategy based on load.
    const heavyLoad = visibleHexagons.length > 180;
    const extremeLoad = visibleHexagons.length > 320;
    const pulseEnabled = !heavyLoad && visibleHexagons.length <= 120;
    const blurScale = heavyLoad ? (extremeLoad ? 0 : 0.55) : 1;
    const shadowBlurPx = blurScale > 0 ? Math.max(8, FOG_CONFIG.shadowBlur * blurScale) : 0;
    const revealBlurPx = blurScale > 0 ? Math.max(6, FOG_CONFIG.revealBlur * blurScale) : 0;

    // Use a map to store geometries calculated for this frame.
    const frameGeometries = new Map();

    // Step 4: "Cut out" the revealed areas using composite operations.
    if (heavyLoad) {
        // --- Simplified Rendering (for performance) ---
        // Draws soft, blurred circles instead of precise hexagons.
        fogCtx.globalCompositeOperation = 'destination-out';
        fogCtx.filter = shadowBlurPx > 0 ? `blur(${shadowBlurPx}px)` : 'none';
        fogCtx.fillStyle = 'rgba(0, 0, 0, 0.75)';

        for (const hexId of visibleHexagons) {
            try {
                let boundary = boundaryCache.get(hexId);
                if (!boundary) { boundary = h3.cellToBoundary(hexId); boundaryCache.set(hexId, boundary); }
                let center = centerCache.get(hexId);
                if (!center) { center = h3.cellToLatLng(hexId); centerCache.set(hexId, center); }

                const centerPixels = map.project([center[1], center[0]]);
                const sample = boundary[0] || center;
                const samplePixels = map.project([sample[1], sample[0]]);
                const radius = Math.max(4, Math.hypot(samplePixels.x - centerPixels.x, samplePixels.y - centerPixels.y) * 1.1);

                frameGeometries.set(hexId, { center, centerPixels, radius });

                fogCtx.beginPath();
                fogCtx.arc(centerPixels.x, centerPixels.y, radius, 0, Math.PI * 2);
                fogCtx.fill();
            } catch (e) { /* Ignore invalid hexIDs */ }
        }

        // Draw the inner, sharper reveal area.
        fogCtx.filter = revealBlurPx > 0 ? `blur(${revealBlurPx}px)` : 'none';
        fogCtx.fillStyle = 'white';
        for (const hexId of visibleHexagons) {
            const geom = frameGeometries.get(hexId);
            if (!geom) continue;
            fogCtx.beginPath();
            fogCtx.arc(geom.centerPixels.x, geom.centerPixels.y, geom.radius * 0.92, 0, Math.PI * 2);
            fogCtx.fill();
        }

    } else {
        // --- Detailed Rendering ---
        // Draws precise hexagons with a pulsing animation.
        fogCtx.globalCompositeOperation = 'destination-out';
        fogCtx.filter = `blur(${shadowBlurPx}px)`;

        const shadowPath = new Path2D();
        for (const hexId of visibleHexagons) {
            try {
                let boundary = boundaryCache.get(hexId);
                if (!boundary) { boundary = h3.cellToBoundary(hexId); boundaryCache.set(hexId, boundary); }
                let center = centerCache.get(hexId);
                if (!center) { center = h3.cellToLatLng(hexId); centerCache.set(hexId, center); }

                const path = new Path2D();
                const firstProjected = map.project([boundary[0][1], boundary[0][0]]);
                path.moveTo(firstProjected.x, firstProjected.y);
                for (let j = 1; j < boundary.length; j++) {
                    const projected = map.project([boundary[j][1], boundary[j][0]]);
                    path.lineTo(projected.x, projected.y);
                }
                path.closePath();
                shadowPath.addPath(path);

                frameGeometries.set(hexId, { path, center, centerPixels: map.project([center[1], center[0]]) });
            } catch (e) { /* Ignore invalid hexIDs */ }
        }
        fogCtx.fillStyle = 'rgba(0, 0, 0, 0.85)';
        fogCtx.fill(shadowPath);

        // Draw the inner, sharper reveal area.
        fogCtx.filter = `blur(${revealBlurPx}px)`;
        if (pulseEnabled) {
            // Animate each hexagon with a subtle pulse.
            for (const hexId of visibleHexagons) {
                const geom = frameGeometries.get(hexId);
                if (!geom) continue;
                const pulse = 1 + Math.sin(animationTime * FOG_CONFIG.animationSpeed + geom.center[0] + geom.center[1]) * FOG_CONFIG.pulseAmplitude;
                fogCtx.save();
                fogCtx.translate(geom.centerPixels.x, geom.centerPixels.y);
                fogCtx.scale(pulse, pulse);
                fogCtx.translate(-geom.centerPixels.x, -geom.centerPixels.y);
                fogCtx.fillStyle = 'white';
                fogCtx.fill(geom.path);
                fogCtx.restore();
            }
        } else {
            // Draw all hexagons statically if pulsing is disabled.
            const revealPath = new Path2D();
            for (const hexId of visibleHexagons) {
                const geom = frameGeometries.get(hexId);
                if (geom) revealPath.addPath(geom.path);
            }
            fogCtx.fillStyle = 'white';
            fogCtx.fill(revealPath);
        }
    }

    // Reset canvas state for the next frame.
    fogCtx.filter = 'none';
    fogCtx.globalCompositeOperation = 'source-over';
}

export { createCloudTexture, drawFog };
