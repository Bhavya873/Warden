// Renders the two floor grids (Baseline vs. Warden — the only view; no mode selector),
// connects to the WS server, wires up the live controls, and drives the Chart.js
// dashboard. Message schema: see server/ws_server.py's module docstring — the server
// always sends {"type": "tick_split", "boards": {"naive": {...}, "warden": {...}}}.
// "naive" is the wire-protocol/internal name (unchanged); "Baseline" is just the
// user-facing label for it, renamed because "Naive" read as unclear/judgmental.

const WS_URL = "wss://warden-warehouse-robots.up.railway.app";
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

// The canvases are fluid (width:100%; height:auto in style.css) but their backing-
// store pixel size was a hardcoded 460x460 attribute -- the browser was rescaling that
// bitmap to fit the actual (different, viewport-dependent) layout size on every paint,
// and with no devicePixelRatio handling the result was blurry on top of that. Syncing
// the backing store to the element's real on-screen size (in device pixels) removes
// both problems; drawFloor's cellSize math already derives from canvas.width, so no
// ctx.scale() is needed on top of this.
function syncCanvasBackingSize(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const size = Math.round(canvas.clientWidth * dpr);
  if (size > 0 && canvas.width !== size) {
    canvas.width = size;
    canvas.height = size; // square canvas, CSS keeps it square via height:auto
    gridBackground.gridSize = null; // cache was sized for the old backing-store width
  }
}
const canvasResizeObserver = new ResizeObserver((entries) => {
  for (const entry of entries) syncCanvasBackingSize(entry.target);
});
canvasResizeObserver.observe(floorNaiveCanvas);
canvasResizeObserver.observe(floorWardenCanvas);

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

// Derived from the whole visible rolling window (LOAD_CHART_WINDOW ticks), not just
// the latest tick's value -- a single noisy tick can't move it, since one point among
// 150 barely shifts the window's max, but a real sustained change (e.g. dragging the
// Robots slider down) shrinks it once the old high values scroll out of the window,
// and a real spike grows it immediately. Always a whole-number multiple of
// CPU_AXIS_STEP, so the axis labels are always whole numbers.
let cpuAxisMax = CPU_AXIS_STEP; // reassigned on reset — see resetBtn handler
function updateCpuAxisMax() {
  const windowMax = Math.max(0, ...loadChart.data.datasets[0].data, ...loadChart.data.datasets[1].data);
  let needed = CPU_AXIS_STEP;
  while (windowMax >= needed) {
    needed += CPU_AXIS_STEP;
  }
  cpuAxisMax = needed;
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

  // The server keeps broadcasting the same frozen state every tick while paused (so
  // controls stay responsive), which would otherwise push duplicate points onto the
  // rolling window and make the sparkline visibly scroll even though nothing changed.
  if (!state.paused) {
    pushRolling(loadChart.data.datasets[0].data, stepMs(naive));
    pushRolling(loadChart.data.datasets[1].data, stepMs(warden));
    pushRolling(loadChart.data.labels, naive.tick); // both boards are stepped together, one shared timeline
    updateCpuAxisMax();
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
// per (gridSize, width) instead -- both floor canvases share the cache when they're the
// same size, which is the common case, but width is now part of the key (not just
// gridSize) since the backing-store size is synced to the fluid layout and can change
// independently of gridSize (see syncCanvasBackingSize above), unlike when it was a
// fixed 460x460 constant. Includes the panel-colored background fill too, replacing the
// per-tick clearRect.
let gridBackground = { gridSize: null, width: null, canvas: null };

function getGridBackground(gridSize, width, height) {
  if (gridBackground.gridSize === gridSize && gridBackground.width === width && gridBackground.canvas) {
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

  gridBackground = { gridSize, width, canvas: bg };
  return bg;
}

function drawFloor(ctx, canvas, gridSize, robots) {
  const cellSize = canvas.width / gridSize;
  ctx.drawImage(getGridBackground(gridSize, canvas.width, canvas.height), 0, 0);

  const radius = cellSize * 0.32;

  // Batched by style via Path2D, built in a single walk over `robots` -- the previous
  // version walked the array 7 times (halo, 4 outcome buckets, direction, near-miss),
  // recomputing each robot's cx/cy fresh every time. At max robot count / grid size that
  // was 7x the array-scan and coordinate-math cost for no drawing benefit -- Path2D lets
  // several paths accumulate concurrently, so one pass fills all of them, then a fixed
  // handful of fill()/stroke() calls draws the batches.
  const haloPath = new Path2D();
  const outcomePaths = { moved: new Path2D(), waiting: new Path2D(), conflict: new Path2D(), idle: new Path2D() };
  const directionPath = new Path2D();
  const nearMissPath = new Path2D();
  let anyMoving = false;
  let anyNearMiss = false;

  for (const robot of robots) {
    const cx = robot.x * cellSize + cellSize / 2;
    const cy = robot.y * cellSize + cellSize / 2;

    haloPath.moveTo(cx + radius + 1.5, cy);
    haloPath.arc(cx, cy, radius + 1.5, 0, Math.PI * 2);

    const outcomePath = outcomePaths[robot.outcome] ?? outcomePaths.idle;
    outcomePath.moveTo(cx + radius, cy);
    outcomePath.arc(cx, cy, radius, 0, Math.PI * 2);

    if (robot.dx !== 0 || robot.dy !== 0) {
      anyMoving = true;
      directionPath.moveTo(cx, cy);
      directionPath.lineTo(cx + robot.dx * radius * 1.5, cy + robot.dy * radius * 1.5);
    }

    // Near-miss ring: the filter/coordinator approved this move but ground truth caught
    // it — a distinct ring, not red, so it isn't mistaken for a caught conflict.
    if (robot.near_miss) {
      anyNearMiss = true;
      nearMissPath.moveTo(cx + radius * 1.7, cy);
      nearMissPath.arc(cx, cy, radius * 1.7, 0, Math.PI * 2);
    }
  }

  ctx.fillStyle = COLORS.panel;
  ctx.fill(haloPath);

  for (const outcome in OUTCOME_COLORS) {
    ctx.fillStyle = OUTCOME_COLORS[outcome];
    ctx.fill(outcomePaths[outcome]);
  }

  if (anyMoving) {
    ctx.strokeStyle = COLORS.ink;
    ctx.lineWidth = 1.5;
    ctx.lineCap = "round";
    ctx.stroke(directionPath);
  }

  if (anyNearMiss) {
    ctx.strokeStyle = COLORS.nearMiss;
    ctx.lineWidth = 2.5;
    ctx.stroke(nearMissPath);
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
