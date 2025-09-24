/**
 * @module Config
 * @description Centralized configuration for the frontend application.
 */

// Configuration for the fog visual effect.
export const FOG_CONFIG = {
    shadowBlur: 55,     // Blur radius for the dark edge of the fog.
    revealBlur: 40,     // Blur radius for the revealed area.
    pulseAmplitude: 0.04, // How much the revealed areas "breathe".
    animationSpeed: 0.0015, // Speed of the pulsing animation.
};

// Configuration for the map.
export const MAP_CONFIG = {
    // A free, privacy-friendly map style.
    style: 'https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json',
    // Initial center point (Moscow).
    center: [37.6173, 55.7558],
    zoom: 12,
    // Restrict map panning to a reasonable area around Moscow.
    maxBounds: [[36.0, 55.0], [39.0, 56.5]],
    // Default H3 resolution to use for client-side calculations if not provided by the server.
    defaultH3Resolution: 11,
};

// API endpoint paths.
export const API_ENDPOINTS = {
    debugMode: '/api/v1/debug-mode',
    circles: '/api/v1/circles',
    visit: '/api/v1/visit',
    setRadius: '/api/v1/radius',
    deleteCircle: '/api/v1/circle',
    clearDb: '/api/v1/dev/clear-db',
};
