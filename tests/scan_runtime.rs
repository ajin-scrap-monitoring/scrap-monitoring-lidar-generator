use std::{
    collections::{BTreeSet, VecDeque},
    fs,
    os::unix::fs::{FileTypeExt, MetadataExt, PermissionsExt},
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
    time::Duration,
};

use hyper_util::rt::TokioIo;
use scrap_monitoring_lidar_simulator::{
    scan_runtime::{
        ClockReading, GrpcScanRuntime, RuntimeClock, RuntimeIdSource, ScanRuntimeConfig,
        ScanRuntimeError,
    },
    wire::{
        ScanFrame, ScanSample, SubscribeRequest, lidar_scan_source_client::LidarScanSourceClient,
    },
};
use serde_json::Value;
use tempfile::TempDir;
use tokio::net::UnixStream;
use tonic::transport::{Channel, Endpoint};
use tower::service_fn;
use uuid::Uuid;

const FIXED_UUID: Uuid = Uuid::from_u128(0x12345678_1234_5678_1234_567812345678);

#[derive(Debug)]
struct SequenceClock {
    readings: Mutex<VecDeque<ClockReading>>,
    last: Mutex<ClockReading>,
}

impl SequenceClock {
    fn new(readings: impl IntoIterator<Item = ClockReading>) -> Self {
        let readings: VecDeque<_> = readings.into_iter().collect();
        let last = *readings.front().expect("at least one clock reading");
        Self {
            readings: Mutex::new(readings),
            last: Mutex::new(last),
        }
    }
}

impl RuntimeClock for SequenceClock {
    fn read(&self) -> Result<ClockReading, ScanRuntimeError> {
        let next = self.readings.lock().unwrap().pop_front();
        if let Some(reading) = next {
            *self.last.lock().unwrap() = reading;
            Ok(reading)
        } else {
            Ok(*self.last.lock().unwrap())
        }
    }
}

#[derive(Debug)]
struct FixedIds;

impl RuntimeIdSource for FixedIds {
    fn next_id(&self) -> Uuid {
        FIXED_UUID
    }
}

fn reading(unix_ms: i64, monotonic_ns: u64) -> ClockReading {
    ClockReading {
        unix_ms,
        monotonic_ns,
    }
}

fn config(root: &Path) -> ScanRuntimeConfig {
    ScanRuntimeConfig {
        sensor_ids: ["lidar_1".to_owned(), "lidar_2".to_owned()],
        socket_directory: root.join("sockets"),
        status_directory: root.join("status"),
        edge_id: "synthetic-edge".to_owned(),
        config_revision: "synthetic-r1".to_owned(),
        site_id: "synthetic-site".to_owned(),
        deployment_revision: "test-r1".to_owned(),
        service_version: "0.9.0".to_owned(),
    }
}

fn frame(sensor_id: &str, instance_id: &str, sequence: u64) -> ScanFrame {
    ScanFrame {
        schema_version: "1.0".to_owned(),
        edge_id: "synthetic-edge".to_owned(),
        sensor_id: sensor_id.to_owned(),
        sequence,
        acquired_at_unix_ms: 1_800_000_001_000 + i64::try_from(sequence).unwrap(),
        acquired_monotonic_ns: 10_000_000_000 + sequence,
        sdk_status: "OK".to_owned(),
        scan_hz: 3.0,
        samples: vec![ScanSample {
            angle_mdeg: u32::try_from(sequence).unwrap(),
            distance_mm: 1_000,
            quality: 12,
        }],
        instance_id: instance_id.to_owned(),
        config_revision: "synthetic-r1".to_owned(),
    }
}

async fn connect(path: PathBuf) -> LidarScanSourceClient<Channel> {
    let channel = Endpoint::try_from("http://[::]:50051")
        .unwrap()
        .connect_with_connector(service_fn(move |_| {
            let path = path.clone();
            async move { UnixStream::connect(path).await.map(TokioIo::new) }
        }))
        .await
        .unwrap();
    LidarScanSourceClient::new(channel)
}

async fn runtime(root: &Path) -> GrpcScanRuntime {
    GrpcScanRuntime::start(
        config(root),
        Arc::new(SequenceClock::new([reading(
            1_800_000_000_123,
            1_000_000_000,
        )])),
        Arc::new(FixedIds),
    )
    .await
    .unwrap()
}

#[tokio::test]
async fn two_uds_lanes_retain_two_frames_and_track_per_subscriber_loss() {
    let root = TempDir::new().unwrap();
    let mut runtime = runtime(root.path()).await;
    let instances = runtime.instance_ids();
    for sequence in 1..=3 {
        runtime
            .publish(frame("lidar_1", &instances["lidar_1"], sequence))
            .await
            .unwrap();
    }
    runtime
        .publish(frame("lidar_2", &instances["lidar_2"], 9))
        .await
        .unwrap();

    let mut first_client = connect(runtime.socket_paths()[0].clone()).await;
    let mut first = first_client
        .subscribe_scans(SubscribeRequest {
            consumer_id: "consumer-a".to_owned(),
        })
        .await
        .unwrap()
        .into_inner();
    assert_eq!(first.message().await.unwrap().unwrap().sequence, 2);
    assert_eq!(first.message().await.unwrap().unwrap().sequence, 3);

    let mut replay_client = connect(runtime.socket_paths()[0].clone()).await;
    let mut replay = replay_client
        .subscribe_scans(SubscribeRequest {
            consumer_id: "consumer-a".to_owned(),
        })
        .await
        .unwrap()
        .into_inner();
    assert_eq!(replay.message().await.unwrap().unwrap().sequence, 2);

    let mut second_client = connect(runtime.socket_paths()[1].clone()).await;
    let mut second = second_client
        .subscribe_scans(SubscribeRequest {
            consumer_id: "consumer-b".to_owned(),
        })
        .await
        .unwrap()
        .into_inner();
    assert_eq!(second.message().await.unwrap().unwrap().sequence, 9);
    let stats = runtime.stats();
    assert_eq!(stats.sensors[0].frame_loss, 2);
    assert_eq!(stats.sensors[0].published_frames, 3);
    assert_eq!(stats.sensors[1].frame_loss, 0);
    assert_eq!(stats.sensors[1].published_frames, 1);

    drop(first);
    drop(replay);
    drop(second);
    runtime.close().await;
}

#[tokio::test(flavor = "current_thread")]
async fn slow_uds_subscriber_resumes_at_retained_frames_and_reports_loss() {
    let root = TempDir::new().unwrap();
    let mut runtime = runtime(root.path()).await;
    let instance = runtime.instance_ids()["lidar_1"].clone();
    let mut client = connect(runtime.socket_paths()[0].clone()).await;
    let mut stream = client
        .subscribe_scans(SubscribeRequest {
            consumer_id: "slow-consumer".to_owned(),
        })
        .await
        .unwrap()
        .into_inner();

    for sequence in 1..=5 {
        runtime
            .publish(frame("lidar_1", &instance, sequence))
            .await
            .unwrap();
    }

    assert_eq!(stream.message().await.unwrap().unwrap().sequence, 4);
    assert_eq!(stream.message().await.unwrap().unwrap().sequence, 5);
    assert_eq!(runtime.stats().sensors[0].frame_loss, 3);
    runtime.close().await;
}

#[tokio::test]
async fn consumer_limits_use_utf8_bytes_and_release_cancelled_slots() {
    let root = TempDir::new().unwrap();
    let mut runtime = runtime(root.path()).await;
    let mut client = connect(runtime.socket_paths()[0].clone()).await;

    for invalid in [String::new(), "x".repeat(129), "가".repeat(43)] {
        let error = client
            .subscribe_scans(SubscribeRequest {
                consumer_id: invalid,
            })
            .await
            .unwrap_err();
        assert_eq!(error.code(), tonic::Code::InvalidArgument);
        assert_eq!(error.message(), "consumer_id required");
    }

    let mut subscriptions = Vec::new();
    for index in 0..8 {
        subscriptions.push(
            client
                .subscribe_scans(SubscribeRequest {
                    consumer_id: if index < 2 {
                        "duplicate".to_owned()
                    } else {
                        format!("consumer-{index}")
                    },
                })
                .await
                .unwrap()
                .into_inner(),
        );
    }
    assert_eq!(runtime.stats().sensors[0].subscribers, 8);
    let error = client
        .subscribe_scans(SubscribeRequest {
            consumer_id: "ninth".to_owned(),
        })
        .await
        .unwrap_err();
    assert_eq!(error.code(), tonic::Code::ResourceExhausted);
    assert_eq!(error.message(), "subscriber limit");

    subscriptions.clear();
    for _ in 0..100 {
        if runtime.stats().sensors[0].subscribers == 0 {
            break;
        }
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
    assert_eq!(runtime.stats().sensors[0].subscribers, 0);
    let accepted_128 = client
        .subscribe_scans(SubscribeRequest {
            consumer_id: "x".repeat(128),
        })
        .await
        .unwrap();
    drop(accepted_128);
    let accepted_multibyte = client
        .subscribe_scans(SubscribeRequest {
            consumer_id: "가".repeat(42),
        })
        .await
        .unwrap();
    drop(accepted_multibyte);
    runtime.close().await;
}

#[tokio::test]
async fn close_drains_grpc_stream_and_releases_its_subscriber_slot() {
    let root = TempDir::new().unwrap();
    let mut runtime = runtime(root.path()).await;
    let instance = runtime.instance_ids()["lidar_1"].clone();
    runtime
        .publish(frame("lidar_1", &instance, 1))
        .await
        .unwrap();
    runtime
        .publish(frame("lidar_1", &instance, 2))
        .await
        .unwrap();
    let mut client = connect(runtime.socket_paths()[0].clone()).await;
    let mut stream = client
        .subscribe_scans(SubscribeRequest {
            consumer_id: "drain".to_owned(),
        })
        .await
        .unwrap()
        .into_inner();
    assert_eq!(runtime.stats().sensors[0].subscribers, 1);
    runtime.close().await;
    assert_eq!(stream.message().await.unwrap().unwrap().sequence, 1);
    assert_eq!(stream.message().await.unwrap().unwrap().sequence, 2);
    assert!(stream.message().await.unwrap().is_none());
    assert_eq!(runtime.stats().sensors[0].subscribers, 0);
}

#[tokio::test]
async fn status_has_exact_schema_and_transitions_to_healthy() {
    let root = TempDir::new().unwrap();
    let clock = Arc::new(SequenceClock::new([
        reading(1_800_000_000_123, 1_000_000_000),
        reading(1_800_000_000_123, 1_000_000_000),
        reading(1_800_000_001_000, 2_000_000_000),
        reading(1_800_000_005_000, 5_000_000_000),
    ]));
    let mut runtime = GrpcScanRuntime::start(config(root.path()), clock, Arc::new(FixedIds))
        .await
        .unwrap();
    let status_path = root
        .path()
        .join("status/lidar-driver-a/lidar-driver-a.json");
    let initial_bytes = fs::read(&status_path).unwrap();
    assert!(initial_bytes.is_ascii());
    assert_eq!(initial_bytes.last(), Some(&b'\n'));
    let initial: Value = serde_json::from_slice(&initial_bytes).unwrap();
    let field_names: BTreeSet<_> = initial
        .as_object()
        .unwrap()
        .keys()
        .map(String::as_str)
        .collect();
    assert_eq!(
        field_names,
        BTreeSet::from([
            "schema_version",
            "service",
            "edge_id",
            "sensor_id",
            "config_revision",
            "instance_id",
            "started_at",
            "updated_at",
            "last_progress_at",
            "state",
            "reason_codes",
            "sequence",
            "frame_loss",
            "sdk_errors",
            "last_scan_unix_ms",
            "reported_at",
            "service_version",
            "site_id",
            "deployment_revision",
        ])
    );
    assert_eq!(initial["service"], "lidar-driver-a");
    assert_eq!(initial["sensor_id"], "lidar_1");
    assert_eq!(initial["state"], "STARTING");
    assert_eq!(initial["sequence"], 0);
    assert_eq!(initial["last_scan_unix_ms"], 0);
    assert_eq!(initial["updated_at"], initial["reported_at"]);
    assert_eq!(initial["updated_at"], initial["last_progress_at"]);
    assert_eq!(initial["started_at"], "2027-01-15T08:00:00.123Z");
    assert_eq!(initial["updated_at"], "2027-01-15T08:00:00.123Z");

    let second_status: Value = serde_json::from_slice(
        &fs::read(
            root.path()
                .join("status/lidar-driver-b/lidar-driver-b.json"),
        )
        .unwrap(),
    )
    .unwrap();
    assert_eq!(second_status["service"], "lidar-driver-b");
    assert_eq!(second_status["sensor_id"], "lidar_2");

    let instance = runtime.instance_ids()["lidar_1"].clone();
    let receipt = runtime
        .publish(frame("lidar_1", &instance, 7))
        .await
        .unwrap();
    assert_eq!(receipt.published_at_monotonic_ns, 2_000_000_000);
    tokio::time::sleep(Duration::from_millis(2_100)).await;
    let healthy: Value = serde_json::from_slice(&fs::read(&status_path).unwrap()).unwrap();
    assert_eq!(healthy.as_object().unwrap().len(), 19);
    assert_eq!(healthy["state"], "HEALTHY");
    assert_eq!(healthy["sequence"], 7);
    assert_eq!(healthy["last_scan_unix_ms"], 1_800_000_001_007_i64);
    assert_eq!(healthy["updated_at"], healthy["reported_at"]);
    assert_eq!(healthy["updated_at"], "2027-01-15T08:00:05.000Z");
    assert_eq!(healthy["last_progress_at"], "2027-01-15T08:00:02.000Z");
    assert_eq!(
        fs::read(&status_path).unwrap(),
        include_bytes!("fixtures/rust-status/lidar-driver-a.json")
    );
    assert!(
        fs::read_dir(status_path.parent().unwrap())
            .unwrap()
            .all(|entry| !entry
                .unwrap()
                .file_name()
                .to_string_lossy()
                .ends_with(".tmp"))
    );

    runtime.close().await;
    assert!(status_path.is_file());
}

#[tokio::test]
async fn invalid_publish_does_not_mutate_lane_state() {
    let root = TempDir::new().unwrap();
    let mut runtime = runtime(root.path()).await;
    let instance = runtime.instance_ids()["lidar_1"].clone();
    let mut invalid_frames = Vec::new();
    let mut value = frame("lidar_1", &instance, 1);
    value.schema_version = "2.0".to_owned();
    invalid_frames.push(value);
    let mut value = frame("lidar_1", &instance, 1);
    value.edge_id = "other".to_owned();
    invalid_frames.push(value);
    let mut value = frame("lidar_1", &instance, 1);
    value.config_revision = "other".to_owned();
    invalid_frames.push(value);
    let mut value = frame("lidar_1", &instance, 1);
    value.instance_id = "other".to_owned();
    invalid_frames.push(value);
    let mut value = frame("lidar_1", &instance, 1);
    value.sdk_status = "ERROR".to_owned();
    invalid_frames.push(value);
    invalid_frames.push(frame("unknown", &instance, 1));

    for invalid in invalid_frames {
        assert!(runtime.publish(invalid).await.is_err());
    }
    assert_eq!(runtime.stats().sensors[0].published_frames, 0);
    assert_eq!(runtime.stats().sensors[1].published_frames, 0);
    runtime.close().await;
}

#[tokio::test]
async fn preflight_preserves_every_path_when_one_endpoint_is_not_a_socket() {
    let root = TempDir::new().unwrap();
    let config = config(root.path());
    fs::create_dir_all(&config.socket_directory).unwrap();
    let first = config.socket_directory.join("lidar_1.sock");
    let stale = std::os::unix::net::UnixListener::bind(&first).unwrap();
    drop(stale);
    let second = config.socket_directory.join("lidar_2.sock");
    fs::write(&second, b"preserve").unwrap();

    assert!(
        GrpcScanRuntime::start(
            config,
            Arc::new(SequenceClock::new([reading(1, 1)])),
            Arc::new(FixedIds),
        )
        .await
        .is_err()
    );
    assert!(fs::symlink_metadata(first).unwrap().file_type().is_socket());
    assert_eq!(fs::read(second).unwrap(), b"preserve");
}

#[tokio::test]
async fn stale_sockets_are_replaced_after_complete_preflight() {
    let root = TempDir::new().unwrap();
    let config = config(root.path());
    fs::create_dir_all(&config.socket_directory).unwrap();
    let paths = [
        config.socket_directory.join("lidar_1.sock"),
        config.socket_directory.join("lidar_2.sock"),
    ];
    for path in &paths {
        let listener = std::os::unix::net::UnixListener::bind(path).unwrap();
        drop(listener);
    }
    let mut runtime = GrpcScanRuntime::start(
        config,
        Arc::new(SequenceClock::new([reading(1, 1)])),
        Arc::new(FixedIds),
    )
    .await
    .unwrap();
    for path in &paths {
        let metadata = fs::symlink_metadata(path).unwrap();
        assert!(metadata.file_type().is_socket());
        drop(connect(path.clone()).await);
    }
    runtime.close().await;
    for path in paths {
        assert!(!path.exists());
    }
}

#[tokio::test]
async fn sockets_are_mode_0660_and_replaced_path_is_preserved_on_close() {
    let root = TempDir::new().unwrap();
    let mut runtime = runtime(root.path()).await;
    let paths = runtime.socket_paths();
    for path in &paths {
        assert_eq!(
            fs::metadata(path).unwrap().permissions().mode() & 0o777,
            0o660
        );
    }
    fs::remove_file(&paths[0]).unwrap();
    fs::write(&paths[0], b"replacement").unwrap();
    runtime.close().await;
    assert_eq!(fs::read(&paths[0]).unwrap(), b"replacement");
    assert!(!paths[1].exists());
}

#[tokio::test]
async fn replacement_socket_inode_is_preserved_on_close() {
    let root = TempDir::new().unwrap();
    let mut runtime = runtime(root.path()).await;
    let path = runtime.socket_paths()[0].clone();
    fs::remove_file(&path).unwrap();
    let replacement = std::os::unix::net::UnixListener::bind(&path).unwrap();
    let replacement_metadata = fs::symlink_metadata(&path).unwrap();

    runtime.close().await;

    let remaining = fs::symlink_metadata(&path).unwrap();
    assert!(remaining.file_type().is_socket());
    assert_eq!(remaining.dev(), replacement_metadata.dev());
    assert_eq!(remaining.ino(), replacement_metadata.ino());
    drop(replacement);
}

#[tokio::test]
async fn initial_status_failure_rolls_back_both_owned_sockets() {
    let root = TempDir::new().unwrap();
    let config = config(root.path());
    let status_directory = config.status_directory.join("lidar-driver-b");
    fs::create_dir_all(&status_directory).unwrap();
    let collision = status_directory.join(format!(".lidar-driver-b.json.{FIXED_UUID}.tmp"));
    fs::write(&collision, b"owned-by-someone-else").unwrap();
    let result = GrpcScanRuntime::start(
        config.clone(),
        Arc::new(SequenceClock::new([reading(1, 1)])),
        Arc::new(FixedIds),
    )
    .await;
    assert!(result.is_err());
    assert_eq!(fs::read(collision).unwrap(), b"owned-by-someone-else");
    assert!(
        !config
            .status_directory
            .join("lidar-driver-a/lidar-driver-a.json")
            .exists()
    );
    assert!(!config.socket_directory.join("lidar_1.sock").exists());
    assert!(!config.socket_directory.join("lidar_2.sock").exists());
}

#[tokio::test]
async fn periodic_status_failure_is_reported_as_fatal() {
    let root = TempDir::new().unwrap();
    let mut runtime = runtime(root.path()).await;
    let service = root.path().join("status/lidar-driver-a");
    let displaced = root.path().join("status/lidar-driver-a.displaced");
    fs::rename(&service, &displaced).unwrap();
    fs::write(&service, b"block-directory").unwrap();
    let mut fatal = runtime.fatal_receiver();
    tokio::time::timeout(Duration::from_secs(3), fatal.changed())
        .await
        .unwrap()
        .unwrap();
    assert!(
        fatal
            .borrow()
            .as_deref()
            .unwrap()
            .starts_with("driver status write failed:")
    );
    runtime.close().await;
}

#[tokio::test]
async fn endpoint_allows_100_bytes_and_rejects_101_bytes() {
    fn socket_directory(endpoint_bytes: usize, marker: char) -> PathBuf {
        let filename_bytes = "/lidar_1.sock".len();
        let directory_bytes = endpoint_bytes - "unix:".len() - filename_bytes;
        let prefix = "/tmp/";
        let process = format!("{:08x}", std::process::id());
        let fill = directory_bytes - prefix.len() - process.len();
        PathBuf::from(format!(
            "{prefix}{}{process}",
            marker.to_string().repeat(fill)
        ))
    }

    let status = TempDir::new().unwrap();
    let accepted_directory = socket_directory(100, 'a');
    let mut accepted = config(status.path());
    accepted.socket_directory = accepted_directory.clone();
    let mut runtime = GrpcScanRuntime::start(
        accepted,
        Arc::new(SequenceClock::new([reading(1, 1)])),
        Arc::new(FixedIds),
    )
    .await
    .unwrap();
    runtime.close().await;
    fs::remove_dir(&accepted_directory).unwrap();

    let rejected_directory = socket_directory(101, 'b');
    let mut rejected = config(status.path());
    rejected.socket_directory = rejected_directory.clone();
    assert!(
        GrpcScanRuntime::start(
            rejected,
            Arc::new(SequenceClock::new([reading(1, 1)])),
            Arc::new(FixedIds),
        )
        .await
        .is_err()
    );
    assert!(!rejected_directory.exists());
}

#[tokio::test]
async fn runtime_rejects_relative_paths_duplicate_sensors_and_unsafe_identities() {
    let root = TempDir::new().unwrap();
    let cases = [
        {
            let mut value = config(root.path());
            value.socket_directory = PathBuf::from("relative");
            value
        },
        {
            let mut value = config(root.path());
            value.sensor_ids[1] = value.sensor_ids[0].clone();
            value
        },
        {
            let mut value = config(root.path());
            value.edge_id = "unsafe/value".to_owned();
            value
        },
        {
            let mut value = config(root.path());
            value.service_version = "unsafe value".to_owned();
            value
        },
    ];
    for invalid in cases {
        assert!(
            GrpcScanRuntime::start(
                invalid,
                Arc::new(SequenceClock::new([reading(1, 1)])),
                Arc::new(FixedIds),
            )
            .await
            .is_err()
        );
    }
}
