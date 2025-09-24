/**
 * Main application entry point.
 *
 * This script initializes the application by setting up the map, UI,
 * API interactions, and state management. It follows a modular pattern
 * to keep the code organized and maintainable.
 */

import * as UI from './ui.js';
import * as API from './api.js';
import * as Map from './map.js';
import { createCloudTexture, drawFog } from './fog.js';
import { FOG_CONFIG, MAP_CONFIG } from './config.js';

// Main application execution block.
(async function main() {
    // --- 1. Initialization and Authentication Check ---
    const tg = window.Telegram?.WebApp || {};
    if (tg.ready) tg.ready();

    const { noAuthMode, debugAuthMode } = await API.getDebugSettings();

    // If not in a debug mode and Telegram initData is missing, halt execution.
    if (!tg.initData && !noAuthMode) {
        UI.showInitializationError();
        return;
    }

    // --- 2. State Management ---
    // Central object to hold the application's state.
    const AppState = {
        knownHexagons: new Set(),
        isFetching: false,
        fogEnabled: true,
        animationTime: 0,
        h3Resolution: MAP_CONFIG.defaultH3Resolution,
        currentRadius: 50, // Default radius
        ignoreNextClick: false,
        lastKnownPosition: null,
        deleteMode: false,
        noAuthMode: noAuthMode,
        debugAuthMode: debugAuthMode,
        map: null, // Will be initialized by the Map module
    };

    // --- 3. Map and Fog Initialization ---
    AppState.map = Map.initMap();
    const fogCtx = UI.elements.fogCanvas.getContext('2d');
    const cloudTexture = createCloudTexture(512, 512);
    const cloudPattern = fogCtx.createPattern(cloudTexture, 'repeat');
    const drawFogLoop = () => drawFog(fogCtx, AppState.map, AppState.fogEnabled, AppState.knownHexagons, AppState.animationTime++, FOG_CONFIG, window.devicePixelRatio || 1, cloudPattern);

    // --- 4. Core Logic Functions ---

    /**
     * Fetches hexagon data from the server and updates the map.
     */
    async function updateHexagons() {
        if (AppState.isFetching) return;
        AppState.isFetching = true;
        const loaderTimeout = setTimeout(() => UI.toggleLoader(true), 500);

        const newHexs = await API.fetchHexagons(AppState.map);
        let addedCount = 0;
        newHexs.forEach(hexId => {
            if (!AppState.knownHexagons.has(hexId)) {
                AppState.knownHexagons.add(hexId);
                addedCount++;
            }
        });

        if (addedCount > 0) {
            UI.updateCount(AppState.knownHexagons.size);
        }
        AppState.map.triggerRepaint();

        clearTimeout(loaderTimeout);
        UI.toggleLoader(false);
        AppState.isFetching = false;
    }

    /**
     * Handles the "Explore" button click event.
     */
    async function handleVisit() {
        if (!AppState.lastKnownPosition) {
            if (noAuthMode) alert('Click on the map to add points.');
            else alert('Location not determined.');
            return;
        }
        UI.setOpenButtonState(true, 'Exploring...');
        const result = await API.recordVisit(AppState.lastKnownPosition.latitude, AppState.lastKnownPosition.longitude);
        if (result?.added > 0) {
            const hexId = h3.latLngToCell(AppState.lastKnownPosition.latitude, AppState.lastKnownPosition.longitude, AppState.h3Resolution);
            AppState.knownHexagons.add(hexId);
            UI.updateCount(AppState.knownHexagons.size);
            AppState.map.triggerRepaint();
        }
        UI.setOpenButtonState(false, `Explore ${AppState.currentRadius}m Around`);
    }

    /**
     * Handles click events on the map for manual visits or deleting hexagons.
     */
    async function handleMapClick(e) {
        if (AppState.ignoreNextClick) return;

        const lngLat = AppState.map.unproject(e.point);

        if (AppState.deleteMode) {
            // --- Delete Mode Logic ---
            const targetHexId = h3.latLngToCell(lngLat.lat, lngLat.lng, AppState.h3Resolution);
            if (!AppState.knownHexagons.has(targetHexId)) return;

            const result = await API.deleteCircle(targetHexId);
            if (result?.deleted > 0) {
                AppState.knownHexagons.delete(targetHexId);
                UI.updateCount(AppState.knownHexagons.size);
                AppState.map.triggerRepaint();
                console.log("Deleted hexagon:", targetHexId);
            }
        } else if (noAuthMode && !AppState.lastKnownPosition) {
            // --- Manual Visit Logic (in no-auth mode) ---
            const result = await API.recordVisit(lngLat.lat, lngLat.lng);
            if (result?.added > 0) {
                const hexId = h3.latLngToCell(lngLat.lat, lngLat.lng, AppState.h3Resolution);
                AppState.knownHexagons.add(hexId);
                UI.updateCount(AppState.knownHexagons.size);
                AppState.map.triggerRepaint();
            }
        }
    }

    // --- 5. Event Listener Initialization ---
    Map.initMapEventListeners(AppState, updateHexagons, drawFogLoop);
    UI.initEventListeners(AppState, updateHexagons, handleVisit, handleMapClick);

    // Show debug panel if in a debug mode.
    if (noAuthMode || debugAuthMode) {
        UI.elements.debugPanel.style.display = 'flex';
        UI.elements.toggleFogBtn.style.display = 'inline-block';
    }

})();
