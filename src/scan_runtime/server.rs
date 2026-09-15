//! Two-lane server-streaming gRPC runtime over Unix domain sockets.

use std::{
    collections::BTreeMap,
    future::Future,
    os::unix::fs::{FileTypeExt, MetadataExt, PermissionsExt},
    path::{Path, PathBuf},
    pin::Pin,
    sync::{
        Arc, Mutex,
        atomic::{AtomicU64, Ordering},
    },
    task::{Context, Poll},
};

use tokio::{
    net::UnixListener,
    sync::{OwnedSemaphorePermit, Semaphore, watch},
    task::JoinHandle,
};
use tokio_stream::{Stream, wrappers::UnixListenerStream};
use tonic::{Request, Response, Status, transport::Server};
use uuid::Uuid;

use crate::wire::{
    ScanFrame, SubscribeRequest,
    lidar_scan_source_server::{LidarScanSource, LidarScanSourceServer},
};

use super::{
    GRACEFUL_STOP_TIMEOUT, LatestTwo, MAX_GRPC_MESSAGE_BYTES, MAX_SUBSCRIBERS_PER_SENSOR, Result,
    RingDelivery, RuntimeClock, STATUS_INTERVAL, ScanRuntimeError, StatusIdentity,
    SystemRuntimeClock, io_error,
    status::{DriverStatus, prepare_atomic_status, status_path, temporary_status_path},
};

const SERVICES: [&str; 2] = ["lidar-driver-a", "lidar-driver-b"];
type BeforeBindHook = dyn Fn(usize, &Path) + Send + Sync;

pub trait RuntimeIdSource: Send + Sync + 'static {
    fn next_id(&self) -> Uuid;
}

#[derive(Clone, Copy, Debug, Default)]
pub struct SystemRuntimeIdSource;

impl RuntimeIdSource for SystemRuntimeIdSource {
    fn next_id(&self) -> Uuid {
        Uuid::new_v4()
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ScanRuntimeConfig {
    pub sensor_ids: [String; 2],
    pub socket_directory: PathBuf,
    pub status_directory: PathBuf,
    pub edge_id: String,
    pub config_revision: String,
    pub site_id: String,
    pub deployment_revision: String,
    pub service_version: String,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SensorRuntimeStats {
    pub sensor_id: String,
    pub published_frames: u64,
    pub frame_loss: u64,
    pub subscribers: usize,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ScanRuntimeStats {
    pub sensors: [SensorRuntimeStats; 2],
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct SocketIdentity {
    device: u64,
    inode: u64,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
struct LaneProgress {
    sequence: u64,
    last_scan_unix_ms: i64,
    last_progress_monotonic_ns: Option<u64>,
}

#[derive(Debug)]
struct LaneShared {
    identity: StatusIdentity,
    status_path: PathBuf,
    socket_path: PathBuf,
    socket_identity: SocketIdentity,
    buffer: LatestTwo<ScanFrame>,
    permits: Arc<Semaphore>,
    frame_loss: AtomicU64,
    published_frames: AtomicU64,
    progress: Mutex<LaneProgress>,
}

struct AbortOnDrop<T> {
    task: Option<JoinHandle<T>>,
}

impl<T> AbortOnDrop<T> {
    fn new(task: JoinHandle<T>) -> Self {
        Self { task: Some(task) }
    }

    async fn join(mut self) -> std::result::Result<T, tokio::task::JoinError> {
        let result = self
            .task
            .as_mut()
            .expect("guard always owns a task before join")
            .await;
        self.task.take();
        result
    }
}

impl<T> Drop for AbortOnDrop<T> {
    fn drop(&mut self) {
        if let Some(task) = &self.task {
            task.abort();
        }
    }
}

impl LaneShared {
    fn stats(&self) -> SensorRuntimeStats {
        SensorRuntimeStats {
            sensor_id: self.identity.sensor_id.clone(),
            published_frames: self.published_frames.load(Ordering::Relaxed),
            frame_loss: self.frame_loss.load(Ordering::Relaxed),
            subscribers: MAX_SUBSCRIBERS_PER_SENSOR - self.permits.available_permits(),
        }
    }

    fn add_frame_loss(&self, lost: u64) -> std::result::Result<(), Status> {
        self.frame_loss
            .fetch_update(Ordering::Relaxed, Ordering::Relaxed, |value| {
                value.checked_add(lost)
            })
            .map(|_| ())
            .map_err(|_| Status::internal("frame loss counter exhausted"))
    }
}

#[derive(Clone, Debug)]
struct SensorService {
    lane: Arc<LaneShared>,
}

pub struct SubscriberStream {
    lane: Arc<LaneShared>,
    cursor: u64,
    pending: Option<DeliveryFuture>,
    _permit: OwnedSemaphorePermit,
}

type DeliveryFuture =
    Pin<Box<dyn Future<Output = Option<RingDelivery<ScanFrame>>> + Send + 'static>>;

impl Stream for SubscriberStream {
    type Item = std::result::Result<ScanFrame, Status>;

    fn poll_next(mut self: Pin<&mut Self>, context: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        if self.pending.is_none() {
            let buffer = self.lane.buffer.clone();
            let cursor = self.cursor;
            self.pending = Some(Box::pin(async move { buffer.next_after(cursor).await }));
        }
        let result = self
            .pending
            .as_mut()
            .expect("pending delivery was initialized")
            .as_mut()
            .poll(context);
        let Poll::Ready(delivery) = result else {
            return Poll::Pending;
        };
        self.pending = None;
        let Some(delivery) = delivery else {
            return Poll::Ready(None);
        };
        self.cursor = delivery.serial;
        if let Err(error) = self.lane.add_frame_loss(delivery.lost) {
            return Poll::Ready(Some(Err(error)));
        }
        Poll::Ready(Some(Ok((*delivery.item).clone())))
    }
}

#[tonic::async_trait]
impl LidarScanSource for SensorService {
    type SubscribeScansStream = SubscriberStream;

    async fn subscribe_scans(
        &self,
        request: Request<SubscribeRequest>,
    ) -> std::result::Result<Response<Self::SubscribeScansStream>, Status> {
        let consumer_size = request.into_inner().consumer_id.len();
        if !(1..=128).contains(&consumer_size) {
            return Err(Status::invalid_argument("consumer_id required"));
        }
        let permit = Arc::clone(&self.lane.permits)
            .try_acquire_owned()
            .map_err(|_| Status::resource_exhausted("subscriber limit"))?;
        Ok(Response::new(SubscriberStream {
            lane: Arc::clone(&self.lane),
            cursor: 0,
            pending: None,
            _permit: permit,
        }))
    }
}

pub struct GrpcScanRuntime {
    lanes: [Arc<LaneShared>; 2],
    shutdown: watch::Sender<bool>,
    fatal: watch::Receiver<Option<String>>,
    clock: Arc<dyn RuntimeClock>,
    server_tasks: Vec<JoinHandle<()>>,
    status_task: Option<JoinHandle<()>>,
    closed: bool,
}

impl GrpcScanRuntime {
    pub async fn start_system(config: ScanRuntimeConfig) -> Result<Self> {
        Self::start(
            config,
            Arc::new(SystemRuntimeClock),
            Arc::new(SystemRuntimeIdSource),
        )
        .await
    }

    pub async fn start(
        config: ScanRuntimeConfig,
        clock: Arc<dyn RuntimeClock>,
        id_source: Arc<dyn RuntimeIdSource>,
    ) -> Result<Self> {
        Self::start_inner(config, clock, id_source, None).await
    }

    async fn start_inner(
        config: ScanRuntimeConfig,
        clock: Arc<dyn RuntimeClock>,
        id_source: Arc<dyn RuntimeIdSource>,
        before_bind: Option<&BeforeBindHook>,
    ) -> Result<Self> {
        let paths = validate_and_preflight(&config)?;
        let instance_ids: [Uuid; 2] = std::array::from_fn(|_| id_source.next_id());
        let temporary_socket_paths: [PathBuf; 2] = std::array::from_fn(|index| {
            temporary_socket_path(&paths.socket_paths[index], instance_ids[index])
        });
        for path in &temporary_socket_paths {
            preflight_temporary_socket(path)?;
        }
        create_runtime_directories(&config)?;
        for socket_path in &paths.socket_paths {
            remove_stale_socket(socket_path)?;
        }

        let mut listeners = Vec::with_capacity(2);
        let mut owned_sockets = Vec::with_capacity(2);
        for (index, socket_path) in paths.socket_paths.iter().enumerate() {
            if let Some(hook) = before_bind {
                hook(index, socket_path);
            }
            let (listener, identity) =
                match bind_owned_socket(&temporary_socket_paths[index], socket_path) {
                    Ok(result) => result,
                    Err(error) => {
                        cleanup_owned_sockets(&owned_sockets);
                        return Err(error);
                    }
                };
            owned_sockets.push((socket_path.clone(), identity));
            listeners.push(listener);
        }

        let started_at = match clock.read() {
            Ok(reading) => reading,
            Err(error) => {
                drop(listeners);
                cleanup_owned_sockets(&owned_sockets);
                return Err(error);
            }
        };
        let lanes = std::array::from_fn(|index| {
            Arc::new(LaneShared {
                identity: StatusIdentity {
                    service: SERVICES[index].to_owned(),
                    edge_id: config.edge_id.clone(),
                    sensor_id: config.sensor_ids[index].clone(),
                    config_revision: config.config_revision.clone(),
                    instance_id: instance_ids[index].to_string(),
                    service_version: config.service_version.clone(),
                    site_id: config.site_id.clone(),
                    deployment_revision: config.deployment_revision.clone(),
                },
                status_path: paths.status_paths[index].clone(),
                socket_path: paths.socket_paths[index].clone(),
                socket_identity: owned_sockets[index].1,
                buffer: LatestTwo::new(),
                permits: Arc::new(Semaphore::new(MAX_SUBSCRIBERS_PER_SENSOR)),
                frame_loss: AtomicU64::new(0),
                published_frames: AtomicU64::new(0),
                progress: Mutex::new(LaneProgress::default()),
            })
        });
        if let Err(error) = write_all_statuses(&lanes, started_at.unix_ms, &*clock, &*id_source) {
            drop(listeners);
            cleanup_owned_sockets(&owned_sockets);
            return Err(error);
        }

        let (shutdown, _) = watch::channel(false);
        let (fatal_sender, fatal) = watch::channel(None);
        let mut server_tasks = Vec::with_capacity(2);
        for (listener, lane) in listeners.into_iter().zip(&lanes) {
            let service = LidarScanSourceServer::new(SensorService {
                lane: Arc::clone(lane),
            })
            .max_encoding_message_size(MAX_GRPC_MESSAGE_BYTES);
            let mut shutdown_receiver = shutdown.subscribe();
            let shutdown_state = shutdown_receiver.clone();
            let fatal_sender = fatal_sender.clone();
            let sensor_id = lane.identity.sensor_id.clone();
            let serve_task = tokio::spawn(async move {
                Server::builder()
                    .add_service(service)
                    .serve_with_incoming_shutdown(
                        UnixListenerStream::new(listener),
                        wait_for_shutdown(&mut shutdown_receiver),
                    )
                    .await
            });
            server_tasks.push(tokio::spawn(async move {
                let result = AbortOnDrop::new(serve_task).join().await;
                if !*shutdown_state.borrow() {
                    let detail = match result {
                        Ok(Ok(())) => "serve task stopped unexpectedly".to_owned(),
                        Ok(Err(error)) => error.to_string(),
                        Err(error) => format!("serve task terminated: {error}"),
                    };
                    fatal_sender
                        .send_replace(Some(format!("gRPC lane {sensor_id} failed: {detail}")));
                }
            }));
        }

        let status_lanes = lanes.clone();
        let mut status_shutdown = shutdown.subscribe();
        let status_clock = Arc::clone(&clock);
        let status_ids = Arc::clone(&id_source);
        let status_fatal = fatal_sender;
        let status_task = tokio::spawn(async move {
            loop {
                tokio::select! {
                    () = tokio::time::sleep(STATUS_INTERVAL) => {
                        if let Err(error) = write_all_statuses(
                            &status_lanes,
                            started_at.unix_ms,
                            &*status_clock,
                            &*status_ids,
                        ) {
                            status_fatal.send_replace(Some(format!(
                                "driver status write failed: {error}"
                            )));
                            return;
                        }
                    }
                    result = status_shutdown.changed() => {
                        if result.is_err() || *status_shutdown.borrow() {
                            return;
                        }
                    }
                }
            }
        });

        Ok(Self {
            lanes,
            shutdown,
            fatal,
            clock,
            server_tasks,
            status_task: Some(status_task),
            closed: false,
        })
    }

    pub fn instance_ids(&self) -> BTreeMap<String, String> {
        self.lanes
            .iter()
            .map(|lane| {
                (
                    lane.identity.sensor_id.clone(),
                    lane.identity.instance_id.clone(),
                )
            })
            .collect()
    }

    pub fn socket_paths(&self) -> [PathBuf; 2] {
        self.lanes.each_ref().map(|lane| lane.socket_path.clone())
    }

    pub fn fatal_receiver(&self) -> watch::Receiver<Option<String>> {
        self.fatal.clone()
    }

    pub fn stats(&self) -> ScanRuntimeStats {
        ScanRuntimeStats {
            sensors: self.lanes.each_ref().map(|lane| lane.stats()),
        }
    }

    pub async fn publish(&self, frame: ScanFrame) -> Result<()> {
        let lane = self
            .lanes
            .iter()
            .find(|lane| lane.identity.sensor_id == frame.sensor_id)
            .ok_or_else(|| {
                ScanRuntimeError::Invalid(format!(
                    "unknown gRPC frame sensor_id: {}",
                    frame.sensor_id
                ))
            })?;
        if frame.schema_version != "1.0"
            || frame.edge_id != lane.identity.edge_id
            || frame.config_revision != lane.identity.config_revision
            || frame.instance_id != lane.identity.instance_id
            || frame.sdk_status != "OK"
        {
            return Err(ScanRuntimeError::Invalid(
                "gRPC frame identity does not match its sensor lane".to_owned(),
            ));
        }
        let reading = self.clock.read()?;
        let sequence = frame.sequence;
        let last_scan_unix_ms = frame.acquired_at_unix_ms;
        lane.buffer
            .publish(frame)
            .await
            .map_err(|error| ScanRuntimeError::Grpc(error.to_string()))?;
        {
            let mut progress = lane.progress.lock().map_err(|_| {
                ScanRuntimeError::Grpc("scan lane progress lock is poisoned".to_owned())
            })?;
            progress.sequence = sequence;
            progress.last_scan_unix_ms = last_scan_unix_ms;
            progress.last_progress_monotonic_ns = Some(reading.monotonic_ns);
        }
        lane.published_frames
            .fetch_update(Ordering::Relaxed, Ordering::Relaxed, |value| {
                value.checked_add(1)
            })
            .map_err(|_| ScanRuntimeError::Exhausted("published frame counter is exhausted"))?;
        Ok(())
    }

    pub async fn close(&mut self) {
        if self.closed {
            return;
        }
        for lane in &self.lanes {
            lane.buffer.close().await;
        }
        self.shutdown.send_replace(true);
        let deadline = tokio::time::Instant::now() + GRACEFUL_STOP_TIMEOUT;
        if let Some(mut task) = self.status_task.take()
            && tokio::time::timeout_at(deadline, &mut task).await.is_err()
        {
            task.abort();
            let _ = task.await;
        }
        for task in &mut self.server_tasks {
            if tokio::time::timeout_at(deadline, &mut *task).await.is_err() {
                task.abort();
                let _ = (&mut *task).await;
            }
        }
        for lane in &self.lanes {
            remove_owned_socket(&lane.socket_path, lane.socket_identity);
        }
        self.closed = true;
    }
}

impl Drop for GrpcScanRuntime {
    fn drop(&mut self) {
        if self.closed {
            return;
        }
        self.shutdown.send_replace(true);
        if let Some(task) = &self.status_task {
            task.abort();
        }
        for task in &self.server_tasks {
            task.abort();
        }
        for lane in &self.lanes {
            remove_owned_socket(&lane.socket_path, lane.socket_identity);
        }
    }
}

struct RuntimePaths {
    socket_paths: [PathBuf; 2],
    status_paths: [PathBuf; 2],
}

fn validate_and_preflight(config: &ScanRuntimeConfig) -> Result<RuntimePaths> {
    if config.sensor_ids[0] == config.sensor_ids[1]
        || config
            .sensor_ids
            .iter()
            .any(|value| !driver_identity(value))
    {
        return Err(ScanRuntimeError::Invalid(
            "gRPC scan source requires exactly two unique driver-compatible sensor identifiers"
                .to_owned(),
        ));
    }
    for (name, value) in [
        ("edge_id", config.edge_id.as_str()),
        ("config_revision", config.config_revision.as_str()),
    ] {
        if !driver_identity(value) {
            return Err(ScanRuntimeError::Invalid(format!(
                "gRPC {name} must be driver-compatible"
            )));
        }
    }
    for (name, value) in [
        ("site_id", config.site_id.as_str()),
        ("deployment_revision", config.deployment_revision.as_str()),
    ] {
        if !deployment_identity(value) {
            return Err(ScanRuntimeError::Invalid(format!(
                "gRPC {name} must be a safe deployment identifier"
            )));
        }
    }
    if !deployment_identity(&config.service_version) {
        return Err(ScanRuntimeError::Invalid(
            "gRPC service_version must be a safe deployment identifier".to_owned(),
        ));
    }
    for root in [&config.socket_directory, &config.status_directory] {
        if !root.is_absolute() || root.to_str().is_none() {
            return Err(ScanRuntimeError::Invalid(
                "gRPC socket and status directories must be absolute UTF-8 paths".to_owned(),
            ));
        }
        preflight_directory_chain(root)?;
    }
    let socket_paths = config
        .sensor_ids
        .each_ref()
        .map(|sensor| config.socket_directory.join(format!("{sensor}.sock")));
    for path in &socket_paths {
        let text = path.to_str().ok_or_else(|| {
            ScanRuntimeError::Invalid(format!(
                "gRPC endpoint path is not UTF-8: {}",
                path.display()
            ))
        })?;
        let endpoint = format!("unix:{text}");
        if endpoint.len() > 100 {
            return Err(ScanRuntimeError::Invalid(format!(
                "gRPC endpoint exceeds 100 bytes: {endpoint}"
            )));
        }
        preflight_socket(path)?;
    }
    let status_paths =
        std::array::from_fn(|index| status_path(&config.status_directory, SERVICES[index]));
    for path in &status_paths {
        let directory = path
            .parent()
            .expect("status path builder always creates a parent");
        preflight_directory_chain(directory)?;
        preflight_status_target(path)?;
    }
    Ok(RuntimePaths {
        socket_paths,
        status_paths,
    })
}

fn create_runtime_directories(config: &ScanRuntimeConfig) -> Result<()> {
    std::fs::create_dir_all(&config.socket_directory).map_err(|source| {
        io_error(
            "gRPC socket directory create",
            &config.socket_directory,
            source,
        )
    })?;
    for service in SERVICES {
        let path = config.status_directory.join(service);
        std::fs::create_dir_all(&path)
            .map_err(|source| io_error("status directory create", &path, source))?;
    }
    Ok(())
}

fn preflight_directory_chain(path: &Path) -> Result<()> {
    let mut current = PathBuf::new();
    for component in path.components() {
        current.push(component);
        match std::fs::symlink_metadata(&current) {
            Ok(metadata) if metadata.file_type().is_dir() => {}
            Ok(_) => {
                return Err(ScanRuntimeError::Invalid(format!(
                    "runtime directory component is not a directory: {}",
                    current.display()
                )));
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => break,
            Err(source) => return Err(io_error("runtime path preflight", &current, source)),
        }
    }
    Ok(())
}

fn preflight_socket(path: &Path) -> Result<()> {
    match std::fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_socket() => Ok(()),
        Ok(_) => Err(ScanRuntimeError::Invalid(format!(
            "gRPC endpoint path exists and is not a socket: {}",
            path.display()
        ))),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(source) => Err(io_error("gRPC socket preflight", path, source)),
    }
}

fn preflight_status_target(path: &Path) -> Result<()> {
    match std::fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_file() => Ok(()),
        Ok(_) => Err(ScanRuntimeError::Invalid(format!(
            "status target exists and is not a regular file: {}",
            path.display()
        ))),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(source) => Err(io_error("status target preflight", path, source)),
    }
}

fn remove_stale_socket(path: &Path) -> Result<()> {
    match std::fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_socket() => std::fs::remove_file(path)
            .map_err(|source| io_error("stale gRPC socket remove", path, source)),
        Ok(_) => Err(ScanRuntimeError::Invalid(format!(
            "gRPC endpoint path exists and is not a socket: {}",
            path.display()
        ))),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(source) => Err(io_error("stale gRPC socket inspect", path, source)),
    }
}

fn temporary_socket_path(target: &Path, id: Uuid) -> PathBuf {
    let name = target
        .file_name()
        .and_then(|value| value.to_str())
        .expect("validated socket path has a UTF-8 file name");
    let id = id.simple().to_string();
    target.with_file_name(format!("{name}.{}", &id[..6]))
}

fn preflight_temporary_socket(path: &Path) -> Result<()> {
    let text = path.to_str().ok_or_else(|| {
        ScanRuntimeError::Invalid(format!(
            "gRPC temporary socket path is not UTF-8: {}",
            path.display()
        ))
    })?;
    if text.len() > 103 {
        return Err(ScanRuntimeError::Invalid(format!(
            "gRPC temporary socket path exceeds 103 bytes: {text}"
        )));
    }
    match std::fs::symlink_metadata(path) {
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Ok(_) => Err(ScanRuntimeError::Invalid(format!(
            "gRPC temporary socket path already exists: {}",
            path.display()
        ))),
        Err(source) => Err(io_error("gRPC temporary socket preflight", path, source)),
    }
}

fn bind_owned_socket(temporary: &Path, target: &Path) -> Result<(UnixListener, SocketIdentity)> {
    let listener = UnixListener::bind(temporary)
        .map_err(|source| io_error("gRPC UDS bind", temporary, source))?;
    let identity = match socket_identity(temporary) {
        Ok(identity) => identity,
        Err(error) => {
            drop(listener);
            return Err(error);
        }
    };
    if let Err(error) = validate_listener_descriptor(&listener, temporary) {
        drop(listener);
        remove_owned_socket(temporary, identity);
        return Err(error);
    }
    if let Err(source) = std::fs::set_permissions(temporary, std::fs::Permissions::from_mode(0o660))
    {
        drop(listener);
        remove_owned_socket(temporary, identity);
        return Err(io_error("gRPC socket chmod", temporary, source));
    }
    match socket_identity(temporary) {
        Ok(current) if current == identity => {}
        Ok(_) => {
            drop(listener);
            return Err(ScanRuntimeError::Invalid(format!(
                "gRPC temporary socket path changed during chmod: {}",
                temporary.display()
            )));
        }
        Err(error) => {
            drop(listener);
            return Err(error);
        }
    }
    if let Err(source) = rustix::fs::renameat_with(
        rustix::fs::CWD,
        temporary,
        rustix::fs::CWD,
        target,
        rustix::fs::RenameFlags::NOREPLACE,
    ) {
        drop(listener);
        remove_owned_socket(temporary, identity);
        return Err(io_error(
            "gRPC socket publish",
            target,
            std::io::Error::from(source),
        ));
    }
    match socket_identity(target) {
        Ok(current) if current == identity => Ok((listener, identity)),
        Ok(_) => {
            drop(listener);
            Err(ScanRuntimeError::Invalid(format!(
                "gRPC socket path changed during publish: {}",
                target.display()
            )))
        }
        Err(error) => {
            drop(listener);
            Err(error)
        }
    }
}

fn validate_listener_descriptor(listener: &UnixListener, path: &Path) -> Result<()> {
    // Linux exposes different inode namespaces for the AF_UNIX descriptor and its
    // filesystem node. The descriptor proves socket type and bound address; the
    // path identity captured separately is the cleanup ownership token.
    let stat = rustix::fs::fstat(listener)
        .map_err(|source| io_error("gRPC listener identity", path, std::io::Error::from(source)))?;
    let local_path = listener
        .local_addr()
        .map_err(|source| io_error("gRPC listener address", path, source))?
        .as_pathname()
        .map(Path::to_path_buf);
    if rustix::fs::FileType::from_raw_mode(stat.st_mode) != rustix::fs::FileType::Socket
        || local_path.as_deref() != Some(path)
    {
        return Err(ScanRuntimeError::Invalid(format!(
            "gRPC listener does not own its bound socket: {}",
            path.display()
        )));
    }
    Ok(())
}

fn socket_identity(path: &Path) -> Result<SocketIdentity> {
    let metadata = std::fs::symlink_metadata(path)
        .map_err(|source| io_error("gRPC socket identity", path, source))?;
    if !metadata.file_type().is_socket() {
        return Err(ScanRuntimeError::Invalid(format!(
            "bound gRPC endpoint is not a socket: {}",
            path.display()
        )));
    }
    Ok(SocketIdentity {
        device: metadata.dev(),
        inode: metadata.ino(),
    })
}

fn remove_owned_socket(path: &Path, expected: SocketIdentity) {
    let Ok(metadata) = std::fs::symlink_metadata(path) else {
        return;
    };
    if metadata.file_type().is_socket()
        && metadata.dev() == expected.device
        && metadata.ino() == expected.inode
    {
        let _ = std::fs::remove_file(path);
    }
}

fn cleanup_owned_sockets(sockets: &[(PathBuf, SocketIdentity)]) {
    for (path, identity) in sockets {
        remove_owned_socket(path, *identity);
    }
}

async fn wait_for_shutdown(receiver: &mut watch::Receiver<bool>) {
    loop {
        if *receiver.borrow() || receiver.changed().await.is_err() {
            return;
        }
    }
}

fn write_all_statuses(
    lanes: &[Arc<LaneShared>; 2],
    started_at_unix_ms: i64,
    clock: &dyn RuntimeClock,
    id_source: &dyn RuntimeIdSource,
) -> Result<()> {
    let progress = lanes
        .iter()
        .map(|lane| {
            lane.progress.lock().map_err(|_| {
                ScanRuntimeError::Grpc("scan lane progress lock is poisoned".to_owned())
            })
        })
        .collect::<Result<Vec<_>>>()?;
    let now = clock.read()?;
    let mut pending = Vec::with_capacity(2);
    for (lane, progress) in lanes.iter().zip(&progress) {
        let status = DriverStatus::new(
            &lane.identity,
            started_at_unix_ms,
            now,
            progress.last_progress_monotonic_ns,
            progress.sequence,
            lane.frame_loss.load(Ordering::Relaxed),
            progress.last_scan_unix_ms,
        )?;
        pending.push((Arc::clone(lane), status, id_source.next_id()));
    }
    drop(progress);
    for (lane, _, temporary_id) in &pending {
        let temporary = temporary_status_path(&lane.status_path, *temporary_id)?;
        match std::fs::symlink_metadata(&temporary) {
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Ok(_) => {
                return Err(ScanRuntimeError::Invalid(format!(
                    "status temporary path already exists: {}",
                    temporary.display()
                )));
            }
            Err(source) => {
                return Err(io_error("status temporary preflight", &temporary, source));
            }
        }
    }
    let prepared = pending
        .into_iter()
        .map(|(lane, status, temporary_id)| {
            prepare_atomic_status(&lane.status_path, &status, temporary_id)
        })
        .collect::<Result<Vec<_>>>()?;
    for status in prepared {
        status.commit()?;
    }
    Ok(())
}

fn driver_identity(value: &str) -> bool {
    safe_identity(value, 64)
}

fn deployment_identity(value: &str) -> bool {
    safe_identity(value, 128)
}

fn safe_identity(value: &str, maximum: usize) -> bool {
    let bytes = value.as_bytes();
    (1..=maximum).contains(&bytes.len())
        && bytes[0].is_ascii_alphanumeric()
        && bytes[1..]
            .iter()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'.' | b'-'))
}

#[cfg(test)]
mod tests {
    use std::{
        fs,
        sync::{Arc, mpsc},
        thread,
        time::Duration,
    };

    use tempfile::TempDir;

    use super::*;
    use crate::scan_runtime::ClockReading;

    #[derive(Debug)]
    struct SignalingClock(mpsc::SyncSender<()>);

    impl RuntimeClock for SignalingClock {
        fn read(&self) -> Result<ClockReading> {
            self.0
                .send(())
                .map_err(|_| ScanRuntimeError::Clock("test clock receiver closed"))?;
            Ok(ClockReading {
                unix_ms: 1_800_000_000_000,
                monotonic_ns: 300,
            })
        }
    }

    #[derive(Debug)]
    struct FixedIds;

    impl RuntimeIdSource for FixedIds {
        fn next_id(&self) -> Uuid {
            Uuid::nil()
        }
    }

    fn status_test_lane(root: &Path, index: usize) -> Arc<LaneShared> {
        let service = SERVICES[index];
        let directory = root.join(service);
        fs::create_dir_all(&directory).unwrap();
        Arc::new(LaneShared {
            identity: StatusIdentity {
                service: service.to_owned(),
                edge_id: "edge".to_owned(),
                sensor_id: format!("lidar_{}", index + 1),
                config_revision: "r1".to_owned(),
                instance_id: Uuid::nil().to_string(),
                service_version: "0.9.0".to_owned(),
                site_id: "site".to_owned(),
                deployment_revision: "d1".to_owned(),
            },
            status_path: status_path(root, service),
            socket_path: root.join(format!("lidar_{}.sock", index + 1)),
            socket_identity: SocketIdentity {
                device: 0,
                inode: 0,
            },
            buffer: LatestTwo::new(),
            permits: Arc::new(Semaphore::new(MAX_SUBSCRIBERS_PER_SENSOR)),
            frame_loss: AtomicU64::new(0),
            published_frames: AtomicU64::new(0),
            progress: Mutex::new(LaneProgress::default()),
        })
    }

    #[test]
    fn status_locks_progress_before_reading_the_clock() {
        let root = TempDir::new().unwrap();
        let lanes = [
            status_test_lane(root.path(), 0),
            status_test_lane(root.path(), 1),
        ];
        let guarded_lane = Arc::clone(&lanes[0]);
        let progress = guarded_lane.progress.lock().unwrap();
        let (clock_sender, clock_receiver) = mpsc::sync_channel(1);
        let (started_sender, started_receiver) = mpsc::sync_channel(1);
        let writer = thread::spawn(move || {
            started_sender.send(()).unwrap();
            write_all_statuses(
                &lanes,
                1_800_000_000_000,
                &SignalingClock(clock_sender),
                &FixedIds,
            )
        });

        started_receiver.recv().unwrap();
        assert!(
            clock_receiver
                .recv_timeout(Duration::from_millis(100))
                .is_err()
        );
        drop(progress);
        clock_receiver.recv_timeout(Duration::from_secs(1)).unwrap();
        writer.join().unwrap().unwrap();
    }

    #[tokio::test]
    async fn second_bind_failure_removes_only_the_first_owned_socket() {
        let root = TempDir::new().unwrap();
        let config = ScanRuntimeConfig {
            sensor_ids: ["lidar_1".to_owned(), "lidar_2".to_owned()],
            socket_directory: root.path().join("sockets"),
            status_directory: root.path().join("status"),
            edge_id: "edge".to_owned(),
            config_revision: "r1".to_owned(),
            site_id: "site".to_owned(),
            deployment_revision: "d1".to_owned(),
            service_version: "0.9.0".to_owned(),
        };
        let first = config.socket_directory.join("lidar_1.sock");
        let second = config.socket_directory.join("lidar_2.sock");
        let hook = |index: usize, path: &Path| {
            if index == 1 {
                fs::write(path, b"racing-owner").unwrap();
            }
        };

        let result = GrpcScanRuntime::start_inner(
            config,
            Arc::new(SystemRuntimeClock),
            Arc::new(SystemRuntimeIdSource),
            Some(&hook),
        )
        .await;
        assert!(result.is_err());
        assert!(!first.exists());
        assert_eq!(fs::read(second).unwrap(), b"racing-owner");
    }
}
