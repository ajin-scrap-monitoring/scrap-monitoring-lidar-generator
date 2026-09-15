//! Sensor-local latest-two serial buffer.

use std::{collections::VecDeque, sync::Arc};

use tokio::sync::{Mutex, Notify};

#[derive(Clone, Copy, Debug, Eq, PartialEq, thiserror::Error)]
pub enum RingError {
    #[error("scan stream has stopped")]
    Closed,
    #[error("scan publication serial is exhausted")]
    SerialExhausted,
}

#[derive(Debug)]
pub struct RingDelivery<T> {
    pub serial: u64,
    pub item: Arc<T>,
    pub lost: u64,
}

#[derive(Debug)]
struct State<T> {
    serial: u64,
    closed: bool,
    entries: VecDeque<(u64, Arc<T>)>,
}

#[derive(Debug)]
struct Shared<T> {
    state: Mutex<State<T>>,
    changed: Notify,
}

#[derive(Debug)]
pub struct LatestTwo<T> {
    shared: Arc<Shared<T>>,
}

impl<T> Clone for LatestTwo<T> {
    fn clone(&self) -> Self {
        Self {
            shared: Arc::clone(&self.shared),
        }
    }
}

impl<T> Default for LatestTwo<T> {
    fn default() -> Self {
        Self::new()
    }
}

impl<T> LatestTwo<T> {
    pub fn new() -> Self {
        Self {
            shared: Arc::new(Shared {
                state: Mutex::new(State {
                    serial: 0,
                    closed: false,
                    entries: VecDeque::with_capacity(2),
                }),
                changed: Notify::new(),
            }),
        }
    }

    pub async fn publish(&self, item: T) -> Result<u64, RingError> {
        let mut state = self.shared.state.lock().await;
        if state.closed {
            return Err(RingError::Closed);
        }
        let serial = state
            .serial
            .checked_add(1)
            .ok_or(RingError::SerialExhausted)?;
        state.serial = serial;
        if state.entries.len() == 2 {
            state.entries.pop_front();
        }
        state.entries.push_back((serial, Arc::new(item)));
        drop(state);
        self.shared.changed.notify_waiters();
        Ok(serial)
    }

    pub async fn next_after(&self, cursor: u64) -> Option<RingDelivery<T>> {
        loop {
            let changed = self.shared.changed.notified();
            {
                let state = self.shared.state.lock().await;
                if let Some((serial, item)) =
                    state.entries.iter().find(|(serial, _)| *serial > cursor)
                {
                    let oldest = state
                        .entries
                        .front()
                        .expect("an available entry implies a non-empty ring")
                        .0;
                    return Some(RingDelivery {
                        serial: *serial,
                        item: Arc::clone(item),
                        lost: oldest.saturating_sub(cursor).saturating_sub(1),
                    });
                }
                if state.closed {
                    return None;
                }
            }
            changed.await;
        }
    }

    pub async fn close(&self) {
        let mut state = self.shared.state.lock().await;
        state.closed = true;
        drop(state);
        self.shared.changed.notify_waiters();
    }

    pub async fn is_closed(&self) -> bool {
        self.shared.state.lock().await.closed
    }
}
