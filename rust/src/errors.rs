//! Transport errors shared by every layer, so only the USB layer needs to
//! know about the backend's own exceptions.

use std::error::Error;
use std::fmt;

/// A transport command failed.
#[derive(Debug, Clone)]
pub struct TransportError {
    pub message: String,
}

impl TransportError {
    pub fn new(message: impl Into<String>) -> Self {
        Self { message: message.into() }
    }
}

impl fmt::Display for TransportError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}

impl Error for TransportError {}

/// The command was not accepted (out of turn / peer not ready) or did not
/// finish in time.
#[derive(Debug, Clone)]
pub struct TransportTimeout(pub TransportError);

impl fmt::Display for TransportTimeout {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.0.fmt(f)
    }
}

impl Error for TransportTimeout {}

/// The firmware stalled the pipe (an idle RX expired); reset and retry.
#[derive(Debug, Clone)]
pub struct TransportStall(pub TransportError);

impl fmt::Display for TransportStall {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.0.fmt(f)
    }
}

impl Error for TransportStall {}

/// Map a libusb error (by its message) to the matching transport error.
pub fn classify(message: &str) -> TransportError {
    let text = message.to_lowercase();
    if text.contains("timed out") {
        return TransportError::new(message);
    }
    if text.contains("pipe") {
        return TransportError::new(message);
    }
    TransportError::new(message)
}