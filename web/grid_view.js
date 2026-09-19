// Renders the two floor grids (Baseline vs. Warden — the only view; no mode selector),
// connects to the WS server, wires up the live controls, and drives the Chart.js
// dashboard. Message schema: see server/ws_server.py's module docstring — the server
// always sends {"type": "tick_split", "boards": {"naive": {...}, "warden": {...}}}.
// "naive" is the wire-protocol/internal name (unchanged); "Baseline" is just the
// user-facing label for it, renamed because "Naive" read as unclear/judgmental.

const WS_URL = "ws://localhost:8765";
const LOAD_CHART_WINDOW = 150; // ticks of history kept for the rolling line chart

// Mirrors the custom properties in style.css — kept in sync by hand, small enough not
// to warrant a shared token pipeline between CSS and canvas/Chart.js drawing code.
const COLORS = {
  line: "#232838",
  ink: "#eef1f6",
  inkMuted: "#8892a3",
  panel: "#12161f", // --surface — canvas backdrop
  structural: "#5b8cff", // Warden series
  accentNaive: "#e8935c", // Naive series
  moved: "#34d399",
  waiting: "#fbbf24",
  conflict: "#f87171",
  nearMiss: "#c084fc",
};

// Chart.js defaults assume a light page — set text/grid colors explicitly for the dark
// theme, and match the page's typeface instead of the browser's default sans-serif.
Chart.defaults.font.family = "'VT323', ui-monospace, monospace";
Chart.defaults.color = COLORS.inkMuted;
Chart.defaults.borderColor = COLORS.line;

const floorNaiveCanvas = document.getElementById("floor-naive");
const floorNaiveCtx = floorNaiveCanvas.getContext("2d");
const floorWardenCanvas = document.getElementById("floor-warden");
const floorWardenCtx = floorWardenCanvas.getContext("2d");

const playPauseBtn = document.getElementById("play-pause-btn");
const resetBtn = document.getElementById("reset-btn");
const robotCountInput = document.getElementById("robot-count");
const robotCountValue = document.getElementById("robot-count-value");
const gridSizeInput = document.getElementById("grid-size");
const gridSizeValue = document.getElementById("grid-size-value");
const broadcastLagSelect = document.getElementById("broadcast-lag");
const liveDot = document.getElementById("live-dot");
const connectionText = document.getElementById("connection-text");
const statTick = document.getElementById("stat-tick");
const loadAvgNaiveEl = document.getElementById("load-avg-naive");
const loadAvgWardenEl = document.getElementById("load-avg-warden");

const compareEls = {
  naive: {
    moves: document.getElementById("cmp-naive-moves"),
    load: document.getElementById("cmp-naive-load"),
    conflicts: document.getElementById("cmp-naive-conflicts"),
  },
  warden: {
    moves: document.getElementById("cmp-warden-moves"),
    load: document.getElementById("cmp-warden-load"),
    conflicts: document.getElementById("cmp-warden-conflicts"),
  },
};

const reductionPctEl = document.getElementById("cmp-reduction-pct");
const reductionWordEl = document.getElementById("cmp-reduction-word");

let socket = null;

// --- Chart: a single, unobtrusive server-load sparkline. The comparison table covers
// the point-in-time numbers; this is the only place trend-over-time earns a chart. No
// title, legend, or axis labels — those would just repeat what the table already says. --

const loadChart = new Chart(document.getElementById("load-chart"), {
  type: "line",
  data: {
    labels: [],
    datasets: [
      {
        label: "Baseline",
        data: [],
        borderColor: COLORS.accentNaive,
        backgroundColor: "transparent",
        pointRadius: 0,
        borderWidth: 2,
        tension: 0.25,
      },
      {
        label: "Warden",
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
    plugins: { legend: { display: false } },
    scales: {
      x: {
        display: true,
        grid: { display: false },
        ticks: { maxTicksLimit: 5, font: { size: 13 } },
      },
      y: {
        display: true,
        beginAtZero: true,
        grid: { display: false },
        ticks: { maxTicksLimit: 4, font: { size: 13 } },
      },
    },
  },
});

function pushRolling(data, value) {
  data.push(value);
  if (data.length > LOAD_CHART_WINDOW) data.shift();
}

function average(values) {
  return values.length > 0 ? values.reduce((sum, v) => sum + v, 0) / values.length : 0;
}

// Chart.js's built-in auto-scaling recomputes the axis max every single tick, so a
// value oscillating quickly (e.g. 80 <-> 120) makes the whole axis visibly snap back
// and forth. An EMA-smoothed max grows fast (so real spikes are never clipped) but
// shrinks slowly (so a brief dip doesn't yank the axis back down), which reads as a
// steady axis instead of a jittery one.
function makeSmoothedMax(seed) {
  let current = seed;
  return function smoothedMax(dataMax) {
    const target = Math.max(dataMax * 1.15, seed);
    const alpha = target > current ? 0.3 : 0.02;
    current += (target - current) * alpha;
    return current;
  };
}

let loadSmoothedMax = makeSmoothedMax(5); // reassigned on reset — see resetBtn handler

// --- WebSocket -----------------------------------------------------------

function connect() {
  socket = new WebSocket(WS_URL);

  socket.addEventListener("open", () => {
    connectionText.textContent = "connected";
    liveDot.classList.add("connected");
  });

  socket.addEventListener("close", () => {
    connectionText.textContent = "disconnected, retrying…";
    liveDot.classList.remove("connected");
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

  // The server keeps broadcasting the same frozen state every tick while paused (so
  // controls stay responsive), which would otherwise push duplicate points onto the
  // rolling window and make the sparkline visibly scroll even though nothing changed.
  if (!state.paused) {
    pushRolling(loadChart.data.datasets[0].data, naive.stats.queue_depth);
    pushRolling(loadChart.data.datasets[1].data, warden.stats.queue_depth);
    pushRolling(loadChart.data.labels, naive.tick); // both boards are stepped together, one shared timeline
    const loadDataMax = Math.max(...loadChart.data.datasets[0].data, ...loadChart.data.datasets[1].data);
    loadChart.options.scales.y.max = loadSmoothedMax(loadDataMax);
    loadChart.update("none");
    loadAvgNaiveEl.textContent = `avg ${average(loadChart.data.datasets[0].data).toFixed(1)}`;
    loadAvgWardenEl.textContent = `avg ${average(loadChart.data.datasets[1].data).toFixed(1)}`;
  }

  statTick.textContent = `(tick ${naive.tick})`;
  updateCompareRow(compareEls.naive, naive);
  updateCompareRow(compareEls.warden, warden);
  updateReductionHero(naive, warden);

  syncControls(naive.robot_count, state.grid_size, state.broadcast_lag);

  // Don't stomp the label mid-click — harmless either way since the next broadcast
  // (100ms later) re-syncs it, but avoids a visible flicker right after clicking.
  if (document.activeElement !== playPauseBtn) {
    playPauseBtn.textContent = state.paused ? "Play" : "Pause";
  }
}

function updateCompareRow(els, board) {
  els.moves.textContent = board.totals.instant_moves + board.totals.confirmed_checks;
  els.load.textContent = board.stats.queue_depth;
  els.conflicts.textContent = board.totals.conflicts_avoided;
}

function requestRate(board) {
  const moves = board.totals.instant_moves + board.totals.confirmed_checks;
  return moves > 0 ? board.totals.confirmed_checks / moves : null;
}

// The headline stat: not the two boards' request rates side by side (which makes the
// reader do the subtraction themselves), but the comparison already done — how much
// lower Warden's rate is than Baseline's, computed fresh every tick.
function updateReductionHero(naive, warden) {
  const naiveRate = requestRate(naive);
  const wardenRate = requestRate(warden);

  if (naiveRate === null || wardenRate === null || naiveRate === 0) {
    reductionPctEl.textContent = "—";
    return;
  }

  const change = (1 - wardenRate / naiveRate) * 100;
  reductionWordEl.textContent = change >= 0 ? "fewer" : "more";
  reductionPctEl.textContent = `${Math.round(Math.abs(change))}%`;
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

playPauseBtn.addEventListener("click", () => {
  const willPause = playPauseBtn.textContent === "Pause";
  send({ action: "set_paused", paused: willPause });
  playPauseBtn.textContent = willPause ? "Play" : "Pause"; // optimistic; next broadcast confirms it
});

resetBtn.addEventListener("click", () => {
  send({ action: "reset" });

  // The server resets its own tick count to 0, but this rolling history is purely
  // client-side — without clearing it, the sparkline and averages would keep showing
  // pre-reset data mixed in with the new run until the 150-tick window scrolled past it.
  loadChart.data.labels.length = 0;
  loadChart.data.datasets[0].data.length = 0;
  loadChart.data.datasets[1].data.length = 0;
  loadSmoothedMax = makeSmoothedMax(5);
  loadChart.update("none");
  loadAvgNaiveEl.textContent = "avg 0";
  loadAvgWardenEl.textContent = "avg 0";
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
