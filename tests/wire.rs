use prost::Message;
use scrap_monitoring_lidar_simulator::wire::{ScanFrame, ScanSample, SubscribeRequest};
use sha2::{Digest, Sha256};

fn decode_hex(value: &str) -> Vec<u8> {
    assert_eq!(value.len() % 2, 0);
    value
        .as_bytes()
        .as_chunks::<2>()
        .0
        .iter()
        .map(|pair| {
            let high = (pair[0] as char).to_digit(16).unwrap();
            let low = (pair[1] as char).to_digit(16).unwrap();
            ((high << 4) | low) as u8
        })
        .collect()
}

#[test]
fn generated_message_round_trips_every_external_field() {
    let frame = ScanFrame {
        schema_version: "1.0".into(),
        edge_id: "synthetic-edge".into(),
        sensor_id: "lidar_1".into(),
        sequence: u64::MAX,
        acquired_at_unix_ms: 1_800_000_000_000,
        acquired_monotonic_ns: u64::MAX - 1,
        sdk_status: "OK".into(),
        scan_hz: 10.0,
        samples: vec![ScanSample {
            angle_mdeg: 359_999,
            distance_mm: 30_000,
            quality: 63,
        }],
        instance_id: "synthetic-instance".into(),
        config_revision: "synthetic-r1".into(),
    };
    assert_eq!(
        ScanFrame::decode(frame.encode_to_vec().as_slice()).unwrap(),
        frame
    );
    let request = SubscribeRequest {
        consumer_id: "consumer".into(),
    };
    assert_eq!(request.encode_to_vec(), b"\x0a\x08consumer");
}

#[test]
fn proto_matches_source_metadata_and_integration_bundle() {
    let proto = include_bytes!("../contracts/lidar/v1/lidar.proto");
    let source: serde_json::Value =
        serde_json::from_str(include_str!("../contracts/lidar/v1/upstream.json")).unwrap();
    let checksum: String = Sha256::digest(proto)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect();
    assert_eq!(checksum, source["sha256"]);
    assert_eq!(
        proto.as_slice(),
        include_bytes!("../edge-platform-integration/v1/lidar.proto").as_slice()
    );
}

#[test]
fn model_v1_fixture_protobuf_decodes_with_exact_fields_and_sample_order() {
    let fixture: serde_json::Value =
        serde_json::from_str(include_str!("fixtures/model-v1/scan-frames.json")).unwrap();
    let mut checked = 0;
    for record in fixture["records"]
        .as_array()
        .unwrap()
        .iter()
        .chain(std::iter::once(&fixture["stable_sort_case"]))
    {
        let (Some(frame), Some(encoded)) = (record.get("frame"), record.get("protobuf_hex")) else {
            continue;
        };
        if frame.is_null() || encoded.is_null() {
            continue;
        }
        let decoded = ScanFrame::decode(decode_hex(encoded.as_str().unwrap()).as_slice()).unwrap();
        assert_eq!(decoded.schema_version, frame["schema_version"]);
        assert_eq!(decoded.edge_id, frame["edge_id"]);
        assert_eq!(decoded.sensor_id, frame["sensor_id"]);
        assert_eq!(decoded.sequence, frame["sequence"].as_u64().unwrap());
        assert_eq!(
            decoded.acquired_at_unix_ms,
            frame["acquired_at_unix_ms"].as_i64().unwrap()
        );
        assert_eq!(
            decoded.acquired_monotonic_ns,
            frame["acquired_monotonic_ns"].as_u64().unwrap()
        );
        assert_eq!(decoded.sdk_status, frame["sdk_status"]);
        assert_eq!(decoded.scan_hz, frame["scan_hz"].as_f64().unwrap());
        assert_eq!(decoded.instance_id, frame["instance_id"]);
        assert_eq!(decoded.config_revision, frame["config_revision"]);
        let expected_samples = frame["samples"].as_array().unwrap();
        assert_eq!(decoded.samples.len(), expected_samples.len());
        for (index, (actual, expected)) in decoded.samples.iter().zip(expected_samples).enumerate()
        {
            assert_eq!(
                u64::from(actual.angle_mdeg),
                expected["angle_mdeg"].as_u64().unwrap(),
                "angle mismatch at sample {index}"
            );
            assert_eq!(
                u64::from(actual.distance_mm),
                expected["distance_mm"].as_u64().unwrap(),
                "distance mismatch at sample {index}"
            );
            assert_eq!(
                u64::from(actual.quality),
                expected["quality"].as_u64().unwrap(),
                "quality mismatch at sample {index}"
            );
        }
        checked += 1;
    }
    assert_eq!(checked, 7);
}
