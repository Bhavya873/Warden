// Renders the floor grid(s), connects to the WS server, wires up the live controls, and
// drives the Chart.js dashboard. Message schema this expects: see server/ws_server.py's
// module docstring — single mode sends {"type": "tick", ...}, split mode sends
// {"type": "tick_split", "boards": {"naive": {...}, "warden": {...}}}.

const WS_URL = "ws://localhost:8765";
const LOAD_CHART_WINDOW = 150; // ticks of history kept for the rolling line chart

// Mirrors the custom properties in style.css — kept in sync by hand, small enough not
// to warrant a shared token pipeline between CSS and canvas/Chart.js drawing code.
const COLORS = {
  line: "#e4e7ec",
  ink: "#14181f",
  inkMuted: "#6b7280",
  structural: "#3b5bdb", // Warden series in split charts
  accentNaive: "#c9682e", // Naive series in split charts
  moved: "#2e9e4f",
  waiting: "#c98a12",
  conflict: "#d64545",
  nearMiss: "#8b3fc9",
};

// Chart.js otherwise falls back to the browser's default sans-serif for titles/legend
// text, which visually mismatches the rest of the page.
Chart.defaults.font.family = "'IBM Plex Sans', system-ui, sans-serif";

const floorCanvas = document.getElementById("floor");
const floorCtx = floorCanvas.getContext("2d");
const splitFloors = document.getElementById("split-floors");
const floorNaiveCanvas = document.getElementById("floor-naive");
const floorNaiveCtx = floorNaiveCanvas.getContext("2d");
const floorWardenCanvas = document.getElementById("floor-warden");
const floorWardenCtx = floorWardenCanvas.getContext("2d");

const modeSelect = document.getElementById("mode");
const robotCountInput = document.getElementById("robot-count");
const robotCountValue = document.getElementById("robot-count-value");
const gridSizeInput = document.getElementById("grid-size");
const gridSizeValue = document.getElementById("grid-size-value");
const broadcastLagSelect = document.getElementById("broadcast-lag");
const connectionText = document.getElementById("connection-text");
const statTick = document.getElementById("stat-tick");

const totalsSingle = document.getElementById("totals-single");
const totalMoves = document.getElementById("total-moves");
const totalConfirmed = document.getElementById("total-confirmed");
const totalConflicts = document.getElementById("total-conflicts");
const totalNearMisses = document.getElementById("total-near-misses");

const totalsSplit = document.getElementById("totals-split");
const splitTotalEls = {
  naive: {
    moves: document.getElementById("split-naive-total-moves"),
    confirmed: document.getElementById("split-naive-total-confirmed"),
    conflicts: document.getElementById("split-naive-total-conflicts"),
    nearMisses: document.getElementById("split-naive-total-near-misses"),
  },
  warden: {
    moves: document.getElementById("split-warden-total-moves"),
    confirmed: document.getElementById("split-warden-total-confirmed"),
    conflicts: document.getElementById("split-warden-total-conflicts"),
    nearMisses: document.getElementById("split-warden-total-near-misses"),
  },
};

let socket = null;

// --- Charts ------------------------------------------------------------
// Recreated (destroy + new Chart) whenever switching between single and split mode,
// since the two shapes need a different number of datasets — simpler and safer than
// trying to reshape datasets in place.

let mixChart = null;
let loadChart = null;
let chartsMode = null; // "single" | "split"

function destroyCharts() {
  if (mixChart) mixChart.destroy();
  if (loadChart) loadChart.destroy();
  mixChart = null;
  loadChart = null;
}

function createSingleCharts() {
  destroyCharts();
  mixChart = new Chart(document.getElementById("mix-chart"), {
    type: "bar",
    data: {
      labels: ["Instant", "Confirmed", "Conflicts avoided", "Near-misses"],
      datasets: [
        {
          label: "This tick",
          data: [0, 0, 0, 0],
          // Near-misses use purple, not red — a near-miss is the filter/coordinator
          // being wrong, not a correctly-caught conflict (tasks/staleness-finding.md).
          backgroundColor: [COLORS.moved, COLORS.waiting, COLORS.conflict, COLORS.nearMiss],
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false }, title: { display: true, text: "Move outcomes (this tick)" } },
      scales: { y: { beginAtZero: true } },
    },
  });

  loadChart = new Chart(document.getElementById("load-chart"), {
    type: "line",
    data: {
      labels: [],
      datasets: [
        {
          label: "Coordinator queue depth",
          data: [],
          borderColor: COLORS.structural,
          backgroundColor: "transparent",
          pointRadius: 0,
          tension: 0.2,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: { legend: { display: false }, title: { display: true, text: "Coordinator load (rolling)" } },
      scales: { x: { display: false }, y: { beginAtZero: true } },
    },
  });

  chartsMode = "single";
}

function createSplitCharts() {
  destroyCharts();
  mixChart = new Chart(document.getElementById("mix-chart"), {
    type: "bar",
    data: {
      labels: ["Instant", "Confirmed", "Conflicts avoided", "Near-misses"],
      datasets: [
        { label: "Naive", data: [0, 0, 0, 0], backgroundColor: COLORS.accentNaive },
        { label: "Warden", data: [0, 0, 0, 0], backgroundColor: COLORS.structural },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: true }, title: { display: true, text: "Move outcomes (this tick)" } },
      scales: { y: { beginAtZero: true } },
    },
  });

  loadChart = new Chart(document.getElementById("load-chart"), {
    type: "line",
    data: {
      labels: [],
      datasets: [
        {
          label: "Naive queue depth",
          data: [],
          borderColor: COLORS.accentNaive,
          backgroundColor: "transparent",
          pointRadius: 0,
          tension: 0.2,
        },
        {
          label: "Warden queue depth",
          data: [],
          borderColor: COLORS.structural,
          backgroundColor: "transparent",
          pointRadius: 0,
          tension: 0.2,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: { legend: { display: true }, title: { display: true, text: "Coordinator load (rolling)" } },
      scales: { x: { display: false }, y: { beginAtZero: true } },
    },
  });

  chartsMode = "split";
}

function pushRolling(labels, data, tick, value) {
  labels.push(tick);
  data.push(value);
  if (labels.length > LOAD_CHART_WINDOW) {
    labels.shift();
    data.shift();
  }
}

// --- WebSocket -----------------------------------------------------------

function connect() {
  socket = new WebSocket(WS_URL);

  socket.addEventListener("open", () => {
    connectionText.textContent = "connected";
  });

  socket.addEventListener("close", () => {
    connectionText.textContent = "disconnected, retrying…";
    setTimeout(connect, 1000);
  });

  socket.addEventListener("error", () => {
    socket.close();
  });

  socket.addEventListener("message", (event) => {
    const state = JSON.parse(event.data);
    if (state.type === "tick_split") {
      renderSplit(state);
    } else {
      renderSingle(state);
    }
  });
}

// --- Rendering: single mode ------------------------------------------------

function renderSingle(state) {
  floorCanvas.classList.remove("hidden");
  splitFloors.classList.remove("active");
  totalsSingle.classList.remove("hidden");
  totalsSplit.classList.remove("active");

  if (chartsMode !== "single") createSingleCharts();

  drawFloor(floorCtx, floorCanvas, state.grid_size, state.robots);

  mixChart.data.datasets[0].data = [
    state.stats.instant_moves,
    state.stats.confirmed_checks,
    state.stats.conflicts_avoided,
    state.stats.near_misses,
  ];
  mixChart.update("none");
  pushRolling(loadChart.data.labels, loadChart.data.datasets[0].data, state.tick, state.stats.queue_depth);
  loadChart.update("none");

  statTick.textContent = `(tick ${state.tick})`;
  totalMoves.textContent = state.totals.instant_moves + state.totals.confirmed_checks;
  totalConfirmed.textContent = state.totals.confirmed_checks;
  totalConflicts.textContent = state.totals.conflicts_avoided;
  totalNearMisses.textContent = state.totals.near_misses;

  syncControls(state.mode, state.robot_count, state.grid_size, state.broadcast_lag);
}

// --- Rendering: split mode ---------------------------------------------

function renderSplit(state) {
  floorCanvas.classList.add("hidden");
  splitFloors.classList.add("active");
  totalsSingle.classList.add("hidden");
  totalsSplit.classList.add("active");

  if (chartsMode !== "split") createSplitCharts();

  const naive = state.boards.naive;
  const warden = state.boards.warden;

  drawFloor(floorNaiveCtx, floorNaiveCanvas, state.grid_size, naive.robots);
  drawFloor(floorWardenCtx, floorWardenCanvas, state.grid_size, warden.robots);

  mixChart.data.datasets[0].data = [
    naive.stats.instant_moves,
    naive.stats.confirmed_checks,
    naive.stats.conflicts_avoided,
    naive.stats.near_misses, // always 0 — naive has no filter to go stale
  ];
  mixChart.data.datasets[1].data = [
    warden.stats.instant_moves,
    warden.stats.confirmed_checks,
    warden.stats.conflicts_avoided,
    warden.stats.near_misses,
  ];
  mixChart.update("none");

  pushRolling(loadChart.data.labels, loadChart.data.datasets[0].data, naive.tick, naive.stats.queue_depth);
  // naive and warden boards are stepped together, so they share one label timeline —
  // only push the second dataset's value, not a second set of labels.
  loadChart.data.datasets[1].data.push(warden.stats.queue_depth);
  if (loadChart.data.datasets[1].data.length > LOAD_CHART_WINDOW) {
    loadChart.data.datasets[1].data.shift();
  }
  loadChart.update("none");

  statTick.textContent = `(tick ${naive.tick})`;
  splitTotalEls.naive.moves.textContent = naive.totals.instant_moves + naive.totals.confirmed_checks;
  splitTotalEls.naive.confirmed.textContent = naive.totals.confirmed_checks;
  splitTotalEls.naive.conflicts.textContent = naive.totals.conflicts_avoided;
  splitTotalEls.naive.nearMisses.textContent = naive.totals.near_misses;
  splitTotalEls.warden.moves.textContent = warden.totals.instant_moves + warden.totals.confirmed_checks;
  splitTotalEls.warden.confirmed.textContent = warden.totals.confirmed_checks;
  splitTotalEls.warden.conflicts.textContent = warden.totals.conflicts_avoided;
  splitTotalEls.warden.nearMisses.textContent = warden.totals.near_misses;

  syncControls("split", naive.robot_count, state.grid_size, state.broadcast_lag);
}

// --- Shared floor drawing -------------------------------------------------

// robot.outcome -> fill color. "idle" (sitting at its current target, nothing pending)
// stays a quiet neutral so the three active states read clearly against it.
const OUTCOME_COLORS = {
  moved: COLORS.moved,
  waiting: COLORS.waiting,
  conflict: COLORS.conflict,
  idle: COLORS.inkMuted,
};

function drawFloor(ctx, canvas, gridSize, robots) {
  const cellSize = canvas.width / gridSize;

  ctx.clearRect(0, 0, canvas.width, canvas.height);

  ctx.strokeStyle = COLORS.line;
  ctx.lineWidth = 1;
  for (let i = 0; i <= gridSize; i++) {
    const p = i * cellSize;
    ctx.beginPath();
    ctx.moveTo(p, 0);
    ctx.lineTo(p, canvas.height);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(0, p);
    ctx.lineTo(canvas.width, p);
    ctx.stroke();
  }

  const radius = cellSize * 0.35;

  for (const robot of robots) {
    const cx = robot.x * cellSize + cellSize / 2;
    const cy = robot.y * cellSize + cellSize / 2;

    ctx.fillStyle = OUTCOME_COLORS[robot.outcome] || COLORS.inkMuted;
    ctx.strokeStyle = COLORS.ink;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.arc(cx, cy, radius, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();

    if (robot.dx !== 0 || robot.dy !== 0) {
      ctx.strokeStyle = COLORS.ink;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.lineTo(cx + robot.dx * radius * 1.6, cy + robot.dy * radius * 1.6);
      ctx.stroke();
    }

    // Near-miss: the filter/coordinator approved this move but ground truth caught it —
    // a distinct ring, not red, so it isn't mistaken for a caught conflict.
    if (robot.near_miss) {
      ctx.strokeStyle = COLORS.nearMiss;
      ctx.lineWidth = 3;
      ctx.beginPath();
      ctx.arc(cx, cy, radius * 1.8, 0, Math.PI * 2);
      ctx.stroke();
    }
  }
}

// --- Controls --------------------------------------------------------------

function syncControls(mode, robotCount, gridSize, broadcastLag) {
  if (modeSelect.value !== mode) {
    modeSelect.value = mode;
  }
  // Don't stomp on a control the user is actively dragging.
  if (document.activeElement !== robotCountInput) {
    robotCountInput.value = robotCount;
    robotCountValue.textContent = robotCount;
  }
  if (document.activeElement !== gridSizeInput) {
    gridSizeInput.value = gridSize;
    gridSizeValue.textContent = gridSize;
  }
  if (broadcastLagSelect.value !== broadcastLag) {
    broadcastLagSelect.value = broadcastLag;
  }
}

function send(message) {
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify(message));
  }
}

modeSelect.addEventListener("change", () => {
  send({ action: "set_mode", mode: modeSelect.value });
});

broadcastLagSelect.addEventListener("change", () => {
  send({ action: "set_broadcast_lag", level: broadcastLagSelect.value });
});

const DEBOUNCE_MS = 150;
let robotCountDebounce = null;
robotCountInput.addEventListener("input", () => {
  robotCountValue.textContent = robotCountInput.value;
  clearTimeout(robotCountDebounce);
  robotCountDebounce = setTimeout(() => {
    send({ action: "set_robot_count", count: Number(robotCountInput.value) });
  }, DEBOUNCE_MS);
});

let gridSizeDebounce = null;
gridSizeInput.addEventListener("input", () => {
  gridSizeValue.textContent = gridSizeInput.value;
  clearTimeout(gridSizeDebounce);
  gridSizeDebounce = setTimeout(() => {
    send({ action: "set_grid_size", size: Number(gridSizeInput.value) });
  }, DEBOUNCE_MS);
});

connect();
