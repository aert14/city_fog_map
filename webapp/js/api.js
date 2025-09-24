/**
 * @module API
 * @description Handles all communication with the backend API.
 */

import { API_ENDPOINTS } from './config.js';

/**
 * A wrapper around the global Telegram WebApp object.
 * Returns an empty object if not running inside Telegram.
 */
const tg = window.Telegram?.WebApp || {};

/**
 * Fetches the debug status of the backend.
 * @returns {Promise<{noAuthMode: boolean, debugAuthMode: boolean}>}
 */
export async function getDebugSettings() {
    try {
        const response = await fetch(API_ENDPOINTS.debugMode);
        if (!response.ok) return { noAuthMode: false, debugAuthMode: false };
        const data = await response.json();
        return {
            noAuthMode: !!data.no_auth_mode,
            debugAuthMode: !!data.debug_auth_mode
        };
    } catch (error) {
        console.warn('[API] Failed to check debug mode:', error);
        return { noAuthMode: false, debugAuthMode: false };
    }
}

/**
 * Fetches explored hexagons from the server within a given map bounding box.
 * @param {maplibregl.Map} map - The MapLibre map instance.
 * @returns {Promise<string[]>} A promise that resolves to an array of H3 hexagon IDs.
 */
export async function fetchHexagons(map) {
    try {
        const bounds = map.getBounds();
        const bbox = [bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth()].join(',');
        const response = await fetch(`${API_ENDPOINTS.circles}?bbox=${bbox}`, {
            headers: { 'X-Telegram-Init': tg.initData || '' }
        });
        if (!response.ok) {
            throw new Error(`Network error: ${response.statusText}`);
        }
        const data = await response.json();
        return data.hexagons || [];
    } catch (error) {
        console.error('[API] Failed to fetch hexagons:', error);
        return [];
    }
}

/**
 * Sends a "visit" request to the server for a given location.
 * @param {number} lat - The latitude of the visit.
 * @param {number} lon - The longitude of the visit.
 * @returns {Promise<object|null>} A promise that resolves to the server's response or null on failure.
 */
export async function recordVisit(lat, lon) {
    try {
        const response = await fetch(API_ENDPOINTS.visit, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Telegram-Init': tg.initData || '' },
            body: JSON.stringify({ lat, lon })
        });
        if (!response.ok) {
            throw new Error(`Server error: ${response.statusText}`);
        }
        return await response.json();
    } catch (error) {
        console.error('[API] Failed to record visit:', error);
        return null;
    }
}

/**
 * Updates the user's exploration radius on the server.
 * @param {number} radius - The new radius in meters.
 * @returns {Promise<object|null>} A promise that resolves to the server's response or null on failure.
 */
export async function setRadius(radius) {
    try {
        const response = await fetch(API_ENDPOINTS.setRadius, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Telegram-Init': tg.initData || '' },
            body: JSON.stringify({ radius_m: radius })
        });
        if (!response.ok) {
            throw new Error(`Server error: ${response.statusText}`);
        }
        return await response.json();
    } catch (error) {
        console.warn('[API] Radius update error:', error);
        return null;
    }
}

/**
 * Deletes a specific explored hexagon from the server.
 * @param {string} geokey - The H3 ID of the hexagon to delete.
 * @returns {Promise<object|null>} A promise that resolves to the server's response or null on failure.
 */
export async function deleteCircle(geokey) {
    try {
        const response = await fetch(API_ENDPOINTS.deleteCircle, {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json', 'X-Telegram-Init': tg.initData || '' },
            body: JSON.stringify({ geokey })
        });
        if (!response.ok) {
            const errText = await response.text();
            throw new Error(`Delete failed with status ${response.status}: ${errText}`);
        }
        return await response.json();
    } catch (error) {
        console.warn('[API] Delete circle error:', error);
        return null;
    }
}

/**
 * Sends a request to clear the entire database (dev only).
 * @returns {Promise<object|null>} A promise that resolves to the server's response or null on failure.
 */
export async function clearDatabase() {
    try {
        const response = await fetch(API_ENDPOINTS.clearDb, { method: 'POST' });
        if (!response.ok) {
            throw new Error('clear-db request failed');
        }
        return await response.json().catch(() => ({}));
    } catch (error) {
        console.warn('[API] Clear database error:', error);
        alert('Error clearing database');
        return null;
    }
}
