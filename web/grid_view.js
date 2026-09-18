// Renders the floor grid, connects to the WS server, and wires up the live controls.
// Message schema this expects: see server/ws_server.py's module docstring.

const WS_URL = "ws://localhost:8765";

const canvas = document.getElementById("floor");
const ctx = canvas.getContext("2d");

const modeSelect = document.getElementById("mode");
const robotCountInput = document.getElementById("robot-count");
const robotCountValue = document.getElementById("robot-count-value");
const gridSizeInput = document.getElementById("grid-size");
const gridSizeValue = document.getElementById("grid-size-value");
const connectionStatus = document.getElementById("connection-status");

const statTick = document.getElementById("stat-tick");
const statInstant = document.getElementById("stat-instant");
const statConfirmed = document.getElementById("stat-confirmed");
const statQueue = document.getElementById("stat-queue");

let socket = null;

function connect() {
  socket = new WebSocket(WS_URL);

  socket.addEventListener("open", () => {
    connectionStatus.textContent = "connected";
  });

  socket.addEventListener("close", () => {
    connectionStatus.textContent = "disconnected — retrying…";
    setTimeout(connect, 1000);
  });

  socket.addEventListener("error", () => {
    socket.close();
  });

  socket.addEventListener("message", (event) => {
    render(JSON.parse(event.data));
  });
}

function render(state) {
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

  statTick.textContent = state.tick;
  statInstant.textContent = state.stats.instant_moves;
  statConfirmed.textContent = state.stats.confirmed_checks;
  statQueue.textContent = state.stats.queue_depth;

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
