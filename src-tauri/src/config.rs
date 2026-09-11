//! Layered, validated configuration.
//!
//! Precedence (later wins): embedded `config/default.toml` < embedded `<environment>.toml` <
//! user file < `LEAD_*` environment variables. Defaults are compiled in, so the app always starts
//! offline. An invalid user layer is reported as a warning and ignored, never fatal.

use std::io::ErrorKind;
use std::path::Path;

use serde::{Deserialize, Serialize};
use toml::{Table, Value};

const DEFAULT_TOML: &str = include_str!("../../config/default.toml");
const DEVELOPMENT_TOML: &str = include_str!("../../config/development.toml");
const PRODUCTION_TOML: &str = include_str!("../../config/production.toml");

pub const LOG_LEVELS: [&str; 5] = ["trace", "debug", "info", "warn", "error"];

/// (variable, section, key) overrides, applied last.
const ENV_OVERRIDES: [(&str, &str, &str); 5] = [
    ("LEAD_LOG_LEVEL", "logging", "level"),
    ("LEAD_DATA_DIR", "paths", "data_dir"),
    ("LEAD_LOG_DIR", "paths", "log_dir"),
    ("LEAD_WORKER_PROGRAM", "worker", "program"),
    ("LEAD_OLLAMA_URL", "ollama", "base_url"),
];

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Environment {
    Development,
    Production,
}

impl Environment {
    pub fn name(self) -> &'static str {
        match self {
            Self::Development => "development",
            Self::Production => "production",
        }
    }

    fn overlay(self) -> &'static str {
        match self {
            Self::Development => DEVELOPMENT_TOML,
            Self::Production => PRODUCTION_TOML,
        }
    }
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AppConfig {
    pub paths: PathsConfig,
    pub logging: LoggingConfig,
    pub worker: WorkerConfig,
    pub ollama: OllamaConfig,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PathsConfig {
    pub data_dir: String,
    pub log_dir: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LoggingConfig {
    pub level: String,
    pub max_files: usize,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkerConfig {
    pub program: String,
    pub args: Vec<String>,
    pub startup_timeout_ms: u64,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OllamaConfig {
    pub base_url: String,
    pub timeout_ms: u64,
}

impl AppConfig {
    fn validate(&self) -> Result<(), String> {
        if !LOG_LEVELS.contains(&self.logging.level.as_str()) {
            return Err(format!(
                "logging.level must be one of {LOG_LEVELS:?}, got {:?}",
                self.logging.level
            ));
        }
        if !(1..=365).contains(&self.logging.max_files) {
            return Err("logging.max_files must be between 1 and 365".into());
        }
        if !(1_000..=300_000).contains(&self.worker.startup_timeout_ms) {
            return Err("worker.startup_timeout_ms must be between 1000 and 300000".into());
        }
        let url = &self.ollama.base_url;
        let host = url
            .strip_prefix("http://")
            .or_else(|| url.strip_prefix("https://"));
        if host.is_none_or(|rest| rest.trim_matches('/').is_empty()) {
            return Err(format!(
                "ollama.base_url must be an http(s) URL, got {url:?}"
            ));
        }
        if !(100..=60_000).contains(&self.ollama.timeout_ms) {
            return Err("ollama.timeout_ms must be between 100 and 60000".into());
        }
        Ok(())
    }
}

#[derive(Debug, Clone)]
pub struct LoadedConfig {
    pub config: AppConfig,
    pub environment: Environment,
    /// Layers that contributed, in precedence order (shown in diagnostics).
    pub sources: Vec<String>,
    pub warnings: Vec<String>,
}

/// Loads configuration. `env` looks up environment variables (injected for tests).
pub fn load(user_file: &Path, env: &dyn Fn(&str) -> Option<String>) -> LoadedConfig {
    let mut warnings = Vec::new();
    let build_default = if cfg!(debug_assertions) {
        Environment::Development
    } else {
        Environment::Production
    };
    let environment = match env("LEAD_ENV").as_deref().map(str::trim) {
        None | Some("") => build_default,
        Some("development") => Environment::Development,
        Some("production") => Environment::Production,
        Some(other) => {
            warnings.push(format!(
                "LEAD_ENV={other:?} is not development|production; using {}",
                build_default.name()
            ));
            build_default
        }
    };

    let mut table = embedded(DEFAULT_TOML);
    merge(&mut table, embedded(environment.overlay()));
    let built_in = table.clone();
    let mut sources = vec![
        "embedded:default.toml".to_owned(),
        format!("embedded:{}.toml", environment.name()),
    ];

    match std::fs::read_to_string(user_file) {
        Ok(text) => match text.parse::<Table>() {
            Ok(user) => {
                merge(&mut table, user);
                sources.push(format!("file:{}", user_file.display()));
            }
            Err(error) => warnings.push(format!("ignored {}: {error}", user_file.display())),
        },
        Err(error) if error.kind() == ErrorKind::NotFound => {}
        Err(error) => warnings.push(format!("could not read {}: {error}", user_file.display())),
    }
    for (variable, section, key) in ENV_OVERRIDES {
        if let Some(value) = env(variable).filter(|value| !value.trim().is_empty()) {
            let entry = table
                .entry(section)
                .or_insert_with(|| Value::Table(Table::new()));
            if let Value::Table(section) = entry {
                section.insert(key.to_owned(), Value::String(value));
            }
            sources.push(format!("env:{variable}"));
        }
    }

    match parse(table) {
        Ok(config) => LoadedConfig {
            config,
            environment,
            sources,
            warnings,
        },
        Err(error) => {
            warnings.push(format!(
                "configuration overrides rejected ({error}); using built-in defaults"
            ));
            sources.truncate(2);
            let config = parse(built_in).expect("embedded configuration is valid (unit-tested)");
            LoadedConfig {
                config,
                environment,
                sources,
                warnings,
            }
        }
    }
}

fn embedded(text: &str) -> Table {
    text.parse().expect("embedded TOML is valid (unit-tested)")
}

fn merge(base: &mut Table, overlay: Table) {
    for (key, value) in overlay {
        match (base.get_mut(&key), value) {
            (Some(Value::Table(existing)), Value::Table(incoming)) => merge(existing, incoming),
            (_, value) => {
                base.insert(key, value);
            }
        }
    }
}

fn parse(table: Table) -> Result<AppConfig, String> {
    let config: AppConfig = Value::Table(table).try_into().map_err(|e| format!("{e}"))?;
    config.validate()?;
    Ok(config)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn missing() -> &'static Path {
        Path::new("definitely-missing-lead-config.toml")
    }

    fn env_of(pairs: &'static [(&'static str, &'static str)]) -> impl Fn(&str) -> Option<String> {
        move |key| {
            pairs
                .iter()
                .find(|(k, _)| *k == key)
                .map(|(_, v)| (*v).to_owned())
        }
    }

    #[test]
    fn embedded_layers_are_valid_for_every_environment() {
        for environment in [Environment::Development, Environment::Production] {
            let mut table = embedded(DEFAULT_TOML);
            merge(&mut table, embedded(environment.overlay()));
            parse(table).expect("valid embedded configuration");
        }
    }

    #[test]
    fn environment_overlays_apply() {
        let dev = load(missing(), &env_of(&[("LEAD_ENV", "development")]));
        assert_eq!(dev.environment, Environment::Development);
        assert_eq!(dev.config.logging.level, "debug");
        let prod = load(missing(), &env_of(&[("LEAD_ENV", "production")]));
        assert_eq!(prod.config.logging.level, "info");
        assert_eq!(prod.config.worker.startup_timeout_ms, 30_000);
        assert!(dev.warnings.is_empty() && prod.warnings.is_empty());
    }

    #[test]
    fn user_file_then_env_vars_override_in_order() {
        let dir = tempfile::tempdir().expect("temp dir");
        let file = dir.path().join("local.toml");
        std::fs::write(
            &file,
            "[logging]\nlevel = \"warn\"\n[paths]\ndata_dir = \"D:/from-file\"\n",
        )
        .expect("write config");
        let loaded = load(&file, &env_of(&[("LEAD_DATA_DIR", "E:/from-env")]));
        assert!(loaded.warnings.is_empty(), "{:?}", loaded.warnings);
        assert_eq!(loaded.config.logging.level, "warn");
        assert_eq!(loaded.config.paths.data_dir, "E:/from-env");
        assert_eq!(loaded.sources.len(), 4);
        assert_eq!(
            loaded.sources.last().map(String::as_str),
            Some("env:LEAD_DATA_DIR")
        );
    }

    #[test]
    fn invalid_overrides_fall_back_to_defaults_with_a_warning() {
        let dir = tempfile::tempdir().expect("temp dir");
        let cases = [
            "[logging]\nlevel = \"loud\"\n",
            "[logging]\nunknown_key = 1\n",
            "[worker]\nstartup_timeout_ms = 5\n",
            "this is not toml = = =",
        ];
        for (index, content) in cases.iter().enumerate() {
            let file = dir.path().join(format!("case{index}.toml"));
            std::fs::write(&file, content).expect("write config");
            let loaded = load(&file, &env_of(&[("LEAD_ENV", "production")]));
            assert_eq!(
                loaded.warnings.len(),
                1,
                "case {index}: {:?}",
                loaded.warnings
            );
            assert_eq!(loaded.config.logging.level, "info", "case {index}");
            assert_eq!(loaded.sources.len(), 2, "case {index}");
        }
        let bad_url = load(missing(), &env_of(&[("LEAD_OLLAMA_URL", "ftp://example")]));
        assert_eq!(bad_url.warnings.len(), 1);
        assert_eq!(bad_url.config.ollama.base_url, "http://127.0.0.1:11434");
    }

    #[test]
    fn unknown_environment_name_warns_and_uses_build_default() {
        let loaded = load(missing(), &env_of(&[("LEAD_ENV", "staging")]));
        assert_eq!(loaded.warnings.len(), 1);
        assert_eq!(loaded.environment, Environment::Development); // tests are debug builds
    }
}
