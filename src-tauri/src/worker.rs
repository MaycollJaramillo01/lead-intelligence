//! Supervisor for the Python research worker (JSON Lines over stdio, see docs/decisions).
//!
//! Guarantees: at most one worker process per supervisor; a `worker.ready` handshake plus an
//! explicit health check before it is reported as running; timeouts on every request; bounded
//! automatic restarts after a crash or a hang; graceful shutdown with a hard-kill fallback. The
//! worker also exits by itself when its stdin closes, so it cannot outlive a crashed desktop core.

use std::collections::{HashMap, VecDeque};
use std::path::PathBuf;
use std::process::{ExitStatus, Stdio};
use std::sync::atomic::{AtomicU64, Ordering::SeqCst};
use std::sync::{Arc, Mutex, MutexGuard, PoisonError, Weak};
use std::time::{Duration, Instant};

use serde::Serialize;
use serde::de::DeserializeOwned;
use serde_json::{Map, Value};
use tokio::io::{AsyncBufRead, AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};
use tokio::process::{Child, ChildStderr, ChildStdin, ChildStdout, Command};
use tokio::sync::{oneshot, watch};
use tokio::task::JoinHandle;
use tracing::{Instrument, debug, error, info, info_span, warn};
use uuid::Uuid;

use crate::error::AppError;
use crate::protocol::{
    DATA_CONTRACT_VERSION, EVENT_PROTOCOL_ERROR, EVENT_READY, Envelope, ErrorCode, HealthResult,
    MAX_FRAME_BYTES, RESULT_SUFFIX, WorkerState, WorkerStatus,
};

pub const HEALTH: &str = "health";
pub const DIAGNOSTICS: &str = "diagnostics";
pub const BUSINESSES_LIST: &str = "businesses.list";
pub const BUSINESSES_GET: &str = "businesses.get";
pub const DATASETS_LIST: &str = "datasets.list";
pub const SOURCES_LIST: &str = "sources.list";
pub const SOURCES_PREVIEW_IMPORT: &str = "sources.preview_import";
pub const CAMPAIGNS_LIST: &str = "campaigns.list";
pub const CAMPAIGNS_SAVE: &str = "campaigns.save";
pub const CAMPAIGNS_VALIDATE: &str = "campaigns.validate";
pub const DISCOVERY_START: &str = "discovery.start";
pub const DISCOVERY_CONTROL: &str = "discovery.control";
pub const DISCOVERY_RUNS: &str = "discovery.runs";
pub const DISCOVERY_CANDIDATES: &str = "discovery.candidates";
pub const DISCOVERY_CANDIDATE: &str = "discovery.candidate";
pub const CRAWL_START: &str = "crawl.start";
pub const CRAWL_CONTROL: &str = "crawl.control";
pub const CRAWL_RUNS: &str = "crawl.runs";
pub const CRAWL_URLS: &str = "crawl.urls";
pub const CRAWL_URL: &str = "crawl.url";
pub const RESOLUTION_START: &str = "resolution.start";
pub const RESOLUTION_CONTROL: &str = "resolution.control";
pub const RESOLUTION_RUNS: &str = "resolution.runs";
pub const MATCHES_LIST: &str = "matches.list";
pub const MATCHES_GET: &str = "matches.get";
pub const MATCHES_ACT: &str = "matches.act";
pub const BUSINESSES_CHOOSE_VALUE: &str = "businesses.choose_value";
pub const LATINO_START: &str = "latino.start";
pub const LATINO_CONTROL: &str = "latino.control";
pub const LATINO_RUNS: &str = "latino.runs";
pub const LATINO_GET: &str = "latino.get";
pub const LATINO_REVIEW: &str = "latino.review";
pub const DEV_SEED_SYNTHETIC: &str = "dev.seed_synthetic";
const SHUTDOWN: &str = "shutdown";

/// The worker bounds diagnostics at 20 s; allow for transport and scheduling on top.
pub const DIAGNOSTICS_TIMEOUT: Duration = Duration::from_secs(25);
/// Store reads are bounded at 10 s by the worker; the synthetic seed at 60 s.
pub const DATA_TIMEOUT: Duration = Duration::from_secs(15);
pub const SEED_TIMEOUT: Duration = Duration::from_secs(70);
/// Validating a campaign and starting a run may ask a source for an estimate; the worker
/// bounds both at 60 s. Reading an imported file is bounded at 30 s.
pub const DISCOVERY_TIMEOUT: Duration = Duration::from_secs(70);
pub const IMPORT_TIMEOUT: Duration = Duration::from_secs(40);
/// A review decision recomputes the clusters of a whole project; the worker bounds it at 60 s.
pub const RESOLUTION_TIMEOUT: Duration = Duration::from_secs(70);
const SHUTDOWN_TIMEOUT: Duration = Duration::from_secs(5);
const READER_DRAIN_TIMEOUT: Duration = Duration::from_secs(2);
const HEALTH_FAILURES_BEFORE_RESTART: u32 = 2;
const MAX_RESTARTS: usize = 3;
const RESTART_WINDOW: Duration = Duration::from_secs(300);
const STDERR_TAIL_LINES: usize = 40;
const STDERR_LINE_CHARS: usize = 400;
const MAX_STDERR_LINE_BYTES: usize = 64 * 1024;
#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

#[derive(Debug, Clone)]
pub struct WorkerLaunch {
    pub program: PathBuf,
    pub args: Vec<String>,
    pub env: Vec<(String, String)>,
}

#[derive(Debug, Clone)]
pub struct SupervisorSettings {
    /// `Err` explains why no worker can be launched (shown to the user as-is).
    pub launch: Result<WorkerLaunch, String>,
    pub startup_timeout: Duration,
    pub health_interval: Duration,
    pub health_timeout: Duration,
}

impl SupervisorSettings {
    pub fn new(launch: Result<WorkerLaunch, String>, startup_timeout: Duration) -> Self {
        Self {
            launch,
            startup_timeout,
            health_interval: Duration::from_secs(30),
            health_timeout: Duration::from_secs(5),
        }
    }
}

/// Called with a snapshot after every lifecycle change (the app forwards it as a Tauri event).
pub type StatusListener = Arc<dyn Fn(WorkerStatus) + Send + Sync>;

pub struct WorkerSupervisor {
    settings: SupervisorSettings,
    /// Serialises start/stop/restart/recovery; `Some` while a process is owned.
    lifecycle: tokio::sync::Mutex<Option<Process>>,
    shared: Arc<Shared>,
    this: Weak<WorkerSupervisor>,
}

struct Process {
    child: Child,
    generation: u64,
    readers: Vec<JoinHandle<()>>,
    watchdog: Option<JoinHandle<()>>,
}

type Waiter = (u64, oneshot::Sender<Envelope>);

struct Shared {
    status: Mutex<WorkerStatus>,
    pending: Mutex<HashMap<Uuid, Waiter>>,
    stdin: tokio::sync::Mutex<Option<ChildStdin>>,
    /// Increments with every spawn; stale readers and watchdogs compare against it.
    generation: AtomicU64,
    /// `true` once an intentional stop is requested; cancels startup and auto-restarts.
    stopping: watch::Sender<bool>,
    restarts: Mutex<VecDeque<Instant>>,
    listener: StatusListener,
}

fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(PoisonError::into_inner)
}

impl WorkerSupervisor {
    pub fn new(settings: SupervisorSettings, listener: StatusListener) -> Arc<Self> {
        Arc::new_cyclic(|this| Self {
            settings,
            lifecycle: tokio::sync::Mutex::new(None),
            shared: Arc::new(Shared {
                status: Mutex::new(WorkerStatus {
                    state: WorkerState::Stopped,
                    pid: None,
                    started_at: None,
                    restart_count: 0,
                    last_error: None,
                    stderr_tail: Vec::new(),
                    health: None,
                }),
                pending: Mutex::default(),
                stdin: tokio::sync::Mutex::new(None),
                generation: AtomicU64::new(0),
                stopping: watch::Sender::new(false),
                restarts: Mutex::default(),
                listener,
            }),
            this: this.clone(),
        })
    }

    pub fn status(&self) -> WorkerStatus {
        lock(&self.shared.status).clone()
    }

    /// Starts the worker unless one is already owned (single instance).
    pub async fn start(&self) -> Result<(), AppError> {
        let mut slot = self.lifecycle.lock().await;
        if slot.is_some() {
            return Ok(());
        }
        self.shared.stopping.send_replace(false);
        self.spawn(&mut slot).await
    }

    /// User-requested restart. Also resets the automatic-restart budget.
    pub async fn restart(&self) -> Result<WorkerStatus, AppError> {
        let mut slot = self.lifecycle.lock().await;
        if let Some(process) = slot.take() {
            self.terminate(process).await;
        }
        lock(&self.shared.restarts).clear();
        self.shared.update(|status| {
            status.restart_count = 0;
            status.last_error = None;
        });
        self.shared.stopping.send_replace(false);
        self.spawn(&mut slot).await?;
        Ok(self.status())
    }

    /// Graceful stop. Returns the exit status when a process was running.
    pub async fn stop(&self) -> Option<ExitStatus> {
        self.shared.stopping.send_replace(true);
        let mut slot = self.lifecycle.lock().await;
        let process = slot.take()?;
        self.terminate(process).await
    }

    /// Sends one request and waits for its `<kind>.result`, bounded by `timeout`.
    pub async fn request(
        &self,
        kind: &str,
        payload: Map<String, Value>,
        timeout: Duration,
    ) -> Result<Envelope, AppError> {
        let envelope = Envelope::request(kind, payload);
        let request_id = envelope.request_id;
        let exchange = async {
            let mut frame = serde_json::to_vec(&envelope)
                .map_err(|e| AppError::Protocol(format!("could not encode the request: {e}")))?;
            frame.push(b'\n');
            let (sender, receiver) = oneshot::channel();
            let generation = self.shared.generation.load(SeqCst);
            lock(&self.shared.pending).insert(request_id, (generation, sender));
            let _entry = PendingEntry {
                shared: &self.shared,
                request_id,
            };
            let started = Instant::now();
            let answer = tokio::time::timeout(timeout, async {
                {
                    let mut stdin = self.shared.stdin.lock().await;
                    let pipe = stdin.as_mut().ok_or_else(|| {
                        AppError::WorkerUnavailable("the worker is not running".into())
                    })?;
                    let written = async {
                        pipe.write_all(&frame).await?;
                        pipe.flush().await
                    };
                    written.await.map_err(|e| {
                        AppError::WorkerUnavailable(format!("could not send the request: {e}"))
                    })?;
                }
                receiver.await.map_err(|_| {
                    AppError::WorkerUnavailable("the worker exited before answering".into())
                })
            })
            .await
            .map_err(|_| AppError::WorkerTimeout {
                kind: kind.to_owned(),
                timeout_ms: u64::try_from(timeout.as_millis()).unwrap_or(u64::MAX),
            })??;
            if let Some(error) = answer.error {
                warn!(code = ?error.code, message = %error.message, "worker rejected the request");
                return Err(match error.code {
                    ErrorCode::NotFound => AppError::NotFound(error.message),
                    ErrorCode::NotAllowed => AppError::NotAllowed(error.message),
                    ErrorCode::InvalidPayload => AppError::InvalidInput(error.message),
                    _ => AppError::Worker(error.message),
                });
            }
            let expected = format!("{kind}{RESULT_SUFFIX}");
            if answer.kind != expected {
                return Err(AppError::Protocol(format!(
                    "expected '{expected}', received '{}'",
                    answer.kind
                )));
            }
            debug!(
                elapsed_ms = started.elapsed().as_millis() as u64,
                "worker answered"
            );
            Ok(answer)
        };
        exchange
            .instrument(info_span!("worker_request", %request_id, kind))
            .await
    }

    pub async fn request_typed<T: DeserializeOwned>(
        &self,
        kind: &str,
        timeout: Duration,
    ) -> Result<T, AppError> {
        self.request(kind, Map::new(), timeout)
            .await?
            .into_payload()
            .map_err(AppError::Protocol)
    }

    /// Like `request_typed`, with a typed payload (a struct that serialises to a JSON object).
    pub async fn request_with<P: Serialize, T: DeserializeOwned>(
        &self,
        kind: &str,
        payload: &P,
        timeout: Duration,
    ) -> Result<T, AppError> {
        let Ok(Value::Object(payload)) = serde_json::to_value(payload) else {
            return Err(AppError::Protocol(format!(
                "'{kind}' payload is not a JSON object"
            )));
        };
        self.request(kind, payload, timeout)
            .await?
            .into_payload()
            .map_err(AppError::Protocol)
    }

    async fn spawn(&self, slot: &mut Option<Process>) -> Result<(), AppError> {
        let launch = match &self.settings.launch {
            Ok(launch) => launch.clone(),
            Err(reason) => return Err(self.fail(reason.clone())),
        };
        self.shared.update(|status| {
            status.state = WorkerState::Starting;
            status.pid = None;
            status.started_at = None;
            status.health = None;
            status.stderr_tail.clear();
        });

        let mut command = Command::new(&launch.program);
        command
            .args(&launch.args)
            .envs(
                launch
                    .env
                    .iter()
                    .map(|(key, value)| (key.as_str(), value.as_str())),
            )
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true);
        #[cfg(windows)]
        command.creation_flags(CREATE_NO_WINDOW);
        let mut child = command.spawn().map_err(|e| {
            self.fail(format!(
                "could not launch {}: {e}",
                launch.program.display()
            ))
        })?;
        let (Some(stdin), Some(stdout), Some(stderr)) =
            (child.stdin.take(), child.stdout.take(), child.stderr.take())
        else {
            return Err(self.fail("the worker's stdio pipes were not created".into()));
        };
        let generation = self.shared.generation.fetch_add(1, SeqCst) + 1;
        let pid = child.id();
        *self.shared.stdin.lock().await = Some(stdin);
        self.shared.update(|status| status.pid = pid);
        info!(pid, generation, program = %launch.program.display(), "python worker spawned");

        let (ready_tx, ready_rx) = oneshot::channel();
        let readers = vec![
            tokio::spawn(read_stdout(
                self.this.clone(),
                self.shared.clone(),
                generation,
                stdout,
                ready_tx,
            )),
            tokio::spawn(read_stderr(self.shared.clone(), stderr)),
        ];
        let mut process = Process {
            child,
            generation,
            readers,
            watchdog: None,
        };

        let handshake = async {
            tokio::time::timeout(self.settings.startup_timeout, ready_rx)
                .await
                .map_err(|_| {
                    format!(
                        "worker did not report ready within {} ms",
                        self.settings.startup_timeout.as_millis()
                    )
                })?
                .map_err(|_| "worker exited during startup (see its stderr output)".to_owned())?;
            let health = self
                .request_typed::<HealthResult>(HEALTH, self.settings.health_timeout)
                .await
                .map_err(|e| format!("startup health check failed: {e}"))?;
            if health.data_contract_version != DATA_CONTRACT_VERSION {
                return Err(format!(
                    "worker speaks data contract v{}, this build needs v{DATA_CONTRACT_VERSION}",
                    health.data_contract_version
                ));
            }
            Ok(health)
        };
        let mut stopping = self.shared.stopping.subscribe();
        let outcome = tokio::select! {
            result = handshake => result,
            _ = stopping.wait_for(|stop| *stop) => Err("startup cancelled: the app is stopping".to_owned()),
        };
        match outcome {
            Ok(health) => {
                process.watchdog = Some(tokio::spawn(watchdog(
                    self.this.clone(),
                    generation,
                    self.settings.health_interval,
                    self.settings.health_timeout,
                )));
                self.shared.update(|status| {
                    status.state = WorkerState::Running;
                    status.started_at = Some(health.started_at);
                    status.health = Some(health);
                });
                *slot = Some(process);
                info!(pid, "python worker running");
                Ok(())
            }
            Err(reason) => {
                self.terminate(process).await;
                Err(self.fail(reason))
            }
        }
    }

    /// Asks the worker to exit, closes its stdin, and kills it if it does not comply in time.
    async fn terminate(&self, mut process: Process) -> Option<ExitStatus> {
        if let Some(watchdog) = process.watchdog.take() {
            watchdog.abort();
        }
        self.shared
            .update(|status| status.state = WorkerState::Stopping);
        if matches!(process.child.try_wait(), Ok(None))
            && let Err(error) = self
                .request(SHUTDOWN, Map::new(), SHUTDOWN_TIMEOUT / 2)
                .await
        {
            debug!(%error, "worker did not acknowledge shutdown");
        }
        self.shared.stdin.lock().await.take(); // EOF on stdin also makes the worker exit
        let exit = match tokio::time::timeout(SHUTDOWN_TIMEOUT, process.child.wait()).await {
            Ok(Ok(status)) => Some(status),
            Ok(Err(error)) => {
                warn!(%error, "could not wait for the worker process");
                None
            }
            Err(_) => {
                warn!("worker ignored the shutdown request; killing it");
                if let Err(error) = process.child.kill().await {
                    warn!(%error, "killing the worker failed");
                }
                process.child.try_wait().ok().flatten()
            }
        };
        for reader in process.readers {
            let abort = reader.abort_handle();
            if tokio::time::timeout(READER_DRAIN_TIMEOUT, reader)
                .await
                .is_err()
            {
                warn!("worker output reader did not finish; aborting it");
                abort.abort();
            }
        }
        self.shared.fail_pending(process.generation);
        self.shared.update(|status| {
            status.state = WorkerState::Stopped;
            status.pid = None;
            status.health = None;
        });
        info!(exit = ?exit, "python worker stopped");
        exit
    }

    fn fail(&self, reason: String) -> AppError {
        error!(%reason, "python worker unavailable");
        self.shared.update(|status| {
            status.state = WorkerState::Failed;
            status.last_error = Some(reason.clone());
        });
        AppError::WorkerUnavailable(reason)
    }

    /// Handles an unexpected exit or hang of `generation`: reap it, then restart within budget.
    async fn recover(&self, generation: u64, reason: &str) {
        let mut slot = self.lifecycle.lock().await;
        if *self.shared.stopping.borrow() {
            return;
        }
        let Some(process) = slot.take_if(|process| process.generation == generation) else {
            return; // already replaced or stopped
        };
        error!(reason, "python worker failed; recovering");
        let reason = match self.terminate(process).await {
            Some(exit) => format!("{reason} ({exit})"),
            None => reason.to_owned(),
        };
        if !self.shared.allow_restart() {
            self.fail(format!(
                "{reason}; automatic restarts exhausted ({MAX_RESTARTS} within {} s), use Restart worker",
                RESTART_WINDOW.as_secs()
            ));
            return;
        }
        let attempt = self.shared.update(|status| {
            status.state = WorkerState::Restarting;
            status.last_error = Some(reason.clone());
            status.restart_count += 1;
            status.restart_count
        });
        drop(slot); // never block stop() during the backoff
        tokio::time::sleep(backoff(attempt)).await;
        let mut slot = self.lifecycle.lock().await;
        if slot.is_some() || *self.shared.stopping.borrow() {
            return;
        }
        if let Err(error) = self.spawn(&mut slot).await {
            error!(%error, "automatic worker restart failed");
        }
    }
}

/// Recovery runs on its own task. A plain (non-async) fn keeps the future types of
/// spawn -> reader -> recover -> spawn from forming a cycle the compiler cannot resolve.
fn spawn_recovery(supervisor: Arc<WorkerSupervisor>, generation: u64, reason: &'static str) {
    tokio::spawn(async move { supervisor.recover(generation, reason).await });
}

fn backoff(attempt: u32) -> Duration {
    Duration::from_millis(500 << attempt.saturating_sub(1).min(4))
}

impl Shared {
    fn update<T>(&self, change: impl FnOnce(&mut WorkerStatus) -> T) -> T {
        let (result, snapshot) = {
            let mut status = lock(&self.status);
            let result = change(&mut status);
            (result, status.clone())
        };
        (self.listener)(snapshot);
        result
    }

    /// Appends to the diagnostic tail without emitting an event (log lines are too frequent).
    fn push_stderr(&self, line: String) {
        let mut status = lock(&self.status);
        if status.stderr_tail.len() >= STDERR_TAIL_LINES {
            status.stderr_tail.remove(0);
        }
        status
            .stderr_tail
            .push(line.chars().take(STDERR_LINE_CHARS).collect());
    }

    fn set_health(&self, health: HealthResult) {
        lock(&self.status).health = Some(health);
    }

    fn fail_pending(&self, generation: u64) {
        // dropping the senders wakes the waiting requests with "worker exited"
        lock(&self.pending).retain(|_, (owner, _)| *owner != generation);
    }

    fn allow_restart(&self) -> bool {
        let mut history = lock(&self.restarts);
        let now = Instant::now();
        while history
            .front()
            .is_some_and(|at| now.duration_since(*at) > RESTART_WINDOW)
        {
            history.pop_front();
        }
        if history.len() >= MAX_RESTARTS {
            return false;
        }
        history.push_back(now);
        true
    }

    fn route(&self, envelope: Envelope, ready: &mut Option<oneshot::Sender<HealthResult>>) {
        let kind = envelope.kind.clone();
        if kind == EVENT_READY {
            match (envelope.into_payload::<HealthResult>(), ready.take()) {
                (Ok(health), Some(sender)) => {
                    if sender.send(health).is_err() {
                        debug!("worker became ready after startup was abandoned");
                    }
                }
                (Ok(_), None) => warn!("duplicate worker.ready event ignored"),
                (Err(problem), _) => warn!(%problem, "invalid worker.ready event"),
            }
        } else if kind == EVENT_PROTOCOL_ERROR || kind.ends_with(RESULT_SUFFIX) {
            let waiter = lock(&self.pending).remove(&envelope.request_id);
            match waiter {
                Some((_, sender)) => {
                    if sender.send(envelope).is_err() {
                        debug!(kind, "requester gave up before the answer arrived");
                    }
                }
                None if kind == EVENT_PROTOCOL_ERROR => warn!(
                    reason = envelope.error.as_ref().map(|e| e.message.as_str()),
                    "worker reported a protocol error"
                ),
                None => {
                    warn!(request_id = %envelope.request_id, kind, "answer for an unknown or expired request")
                }
            }
        } else {
            warn!(kind, "unexpected message from the worker ignored");
        }
    }
}

/// Removes a pending request on every exit path (answer, timeout, cancellation).
struct PendingEntry<'a> {
    shared: &'a Shared,
    request_id: Uuid,
}

impl Drop for PendingEntry<'_> {
    fn drop(&mut self) {
        lock(&self.shared.pending).remove(&self.request_id);
    }
}

async fn read_stdout(
    supervisor: Weak<WorkerSupervisor>,
    shared: Arc<Shared>,
    generation: u64,
    stdout: ChildStdout,
    ready: oneshot::Sender<HealthResult>,
) {
    let mut reader = BufReader::new(stdout);
    let mut ready = Some(ready);
    let mut frame = Vec::new();
    loop {
        match read_frame(&mut reader, &mut frame, MAX_FRAME_BYTES).await {
            Ok(Frame::Line) => match Envelope::from_frame(&frame) {
                Ok(envelope) => shared.route(envelope, &mut ready),
                Err(problem) => warn!(%problem, "invalid frame from the worker discarded"),
            },
            Ok(Frame::Oversize) => {
                warn!(
                    limit = MAX_FRAME_BYTES,
                    "oversized frame from the worker discarded"
                );
            }
            Ok(Frame::Eof) => break,
            Err(error) => {
                warn!(%error, "reading the worker's stdout failed");
                break;
            }
        }
    }
    shared.fail_pending(generation);
    if let Some(supervisor) = supervisor.upgrade() {
        spawn_recovery(supervisor, generation, "worker process exited");
    }
}

async fn read_stderr(shared: Arc<Shared>, stderr: ChildStderr) {
    let mut reader = BufReader::new(stderr);
    let mut line = Vec::new();
    loop {
        match read_frame(&mut reader, &mut line, MAX_STDERR_LINE_BYTES).await {
            Ok(Frame::Line) => {
                let text = String::from_utf8_lossy(&line);
                let text = text.trim_end();
                if !text.is_empty() {
                    shared.push_stderr(forward_worker_log(text));
                }
            }
            Ok(Frame::Oversize) => warn!("oversized stderr line from the worker discarded"),
            Ok(Frame::Eof) => return,
            Err(error) => {
                warn!(%error, "reading the worker's stderr failed");
                return;
            }
        }
    }
}

async fn watchdog(
    supervisor: Weak<WorkerSupervisor>,
    generation: u64,
    interval: Duration,
    timeout: Duration,
) {
    let mut failures = 0;
    loop {
        tokio::time::sleep(interval).await;
        let Some(supervisor) = supervisor.upgrade() else {
            return;
        };
        if supervisor.shared.generation.load(SeqCst) != generation {
            return;
        }
        match supervisor
            .request_typed::<HealthResult>(HEALTH, timeout)
            .await
        {
            Ok(health) => {
                failures = 0;
                supervisor.shared.set_health(health);
            }
            Err(error) => {
                failures += 1;
                warn!(%error, failures, "worker health check failed");
                if failures >= HEALTH_FAILURES_BEFORE_RESTART {
                    // recovery aborts this watchdog, so it must not run inside it
                    spawn_recovery(
                        supervisor,
                        generation,
                        "worker stopped answering health checks",
                    );
                    return;
                }
            }
        }
    }
}

/// Re-emits one worker stderr line into the desktop log; returns a compact form for the tail.
fn forward_worker_log(line: &str) -> String {
    let Ok(Value::Object(mut record)) = serde_json::from_str::<Value>(line) else {
        warn!(target: "worker", line, "worker stderr"); // tracebacks and other raw output
        return line.to_owned();
    };
    let mut take = |key: &str| {
        record.remove(key).map(|value| match value {
            Value::String(text) => text,
            other => other.to_string(),
        })
    };
    let level = take("level").unwrap_or_default();
    let message = take("msg").unwrap_or_default();
    let logger = take("logger").unwrap_or_default();
    let request_id = take("request_id");
    let job_id = take("job_id");
    let exc = take("exc");
    record.remove("ts");
    let extra = Value::Object(record);
    macro_rules! emit {
        ($macro:ident) => {
            tracing::$macro!(
                target: "worker",
                logger = %logger,
                request_id = request_id.as_deref(),
                job_id = job_id.as_deref(),
                exc = exc.as_deref(),
                extra = %extra,
                "{message}"
            )
        };
    }
    match level.as_str() {
        "debug" => emit!(debug),
        "info" => emit!(info),
        "warning" => emit!(warn),
        _ => emit!(error),
    }
    format!("{level} {logger}: {message}")
}

enum Frame {
    Line,
    Oversize,
    Eof,
}

/// Reads one `\n`-terminated frame into `buf` (without the terminator), bounded by `max` bytes.
/// An oversized line is skipped entirely so the stream stays in sync.
async fn read_frame<R: AsyncBufRead + Unpin>(
    reader: &mut R,
    buf: &mut Vec<u8>,
    max: usize,
) -> std::io::Result<Frame> {
    buf.clear();
    let read = (&mut *reader)
        .take(max as u64 + 1)
        .read_until(b'\n', buf)
        .await?;
    if read == 0 {
        return Ok(Frame::Eof);
    }
    if buf.last() == Some(&b'\n') {
        buf.pop();
        if buf.last() == Some(&b'\r') {
            buf.pop();
        }
        return Ok(Frame::Line);
    }
    if buf.len() <= max {
        return Ok(Frame::Line); // last line without a terminator
    }
    loop {
        buf.clear();
        let read = (&mut *reader)
            .take(64 * 1024)
            .read_until(b'\n', buf)
            .await?;
        if read == 0 || buf.last() == Some(&b'\n') {
            buf.clear();
            return Ok(Frame::Oversize);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    async fn frames(input: &[u8], max: usize) -> Vec<String> {
        let mut reader = BufReader::new(input);
        let mut buf = Vec::new();
        let mut out = Vec::new();
        loop {
            match read_frame(&mut reader, &mut buf, max)
                .await
                .expect("in-memory read")
            {
                Frame::Line => out.push(String::from_utf8(buf.clone()).expect("utf8")),
                Frame::Oversize => out.push("<oversize>".into()),
                Frame::Eof => return out,
            }
        }
    }

    #[tokio::test]
    async fn frames_split_on_newlines_and_strip_crlf() {
        assert_eq!(
            frames(b"a\nbb\r\n\nlast", 16).await,
            ["a", "bb", "", "last"]
        );
    }

    #[tokio::test]
    async fn oversized_frames_are_skipped_without_losing_sync() {
        let mut input = vec![b'x'; 100];
        input.extend_from_slice(b"\nok\n");
        assert_eq!(frames(&input, 10).await, ["<oversize>", "ok"]);
        assert_eq!(frames(b"0123456789\n", 10).await, ["0123456789"]);
    }

    #[test]
    fn restart_backoff_grows_and_is_capped() {
        let millis: Vec<u128> = (1..=7).map(|n| backoff(n).as_millis()).collect();
        assert_eq!(millis, [500, 1000, 2000, 4000, 8000, 8000, 8000]);
    }

    #[test]
    fn worker_log_lines_are_compacted_for_the_tail() {
        let json = r#"{"ts":"t","level":"warning","logger":"research_engine.worker","msg":"protocol error","reason":"x"}"#;
        assert_eq!(
            forward_worker_log(json),
            "warning research_engine.worker: protocol error"
        );
        assert_eq!(
            forward_worker_log("Traceback (most recent call last):"),
            "Traceback (most recent call last):"
        );
    }
}
