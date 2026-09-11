#![allow(dead_code)] // each test binary uses a different subset

use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::{Duration, Instant};

use lead_intelligence_lib::config;
use lead_intelligence_lib::protocol::WorkerStatus;
use lead_intelligence_lib::worker::{
    StatusListener, SupervisorSettings, WorkerLaunch, WorkerSupervisor,
};
use lead_intelligence_lib::{dev_python, resolve_worker_launch};

pub fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("repository root")
        .to_path_buf()
}

/// The real research engine from the development virtualenv (`pnpm run setup`).
pub fn real_worker(data_dir: &Path) -> SupervisorSettings {
    let mut config = config::load(Path::new("missing-test-config.toml"), &|_| None).config;
    // a closed port keeps diagnostics fast and independent of a locally running Ollama
    config.ollama.base_url = "http://127.0.0.1:9".into();
    config.ollama.timeout_ms = 500;
    let launch = resolve_worker_launch(&config, Some(&repo_root()), data_dir);
    assert!(
        launch.is_ok(),
        "development worker unavailable ({launch:?}); run `pnpm run setup`"
    );
    SupervisorSettings::new(launch, Duration::from_secs(30))
}

/// A scripted stand-in used only to force failure modes the real worker never shows on demand.
pub fn fake_worker(mode: &str) -> SupervisorSettings {
    let launch = WorkerLaunch {
        program: dev_python(&repo_root()),
        args: vec!["-c".into(), FAKE_WORKER.into(), mode.into()],
        env: Vec::new(),
    };
    let mut settings = SupervisorSettings::new(Ok(launch), Duration::from_secs(10));
    settings.health_interval = Duration::from_millis(200);
    settings.health_timeout = Duration::from_millis(500);
    settings
}

pub fn python_script(script: &str, startup_timeout: Duration) -> SupervisorSettings {
    let launch = WorkerLaunch {
        program: dev_python(&repo_root()),
        args: vec!["-c".into(), script.into()],
        env: Vec::new(),
    };
    SupervisorSettings::new(Ok(launch), startup_timeout)
}

pub fn quiet() -> StatusListener {
    Arc::new(|_| {})
}

pub async fn wait_for(
    supervisor: &WorkerSupervisor,
    timeout: Duration,
    done: impl Fn(&WorkerStatus) -> bool,
) -> WorkerStatus {
    let deadline = Instant::now() + timeout;
    loop {
        let status = supervisor.status();
        if done(&status) {
            return status;
        }
        assert!(
            Instant::now() < deadline,
            "timed out waiting; last status: {status:#?}"
        );
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
}

pub fn process_alive(pid: u32) -> bool {
    #[cfg(windows)]
    {
        let output = std::process::Command::new("tasklist")
            .args(["/FI", &format!("PID eq {pid}"), "/NH", "/FO", "CSV"])
            .output()
            .expect("tasklist runs");
        String::from_utf8_lossy(&output.stdout).contains(&format!("\"{pid}\""))
    }
    #[cfg(not(windows))]
    {
        std::process::Command::new("kill")
            .args(["-0", &pid.to_string()])
            .status()
            .is_ok_and(|status| status.success())
    }
}

pub fn kill_process(pid: u32) {
    #[cfg(windows)]
    let status = std::process::Command::new("taskkill")
        .args(["/F", "/PID", &pid.to_string()])
        .output()
        .expect("taskkill runs")
        .status;
    #[cfg(not(windows))]
    let status = std::process::Command::new("kill")
        .args(["-9", &pid.to_string()])
        .status()
        .expect("kill runs");
    assert!(status.success(), "could not kill {pid}");
}

const FAKE_WORKER: &str = r#"
import datetime, json, sys, uuid
mode = sys.argv[1]
def send(request_id, kind, payload):
    frame = {"request_id": request_id, "protocol_version": 1, "type": kind,
             "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
             "payload": payload, "error": None}
    sys.stdout.write(json.dumps(frame) + "\n")
    sys.stdout.flush()
health = {"status": "ok", "worker_version": "fake", "protocol_version": 1, "python_version": "3",
          "pid": 1, "started_at": "2026-01-01T00:00:00Z", "uptime_s": 0.0}
send(str(uuid.uuid4()), "worker.ready", health)
answered = 0
for line in sys.stdin:
    request = json.loads(line)
    if answered and mode == "hang_after_first":
        continue
    send(request["request_id"], request["type"] + ".result", health)
    answered += 1
    if mode == "crash_after_first":
        sys.exit(3)
"#;
