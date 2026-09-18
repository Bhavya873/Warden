//! Fixed-capacity ring buffer of cell claims. Ground truth for `confirm_check`:
//! always correct, at the cost of being too slow to query on every move.

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
}

impl RingBuffer {
    pub fn new(capacity: usize) -> Self {
        assert!(capacity > 0, "ring buffer capacity must be > 0");
        Self {
            slots: vec![None; capacity],
            write_head: 0,
            current_tick: 0,
        }
    }

    /// Claims `cell` for `robot_id`, expiring after `ttl_ticks`. Always writes into the
    /// oldest slot (ring buffer semantics) — if the buffer is full, the oldest entry is
    /// evicted regardless of its own remaining TTL.
    pub fn claim(&mut self, cell: Cell, robot_id: u32, ttl_ticks: u64) {
        let expiry_tick = self.current_tick + ttl_ticks;
        self.slots[self.write_head] = Some(Entry {
            cell,
            robot_id,
            expiry_tick,
        });
        self.write_head = (self.write_head + 1) % self.slots.len();
    }

    pub fn is_claimed(&self, cell: Cell) -> bool {
        self.slots.iter().any(|slot| match slot {
            Some(entry) => entry.cell == cell && entry.expiry_tick > self.current_tick,
            None => false,
        })
    }

    pub fn release(&mut self, cell: Cell) {
        for slot in self.slots.iter_mut() {
            if slot.is_some_and(|entry| entry.cell == cell) {
                *slot = None;
            }
        }
    }

    /// All currently-claimed (non-expired) cells — used to broadcast ring-buffer contents
    /// to robots' local Ribbon filters.
    pub fn claimed_cells(&self) -> Vec<Cell> {
        self.slots
            .iter()
            .filter_map(|slot| slot.as_ref())
            .filter(|entry| entry.expiry_tick > self.current_tick)
            .map(|entry| entry.cell)
            .collect()
    }

    /// Advances the buffer's clock and evicts expired entries. Run once per tick, not per
    /// lookup, to keep `is_claimed`/`claimed_cells` cheap.
    pub fn tick(&mut self, current_tick: u64) {
        self.current_tick = current_tick;
        for slot in self.slots.iter_mut() {
            if slot.is_some_and(|entry| entry.expiry_tick <= current_tick) {
                *slot = None;
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
