/**
 * @module UI
 * @description Manages all UI elements and user interactions.
 */

import * as API from './api.js';

/**
 * A collection of all DOM elements used by the application.
 * This object is populated by the `initElements` function.
 */
export const elements = {};

/**
 * Selects all DOM elements and stores them in the `elements` object.
 * This must be called after the DOM is fully loaded.
 */
export function initElements() {
    elements.app = document.getElementById('app');
    elements.toolbar = document.getElementById('toolbar');
    elements.openBtn = document.getElementById('openBtn');
    elements.toggleFogBtn = document.getElementById('toggleFogBtn');
    elements.countEl = document.getElementById('count');
    elements.fogCanvas = document.getElementById('fog-canvas');
    elements.mapContainer = document.getElementById('map-container');
    elements.loader = document.getElementById('loader');
    elements.debugPanel = document.getElementById('debugPanel');
    elements.radiusSlider = document.getElementById('radiusSlider');
    elements.radiusValue = document.getElementById('radiusValue');
    elements.deleteModeBtn = document.getElementById('deleteModeBtn');
    elements.clearDbBtn = document.getElementById('clearDbBtn');
}


/**
 * Initializes all event listeners for the UI.
 * @param {object} appState - The central application state object.
 * @param {function} updateHexagons - Function to refresh hexagon data from the server.
 * @param {function} onVisit - Callback function to execute when the "Explore" button is clicked.
 * @param {function} onMapClick - Callback function for handling clicks on the map.
 */
export function initEventListeners(appState, updateHexagons, onVisit, onMapClick) {
    // --- Main Toolbar Buttons ---
    elements.openBtn.addEventListener('click', onVisit);

    elements.toggleFogBtn.addEventListener('click', () => {
        appState.fogEnabled = !appState.fogEnabled;
        elements.toggleFogBtn.textContent = appState.fogEnabled ? 'Hide Fog' : 'Show Fog';
        appState.map.triggerRepaint();
    });

    // --- Debug Panel ---
    if (elements.deleteModeBtn) {
        elements.deleteModeBtn.addEventListener('click', () => {
            appState.deleteMode = !appState.deleteMode;
            elements.deleteModeBtn.textContent = appState.deleteMode ? 'Delete: On' : 'Delete: Off';
            elements.deleteModeBtn.style.background = appState.deleteMode ? '#b91c1c' : '#ef4444';
        });
    }

    if (elements.radiusSlider && elements.radiusValue) {
        elements.radiusValue.textContent = elements.radiusSlider.value;
        elements.radiusSlider.addEventListener('input', async () => {
            elements.radiusValue.textContent = elements.radiusSlider.value;
            const radius = parseInt(elements.radiusSlider.value, 10);
            const result = await API.setRadius(radius);
            if (result) {
                appState.h3Resolution = result.h3_resolution;
                if (result.resolution_changed) {
                    appState.knownHexagons.clear();
                    console.log('H3 resolution changed, clearing local hexagon cache.');
                    updateHexagons();
                }
            }
        });
    }

    if (elements.clearDbBtn) {
        elements.clearDbBtn.addEventListener('click', async () => {
            if (!confirm('Clear the entire database? This action is irreversible.')) return;
            const data = await API.clearDatabase();
            if (data) {
                appState.knownHexagons.clear();
                updateCount(0);
                appState.map.triggerRepaint();
                alert(`DB cleared. circles=${data.cleared_circles ?? '?'}, users=${data.cleared_users ?? '?'}`);
            }
        });
    }

    // --- Map Interactions ---
    appState.map.on('click', (e) => onMapClick(e));
}

/**
 * Updates the "Explored" count in the UI.
 * @param {number} count - The new count to display.
 */
export function updateCount(count) {
    if (elements.countEl) {
        elements.countEl.textContent = count.toLocaleString();
    }
}

/**
 * Shows or hides the loading spinner.
 * @param {boolean} show - True to show the loader, false to hide it.
 */
export function toggleLoader(show) {
    if (elements.loader) {
        elements.loader.style.display = show ? 'flex' : 'none';
    }
}

/**
 * Displays an error message if the app fails to initialize.
 * This typically happens if it's not run inside Telegram.
 */
export function showInitializationError() {
    if (elements.app) {
        elements.app.innerHTML = `
          <div style="display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100%; text-align: center; font-family: sans-serif; color: #ccc;">
            <h2 style="margin: 0; color: #eee;">Oops!</h2>
            <p style="margin: 8px 0 0;">Could not initialize the application.<br>Please make sure you are running this inside Telegram.</p>
          </div>
        `;
    }
}

/**
 * Sets the state and text of the main "Explore" button.
 * @param {boolean} disabled - Whether the button should be disabled.
 * @param {string} text - The text to display on the button.
 */
export function setOpenButtonState(disabled, text) {
    if (elements.openBtn) {
        elements.openBtn.disabled = disabled;
        elements.openBtn.textContent = text;
    }
}
