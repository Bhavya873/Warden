# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

This repository currently contains only a design document and a README — no implementation exists yet. There is no build system, no dependencies, no lint config, and no tests to run. When code is added, this file should be updated with the actual commands (build/lint/test/run a single test) and any structure that spans multiple files.

## What this project is

Warden is a collision-free zone-claiming scheme for coordinating a fleet of warehouse robots sharing one floor grid. The full design rationale, architecture, and demo spec live in `Warden — Collision-Free Zone Claiming for Warehouse Robot Fleets.md` — read that file before implementing anything here, since the details below are only a summary.

### Core idea

Before a robot enters a grid cell, it must know whether another robot is about to occupy it — without asking every robot (or a central coordinator) on every single move, which floods the network. The design exploits the fact that most moves are uncontested, so the system should pay almost no cost in the common case and only pay for a real network check when there's genuine chance of conflict.

### Architecture (two-tier fast-reject-then-confirm)

1. **Per-robot Ribbon filter** — a local, in-memory approximate membership structure representing "cells I believe are currently claimed by another robot." Zero false negatives (never misses a real claim), but can have false positives ("maybe claimed" when actually free). Kept fresh via periodic broadcast from every robot announcing cells it occupies or is about to enter.
2. **Coordinator-held ring buffer** — the exact ground truth: currently-claimed cells, each with a short expiry so claims fall off automatically as robots move on. Fixed-size, O(1) insert/evict, bounded memory regardless of fleet size.

Decision flow per move:
- Local filter says **definitely free** → proceed immediately, no network call (the overwhelming majority of moves).
- Local filter says **maybe claimed** → only then send a confirm-check to the coordinator (or the robot believed to hold the cell) before proceeding.

This mirrors the fast-reject-then-confirm pattern from prior duplicate-detection work (referenced in the doc as the PRP duplicate-packet and Tesla perception-dedup projects): the exact structure (ring buffer) only has to answer the rare "maybe" case; the approximate structure (Ribbon filter) absorbs nearly all traffic.

### Demo requirements (from the design doc)

The intended demo is a visual warehouse floor-grid simulation with:
- Robots as moving dots, color-flashed per move: green (filter said free, no network call), yellow (filter said maybe, confirm round-trip shown), red (confirm found a real conflict, one robot waits).
- Live counters: instant moves vs. confirmed checks vs. conflicts avoided.
- A robot-density slider and a toggleable "naive mode" (every move confirmed over the network, no filter) for a side-by-side comparison showing Warden staying mostly green under load while naive mode floods the coordinator.

When implementing, treat the correctness invariant as non-negotiable: the Ribbon filter must never produce a false negative (a claimed cell reported as free), since that's the one failure mode that causes an actual collision. False positives (unnecessary confirm-checks) are acceptable and expected.
