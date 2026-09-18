# Warden — Collision-Free Zone Claiming for Warehouse Robot Fleets

2026-09-17 · @Someone

## The Problem

A fleet of warehouse robots shares one floor grid. Before entering a cell, a robot needs to know whether another robot is about to occupy it. Get this wrong and robots collide or have to emergency-stop; the naive fix (ask every other robot, or a central coordinator, before every single move) doesn't scale — it floods the network and adds latency to every movement decision, at exactly the moment real-time responsiveness matters most.

The honest constraint: most of the time, the cell a robot wants to enter is *not* contested. Only a small fraction of moves happen near another robot. So the system should spend almost no cost confirming the common case (nobody's there) and only pay the cost of a real check when there's a genuine chance of conflict.

This is the same shape of problem as the PRP duplicate-packet and Tesla perception-dedup projects: a high-frequency stream of yes/no questions, where most answers are "no" and a wrong "no" (false negative) is unacceptable, but a wrong "maybe" that gets double-checked is perfectly fine.

## The Solution

Each robot keeps a local **Ribbon filter** representing "cells I believe are currently claimed by another robot," kept fresh by a lightweight periodic broadcast from every robot announcing the cells it currently occupies or is about to enter. Before moving into a cell, a robot checks its own local filter first — no network call needed for the check itself:

- **Filter says "definitely free"** → proceed immediately. This is the overwhelming majority of moves, and it costs nothing but a local, in-memory lookup.
- **Filter says "maybe claimed"** → only now send a quick confirm-check to a lightweight coordinator (or directly to the robot believed to hold that cell) before proceeding.

A **ring buffer**, held by the coordinator (or a small shared service), stores the actual ground truth: the real list of currently-claimed cells, each with a short expiry so a claim automatically falls off as the robot that made it moves on. This is the exact structure the "maybe" case falls back to — small, bounded in size, and always correct, at the cost of being too slow to check on every single move.

**Why this combination and not just one or the other:** the Ribbon filter alone can't be trusted for a real conflict decision, since a false positive is fine but a false negative (missing a real claim) would let robots collide. The ring buffer alone is exact but too slow to query on every move at fleet scale. Pairing them means the exact structure only has to answer the rare "maybe" case, while the filter absorbs almost all of the traffic — the same fast-reject-then-confirm pattern used in real network duplicate-detection systems (e.g., PRP).

## Techniques Used

| Technique | Role | Why it fits |
| --- | --- | --- |
| Ribbon filter ([SIGMOD 2021](https://arxiv.org/abs/2103.02515)) | Fast, local, low-memory "probably claimed" check per robot | Near-optimal memory per key, zero false negatives — safe to trust for the "definitely free" case |
| Ring buffer | Fixed-size, self-expiring ground-truth log of real claims | O(1) insert/evict, bounded memory regardless of fleet size or runtime |
| Fast-reject-then-confirm pattern | Two-stage decision: cheap filter first, expensive exact check only when needed | Keeps the common case (cell is free) nearly free, isolates network/coordination cost to genuine contention |
| Periodic filter refresh / broadcast | Keeps each robot's local filter reasonably current | Avoids a full network round-trip on every move while tolerating some staleness (caught by the confirm step) |

This is the same architectural pattern used in the earlier PRP duplicate-packet project (Ribbon filter as a fast gate in front of an exact structure) — applied here to spatial resource contention instead of packet deduplication.

## Demo

**Visual:** a warehouse floor grid rendered on screen, robots shown as moving dots, each about to enter a cell.

**Per-move color coding:**

- **Green flash** — local filter said "free," robot proceeded instantly, no network call shown.
- **Yellow flash** — filter said "maybe," a small animated ping travels to the coordinator and back before the robot proceeds.
- **Red flash** — the confirm check found a real conflict; one robot waits.

**Live counters:** instant moves vs. confirmed checks vs. conflicts avoided, updating in real time as the simulation runs.

**Stress test:** a robot-density slider. At low density, the grid is almost entirely green. As density climbs, the yellow trickle increases (more genuine near-contention), while a toggled-on "naive mode" — every move confirmed over the network, no filter — visibly floods with network-check animations and the coordinator's confirm queue backs up. The side-by-side comparison (Warden's mostly-green grid vs. naive mode's mostly-yellow, backed-up grid) is the core visual punchline: most of the safety guarantee is delivered for nearly free.

**One-line pitch:** "Watch a 50-robot warehouse floor stay almost entirely collision-check-free — while a naive version drowns the coordinator in the same scenario."

Build spec for an implementation agent: [Implementation Guide](file/25374490-7726)
