//! Per-robot local "probably claimed" check, built from the `ribbon-filter` crate
//! (SIGMOD 2021 Ribbon filter: <https://arxiv.org/abs/2103.02515>). Static structure —
//! rebuilt from scratch on each periodic broadcast refresh (see build spec §6 Phase 3
//! step 2), so a full-rebuild API is the right fit, not incremental insert/delete.
//!
//! No false negatives against the key set it was built from — that guarantee only holds
//! for what the filter has been told; a key claimed after the last rebuild is invisible
//! to it until the next refresh (see build spec §6 Phase 3 step 6, the staleness amendment).

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use ribbon_filter::{Mode, Params, RibbonBuilder, RibbonFilter as InnerFilter};
use std::collections::hash_map::DefaultHasher;
use std::hash::BuildHasherDefault;

type Cell = (i32, i32);
type Hasher = BuildHasherDefault<DefaultHasher>;

/// Ribbon width and space overhead are implementation constants, not tuned per call —
/// see build spec §6 Phase 4 step 2 for where these get tuned against memory footprint.
const RIBBON_WIDTH: usize = 16;
const SPACE_OVERHEAD: f64 = 0.10;
const RETRY_LIMIT: usize = 8;
const GROW_LIMIT: usize = 4;

pub struct CellFilter {
    inner: InnerFilter<Hasher>,
}

impl CellFilter {
    pub fn build(cells: &[Cell], target_fpr: f64) -> Result<Self, String> {
        let r = Params::r_from_fpr(target_fpr).map_err(|e| e.to_string())?;
        let n = cells.len().max(1);
        let params = Params::from_expected_items(n, SPACE_OVERHEAD, RIBBON_WIDTH, r, Mode::Standard)
            .map_err(|e| e.to_string())?
            .with_retry_policy(RETRY_LIMIT, GROW_LIMIT)
            .map_err(|e| e.to_string())?;
        let builder = RibbonBuilder::new(params, Hasher::default()).map_err(|e| e.to_string())?;
        let filter = builder.build(cells).map_err(|e| e.to_string())?;
        Ok(Self { inner: filter })
    }

    pub fn contains(&self, cell: Cell) -> bool {
        self.inner.contains(&cell)
    }
}

#[pyclass(name = "RibbonFilter")]
pub struct PyRibbonFilter {
    inner: CellFilter,
}

#[pymethods]
impl PyRibbonFilter {
    /// Builds a filter from the full set of currently-claimed cells (e.g. from
    /// `RingBuffer.claimed_cells()`). Call again on each refresh to rebuild.
    #[new]
    #[pyo3(signature = (cells, target_fpr=0.01))]
    fn new(cells: Vec<Cell>, target_fpr: f64) -> PyResult<Self> {
        CellFilter::build(&cells, target_fpr)
            .map(|inner| Self { inner })
            .map_err(PyValueError::new_err)
    }

    /// True = "maybe claimed" (confirm-check needed). False = "definitely free."
    fn contains(&self, cell: Cell) -> bool {
        self.inner.contains(cell)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cells(n: i32) -> Vec<Cell> {
        (0..n).map(|i| (i, i * 2)).collect()
    }

    #[test]
    fn no_false_negatives_for_inserted_keys() {
        let keys = cells(2000);
        let filter = CellFilter::build(&keys, 0.01).expect("build");
        for &key in &keys {
            assert!(filter.contains(key), "false negative for {key:?}");
        }
    }

    #[test]
    fn measured_fp_rate_near_target() {
        let keys = cells(5000);
        let target_fpr = 0.01;
        let filter = CellFilter::build(&keys, target_fpr).expect("build");

        // Non-inserted keys: large negative x/y range, disjoint from `cells(5000)`.
        let probes = 20_000;
        let false_positives = (0..probes)
            .map(|i| (-1_000_000 - i, -1_000_000 - i * 2))
            .filter(|&probe| filter.contains(probe))
            .count();
        let measured_fpr = false_positives as f64 / probes as f64;

        // Loose bound (3x target) — this is a statistical check on a probabilistic
        // structure, not an exact one; it catches gross misconfiguration, not tuning noise.
        assert!(
            measured_fpr < target_fpr * 3.0,
            "measured FP rate {measured_fpr} far exceeds target {target_fpr}"
        );
    }

    #[test]
    fn empty_key_set_builds_and_matches_nothing() {
        let filter = CellFilter::build(&[], 0.01).expect("build");
        assert!(!filter.contains((0, 0)));
        assert!(!filter.contains((5, 5)));
    }
}
