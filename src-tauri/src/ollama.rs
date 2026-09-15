//! Starts the local Ollama server with the app and stops the one it started when the app exits.
//!
//! Only a server this app launched is ever stopped: an Ollama that already answered on the
//! configured address (its tray app, another tool) is used as it is (D-094).

use std::net::{TcpStream, ToSocketAddrs};
#[cfg(windows)]
use std::os::windows::process::CommandExt;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use tracing::{info, warn};

#[cfg(windows)]
use crate::worker::CREATE_NO_WINDOW;

const DEFAULT_PORT: u16 = 11434;
/// A listening loopback port answers at once; a closed one on Windows takes ~2 s to refuse.
const PROBE_TIMEOUT: Duration = Duration::from_millis(300);

/// The Ollama server this app started, if it started one.
#[derive(Default)]
pub struct OllamaServer {
    child: Arc<Mutex<Option<Child>>>,
}

/// `http://localhost/` becomes `localhost:11434`: the address `ollama serve` binds and the probe
/// connects to. None when the URL is not http(s).
pub fn authority(base_url: &str) -> Option<String> {
    let rest = base_url
        .strip_prefix("http://")
        .or_else(|| base_url.strip_prefix("https://"))?;
    let host = rest.split('/').next().filter(|host| !host.is_empty())?;
    let has_port = match host.strip_prefix('[') {
        Some(bracketed) => bracketed.contains("]:"),
        None => host.contains(':'),
    };
    Some(if has_port {
        host.to_owned()
    } else {
        format!("{host}:{DEFAULT_PORT}")
    })
}

fn answering(address: &str) -> bool {
    address.to_socket_addrs().is_ok_and(|mut addresses| {
        addresses.any(|socket| TcpStream::connect_timeout(&socket, PROBE_TIMEOUT).is_ok())
    })
}

/// Ollama's default per-user install, then whatever `ollama` the PATH finds.
fn executable() -> PathBuf {
    std::env::var_os("LOCALAPPDATA")
        .map(|dir| {
            PathBuf::from(dir)
                .join("Programs")
                .join("Ollama")
                .join("ollama.exe")
        })
        .filter(|path| path.is_file())
        .unwrap_or_else(|| PathBuf::from("ollama"))
}

impl OllamaServer {
    /// Probes and starts on a background thread, so the window never waits for it.
    pub fn start(base_url: &str) -> Self {
        let server = Self::default();
        let Some(address) = authority(base_url) else {
            return server;
        };
        let slot = Arc::clone(&server.child);
        std::thread::spawn(move || {
            if answering(&address) {
                info!(%address, "ollama is already running; it is used and left alone");
                return;
            }
            let mut command = Command::new(executable());
            command
                .arg("serve")
                .env("OLLAMA_HOST", &address)
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null());
            #[cfg(windows)]
            command.creation_flags(CREATE_NO_WINDOW);
            match command.spawn() {
                Ok(child) => {
                    info!(pid = child.id(), %address, "ollama server started with the app");
                    if let Ok(mut guard) = slot.lock() {
                        *guard = Some(child);
                    }
                }
                Err(error) => {
                    warn!(%error, "ollama could not be started; local AI stays unavailable");
                }
            }
        });
        server
    }

    /// Stops the server this app started, together with the model runners it spawned.
    pub fn stop(&self) {
        let Some(mut child) = self.child.lock().ok().and_then(|mut guard| guard.take()) else {
            return;
        };
        if matches!(child.try_wait(), Ok(Some(_))) {
            return; // it had already exited, e.g. another server held the port
        }
        // ponytail: an app crash skips this and leaves the server running; a Job Object would
        // tie it to the core, but the crate forbids `unsafe`
        #[cfg(windows)]
        if let Err(error) = Command::new("taskkill")
            .args(["/PID", &child.id().to_string(), "/T", "/F"])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .creation_flags(CREATE_NO_WINDOW)
            .status()
        {
            warn!(%error, "taskkill could not stop the ollama model runners");
        }
        if let Err(error) = child.kill() {
            warn!(%error, "the ollama server had already stopped");
        }
        let _ = child.wait();
        info!("ollama server stopped with the app");
    }
}

#[cfg(test)]
mod tests {
    use super::authority;

    #[test]
    fn authority_keeps_the_port_and_defaults_to_ollamas() {
        let cases = [
            ("http://127.0.0.1:11434", Some("127.0.0.1:11434")),
            ("http://localhost/", Some("localhost:11434")),
            ("https://[::1]:11500/api", Some("[::1]:11500")),
            ("http://[::1]", Some("[::1]:11434")),
            ("ftp://127.0.0.1", None),
            ("http://", None),
        ];
        for (url, expected) in cases {
            assert_eq!(authority(url).as_deref(), expected, "{url}");
        }
    }
}
