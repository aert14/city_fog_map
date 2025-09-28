// Main application entry point - coordinates all modules
import { getDebugSettings } from './api.js';
import { initializeMap, getMap, ensureAdminSourcesAndLayers, loadAllDistricts, scheduleAdminRefresh, handleDistrictSelection, revealEntireDistrict } from './map.js';
import { initializeUI, startOnboarding, updateStatusForSelection } from './ui.js';
import { state, addToSpatialIndex, updateDistrictProgress, removeFromSpatialIndex } from './state.js';
import { addVisitAt, deleteHexAtPoint, updateHexagonsFromServer } from './api.js';

(async function () {
  // Initialize Telegram WebApp
  const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  if (tg) {
    try {
      tg.ready();
    } catch (_) {}
  }

  // Check if onboarding has been completed
  if (!localStorage.getItem('onboardingCompleted')) {
    startOnboarding();
  }

  const hasInitData = !!(tg && tg.initData);
  const { noAuthMode, debugAuthMode } = await getDebugSettings();

  if (!hasInitData && !noAuthMode) {
    document.getElementById("app").innerHTML = `
      <div style="display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100%; text-align: center; font-family: sans-serif; color: #ccc;">
        <h2 style="margin: 0; color: #eee;">Oops!</h2>
        <p style="margin: 8px 0 0;">Could not initialize the application.<br>Please make sure you are running this inside Telegram.</p>
      </div>
    `;
    return;
  }

  // Initialize DOM elements
  const countEl = document.getElementById("hud-count");
  const fogCanvas = document.getElementById("fog-canvas");
  const fogCtx = fogCanvas.getContext("2d");
  const DPR = window.devicePixelRatio || 1;
  const loader = document.getElementById("loader");

  const FOG_CONFIG = {
    shadowBlur: 55,
    revealBlur: 40,
    pulseAmplitude: 0.04,
    animationSpeed: 0.0015,
  };

  // Initialize texture worker
  const textureWorker = new Worker('texture.worker.js');
  let cloudPattern = null;

  textureWorker.postMessage({ width: 512, height: 512 });

  textureWorker.onmessage = function(e) {
    if (e.data.bitmap) {
      cloudPattern = fogCtx.createPattern(e.data.bitmap, "repeat");
      console.log('Cloud texture generated and pattern created');
    } else if (e.data.imageData) {
      const tempCanvas = document.createElement('canvas');
      tempCanvas.width = 512;
      tempCanvas.height = 512;
      const tempCtx = tempCanvas.getContext('2d');
      tempCtx.putImageData(e.data.imageData, 0, 0);
      cloudPattern = fogCtx.createPattern(tempCanvas, "repeat");
      console.log('Cloud texture generated (fallback mode) and pattern created');
    }
  };

  // Initialize map
  const map = initializeMap();

  // Fog drawing functions
  function drawFogLoop() {
    if (!cloudPattern) return;

    state.animationTime++;
    FogModule.drawFog(
      fogCtx,
      map,
      state.fogEnabled,
      state.spatialIndex,
      state.animationTime,
      FOG_CONFIG,
      DPR,
      cloudPattern,
      state.GRID_SIZE,
      state.fogDataChanged
    );
    state.fogDataChanged = false;
  }

  function forceFogRedraw() {
    if (!cloudPattern) return;
    const wasChanged = state.fogDataChanged;
    state.fogDataChanged = true;
    drawFogLoop();
    state.fogDataChanged = wasChanged;
  }


  // Initialize UI components
  const uiControls = initializeUI({
    map,
    addVisitAt: (lat, lng) => addVisitAt(lat, lng, {
      allKnownHexagons: state.allKnownHexagons,
      addToSpatialIndex,
      updateDistrictProgress: (district, okrug) => updateDistrictProgress(district, okrug, { map }),
      countEl,
      forceFogRedraw,
      map
    }),
    deleteHexAtPoint: (point) => deleteHexAtPoint(point, {
      allKnownHexagons: state.allKnownHexagons,
      removeFromSpatialIndex,
      countEl,
      forceFogRedraw,
      map
    }),
    revealEntireDistrict,
    updateHexagonsFromServer: () => updateHexagonsFromServer({
      map,
      allKnownHexagons: state.allKnownHexagons,
      addToSpatialIndex,
      countEl,
      forceFogRedraw,
      loader
    }),
    allKnownHexagons: state.allKnownHexagons,
    addToSpatialIndex,
    updateDistrictProgress: (district, okrug) => updateDistrictProgress(district, okrug, { map }),
    countEl,
    forceFogRedraw
  });

  // Show debug controls if needed
  if (noAuthMode || debugAuthMode) {
    const toggleFogBtn = document.getElementById("toggleFogBtn");
    const debugPanel = document.getElementById("debugPanel");
    if (toggleFogBtn) toggleFogBtn.style.display = "inline-block";
    if (debugPanel) debugPanel.style.display = "flex";
  }

  // Map event handlers
  let ignoreNextClick = false;
  let lastKnownPosition = null;
  const TARGET_GEO_ZOOM = 17;
  const openBtn = document.getElementById("hud-explore-btn");

  map.on("load", () => {
    const mapContainer = document.getElementById("map-container");
    const controls = mapContainer.querySelector(".maplibregl-control-container");
    if (controls) {
      mapContainer.appendChild(controls);
    }

    const resizeObserver = new ResizeObserver(() => {
      const cssW = mapContainer.clientWidth;
      const cssH = mapContainer.clientHeight;
      fogCanvas.style.width = cssW + "px";
      fogCanvas.style.height = cssH + "px";
      fogCanvas.width = Math.max(1, Math.floor(cssW * DPR));
      fogCanvas.height = Math.max(1, Math.floor(cssH * DPR));
      fogCtx.setTransform(DPR, 0, 0, DPR, 0, 0);
    });
    resizeObserver.observe(mapContainer);

    // Initial data loading
    updateHexagonsFromServer({
      map,
      allKnownHexagons: state.allKnownHexagons,
      addToSpatialIndex,
      countEl,
      forceFogRedraw,
      loader
    });
    loadAllDistricts();

    // Try to get user location
    try {
      const geolocate = map._controls.find(control => control instanceof maplibregl.GeolocateControl);
      if (geolocate) geolocate.trigger();
    } catch (e) {
      console.error(e);
    }

    map.on("render", drawFogLoop);
  });

  // Map movement handlers
  map.on("moveend", () => {
    updateHexagonsFromServer({
      map,
      allKnownHexagons: state.allKnownHexagons,
      addToSpatialIndex,
      countEl,
      forceFogRedraw,
      loader
    });
    scheduleAdminRefresh();
  });

  map.on("movestart", () => {
    ignoreNextClick = true;
  });

  map.on("moveend", () => {
    setTimeout(() => {
      ignoreNextClick = false;
    }, 120);
  });

  // Geolocation handlers
  const geolocate = map._controls.find(control => control instanceof maplibregl.GeolocateControl);
  if (geolocate) {
    geolocate.on("geolocate", (pos) => {
      lastKnownPosition = pos.coords;
      const zoom = Math.max(map.getZoom(), TARGET_GEO_ZOOM);
      map.flyTo({ center: [pos.coords.longitude, pos.coords.latitude], zoom });
      if (openBtn) {
        openBtn.disabled = false;
        openBtn.querySelector('span').textContent = "Исследовать";
      }
    });

    geolocate.on("error", () => {
      if (noAuthMode) {
        if (openBtn) {
          openBtn.querySelector('span').textContent = "Кликните по карте для добавления точек";
          openBtn.disabled = false;
        }
      } else {
        if (openBtn) {
          openBtn.querySelector('span').textContent = "Ошибка геолокации";
        }
      }
    });
  }

  // Main explore button handler
  if (openBtn) {
    openBtn.disabled = true;
    openBtn.querySelector('span').textContent = "Поиск...";

    openBtn.addEventListener("click", async () => {
      if (!lastKnownPosition) {
        if (noAuthMode) {
          alert("Click on the map to add points.");
        } else {
          alert("Location not determined.");
        }
        return;
      }
      openBtn.disabled = true;
      try {
        const result = await addVisitAt(lastKnownPosition.latitude, lastKnownPosition.longitude, {
          allKnownHexagons: state.allKnownHexagons,
          addToSpatialIndex,
          updateDistrictProgress: (district, okrug) => updateDistrictProgress(district, okrug, { map }),
          countEl,
          forceFogRedraw,
          map
        });

        if (result.added > 0) {
          const h3Resolution = window.currentH3Resolution || state.defaultVisitResolution;
          const hexId = h3.latLngToCell(
            lastKnownPosition.latitude,
            lastKnownPosition.longitude,
            h3Resolution,
          );
          if (!state.allKnownHexagons.has(hexId)) {
            state.allKnownHexagons.add(hexId);
            addToSpatialIndex(hexId);
          }
        }
      } catch (error) {
        console.error("[visit] Failed to visit area:", error);
      } finally {
        openBtn.disabled = !lastKnownPosition;
      }
    });
  }

  // Map click handler
  map.on("click", (e) => {
    if (ignoreNextClick) return;

    // Check for district selection first
    const districtFeatures = map.queryRenderedFeatures(e.point, {
      layers: ["district-hit-area"],
    });
    if (districtFeatures && districtFeatures.length > 0) {
      if (uiControls.getSelectionEnabled()) {
        handleDistrictSelection(districtFeatures[0]);
        return;
      }
    }

    // Handle visit or delete
    if ((noAuthMode || debugAuthMode) && !uiControls.getDeleteMode()) {
      const lngLat = map.unproject(e.point);
      addVisitAt(lngLat.lat, lngLat.lng, {
        allKnownHexagons: state.allKnownHexagons,
        addToSpatialIndex,
        updateDistrictProgress: (district, okrug) => updateDistrictProgress(district, okrug, { map }),
        countEl,
        forceFogRedraw,
        map
      }).catch((error) => {
        console.error("[visit] Failed to visit area:", error);
      });
      return;
    }

    if (uiControls.getDeleteMode()) {
      deleteHexAtPoint(e.point, {
        allKnownHexagons: state.allKnownHexagons,
        removeFromSpatialIndex: state.removeFromSpatialIndex,
        countEl,
        forceFogRedraw,
        map
      });
    }
  });
})();