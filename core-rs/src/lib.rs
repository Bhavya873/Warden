use pyo3::prelude::*;

mod ribbon_filter;
mod ring_buffer;

use ribbon_filter::PyRibbonFilter;
use ring_buffer::PyRingBuffer;

#[pyfunction]
fn ping() -> &'static str {
    "pong"
}

#[pymodule]
fn warden_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(ping, m)?)?;
    m.add_class::<PyRingBuffer>()?;
    m.add_class::<PyRibbonFilter>()?;
    Ok(())
}
