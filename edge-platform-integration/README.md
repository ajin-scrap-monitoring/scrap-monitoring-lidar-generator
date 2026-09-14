# Edge platform LiDAR 연동 계약

이 디렉토리는 생성기를 `ajin-edge-platform`의 LiDAR driver test double로 연결하는 데 필요한
계약과 배포 경계를 제공한다. 높이 계산 프로세스의 계약 정본은 `SOURCE.json`이 고정한 외부
Repository commit이다.

전달 파일은 4개다.

| 파일 | 역할 |
|---|---|
| `README.md` | 연결, 변환과 수락 기준 |
| `SOURCE.json` | 담당자 정본 commit과 검토한 source 경로 |
| `v1/lidar.proto` | gRPC scan 계약의 고정 사본 |
| `v1/processing.synthetic.json` | 공개 합성 환경의 완전한 처리 입력 fixture |

## 구성 요소

연동 구성 요소는 다음 4개다.

| 구성 요소 | 책임 |
|---|---|
| 생성기 | 두 센서의 합성 scan 생성과 센서별 gRPC server 제공 |
| `lidar-processing` | 두 gRPC stream 구독과 높이 계산 |
| 생성기 JSON | 합성 환경, 센서 설치, 시나리오와 측정 모델 정의 |
| 처리 JSON | 센서 강체 변환, 단면 ROI, 필터와 융합 보정 정의 |

생성기는 `lidar-driver-a`와 `lidar-driver-b`의 실행 위치를 하나의 프로세스로 대체한다. 공유
적재 모델을 한 번만 계산하지만 `lidar_1.sock`과 `lidar_2.sock`을 독립적인 UDS(Unix Domain
Socket) endpoint로 제공한다. `lidar-processing`이 각 endpoint의 `SubscribeScans`를 호출한다.

```text
Generator container                     Processing container
+-----------------------------+         +-----------------------------+
| Shared synthetic load model |         | lidar-processing            |
| lidar_1 gRPC server          |<--------| subscriber for lidar_1      |
| lidar_2 gRPC server          |<--------| subscriber for lidar_2      |
+-----------------------------+   UDS   +-----------------------------+
```

## Scan 계약

`v1/lidar.proto`는 외부 계약의 고정 사본이다. 생성기는 SDK(Software Development Kit)의 HQ
측정값을 담당자 driver와 같은 규칙으로 정규화하여 `ScanFrame`을 만든다.

| 필드 | 생성기 의미 |
|---|---|
| `schema_version` | `1.0` |
| `edge_id` | 배포 설정의 `EDGE_ID` |
| `sensor_id` | `lidar_1` 또는 `lidar_2` |
| `sequence` | 센서 instance 안에서 1부터 증가하는 완료 scan 순번 |
| `acquired_at_unix_ms` | scan 생성 완료 Unix 시각 |
| `acquired_monotonic_ns` | scan 생성 완료 단조 시각 |
| `sdk_status` | 정상 생성 frame의 `OK` |
| `scan_hz` | 직전 완료 scan과의 단조 시각 간격으로 계산한 주기 |
| `samples` | 각도 오름차순의 mm 및 SDK quality 정규화 결과 |
| `instance_id` | 생성기 재시작마다 센서별로 바뀌는 UUID |
| `config_revision` | 배포 설정의 `CONFIG_REVISION` |

각도는 HQ `angle_z_q14`를 담당자 driver와 같은 정수 반올림식으로 `angle_mdeg`에 변환한다.
거리는 HQ `dist_mm_q2`를 4로 나눈 정수 mm이며 quality는 HQ byte를 오른쪽으로 2 bit 이동한
값이다. 첫 완료 scan은 실제 `scan_hz` 기준을 만들기 위해 전송하지 않는다.

각 센서 server는 최신 frame 2개만 보관한다. 느린 구독자는 오래된 frame을 받지 않으며
`sequence` 간격으로 손실을 확인한다. `consumer_id`는 UTF-8 기준 1-128 byte이며 센서별 동시
구독자는 최대 8개다.

## 환경과 처리 설정

생성기의 `examples/environment.v1.json`은 합성 환경의 정본이다. `lidar-processing`은 이
schema를 직접 읽지 않는다. 배포 준비 단계에서 다음 exporter가 환경과 품질 설정을 담당자
처리 JSON으로 변환한다.

```shell
scrap-monitoring-lidar-generator-export-processing-config \
  --generator-config examples/generator.v2.json \
  --socket-dir /sockets \
  --site-id synthetic-site \
  --edge-id synthetic-edge \
  --config-revision synthetic-r1 \
  --output processing.synthetic.json
```

`SITE_ID`, `EDGE_ID`, `CONFIG_REVISION`과 `SCRAP_LIDAR_GENERATOR_CONFIG` 환경변수로 같은 값을
제공할 수 있다. 실제 edge platform 전체 설정에 결합할 때는 `--base-config`로 담당자 설정을
읽는다. exporter는 배포 revision, camera와 service version manifest 등 다른 최상위 필드를
보존하고 세 식별자가 일치하지 않으면 실패한다. manifest의 `lidar-driver-a`와
`lidar-driver-b`는 생성기 package version으로 갱신한다.

담당자 `lidar-processing`의 `CONFIG_SHA256`은 exporter가 쓴 최종 설정 파일에서 계산한다.

```shell
CONFIG_SHA256="$(sha256sum /config/edge.json | awk '{print $1}')"
```

```shell
scrap-monitoring-lidar-generator-export-processing-config \
  --generator-config /config/generator.v2.json \
  --base-config /config/edge-base.json \
  --socket-dir /sockets \
  --site-id "$SITE_ID" \
  --edge-id "$EDGE_ID" \
  --config-revision "$CONFIG_REVISION" \
  --output /config/edge.json
```

`v1/processing.synthetic.json`은 공개 합성 환경에서 exporter가 만든 검증 fixture다. 두 센서의
아래 방향 90도 구간 중 적재 공간 내부를 연속으로 관측하는 긴 구간을 선택하며 50 mm 단면
bin을 만든다. 이 설정의 `demo`와 `allow_demo_calibration`은 모두 `true`다. `fusion_map`은
균일 높이에서 단면비와 체적비가 일치하는 합성 기준의 항등 mapping이다. 실제 현장
calibration으로 사용하지 않는다.

## 좌표 변환 기준

생성기의 sensor 광선과 담당자 처리 좌표는 다음 식으로 연결한다.

```text
world_point = p0 + distance * (cos(angle) * u0 + sin(angle) * u90)
sdk_point = [distance * cos(-angle), distance * sin(-angle), 0]
section_point = rotation * sdk_point + translation
```

section x축은 환경의 `-u90`, section z축은 환경의 상단 방향이다. `rotation`과
`translation_mm`은 위 두 표현의 같은 광선 교차점이 같은 section x 및 z 좌표를 갖도록
계산한다. exporter는 바닥부터 상단까지 경계 polygon 내부에 남는 50 mm column의 연속 구간을
ROI(Region of Interest)로 선택한다. 담당자 구현이 요구하는 비순환 angle interval 때문에
0-90도 또는 270-360도인 아래 방향 한 quadrant만 사용한다.

`bottom_mm`과 `max_height_mm`은 선택한 각 column의 바닥 및 상단 절대 z 좌표다. 길이는
`(roi_x_max - roi_x_min) / 50`과 같다. `base_weight`는 두 sensor가 각각 0.5이고
`fusion_map`은 공개 합성 환경의 균일 높이 검증을 위한 `[0,0]`, `[1,1]` mapping이다. fixture가
담당자 loader와 `ProcessingEngine`에서 `GOOD` 상태와 sensor별 coverage 1.0을 만드는지는 CI가
직접 검증한다.

## 상태 계약

생성기는 상태 root에 `lidar-driver-a/lidar-driver-a.json`과
`lidar-driver-b/lidar-driver-b.json`을 기록한다. 첫 번째 환경 센서는 service
`lidar-driver-a`, 두 번째 환경 센서는 `lidar-driver-b`에 대응한다. 이 하위 경로는 담당자
orchestrator의 수집 구조와 같다. 상태 schema, 식별자, freshness, sequence, frame loss와 정상
상태 표현은 외부 driver 계약을 따른다.

## 관찰 stream 경계

TCP(Transmission Control Protocol) JSON Lines observation stream은 시각화 전용이다. 환경
형상은 최초 observation header에 포함되며 `lidar-processing`은 이 stream을 읽지 않는다.
scan gRPC 계약에는 환경 형상이나 observation record를 추가하지 않는다.

## 수락 검증

다음 명령은 고정한 외부 commit과 Proto가 같은지 확인하고, 실제 담당자 설정 loader와
`ProcessingEngine`이 생성 frame을 받아 두 센서 단면을 `GOOD` 상태와 전체 coverage로
계산하는지 검증한다.

```shell
uv run --locked python -m tools.verify_edge_platform_contract \
  --edge-platform-root /path/to/ajin-edge-platform
```
