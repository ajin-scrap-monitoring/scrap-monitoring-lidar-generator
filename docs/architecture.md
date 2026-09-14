# 아키텍처

## 실행 구성 요소

실행 구성 요소는 4개다.

| 구성 요소 | 위치 | 책임 |
| --- | --- | --- |
| 합성 생성기 | 이 Repository의 container | 적재 모델, 센서별 scan, 관찰 stream과 상태 생성 |
| `lidar-processing` | 같은 edge 장비의 별도 container | 센서별 scan 구독, 단면 높이 계산과 융합 |
| 상태 수집기 | edge platform | 생성기 상태 파일 수집 |
| 시각화 프로그램 | 별도 개발 장비 | 관찰 stream의 3D 표시, 기록과 MP4 생성 |

생성기와 `lidar-processing`은 같은 host의 UDS(Unix Domain Socket)를 공유한다. 생성기가 센서별
gRPC(Google Remote Procedure Call) server이고 `lidar-processing`이 server-streaming 구독자다.
관찰 stream은 router를 통과할 수 있는 JSON Lines TCP 연결이며 scan 경로와 독립적이다.

```text
                       shared host directory
+------------------+  lidar_1.sock  +------------------+
| generator        |--------------->| lidar-processing |
| shared model     |  lidar_2.sock  | gRPC subscribers |
+--------+---------+--------------->+------------------+
         |
         | JSON Lines TCP
         v
+------------------+
| visualizer       |
| remote machine   |
+------------------+
```

## 패키지 경계

기본 생성기 진입점과 container는 Python 구현을 실행한다. Python 패키지는
`src/scrap_monitoring_lidar_generator/` 아래에 있다.

| 패키지 | 책임 |
| --- | --- |
| `configuration/` | versioned JSON loader와 교차 입력 검증 |
| `geometry/` | 좌표계, 다각형과 광선 교차 |
| `scenario/` | 적재 및 수거 상태와 2.5D 표면 |
| `measurement/` | 센서 회전, 광선 측정과 합성 오차 |
| `scan_stream/` | 외부 ScanFrame 변환, sensor별 gRPC UDS server와 상태 파일 |
| `observation/` | 읽기 전용 적재 모델 관찰 snapshot과 latest-only TCP publisher |
| `edge_integration/` | 합성 환경에서 `lidar-processing` 설정으로의 결정론적 변환 |
| `wire/` | 고정한 외부 Proto의 생성 Python binding |
| `runtime/` | 생성, 출력, 진단과 종료 생명주기 조립 |
| `_cli_settings.py` | CLI와 환경변수 계층 검증 |
| `cli.py` | 생성기 CLI 진입점 |

의존 방향은 다음과 같다.

```text
cli -> configuration
cli -> runtime
runtime -> scenario
runtime -> measurement
runtime -> scan_stream
runtime -> observation
edge_integration -> configuration
scan_stream -> wire
measurement -> geometry
scenario -> geometry
```

`geometry`는 다른 프로젝트 패키지를 참조하지 않는다. `scenario`와 `measurement`는 외부 출력
형식을 알지 않는다. `scan_stream`은 측정 결과를 외부 wire 표현으로만 바꾸며 적재 모델을
변경하지 않는다. `edge_integration`은 실행 중 scan 경로에 참여하지 않는다.

## Rust 설정 검증 경계

Rust crate의 실행 경계는 5개다. `src/lib.rs`는 설정 및 wire API를 공개하며 별도
`scrap-monitoring-lidar-generator-rust` binary는 입력 검증만 수행한다.

| 경로 | 책임 |
| --- | --- |
| `src/configuration/` | 세 JSON loader, 중복 key와 숫자 type 검증, 설정 자료형과 교차 입력 검증 |
| `src/cli.rs` | CLI(Command-Line Interface), 환경변수와 JSON override 계층 및 `check` 명령 |
| `src/error.rs` | 오류 분류, 입력 경로와 메시지 |
| `src/lib.rs`의 `wire` | 고정 Proto에서 빌드 시 생성한 tonic/prost client, server와 message binding |
| `src/main.rs` | 별도 검증 binary의 입력, 출력과 종료 코드 |

`cli`는 `configuration`과 `error`에 의존한다. `configuration`의 다각형 검증은 설정 검증
내부에 있으며 Python 실행 경로와 독립적이다. `build.rs`는
`contracts/lidar/v1/lidar.proto`를 잠근 compiler와 binding 생성기로 처리한다. 생성 파일은
Cargo 빌드 출력에만 두며 고정 Proto 원문과 Python binding을 변경하지 않는다.

`cargo run --locked --bin scrap-monitoring-lidar-generator-rust -- check --config examples/generator.v2.json`은
참조된 세 JSON과 모델 override를 검증한다. `check --runtime`은 UDS 경로, 상태 경로, 배포
식별자와 관찰 endpoint도 요구한다. 두 명령은 scan 생성, socket 생성과 상태 출력을 하지
않으며 성공 시 0, 설정 오류 시 2로 종료한다.

`cargo test --locked`는 Rust 단위 테스트와 `tests/configuration.rs`, `tests/cli.rs`,
`tests/wire.rs`의 공개 입력, override, 오류와 Proto 회귀 검증을 실행한다.
`cargo fmt --check`와 `cargo clippy --locked --all-targets -- -D warnings`는 Rust 정적 검증이다.

## 설정과 계약

설정 정본은 역할별로 분리한다.

| 경로 | 책임 |
| --- | --- |
| `contracts/environment/v1/` | 합성 공간과 센서 설치 schema |
| `contracts/v2/` | 생성 실행 schema |
| `contracts/quality/v1/` | 합성 quality 분포 schema |
| `contracts/lidar/v1/` | `ajin-edge-platform` scan Proto와 고정 출처 |
| `contracts/observation/v1/` | 시각화 관찰 stream schema와 fixture |
| `examples/` | 공개 합성 환경과 실행 입력 정본 |
| `edge-platform-integration/` | 다른 Repository에 전달할 자기완결 통합 묶음 |

생성기의 JSON은 적재 환경, 센서 설치, 시나리오와 합성 측정만 정의한다.
`lidar-processing`은 이 JSON을 직접 읽지 않는다. `edge_integration` exporter가 센서 설치를
처리 좌표 변환, 50 mm 단면 ROI(Region of Interest)와 측정 범위로 변환하고 처리기가 요구하는
데모 calibration을 채운다. exporter는 실제 현장 설정이나 적재율 보정을 만들지 않는다. scan
frame에는 환경 정의를 포함하지 않는다.

scan 계약의 정본은 `ajin-edge-platform`의 고정 commit이다. 로컬 Proto와 생성 binding은
출처 commit과 SHA-256으로 검증한다. `tools/generate_lidar_wire.py --check`는 binding이 고정
Proto와 일치하는지 검사하고, `tools/verify_edge_platform_contract.py`는 실제 `lidar-processing` loader와
높이 계산 engine이 합성 설정과 frame을 수락하는지 검사한다.

## 적재 모델과 측정

`scenario.HeightField`는 경계 다각형 안의 node별 높이와 node 면적을 유지한다. 투입은 활성
투입구 주변에 국소 부피와 요철을 더한다. 각 갱신은 합성 안식각 35도 기준의 경사 이완을 최대
32회 수행하여 높은 node의 부피를 낮은 이웃으로 옮긴다. 수거는 전체 점유 표면을 낮추고 종료
시 빈 상태를 만든다. 같은 설정과 seed는 같은 시뮬레이션 표면을 만든다.

`measurement.SensorRotationScheduler`는 두 센서의 독립 회전과 내부 `scan_id`를 관리한다.
측정점은 sample rate와 rotation rate에서 계산한 시뮬레이션 시각에 생성한다. 한 scan의 배열
길이는 고정 계약이 아니며 회전 경계에 포함된 실제 측정점 수로 정한다. 장면은 적재 표면,
바닥, 외벽과 고정 표면 중 센서에서 가장 가까운 광선 교차를 반환하므로 허공은 거리 0, 벽과
바닥은 해당 교차 거리로 표현한다.

합성 오차는 거리 noise, 낙하물, 빈틈, 수거 가림, 반사 경로 오류, dropout과 quality 분포를
분리해 적용한다. 기준 교차 결과와 최종 측정 결과는 별도 자료형으로 유지한다.

## ScanFrame 변환

`scan_stream.ScanFrameFactory`는 `ajin-edge-platform` SDK(Software Development Kit) adapter의 출력 규칙을
그대로 적용한다.

| 필드 | 생성 규칙 |
| --- | --- |
| `angle_mdeg` | HQ Q14 각도를 외부 driver 정수식으로 millidegree 변환 후 안정 정렬 |
| `distance_mm` | HQ Q2 거리를 정수 나눗셈으로 millimeter 변환 |
| `quality` | 합성 8-bit HQ quality를 오른쪽으로 2 bit 이동 |
| `acquired_at_unix_ms` | scan 완료 시점의 wall clock |
| `acquired_monotonic_ns` | scan 완료 시점의 monotonic clock |
| `scan_hz` | 같은 sensor의 연속 완료 monotonic 시각 차이 |
| `sequence` | 첫 rate 측정용 scan을 건너뛴 뒤 instance별 1부터 증가 |
| `instance_id` | 생성기 process 시작마다 sensor별 새 UUID |

내부 `scan_id`와 wire `sequence`는 책임이 다르다. 내부 값은 시뮬레이션 회전 식별자이고 wire
값은 외부 driver instance의 공개 순서다. 생성기 재시작은 새 `instance_id`와 sequence 1로
시작한다.

## gRPC 출력과 상태

`GrpcScanServer`는 하나의 process에서 정확히 2개 sensor UDS endpoint를 연다. 파일 이름은
환경 JSON의 sensor ID에서 결정한다. 각 endpoint는 최신 frame 2개만 보관하고 최대 구독자
8개를 받는다. 느린 구독자는 가장 오래된 frame을 잃을 수 있으며 다음 `sequence`의 gap으로
유실을 식별한다. 구독자 연결, 종료와 재연결은 생성과 적재 모델을 중단하거나 초기화하지 않는다.

빈 `consumer_id`와 128 byte 초과 값은 gRPC `INVALID_ARGUMENT`, 구독자 상한 초과는
`RESOURCE_EXHAUSTED`로 응답한다. 이 값과 메시지는 `ajin-edge-platform` 구현을 따른다. UDS는 생성기가
시작할 때 mode `0660`으로 만들고 종료할 때 제거한다. 기존 경로가 socket이 아니면 덮어쓰지
않고 시작에 실패한다.

상태 파일은 `ajin-edge-platform` 상태 schema, service 이름과 orchestrator directory 구조를 따른다. 첫
sensor는 `lidar-driver-a`, 둘째 sensor는 `lidar-driver-b`다. 각 파일은 상태 root 아래의
`<service>/<service>.json`에 있다. 생성기는 2초마다 임시 파일을 같은 하위 directory에서
원자적으로 교체한다. 첫 frame 전 상태는 `STARTING`, 게시 후 상태는 `HEALTHY`다.

## 관찰 출력

관찰 publisher는 연결마다 정적 scene header를 1회 보내고 기본 1초마다 동적 적재 모델
snapshot을 보낸다. 최신 대기 snapshot 1개만 유지하며 연결 실패와 느린 수신기는 scan 생성과
gRPC 출력을 막지 않는다. 관찰 stream은 scan 계약, `lidar-processing` 설정과 상태 파일을 변경하지
않는다. 세부 형식은 [`observation.md`](observation.md)가 정본이다.

## 생명주기와 검증

CLI는 입력을 검증한 뒤 공통 적재 모델, 두 센서 측정기, gRPC server, 상태 writer, 관찰
publisher와 선택적 진단 writer를 조립한다. SIGINT와 SIGTERM은 생성을 중단하고 publisher와
server를 닫은 뒤 집계를 기록한다. gRPC 구독자가 없어도 생성과 최신 frame 갱신은 계속된다.

자동 검증은 5개 계층이다.

| 경로 | 검증 범위 |
| --- | --- |
| `tests/unit/` | 설정, 수치 계산, 상태 전이와 변환 |
| `tests/integration/` | 전체 생성, gRPC UDS 구독과 장애 격리 |
| `tests/contract/` | JSON schema, Proto 출처와 인계 fixture |
| `tests/performance/` | 생성 단계, 변환과 local gRPC 전달 부하 |
| `tests/edge/` | digest image의 두 UDS, 관찰과 상태 출력 |

단위 및 통합 검증은 외부 네트워크에 의존하지 않는다. 외부 구현 직접 호환 검증은 별도 고정
checkout을 입력으로 사용한다. pixel 전체를 고정하는 시각 snapshot 검증은 이 Repository의
범위가 아니다.

## Repository 구조

```text
.github/workflows/
contracts/
  environment/v1/
  lidar/v1/
  observation/v1/
  quality/v1/
  v2/
docs/
edge-platform-integration/
examples/
Cargo.toml
Cargo.lock
build.rs
rust-toolchain.toml
src/
  lib.rs
  main.rs
  cli.rs
  error.rs
  configuration/
src/scrap_monitoring_lidar_generator/
  configuration/
  edge_integration/
  geometry/
  measurement/
  observation/
  runtime/
  scan_stream/
  scenario/
  wire/
tests/
tools/
```

`docs/project-spec.md`와 기존 `docs/internal/**`은 읽기 전용 경계다.
