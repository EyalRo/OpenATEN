//! Logging setup shared by the commands: standard levels, one line per
//! record, on stderr. Ported from `src/openaten/log.py`.

pub const LEVELS: [&str; 5] = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"];
pub const ENV_VAR: &str = "OPENATEN_LOG";

/// Normalise a level name to a LEVELS entry, defaulting to INFO.
pub fn normalise(level: &str) -> &'static str {
    let up = level.to_uppercase();
    if LEVELS.contains(&up.as_str()) {
        // Return the canonical entry.
        LEVELS.iter().copied().find(|l| *l == up).unwrap()
    } else {
        "INFO"
    }
}

/// Precedence: `level` (flag), `$OPENATEN_LOG`, `fallback` (config file), INFO.
pub fn resolve(level: Option<&str>, fallback: Option<&str>) -> &'static str {
    let env = std::env::var(ENV_VAR).ok();
    let name: &str = level
        .or(env.as_deref())
        .or(fallback)
        .unwrap_or("INFO");
    normalise(name)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn setup_level_precedence() {
        std::env::set_var(ENV_VAR, "ERROR");
        assert_eq!(resolve(None, None), "ERROR");
        assert_eq!(resolve(Some("debug"), None), "DEBUG");
        std::env::remove_var(ENV_VAR);
        assert_eq!(resolve(Some("bogus"), None), "INFO");
        assert_eq!(resolve(None, Some("warning")), "WARNING");
        std::env::remove_var(ENV_VAR);
    }
}