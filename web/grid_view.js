// Renders the two floor grids (Baseline vs. Warden — the only view; no mode selector),
// connects to the WS server, wires up the live controls, and drives the Chart.js
// dashboard. Message schema: see server/ws_server.py's module docstring — the server
// always sends {"type": "tick_split", "boards": {"naive": {...}, "warden": {...}}}.
// "naive" is the wire-protocol/internal name (unchanged); "Baseline" is just the
// user-facing label for it, renamed because "Naive" read as unclear/judgmental.

const WS_URL = "ws://localhost:8765";
const CPU_AXIS_STEP = 5; // ms -- the CPU-usage graph's y-axis grows/shrinks in steps of this
const LOAD_CHART_WINDOW = 150; // ticks of history kept for the rolling line chart
const TICK_BUDGET_MS = 100; // matches server/ws_server.py's TICK_INTERVAL_SECONDS

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

// Rolling history for Network queue's average, same window as the CPU-usage chart
// data (LOAD_CHART_WINDOW ticks) -- not charted itself, just averaged for its row.
const queueHistory = { naive: [], warden: [] };

const compareEls = {
  naive: {
    moves: document.getElementById("cmp-naive-moves"),
    queue: document.getElementById("cmp-naive-queue"),
    conflicts: document.getElementById("cmp-naive-conflicts"),
  },
  warden: {
    moves: document.getElementById("cmp-warden-moves"),
    queue: document.getElementById("cmp-warden-queue"),
    conflicts: document.getElementById("cmp-warden-conflicts"),
  },
};

const reductionPctEl = document.getElementById("cmp-reduction-pct");
const reductionWordEl = document.getElementById("cmp-reduction-word");

let socket = null;

// --- Chart: a single, unobtrusive CPU-usage sparkline. The comparison table covers
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
        max: CPU_AXIS_STEP, // overwritten in render() as data demands; this is just the initial value
        grid: { display: false },
        ticks: { stepSize: CPU_AXIS_STEP, font: { size: 13 }, callback: (v) => `${v}ms` },
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

// Server CPU usage, in ms: stats.server_seconds is real wall-clock time
// (time.perf_counter(), measured inside core/coordinator.py itself) spent on genuine
// server-side work only -- the coordinator's own tick()/can_move() logic. Deliberately
// excludes Warden's local Ribbon-filter checks and periodic rebuild, which are
// robot-side work in the real architecture (each robot holds its own filter and checks
// it locally before ever contacting the server) -- an earlier version of this timed
// the whole per-tick step instead, which wrongly made Warden look more expensive by
// charging it for robot-side compute the server never actually does.
//
// Each board's own number, not a share of the two combined -- a "share" (own / (own +
// other's)) always sums to 100%, forcing the two lines into perfect mirror images of
// each other regardless of what's actually happening. Plotting each board's raw time
// keeps them genuinely independent on the chart, the way the simulations actually are.
function stepMs(board) {
  return board.stats.server_seconds * 1000;
}

// The CPU-usage graph's y-axis max grows or shrinks by exactly CPU_AXIS_STEP at a time
// -- up when the data reaches or exceeds it, down when it's comfortably (a full step)
// below -- instead of a single fixed constant (which clips at high robot counts) or a
// continuously-smoothed value (which isn't a "fixed" scale at all). Always a whole-
// number multiple of CPU_AXIS_STEP, so the axis labels are always whole numbers too.
let cpuAxisMax = CPU_AXIS_STEP; // reassigned on reset — see resetBtn handler
function updateCpuAxisMax(dataMax) {
  while (dataMax >= cpuAxisMax) {
    cpuAxisMax += CPU_AXIS_STEP;
  }
  while (cpuAxisMax > CPU_AXIS_STEP && dataMax < cpuAxisMax - CPU_AXIS_STEP) {
    cpuAxisMax -= CPU_AXIS_STEP;
  }
  loadChart.options.scales.y.max = cpuAxisMax;
}

// The table row shows this as a % of the server's fixed 100ms tick budget -- a real,
// independent-per-board percentage (each measured against the same fixed denominator),
// unlike the earlier "share of the two combined" version, which was mathematically
// forced to always sum to 100% between the two boards. The graph itself keeps plotting
// raw ms, since that's what has an actual fixed y-axis scale (0/1/2ms) to read against.
function tickBudgetPct(ms) {
  return (ms / TICK_BUDGET_MS) * 100;
}

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

  updateCpuAxisMax(Math.max(stepMs(naive), stepMs(warden)));

  // The server keeps broadcasting the same frozen state every tick while paused (so
  // controls stay responsive), which would otherwise push duplicate points onto the
  // rolling window and make the sparkline visibly scroll even though nothing changed.
  if (!state.paused) {
    pushRolling(loadChart.data.datasets[0].data, stepMs(naive));
    pushRolling(loadChart.data.datasets[1].data, stepMs(warden));
    pushRolling(loadChart.data.labels, naive.tick); // both boards are stepped together, one shared timeline
    loadChart.update("none");
    loadAvgNaiveEl.textContent = `${tickBudgetPct(average(loadChart.data.datasets[0].data)).toFixed(2)}%`;
    loadAvgWardenEl.textContent = `${tickBudgetPct(average(loadChart.data.datasets[1].data)).toFixed(2)}%`;

    pushRolling(queueHistory.naive, naive.stats.queue_depth);
    pushRolling(queueHistory.warden, warden.stats.queue_depth);
  }

  statTick.textContent = `(tick ${naive.tick})`;
  updateCompareRow(compareEls.naive, naive, queueHistory.naive);
  updateCompareRow(compareEls.warden, warden, queueHistory.warden);
  updateReductionHero(naive, warden);

  syncControls(naive.robot_count, state.grid_size, state.broadcast_lag);

  // Don't stomp the label mid-click — harmless either way since the next broadcast
  // (100ms later) re-syncs it, but avoids a visible flicker right after clicking.
  if (document.activeElement !== playPauseBtn) {
    playPauseBtn.textContent = state.paused ? "Play" : "Pause";
  }
}

function updateCompareRow(els, board, queueHist) {
  els.moves.textContent = board.totals.instant_moves + board.totals.confirmed_checks;
  els.queue.textContent = average(queueHist).toFixed(1);
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

// Gridlines never change tick-to-tick (only gridSize does, on a config change) but were
// being fully re-stroked every single tick -- 2*(gridSize+1) separate stroke() calls,
// wasted work at every tick regardless of grid size. Cached to an offscreen canvas once
// per gridSize instead; both floor canvases share the cache since they're always the
// same size and gridSize. Includes the panel-colored background fill too, replacing the
// per-tick clearRect.
let gridBackground = { gridSize: null, canvas: null };

function getGridBackground(gridSize, width, height) {
  if (gridBackground.gridSize === gridSize && gridBackground.canvas) {
    return gridBackground.canvas;
  }
  const bg = document.createElement("canvas");
  bg.width = width;
  bg.height = height;
  const bgCtx = bg.getContext("2d");
  const cellSize = width / gridSize;

  bgCtx.fillStyle = COLORS.panel;
  bgCtx.fillRect(0, 0, width, height);

  bgCtx.strokeStyle = COLORS.line;
  bgCtx.lineWidth = 1;
  bgCtx.beginPath(); // one path for every line, one stroke() call total, not one per line
  for (let i = 0; i <= gridSize; i++) {
    const p = Math.round(i * cellSize) + 0.5; // crisp 1px lines, not antialiased blur
    bgCtx.moveTo(p, 0);
    bgCtx.lineTo(p, height);
    bgCtx.moveTo(0, p);
    bgCtx.lineTo(width, p);
  }
  bgCtx.stroke();

  gridBackground = { gridSize, canvas: bg };
  return bg;
}

function drawFloor(ctx, canvas, gridSize, robots) {
  const cellSize = canvas.width / gridSize;
  ctx.drawImage(getGridBackground(gridSize, canvas.width, canvas.height), 0, 0);

  const radius = cellSize * 0.32;

  // Batched by style: one path with every robot's arc/line in it, then one fill() or
  // stroke() call for the whole batch, instead of up to 4 separate canvas calls PER
  // ROBOT (which is what this used to do — a real bottleneck at hundreds of robots,
  // since canvas draw-call overhead dominates over the trivial per-robot math).

  // Halo: same color for every robot, so it's just one pass regardless of outcome.
  ctx.fillStyle = COLORS.panel;
  ctx.beginPath();
  for (const robot of robots) {
    const cx = robot.x * cellSize + cellSize / 2;
    const cy = robot.y * cellSize + cellSize / 2;
    ctx.moveTo(cx + radius + 1.5, cy);
    ctx.arc(cx, cy, radius + 1.5, 0, Math.PI * 2);
  }
  ctx.fill();

  // Main dot: one pass per outcome color (at most 4 fill() calls total, not one per robot).
  for (const outcome in OUTCOME_COLORS) {
    ctx.fillStyle = OUTCOME_COLORS[outcome];
    ctx.beginPath();
    let any = false;
    for (const robot of robots) {
      if ((robot.outcome in OUTCOME_COLORS ? robot.outcome : "idle") !== outcome) continue;
      any = true;
      const cx = robot.x * cellSize + cellSize / 2;
      const cy = robot.y * cellSize + cellSize / 2;
      ctx.moveTo(cx + radius, cy);
      ctx.arc(cx, cy, radius, 0, Math.PI * 2);
    }
    if (any) ctx.fill();
  }

  // Direction line: one pass for every moving robot.
  ctx.strokeStyle = COLORS.ink;
  ctx.lineWidth = 1.5;
  ctx.lineCap = "round";
  ctx.beginPath();
  let anyMoving = false;
  for (const robot of robots) {
    if (robot.dx === 0 && robot.dy === 0) continue;
    anyMoving = true;
    const cx = robot.x * cellSize + cellSize / 2;
    const cy = robot.y * cellSize + cellSize / 2;
    ctx.moveTo(cx, cy);
    ctx.lineTo(cx + robot.dx * radius * 1.5, cy + robot.dy * radius * 1.5);
  }
  if (anyMoving) ctx.stroke();

  // Near-miss ring: the filter/coordinator approved this move but ground truth caught
  // it — a distinct ring, not red, so it isn't mistaken for a caught conflict.
  ctx.strokeStyle = COLORS.nearMiss;
  ctx.lineWidth = 2.5;
  ctx.beginPath();
  let anyNearMiss = false;
  for (const robot of robots) {
    if (!robot.near_miss) continue;
    anyNearMiss = true;
    const cx = robot.x * cellSize + cellSize / 2;
    const cy = robot.y * cellSize + cellSize / 2;
    ctx.moveTo(cx + radius * 1.7, cy);
    ctx.arc(cx, cy, radius * 1.7, 0, Math.PI * 2);
  }
  if (anyNearMiss) ctx.stroke();
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
  cpuAxisMax = CPU_AXIS_STEP;
  loadChart.options.scales.y.max = cpuAxisMax;
  loadChart.update("none");
  loadAvgNaiveEl.textContent = "0.00%";
  loadAvgWardenEl.textContent = "0.00%";
  queueHistory.naive.length = 0;
  queueHistory.warden.length = 0;
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
