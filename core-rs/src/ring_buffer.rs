//! Fixed-capacity ring buffer of cell claims. Ground truth for `confirm_check`:
//! always correct, at the cost of being too slow to query on every move.

use std::collections::HashMap;

use pyo3::prelude::*;

pub type Cell = (i32, i32);

#[derive(Clone, Copy)]
struct Entry {
    cell: Cell,
    // Stored per the spec's (cell, robot_id, expiry_tick) shape; not yet read internally —
    // a future confirm-check response or "who holds this cell" query would consume it.
    #[allow(dead_code)]
    robot_id: u32,
    expiry_tick: u64,
}

pub struct RingBuffer {
    slots: Vec<Option<Entry>>,
    write_head: usize,
    current_tick: u64,
    // Secondary index: cell -> slot indices currently holding it. `slots` stays the
    // source of truth for ordering/eviction; this only exists so is_claimed/release/
    // claimed_cells don't have to scan every slot (fixed at MAX_ROBOT_COUNT * 4 = 4000
    // regardless of how many robots are actually in play) on every call. At high
    // contention that per-request O(capacity) scan was the dashboard's actual
    // density-scaling bottleneck -- confirm-check volume grows with contention, and
    // each one used to pay for a full 4000-slot scan. Usually holds 0-1 entries per
    // cell (occasionally a couple, from a cell being re-claimed before its previous
    // claim expired) -- never proportional to capacity.
    index: HashMap<Cell, Vec<usize>>,
}

impl RingBuffer {
    pub fn new(capacity: usize) -> Self {
        assert!(capacity > 0, "ring buffer capacity must be > 0");
        Self {
            slots: vec![None; capacity],
            write_head: 0,
            current_tick: 0,
            index: HashMap::new(),
        }
    }

    fn unindex(&mut self, cell: Cell, slot: usize) {
        if let Some(indices) = self.index.get_mut(&cell) {
            indices.retain(|&i| i != slot);
            if indices.is_empty() {
                self.index.remove(&cell);
            }
        }
    }

    /// Claims `cell` for `robot_id`, expiring after `ttl_ticks`. Always writes into the
    /// oldest slot (ring buffer semantics) — if the buffer is full, the oldest entry is
    /// evicted regardless of its own remaining TTL.
    pub fn claim(&mut self, cell: Cell, robot_id: u32, ttl_ticks: u64) {
        let expiry_tick = self.current_tick + ttl_ticks;
        let slot = self.write_head;
        if let Some(old_entry) = self.slots[slot] {
            self.unindex(old_entry.cell, slot);
        }
        self.slots[slot] = Some(Entry {
            cell,
            robot_id,
            expiry_tick,
        });
        self.index.entry(cell).or_default().push(slot);
        self.write_head = (self.write_head + 1) % self.slots.len();
    }

    pub fn is_claimed(&self, cell: Cell) -> bool {
        let Some(indices) = self.index.get(&cell) else {
            return false;
        };
        indices.iter().any(|&i| match self.slots[i] {
            Some(entry) => entry.cell == cell && entry.expiry_tick > self.current_tick,
            None => false,
        })
    }

    pub fn release(&mut self, cell: Cell) {
        if let Some(indices) = self.index.remove(&cell) {
            for i in indices {
                self.slots[i] = None;
            }
        }
    }

    /// All currently-claimed (non-expired) cells — used to broadcast ring-buffer contents
    /// to robots' local Ribbon filters.
    pub fn claimed_cells(&self) -> Vec<Cell> {
        self.index
            .keys()
            .copied()
            .filter(|&cell| self.is_claimed(cell))
            .collect()
    }

    /// Advances the buffer's clock and evicts expired entries. Run once per tick, not per
    /// lookup, to keep `is_claimed`/`claimed_cells` cheap.
    pub fn tick(&mut self, current_tick: u64) {
        self.current_tick = current_tick;
        for i in 0..self.slots.len() {
            if let Some(entry) = self.slots[i] {
                if entry.expiry_tick <= current_tick {
                    self.slots[i] = None;
                    self.unindex(entry.cell, i);
                }
            }
        }
    }
}

#[pyclass(name = "RingBuffer")]
pub struct PyRingBuffer {
    inner: RingBuffer,
}

#[pymethods]
impl PyRingBuffer {
    #[new]
    fn new(capacity: usize) -> Self {
        Self {
            inner: RingBuffer::new(capacity),
        }
    }

    fn claim(&mut self, cell: Cell, robot_id: u32, ttl_ticks: u64) {
        self.inner.claim(cell, robot_id, ttl_ticks);
    }

    fn is_claimed(&self, cell: Cell) -> bool {
        self.inner.is_claimed(cell)
    }

    fn release(&mut self, cell: Cell) {
        self.inner.release(cell);
    }

    fn claimed_cells(&self) -> Vec<Cell> {
        self.inner.claimed_cells()
    }

    fn tick(&mut self, current_tick: u64) {
        self.inner.tick(current_tick);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn claim_and_is_claimed() {
        let mut rb = RingBuffer::new(4);
        assert!(!rb.is_claimed((1, 1)));
        rb.claim((1, 1), 7, 10);
        assert!(rb.is_claimed((1, 1)));
        assert!(!rb.is_claimed((2, 2)));
    }

    #[test]
    fn release_clears_claim() {
        let mut rb = RingBuffer::new(4);
        rb.claim((1, 1), 7, 10);
        rb.release((1, 1));
        assert!(!rb.is_claimed((1, 1)));
    }

    #[test]
    fn ttl_expiry_via_tick() {
        let mut rb = RingBuffer::new(4);
        rb.claim((1, 1), 7, 5); // expires at tick 5
        rb.tick(4);
        assert!(rb.is_claimed((1, 1)));
        rb.tick(5);
        assert!(!rb.is_claimed((1, 1)));
    }

    #[test]
    fn capacity_eviction_oldest_first() {
        let mut rb = RingBuffer::new(2);
        rb.claim((0, 0), 1, 100);
        rb.claim((1, 1), 2, 100);
        // buffer full (capacity 2) — next claim evicts the oldest slot, (0,0)
        rb.claim((2, 2), 3, 100);
        assert!(!rb.is_claimed((0, 0)));
        assert!(rb.is_claimed((1, 1)));
        assert!(rb.is_claimed((2, 2)));
    }

    #[test]
    fn mixed_claim_release_expire_sequence() {
        let mut rb = RingBuffer::new(3);
        rb.claim((0, 0), 1, 3); // expires at tick 3
        rb.claim((1, 1), 2, 100);
        rb.tick(1);
        rb.release((1, 1));
        rb.claim((2, 2), 3, 100);
        assert!(rb.is_claimed((0, 0))); // not yet expired
        assert!(!rb.is_claimed((1, 1))); // released
        assert!(rb.is_claimed((2, 2)));
        rb.tick(3);
        assert!(!rb.is_claimed((0, 0))); // now expired
    }

    #[test]
    fn claimed_cells_excludes_expired() {
        let mut rb = RingBuffer::new(4);
        rb.claim((0, 0), 1, 2);
        rb.claim((1, 1), 2, 100);
        rb.tick(2);
        let cells = rb.claimed_cells();
        assert_eq!(cells, vec![(1, 1)]);
    }
}
