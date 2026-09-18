# Warden — Todo

Flat checklist view of `tasks/plan.md`, in dependency order. Parallel pairs noted inline.

- [x] **Task 0** — Repo & toolchain scaffold (Cargo/PyO3/maturin, dirs, requirements.txt, pytest config)
- [x] **Task 1** — `ring_buffer.rs` + PyO3 binding *(parallel with Task 2)*
- [x] **Task 2** — `ribbon_filter.rs` + PyO3 binding *(parallel with Task 1)*
- [x] **Task 3** — Grid world + ground-truth collision detection (Phase 1)
- [ ] **Checkpoint 1** — review PyO3 boundary shape (after Task 0/1/2)
- [ ] **Checkpoint 2** — confirm Phase 1 ground-truth check is clean (after Task 3)
- [x] **Task 4** — Naive coordinator end-to-end (Phase 2)
- [ ] **Task W0** — WS plumbing + bare canvas viewer *(parallel, needs only Task 3)*
- [ ] **Task W1** — Naive-mode dashboard wiring *(parallel, needs Task 4)*
- [ ] **Checkpoint 3** — lock logging/metrics schema (after Task 4)
- [x] **Task 5** — Warden mode end-to-end (Phase 3 core)
- [x] **Task 6** — Adversarial staleness test + documented finding (Phase 3 amendment)
- [ ] **Checkpoint 4** — review staleness finding, decide Task 11's design (after Task 6) — most important checkpoint
- [x] **Task 7** — Benchmark harness (Phase 4)
- [ ] **Checkpoint 5** — sanity-check headline figures are measured, not estimated (after Task 7)
- [ ] **Task 8** — Floor renderer + robot-count slider + mode toggle (Phase 5.1)
- [ ] **Checkpoint 6** — confirm WS schema stable before Tasks 10/11 (after Task 8)
- [ ] **Task 9** — Chart.js dashboard + density/stress control (Phase 5.2 + 5.6)
- [ ] **Task 10** — Split-screen mode (Phase 5.3)
- [ ] **Task 11** — Broadcast-lag control (Phase 5 amendment)
- [ ] **Task 12** — Visual design pass (Phase 5.8)
