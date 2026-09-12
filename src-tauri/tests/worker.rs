//! Worker supervisor against real processes: the research engine itself, plus scripted
//! stand-ins for failure modes (crash loops, hangs) that cannot be triggered on demand.

mod common;

use std::time::Duration;

use common::*;
use lead_intelligence_lib::error::AppError;
use lead_intelligence_lib::protocol::{
    ComponentState, DiagnosticsResult, HealthResult, WorkerState,
};
use lead_intelligence_lib::worker::{
    DIAGNOSTICS, DIAGNOSTICS_TIMEOUT, HEALTH, SupervisorSettings, WorkerSupervisor,
};
use serde_json::Map;

#[tokio::test(flavor = "multi_thread")]
async fn real_worker_starts_answers_and_stops_without_leftovers() {
    let data = tempfile::tempdir().expect("temp dir");
    let supervisor = WorkerSupervisor::new(real_worker(data.path()), quiet());

    supervisor.start().await.expect("worker starts");
    let status = supervisor.status();
    assert_eq!(status.state, WorkerState::Running);
    let pid = status.pid.expect("process id");
    let interpreter_pid = status.health.as_ref().expect("health from handshake").pid;

    supervisor.start().await.expect("second start is a no-op");
    assert_eq!(
        supervisor.status().pid,
        Some(pid),
        "only one worker instance"
    );

    let diagnostics: DiagnosticsResult = supervisor
        .request_typed(DIAGNOSTICS, DIAGNOSTICS_TIMEOUT)
        .await
        .expect("diagnostics");
    assert_eq!(
        diagnostics.sqlite.state,
        ComponentState::Ok,
        "{}",
        diagnostics.sqlite.summary
    );
    assert_eq!(
        diagnostics.duckdb.state,
        ComponentState::Ok,
        "{}",
        diagnostics.duckdb.summary
    );
    assert_eq!(diagnostics.ollama.state, ComponentState::Unavailable);
    assert!(data.path().join("lead.sqlite3").is_file());
    assert!(data.path().join("analytics.duckdb").is_file());

    let rejected = supervisor
        .request("no_such_request", Map::new(), Duration::from_secs(5))
        .await;
    assert!(matches!(rejected, Err(AppError::Worker(_))), "{rejected:?}");

    let exit = supervisor.stop().await.expect("exit status");
    assert!(exit.success(), "graceful exit expected, got {exit}");
    assert_eq!(supervisor.status().state, WorkerState::Stopped);
    assert!(!process_alive(pid), "launcher process {pid} left behind");
    assert!(
        !process_alive(interpreter_pid),
        "interpreter {interpreter_pid} left behind"
    );

    let after = supervisor
        .request_typed::<HealthResult>(HEALTH, Duration::from_secs(1))
        .await;
    assert!(
        matches!(after, Err(AppError::WorkerUnavailable(_))),
        "{after:?}"
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn crashed_worker_is_restarted_automatically() {
    let data = tempfile::tempdir().expect("temp dir");
    let supervisor = WorkerSupervisor::new(real_worker(data.path()), quiet());
    supervisor.start().await.expect("worker starts");
    let first = supervisor.status();
    let interpreter = first.health.as_ref().expect("health").pid;

    kill_process(interpreter); // simulate a Python crash

    let recovered = wait_for(&supervisor, Duration::from_secs(60), |s| {
        s.state == WorkerState::Running && s.pid != first.pid
    })
    .await;
    assert_eq!(recovered.restart_count, 1);
    assert!(
        recovered
            .last_error
            .as_deref()
            .unwrap_or_default()
            .contains("exited")
    );
    supervisor.stop().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn crash_loops_stop_after_the_restart_budget() {
    let supervisor = WorkerSupervisor::new(fake_worker("crash_after_first"), quiet());
    supervisor.start().await.expect("first start");
    let failed = wait_for(&supervisor, Duration::from_secs(60), |s| {
        s.state == WorkerState::Failed
    })
    .await;
    assert_eq!(failed.restart_count, 3);
    assert!(
        failed
            .last_error
            .as_deref()
            .unwrap_or_default()
            .contains("exhausted"),
        "{failed:?}"
    );
    assert_eq!(failed.pid, None);
}

#[tokio::test(flavor = "multi_thread")]
async fn hung_worker_is_detected_and_replaced() {
    let supervisor = WorkerSupervisor::new(fake_worker("hang_after_first"), quiet());
    supervisor.start().await.expect("worker starts");
    let first = supervisor.status().pid;
    let replaced = wait_for(&supervisor, Duration::from_secs(60), |s| {
        s.state == WorkerState::Running && s.restart_count >= 1
    })
    .await;
    assert_ne!(replaced.pid, first);
    assert!(
        replaced
            .last_error
            .as_deref()
            .unwrap_or_default()
            .contains("health checks")
    );
    supervisor.stop().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn missing_worker_is_reported_with_its_reason() {
    let settings = SupervisorSettings::new(
        Err("worker executable not found at Z:/missing".into()),
        Duration::from_secs(1),
    );
    let supervisor = WorkerSupervisor::new(settings, quiet());
    let error = supervisor.start().await.expect_err("cannot start");
    assert!(matches!(error, AppError::WorkerUnavailable(_)));
    let status = supervisor.status();
    assert_eq!(status.state, WorkerState::Failed);
    assert!(
        status
            .last_error
            .as_deref()
            .unwrap_or_default()
            .contains("not found")
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn startup_crash_keeps_stderr_for_diagnosis() {
    let script =
        "import sys; sys.stderr.write('ImportError: synthetic startup failure\\n'); sys.exit(4)";
    let supervisor = WorkerSupervisor::new(python_script(script, Duration::from_secs(10)), quiet());
    let error = supervisor.start().await.expect_err("cannot start");
    assert!(
        error.to_string().contains("exited during startup"),
        "{error}"
    );
    let status = supervisor.status();
    assert_eq!(status.state, WorkerState::Failed);
    assert!(
        status
            .stderr_tail
            .iter()
            .any(|line| line.contains("synthetic startup failure")),
        "{:?}",
        status.stderr_tail
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn worker_that_never_becomes_ready_is_killed() {
    let supervisor = WorkerSupervisor::new(
        python_script("import time; time.sleep(120)", Duration::from_millis(1500)),
        quiet(),
    );
    let error = supervisor.start().await.expect_err("never ready");
    assert!(
        error.to_string().contains("did not report ready"),
        "{error}"
    );
    let status = supervisor.status();
    assert_eq!(status.state, WorkerState::Failed);
    assert_eq!(status.pid, None);
}

#[tokio::test(flavor = "multi_thread")]
async fn worker_with_another_data_contract_is_refused() {
    let supervisor = WorkerSupervisor::new(fake_worker_speaking("answer", 99), quiet());
    let error = supervisor.start().await.expect_err("incompatible worker");
    assert!(error.to_string().contains("data contract v99"), "{error}");
    let status = supervisor.status();
    assert_eq!(status.state, WorkerState::Failed);
    assert_eq!(status.pid, None);
}
