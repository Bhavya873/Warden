// Renders the two floor grids (Naive vs. Warden — the only view; no mode selector),
// connects to the WS server, wires up the live controls, and drives the Chart.js
// dashboard. Message schema: see server/ws_server.py's module docstring — the server
// always sends {"type": "tick_split", "boards": {"naive": {...}, "warden": {...}}}.

const WS_URL = "ws://localhost:8765";
const LOAD_CHART_WINDOW = 150; // ticks of history kept for the rolling line chart

// Mirrors the custom properties in style.css — kept in sync by hand, small enough not
// to warrant a shared token pipeline between CSS and canvas/Chart.js drawing code.
const COLORS = {
  line: "#e4e7ec",
  ink: "#14181f",
  inkMuted: "#6b7280",
  panel: "#ffffff",
  structural: "#3b5bdb", // Warden series
  accentNaive: "#c9682e", // Naive series
  moved: "#2e9e4f",
  waiting: "#c98a12",
  conflict: "#d64545",
  nearMiss: "#8b3fc9",
};

// Chart.js otherwise falls back to the browser's default sans-serif for titles/legend
// text, which visually mismatches the rest of the page.
Chart.defaults.font.family = "'IBM Plex Sans', system-ui, sans-serif";

const floorNaiveCanvas = document.getElementById("floor-naive");
const floorNaiveCtx = floorNaiveCanvas.getContext("2d");
const floorWardenCanvas = document.getElementById("floor-warden");
const floorWardenCtx = floorWardenCanvas.getContext("2d");

const robotCountInput = document.getElementById("robot-count");
const robotCountValue = document.getElementById("robot-count-value");
const gridSizeInput = document.getElementById("grid-size");
const gridSizeValue = document.getElementById("grid-size-value");
const broadcastLagSelect = document.getElementById("broadcast-lag");
const connectionText = document.getElementById("connection-text");
const statTick = document.getElementById("stat-tick");

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

const mixChart = new Chart(document.getElementById("mix-chart"), {
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

const loadChart = new Chart(document.getElementById("load-chart"), {
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
        borderWidth: 2,
        tension: 0.25,
      },
      {
        label: "Warden queue depth",
        data: [],
        borderColor: COLORS.structural,
        backgroundColor: "transparent",
        pointRadius: 0,
        borderWidth: 2,
        tension: 0.25,
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

function pushRolling(data, value) {
  data.push(value);
  if (data.length > LOAD_CHART_WINDOW) data.shift();
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
    render(JSON.parse(event.data));
  });
}

// --- Rendering --------------------------------------------------------------

function render(state) {
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

  pushRolling(loadChart.data.datasets[0].data, naive.stats.queue_depth);
  pushRolling(loadChart.data.datasets[1].data, warden.stats.queue_depth);
  pushRolling(loadChart.data.labels, naive.tick); // both boards are stepped together, one shared timeline
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

  syncControls(naive.robot_count, state.grid_size, state.broadcast_lag);
}

// --- Floor drawing -------------------------------------------------

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
    const p = Math.round(i * cellSize) + 0.5; // crisp 1px lines, not antialiased blur
    ctx.beginPath();
    ctx.moveTo(p, 0);
    ctx.lineTo(p, canvas.height);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(0, p);
    ctx.lineTo(canvas.width, p);
    ctx.stroke();
  }

  const radius = cellSize * 0.32;

  for (const robot of robots) {
    const cx = robot.x * cellSize + cellSize / 2;
    const cy = robot.y * cellSize + cellSize / 2;

    // A light halo behind each dot lifts it off the gridlines without a heavy dark
    // outline — a softer, flatter treatment than an ink border on every robot.
    ctx.fillStyle = COLORS.panel;
    ctx.beginPath();
    ctx.arc(cx, cy, radius + 1.5, 0, Math.PI * 2);
    ctx.fill();

    ctx.fillStyle = OUTCOME_COLORS[robot.outcome] || COLORS.inkMuted;
    ctx.beginPath();
    ctx.arc(cx, cy, radius, 0, Math.PI * 2);
    ctx.fill();

    if (robot.dx !== 0 || robot.dy !== 0) {
      ctx.strokeStyle = COLORS.ink;
      ctx.lineWidth = 1.5;
      ctx.lineCap = "round";
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.lineTo(cx + robot.dx * radius * 1.5, cy + robot.dy * radius * 1.5);
      ctx.stroke();
    }

    // Near-miss: the filter/coordinator approved this move but ground truth caught it —
    // a distinct ring, not red, so it isn't mistaken for a caught conflict.
    if (robot.near_miss) {
      ctx.strokeStyle = COLORS.nearMiss;
      ctx.lineWidth = 2.5;
      ctx.beginPath();
      ctx.arc(cx, cy, radius * 1.7, 0, Math.PI * 2);
      ctx.stroke();
    }
  }
}

// --- Controls --------------------------------------------------------------

function syncControls(robotCount, gridSize, broadcastLag) {
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
