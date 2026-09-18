// Renders the floor grid, connects to the WS server, wires up the live controls, and
// drives the Chart.js dashboard (mix bar chart, rolling coordinator-load line, running
// totals). Message schema this expects: see server/ws_server.py's module docstring.

const WS_URL = "ws://localhost:8765";
const LOAD_CHART_WINDOW = 150; // ticks of history kept for the rolling line chart

const canvas = document.getElementById("floor");
const ctx = canvas.getContext("2d");

const modeSelect = document.getElementById("mode");
const robotCountInput = document.getElementById("robot-count");
const robotCountValue = document.getElementById("robot-count-value");
const gridSizeInput = document.getElementById("grid-size");
const gridSizeValue = document.getElementById("grid-size-value");
const connectionText = document.getElementById("connection-text");
const statTick = document.getElementById("stat-tick");

const totalMoves = document.getElementById("total-moves");
const totalConfirmed = document.getElementById("total-confirmed");
const totalConflicts = document.getElementById("total-conflicts");

let socket = null;

// --- Charts ---------------------------------------------------------------

const mixChart = new Chart(document.getElementById("mix-chart"), {
  type: "bar",
  data: {
    labels: ["Instant", "Confirmed", "Conflicts avoided"],
    datasets: [
      {
        label: "This tick",
        data: [0, 0, 0],
        backgroundColor: ["#2fa84f", "#e0b400", "#d64545"],
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

const loadChart = new Chart(document.getElementById("load-chart"), {
  type: "line",
  data: {
    labels: [],
    datasets: [
      {
        label: "Coordinator queue depth",
        data: [],
        borderColor: "#2f6fed",
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

function updateMixChart(stats) {
  mixChart.data.datasets[0].data = [stats.instant_moves, stats.confirmed_checks, stats.conflicts_avoided];
  mixChart.update("none");
}

function updateLoadChart(tick, queueDepth) {
  const labels = loadChart.data.labels;
  const data = loadChart.data.datasets[0].data;
  labels.push(tick);
  data.push(queueDepth);
  if (labels.length > LOAD_CHART_WINDOW) {
    labels.shift();
    data.shift();
  }
  loadChart.update("none");
}

function resetCharts() {
  loadChart.data.labels = [];
  loadChart.data.datasets[0].data = [];
  loadChart.update("none");
}

// --- WebSocket --------------------------------------------------------------

function connect() {
  socket = new WebSocket(WS_URL);

  socket.addEventListener("open", () => {
    connectionText.textContent = "connected";
  });

  socket.addEventListener("close", () => {
    connectionText.textContent = "disconnected — retrying…";
    setTimeout(connect, 1000);
  });

  socket.addEventListener("error", () => {
    socket.close();
  });

  let lastMode = null;
  socket.addEventListener("message", (event) => {
    const state = JSON.parse(event.data);
    if (lastMode !== null && lastMode !== state.mode) {
      resetCharts(); // totals/coordinator reset server-side on a mode switch too
    }
    lastMode = state.mode;
    render(state);
  });
}

function render(state) {
  renderFloor(state);
  updateMixChart(state.stats);
  updateLoadChart(state.tick, state.stats.queue_depth);

  statTick.textContent = `(tick ${state.tick})`;
  totalMoves.textContent = state.totals.instant_moves + state.totals.confirmed_checks;
  totalConfirmed.textContent = state.totals.confirmed_checks;
  totalConflicts.textContent = state.totals.conflicts_avoided;

  if (modeSelect.value !== state.mode) {
    modeSelect.value = state.mode;
  }
  // Don't stomp on a control the user is actively dragging.
  if (document.activeElement !== robotCountInput) {
    robotCountInput.value = state.robot_count;
    robotCountValue.textContent = state.robot_count;
  }
  if (document.activeElement !== gridSizeInput) {
    gridSizeInput.value = state.grid_size;
    gridSizeValue.textContent = state.grid_size;
  }
}

function renderFloor(state) {
  const cellSize = canvas.width / state.grid_size;

  ctx.clearRect(0, 0, canvas.width, canvas.height);

  ctx.strokeStyle = "#ddd";
  ctx.lineWidth = 1;
  for (let i = 0; i <= state.grid_size; i++) {
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
  ctx.fillStyle = "#2f6fed";
  ctx.strokeStyle = "#1a3f99";
  ctx.lineWidth = 2;

  for (const robot of state.robots) {
    const cx = robot.x * cellSize + cellSize / 2;
    const cy = robot.y * cellSize + cellSize / 2;

    ctx.beginPath();
    ctx.arc(cx, cy, radius, 0, Math.PI * 2);
    ctx.fill();

    if (robot.dx !== 0 || robot.dy !== 0) {
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.lineTo(cx + robot.dx * radius * 1.6, cy + robot.dy * radius * 1.6);
      ctx.stroke();
    }
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
