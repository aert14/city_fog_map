/**
 * @module Map
 * @description Manages the MapLibre GL map instance and its interactions.
 */

import { MAP_CONFIG } from './config.js';
import * as UI from './ui.js';

/**
 * Initializes the MapLibre map and its controls.
 * @returns {maplibregl.Map} The initialized map instance.
 */
export function initMap() {
    const map = new maplibregl.Map({
        container: 'map',
        style: MAP_CONFIG.style,
        center: MAP_CONFIG.center,
        zoom: MAP_CONFIG.zoom,
        maxBounds: MAP_CONFIG.maxBounds,
    });

    // Add navigation and geolocation controls.
    map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }));
    const geolocate = new maplibregl.GeolocateControl({
        positionOptions: { enableHighAccuracy: true },
        trackUserLocation: false,
        showUserHeading: true,
        showAccuracyCircle: false,
        fitBoundsOptions: { maxZoom: 22 },
    });
    // A hack to prevent the map from automatically moving when geolocation is updated.
    try { if (geolocate?._updateCamera) { geolocate._updateCamera = () => {}; } } catch (_) {}
    map.addControl(geolocate);

    // Store the geolocate control on the map object for later access.
    map.geolocate = geolocate;

    return map;
}

/**
 * Initializes all map-related event listeners.
 * @param {object} appState - The central application state object.
 * @param {function} updateHexagons - Function to refresh hexagon data from the server.
 * @param {function} drawFogLoop - The main fog drawing function.
 */
export function initMapEventListeners(appState, updateHexagons, drawFogLoop) {
    const { map, geolocate } = appState;

    map.on('load', () => {
        // Move map controls into the map-container to be on top of the fog canvas.
        const mapContainer = UI.elements.mapContainer;
        const controls = mapContainer.querySelector('.maplibregl-control-container');
        if (controls) {
            mapContainer.appendChild(controls);
        }

        // Set up a resize observer to keep the fog canvas the same size as the map.
        const resizeObserver = new ResizeObserver(() => {
            const cssW = mapContainer.clientWidth;
            const cssH = mapContainer.clientHeight;
            const dpr = window.devicePixelRatio || 1;
            UI.elements.fogCanvas.style.width = `${cssW}px`;
            UI.elements.fogCanvas.style.height = `${cssH}px`;
            UI.elements.fogCanvas.width = Math.max(1, Math.floor(cssW * dpr));
            UI.elements.fogCanvas.height = Math.max(1, Math.floor(cssH * dpr));
            UI.elements.fogCanvas.getContext('2d').setTransform(dpr, 0, 0, dpr, 0, 0);
        });
        resizeObserver.observe(mapContainer);

        // Initial data load and geolocation trigger.
        updateHexagons();
        try { geolocate.trigger(); } catch (e) { console.error(e); }

        // Start the fog rendering loop.
        map.on('render', drawFogLoop);
    });

    // --- Geolocation Events ---
    UI.setOpenButtonState(true, 'Locating...');
    geolocate.on('geolocate', (pos) => {
        appState.lastKnownPosition = pos.coords;
        const zoom = Math.max(map.getZoom(), 17); // Zoom in to a reasonable level.
        map.flyTo({ center: [pos.coords.longitude, pos.coords.latitude], zoom });
        UI.setOpenButtonState(false, `Explore ${appState.currentRadius}m Around`);
    });

    geolocate.on('error', () => {
        if (appState.noAuthMode) {
            UI.setOpenButtonState(false, 'Click map to add points');
        } else {
            UI.setOpenButtonState(true, 'Geolocation failed');
        }
    });

    // --- Map Movement Events ---
    // Fetch new hexagons when the user stops moving the map.
    map.on('moveend', updateHexagons);

    // Prevent accidental clicks after dragging/zooming the map (which can create "phantom" circles).
    map.on('movestart', () => { appState.ignoreNextClick = true; });
    map.on('dragend', () => { setTimeout(() => { appState.ignoreNextClick = false; }, 120); });
    map.on('zoomend', () => { setTimeout(() => { appState.ignoreNextClick = false; }, 120); });
}
