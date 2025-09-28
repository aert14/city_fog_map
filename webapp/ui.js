// UI module - handles all user interface interactions and components
import { state, formatProgressSuffix } from './state.js';
import { fetchLeaderboard, fetchAndRenderAchievements } from './api.js';

let deleteMode = false;
let selectionEnabled = true;

export function initializeUI({ map, addVisitAt, deleteHexAtPoint, revealEntireDistrict, updateHexagonsFromServer, allKnownHexagons, addToSpatialIndex, updateDistrictProgress, countEl, forceFogRedraw }) {
  // Store parameters for use in event handlers
  const uiParams = { updateHexagonsFromServer, addToSpatialIndex, updateDistrictProgress, countEl, forceFogRedraw, allKnownHexagons };
  // Get UI elements
  const openBtn = document.getElementById("hud-explore-btn");
  const leaderboardBtn = document.getElementById("hud-leaderboard-btn");
  const leaderboardOverlay = document.getElementById("leaderboard-overlay");
  const leaderboardCloseBtn = document.getElementById("leaderboardCloseBtn");
  const leaderboardLevelSelect = document.getElementById("leaderboardLevel");
  const leaderboardPeriodSelect = document.getElementById("leaderboardPeriod");
  const leaderboardStatus = document.getElementById("leaderboardStatus");
  const leaderboardBody = document.getElementById("leaderboardBody");
  const toggleFogBtn = document.getElementById("toggleFogBtn");
  const profileBtn = document.getElementById("hud-profile-btn");
  const profileOverlay = document.getElementById("profile-overlay");
  const profileCloseBtn = document.getElementById("profile-close-btn");
  const achievementsList = document.getElementById("achievements-list");

  // Debug UI elements
  const deleteModeBtn = document.getElementById("deleteModeBtn");
  const clearDbBtn = document.getElementById("clearDbBtn");
  const selectionToggleBtn = document.getElementById("selectionToggleBtn");
  const revealDistrictBtn = document.getElementById("revealDistrictBtn");

  // Setup event listeners
  setupLeaderboardUI();
  setupProfileUI();
  setupDebugUI();
  setupMainButtons();

  function setupLeaderboardUI() {
    if (leaderboardBtn) {
      leaderboardBtn.addEventListener("click", () => {
        showLeaderboard();
      });
    }

    if (leaderboardCloseBtn) {
      leaderboardCloseBtn.addEventListener("click", hideLeaderboard);
    }

    if (leaderboardOverlay) {
      leaderboardOverlay.addEventListener("click", (event) => {
        if (event.target === leaderboardOverlay) hideLeaderboard();
      });
    }

    if (leaderboardLevelSelect) {
      leaderboardLevelSelect.addEventListener("change", handleLeaderboardLevelChange);
    }

    if (leaderboardPeriodSelect) {
      leaderboardPeriodSelect.addEventListener("change", handleLeaderboardPeriodChange);
    }

    document.addEventListener("keydown", handleLeaderboardKey);
  }

  function setupProfileUI() {
    if (profileBtn) {
      profileBtn.addEventListener('click', () => {
        profileOverlay.style.display = 'flex';
        fetchAndRenderAchievements({ achievementsList });
      });
    }

    if (profileCloseBtn) {
      profileCloseBtn.addEventListener('click', () => {
        profileOverlay.style.display = 'none';
      });
    }

    if (profileOverlay) {
      profileOverlay.addEventListener('click', (event) => {
        if (event.target === profileOverlay) {
          profileOverlay.style.display = 'none';
        }
      });
    }
  }

  function setupDebugUI() {
    if (deleteModeBtn) {
      deleteModeBtn.addEventListener("click", () => setDeleteMode(!deleteMode));
    }

    if (selectionToggleBtn) {
      selectionToggleBtn.addEventListener("click", () => {
        setSelectionEnabled(!selectionEnabled);
      });
    }

    if (revealDistrictBtn) {
      revealDistrictBtn.addEventListener("click", async () => {
        if (!state.selectedDistrictId) {
          alert("Select a district first.");
          return;
        }
        revealDistrictBtn.disabled = true;
        revealDistrictBtn.textContent = "Revealing…";
        try {
          await revealEntireDistrict(state.selectedDistrictId, uiParams);
        } catch (err) {
          console.warn("[debug] reveal district failed", err);
          alert("Failed to reveal district");
        } finally {
          revealDistrictBtn.disabled = false;
          revealDistrictBtn.textContent = "Reveal District";
        }
      });
    }

    if (clearDbBtn) {
      clearDbBtn.addEventListener("click", async () => {
        if (!confirm("Clear the entire database? This action is irreversible."))
          return;
        try {
          const res = await fetch("/api/v1/dev/clear-db", { method: "POST" });
          if (!res.ok) throw new Error("clear-db failed");
          const data = await res.json().catch(() => ({}));
          allKnownHexagons.clear();
          state.spatialIndex.clear();
          state.fogDataChanged = true;
          countEl.textContent = "0";
          forceFogRedraw();
          map.triggerRepaint();
          alert(
            `DB cleared. circles=${data.cleared_circles ?? "?"}, users=${data.cleared_users ?? "?"}`,
          );
        } catch (e) {
          alert("Error clearing database");
          console.warn("[dev] clear-db error", e);
        }
      });
    }
  }

  function setupMainButtons() {
    if (toggleFogBtn) {
      toggleFogBtn.addEventListener("click", () => {
        state.fogEnabled = !state.fogEnabled;
        toggleFogBtn.textContent = state.fogEnabled ? "Hide Fog" : "Show Fog";
        map.triggerRepaint();
      });
    }
  }

  function showLeaderboard() {
    if (!leaderboardOverlay) return;
    leaderboardOverlay.classList.add("visible");
    state.leaderboardState.isOpen = true;
    if (leaderboardStatus) leaderboardStatus.textContent = "";
    fetchLeaderboard(state.leaderboardState, state.leaderboardAbortController)
      .then(entries => {
        renderLeaderboard(entries);
        setLeaderboardLoading(false);
      })
      .catch(error => {
        console.warn("[leaderboard] Failed to fetch leaderboard:", error);
        state.leaderboardState.error = "Unable to load leaderboard.";
        renderLeaderboard([]);
        setLeaderboardLoading(false);
      });
  }

  function hideLeaderboard() {
    if (!leaderboardOverlay) return;
    leaderboardOverlay.classList.remove("visible");
    state.leaderboardState.isOpen = false;
    if (state.leaderboardAbortController) {
      state.leaderboardAbortController.abort();
      state.leaderboardAbortController = null;
    }
  }

  function renderLeaderboard(entries) {
    if (!leaderboardBody) return;
    leaderboardBody.innerHTML = "";
    if (!Array.isArray(entries) || entries.length === 0) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 6;
      cell.textContent = "No data yet. Explore the city to be the first!";
      cell.style.textAlign = "center";
      cell.style.padding = "20px";
      row.appendChild(cell);
      leaderboardBody.appendChild(row);
      return;
    }

    entries.forEach((entry) => {
      const row = document.createElement("tr");

      const rankCell = document.createElement("td");
      rankCell.textContent = entry.rank ?? "-";
      rankCell.className = "rank";
      row.appendChild(rankCell);

      const userCell = document.createElement("td");
      userCell.textContent = entry.username || `User ${entry.user_id ?? ""}`;
      userCell.className = "username";
      row.appendChild(userCell);

      const cellsCell = document.createElement("td");
      cellsCell.textContent =
        typeof entry.visited_cells === "number"
          ? entry.visited_cells.toLocaleString("ru-RU")
          : "-";
      row.appendChild(cellsCell);

      const weightCell = document.createElement("td");
      weightCell.textContent =
        typeof entry.visited_weight === "number"
          ? entry.visited_weight.toFixed(2)
          : "-";
      row.appendChild(weightCell);

      const percentCellsCell = document.createElement("td");
      percentCellsCell.textContent =
        typeof entry.percent_cells === "number"
          ? `${entry.percent_cells.toFixed(1)}%`
          : "-";
      row.appendChild(percentCellsCell);

      const percentWeightCell = document.createElement("td");
      percentWeightCell.textContent =
        typeof entry.percent_weight === "number"
          ? `${entry.percent_weight.toFixed(1)}%`
          : "-";
      row.appendChild(percentWeightCell);

      leaderboardBody.appendChild(row);
    });
  }

  function setLeaderboardLoading(isLoading) {
    state.leaderboardState.loading = isLoading;
    if (leaderboardStatus) {
      leaderboardStatus.textContent = isLoading
        ? "Loading…"
        : state.leaderboardState.error || "";
      leaderboardStatus.style.color = state.leaderboardState.error
        ? "#f87171"
        : "inherit";
    }
  }

  function handleLeaderboardLevelChange(event) {
    const value = event?.target?.value;
    if (!value || !["district", "okrug"].includes(value)) return;
    state.leaderboardState.level = value;
    if (state.leaderboardState.isOpen) {
      fetchLeaderboard(state.leaderboardState, state.leaderboardAbortController)
        .then(entries => {
          renderLeaderboard(entries);
          setLeaderboardLoading(false);
        })
        .catch(error => {
          console.warn("[leaderboard] Failed to fetch leaderboard:", error);
          state.leaderboardState.error = "Unable to load leaderboard.";
          renderLeaderboard([]);
          setLeaderboardLoading(false);
        });
    }
  }

  function handleLeaderboardPeriodChange(event) {
    const value = event?.target?.value;
    if (!value || !["week", "season"].includes(value)) return;
    state.leaderboardState.period = value;
    if (state.leaderboardState.isOpen) {
      fetchLeaderboard(state.leaderboardState, state.leaderboardAbortController)
        .then(entries => {
          renderLeaderboard(entries);
          setLeaderboardLoading(false);
        })
        .catch(error => {
          console.warn("[leaderboard] Failed to fetch leaderboard:", error);
          state.leaderboardState.error = "Unable to load leaderboard.";
          renderLeaderboard([]);
          setLeaderboardLoading(false);
        });
    }
  }

  function handleLeaderboardKey(event) {
    if (event.key === "Escape" && state.leaderboardState.isOpen) {
      hideLeaderboard();
    }
  }

  function setDeleteMode(on) {
    deleteMode = !!on;
    if (deleteModeBtn) {
      deleteModeBtn.textContent = deleteMode ? "Delete: On" : "Delete: Off";
      deleteModeBtn.style.background = deleteMode ? "#b91c1c" : "#ef4444";
    }
  }

  function setSelectionEnabled(on) {
    selectionEnabled = !!on;
    const selectionToggleBtn = document.getElementById("selectionToggleBtn");
    if (selectionToggleBtn) {
      selectionToggleBtn.textContent = selectionEnabled
        ? "Select: On"
        : "Select: Off";
      selectionToggleBtn.style.background = selectionEnabled
        ? "#0ea5e9"
        : "#475569";
    }
  }

  // Return functions for external use
  return {
    showLeaderboard,
    hideLeaderboard,
    setDeleteMode,
    setSelectionEnabled,
    getDeleteMode: () => deleteMode,
    getSelectionEnabled: () => selectionEnabled,
  };
}

export function updateStatusForSelection() {
  if (!state.selectedDistrictId || !state.selectedDistrictFeature) {
    return;
  }
  const suffix = formatProgressSuffix(state.selectedDistrictFeature);
  const label = suffix
    ? `${state.selectedDistrictName} • ${suffix}`
    : state.selectedDistrictName;
  // Note: In the original code this updated some status element, but it's not clear what element
  // You may need to add the status element update here if needed
}

export function startOnboarding() {
  const steps = [
    {
      title: "Добро пожаловать!",
      text: "Это City Fog Map, игра, где вы исследуете реальный мир и открываете его на карте, рассеивая 'туман войны'."
    },
    {
      title: "Как исследовать?",
      text: "Перемещайтесь по городу, и когда будете готовы, нажмите большую кнопку 'Исследовать' внизу, чтобы открыть территорию вокруг вас."
    },
    {
      title: "Следите за прогрессом",
      text: "Кликайте на районы на карте, чтобы увидеть свой прогресс и соревнуйтесь с другими игроками в таблице лидеров. Удачи!"
    }
  ];

  let currentStep = 0;
  const overlay = document.getElementById('onboarding-overlay');
  const titleEl = document.getElementById('onboarding-title');
  const textEl = document.getElementById('onboarding-text');
  const skipBtn = document.getElementById('onboarding-skip-btn');
  const nextBtn = document.getElementById('onboarding-next-btn');

  function showStep(stepIndex) {
    const step = steps[stepIndex];
    titleEl.textContent = step.title;
    textEl.textContent = step.text;
    if (stepIndex === steps.length - 1) {
      nextBtn.textContent = "Начать игру!";
    } else {
      nextBtn.textContent = "Далее";
    }
    overlay.style.display = 'flex';
  }

  function finishOnboarding() {
    overlay.style.display = 'none';
    localStorage.setItem('onboardingCompleted', 'true');
  }

  nextBtn.addEventListener('click', () => {
    currentStep++;
    if (currentStep < steps.length) {
      showStep(currentStep);
    } else {
      finishOnboarding();
    }
  });

  skipBtn.addEventListener('click', finishOnboarding);

  showStep(0);
}
