# Warden 🤖

Warden is a warehouse-robot coordination demo that reduces unnecessary server checks during collision avoidance.

It compares two approaches:

- **Baseline** — every robot asks a central coordinator before moving.
- **Warden** — robots check a small local Ribbon filter first and contact the coordinator only when a cell may be claimed.

Warden is a portfolio project and simulation, not a production distributed system.

## How it works 🧭

Before entering a grid cell, a robot checks its local filter.

```text
Robot wants to move
        |
        v
Check local Ribbon filter
        |
        +-- Clearly free --> move immediately
        |
        +-- Maybe claimed --> ask coordinator --> move or reject
```

The coordinator uses a ring buffer as its record of recent claims. The grid’s live occupancy check has the final word on every move, so an incorrect local guess results in a rejected move, not a collision.

## Why it helps ⚡

Most warehouse cells are empty most of the time. Warden handles those routine checks locally instead of sending every move to the coordinator.

| Density | Robots | Instant moves | Fewer coordinator checks |
| --- | ---: | ---: | ---: |
| Sparse | 10 | 94.9% | 85.9% |
| Moderate | 50 | 85.4% | 66.0% |
| Crowded | 120 | 70.7% | 48.7% |

As the floor gets more crowded, more cells need confirmation and Warden’s advantage shrinks. The filter can also be overly cautious, causing an extra server check for a free cell.

## Built with 🛠️

- **Rust** for the Ribbon filter and ring buffer
- **PyO3** to connect Rust with the Python simulation
- **Python** for robot movement, coordination logic, and benchmarks
- **WebSockets, Canvas, and Chart.js** for the live dashboard

## Run it

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Build the Rust extension
(cd core-rs && maturin develop)

# Start the simulation server
python -m server.ws_server
```

Open `web/index.html` in a browser. The dashboard compares Warden with the baseline using the same robot positions and movement seed.

### Run it with Docker

```bash
docker build -t warden .
docker run --name warden -p 8765:8765 warden
```

Open `http://localhost:8765` in a browser — the container serves the dashboard itself.

Turn it off and remove everything (container + image):

```bash
docker rm -f warden && docker rmi warden
```

## Tests and benchmarks

```bash
pytest

# From core-rs/
cargo test

# Run all benchmark scenarios
python -m sim.scenarios
```

The benchmark runs 5 seeds at 10, 50, and 120 robots for both Warden and the baseline. Results are saved to `results/benchmark_results.csv` and `results/coordinator_load.png`.

## Limits

- Network calls are simulated, not real.
- The project uses one coordinator and no sharding.
- It is a single-process simulation, so its collision guarantee does not directly represent a real distributed warehouse system.