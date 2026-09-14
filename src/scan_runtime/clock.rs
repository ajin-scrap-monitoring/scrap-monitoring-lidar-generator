//! Host-epoch monotonic and wall clock boundary.

use std::time::{SystemTime, UNIX_EPOCH};

use super::{Result, ScanRuntimeError};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ClockReading {
    pub unix_ms: i64,
    pub monotonic_ns: u64,
}

pub trait RuntimeClock: Send + Sync + 'static {
    fn read(&self) -> Result<ClockReading>;
}

#[derive(Clone, Copy, Debug, Default)]
pub struct SystemRuntimeClock;

impl RuntimeClock for SystemRuntimeClock {
    fn read(&self) -> Result<ClockReading> {
        let wall = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|_| ScanRuntimeError::Clock("wall clock precedes Unix epoch"))?;
        let unix_ms = i64::try_from(wall.as_millis())
            .map_err(|_| ScanRuntimeError::Clock("wall clock milliseconds exceed i64"))?;
        let monotonic = rustix::time::clock_gettime(rustix::time::ClockId::Monotonic);
        let seconds = u64::try_from(monotonic.tv_sec)
            .map_err(|_| ScanRuntimeError::Clock("host monotonic seconds are negative"))?;
        let nanoseconds = u64::try_from(monotonic.tv_nsec)
            .map_err(|_| ScanRuntimeError::Clock("host monotonic nanoseconds are negative"))?;
        let monotonic_ns = seconds
            .checked_mul(1_000_000_000)
            .and_then(|value| value.checked_add(nanoseconds))
            .ok_or(ScanRuntimeError::Clock(
                "host monotonic nanoseconds exceed u64",
            ))?;
        Ok(ClockReading {
            unix_ms,
            monotonic_ns,
        })
    }
}
