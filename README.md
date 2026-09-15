# Scrap Monitoring LiDAR Simulator

스크랩 적재 모니터링 개발을 위한 합성 LiDAR(Light Detection and Ranging) 시뮬레이터다. Raspberry
Pi 5 ARM64 엣지 장비에서 센서 2대의 scan을 만들고, 같은 장비의 `lidar-processing`이 구독할
수 있는 gRPC(Google Remote Procedure Call) over UDS(Unix Domain Socket) endpoint를 제공한다.

## 주요 기능

외부 출력은 3개다.

| 출력 | 기본 동작 | 소비자 |
|---|---|---|
| Scan stream | `lidar_1.sock`, `lidar_2.sock`의 server-streaming gRPC | `ajin-edge-platform`의 `lidar-processing` |
| 적재 모델 관찰 stream | 1초 주기의 JSON Lines TCP stream | 별도 시각화 프로그램 |
| 상태 snapshot | sensor별 `lidar-driver-a/`, `lidar-driver-b/` 하위 경로 | edge 상태 수집기와 운영자 |

생성기는 하나의 결정론적 적재 모델을 공유하면서 센서별 독립 회전과 scan을 생성한다. 표면은
적재와 수거, 안식각 기반 확산, 국소 요철과 설정된 측정 왜곡을 반영한다. 관찰 연결 실패는
scan 생성과 gRPC 구독을 중단시키지 않는다.

## 빠른 시작

### 요구 환경

| 용도 | 요구 사항 |
|---|---|
| 개발 | Python 3.14.4, uv 0.12.15, Rust 1.96.0 |
| 엣지 검증 | 64-bit ARM Linux, Docker Engine |
| 배포 검증 | Docker Engine, Git, GitHub CLI, Bash |

엣지 검증 장비는 Python, uv, compiler와 이미지 빌드 도구를 설치하지 않는다. GitHub Container
Registry에 게시된 `linux/arm64` 이미지를 digest로 받아 실행한다.

다음 명령은 새 checkout의 잠금 환경을 구성하고 `lidar-processing`용 합성 처리 설정을 생성한다.

```bash
uv sync --locked --all-groups
uv run --locked scrap-monitoring-lidar-simulator-export-synthetic-processing-config \
  --generator-config examples/generator.v2.json \
  --socket-dir /sockets \
  --site-id synthetic-site \
  --edge-id synthetic-edge \
  --config-revision synthetic-r1 \
  --output /tmp/processing.synthetic.json
uv run --locked pytest tests/integration/test_grpc_scan_server.py
```

두 번째 명령은 `/tmp/processing.synthetic.json`을 만들고 세 번째 명령은 sensor별 UDS(Unix
Domain Socket) 구독과 상태 출력을 검증한다.

## 설정

### 설정 경계

설정은 2개 계층으로 분리한다.

| 계층 | 정본 | 책임 |
|---|---|---|
| 합성 모델 | versioned JSON | 환경 형상, 센서, 시나리오, 측정, 품질, seed와 관찰 복구 정책 |
| 배포 실행 | CLI 인자 또는 환경변수 | 파일 경로, UDS 경로, 배포 식별자, 관찰 endpoint와 진단 override |

공개 합성 입력은 다음 3개다.

| 파일 | 역할 |
|---|---|
| `examples/environment.v1.json` | 적재 공간과 센서 설치 정본 |
| `examples/generator.v2.json` | 시나리오, 측정, 관찰 전송과 진단 설정 |
| `examples/quality-profile.v1.json` | 센서별 합성 quality 분포 |

CLI 인자, 환경변수, JSON, 코드 기본값 순서로 값을 선택한다. 앞선 계층의 값이 있으면 뒤의
계층 값은 사용하지 않는다. 센서 ID, 위치, 방향과 UDS 파일 이름을 환경변수에 중복하지
않는다. `lidar_1.sock`과 `lidar_2.sock`은 환경 JSON의 센서 ID와 하나의 UDS 디렉토리에서
결정된다.

### 환경변수

환경변수는 14개다.

| 환경변수 | CLI 인자 | 필수 여부 및 fallback |
|---|---|---|
| `SCRAP_LIDAR_GENERATOR_CONFIG` | `--config` | 둘 중 하나 필수 |
| `SCRAP_LIDAR_GENERATOR_GRPC_SOCKET_DIR` | `--grpc-socket-dir` | 둘 중 하나 필수, 절대 경로 |
| `SCRAP_LIDAR_GENERATOR_STATUS_DIR` | `--status-dir` | 둘 중 하나 필수, 절대 경로 |
| `SITE_ID` | `--site-id` | 둘 중 하나 필수 |
| `EDGE_ID` | `--edge-id` | 둘 중 하나 필수 |
| `CONFIG_REVISION` | `--config-revision` | 둘 중 하나 필수 |
| `DEPLOYMENT_REVISION` | `--deployment-revision` | 둘 중 하나 필수 |
| `SCRAP_LIDAR_GENERATOR_MEAN_FILL_DURATION_S` | `--mean-fill-duration-s` | `scenario.mean_fill_duration_s` |
| `SCRAP_LIDAR_GENERATOR_COLLECTION_THRESHOLD_CENTER_RATIO` | `--collection-threshold-center-ratio` | `scenario.collection_threshold_range` |
| `SCRAP_LIDAR_GENERATOR_OBSERVATION_HOST` | `--observation-host` | 둘 중 하나 필수 |
| `SCRAP_LIDAR_GENERATOR_OBSERVATION_PORT` | `--observation-port` | 둘 중 하나 필수 |
| `SCRAP_LIDAR_GENERATOR_OBSERVATION_INTERVAL_S` | `--observation-interval-s` | 1초 |
| `SCRAP_LIDAR_GENERATOR_DIAGNOSTICS_ENABLED` | `--diagnostics-enabled` | `diagnostics.enabled` |
| `SCRAP_LIDAR_GENERATOR_DIAGNOSTICS_OUTPUT_PATH` | `--diagnostics-output-path` | `diagnostics.output_path` |

`.env.example`은 공개 합성 모델과 함께 사용할 배포 템플릿이다. `<...>` 값과
`visualizer.example`은 배포 환경에 맞게 바꾼다. 평균 적재 주기 기본값은 86,400초다. 수거
기준 중심값 0.90은 회차별 `0.85-0.95` 범위를 만든다. 관찰 기본 port는 17000이고 동적
snapshot 기본 주기는 1초다.

현재 생성기의 설정에는 크레덴셜이 없다. `.env`에 자격 증명을 넣지 않으며 Docker secret을
추가하지 않는다. 관찰 주소, 배포 식별자와 로컬 경로는 비밀값은 아니지만 장비별 `.env`는
Git에 추가하지 않는다.

진단 기록은 sensor별 앞쪽 일부 scan의 기준 교차점, 적재 표면과 시나리오 상태를 bounded
JSON Lines 파일로 남기는 개발 검증 기능이다. 일반 scan 전송과 관찰 stream을 대체하지 않으며
운영 로그나 Git 추적 대상으로 사용하지 않는다.

### 합성 검증용 처리 설정 생성

`lidar-processing`은 생성기의 환경 JSON을 직접 읽지 않는다. 다음 exporter가 공개 합성
환경을 `lidar-processing`의 센서별 강체 변환, 50 mm 단면 ROI(Region of Interest), 높이 범위,
측정 필터 형식으로 변환한다. `lidar-processing`이 요구하는 calibration 항목에는 합성 검증용 데모
값만 넣는다.

```bash
uv run --locked scrap-monitoring-lidar-simulator-export-synthetic-processing-config \
  --generator-config examples/generator.v2.json \
  --socket-dir /sockets \
  --site-id synthetic-site \
  --edge-id synthetic-edge \
  --config-revision synthetic-r1 \
  --output processing.synthetic.json
```

출력은 공개 합성 환경 전용이며 실제 현장 설정을 읽거나 병합하지 않는다. 실제 장비의 설치값,
처리 보정과 적재율 계산은 이 Repository의 책임이 아니다. `site_id`, `edge_id`와
`config_revision`은 합성 검증 실행에서 frame과 처리 설정의 식별자를 일치시키는 값이다.

합성 검증용 `lidar-processing`에는 출력 파일을 read-only로 mount하고 같은 `SITE_ID`,
`EDGE_ID`, `CONFIG_REVISION`, `DEPLOYMENT_REVISION`을 주입한다. 처리 설정 checksum은 최종
출력 파일에서 계산한다.

```bash
CONFIG_SHA256="$(sha256sum processing.synthetic.json | awk '{print $1}')"
```

계약 필드, 변환 기준, 처리 설정과 소비자 수락 명령은
[`edge-platform-integration/`](edge-platform-integration/)이 정본이다.

## 개발 및 검증

다음 명령은 전체 소스, 계약, 문서와 Python package를 검증한다.

```bash
cargo fmt --check
cargo clippy --locked --all-targets -- -D warnings
cargo clippy --locked --all-targets --all-features -- -D warnings
cargo test --locked --all-targets
cargo test --locked --all-targets --all-features
uv run --locked rumdl check .
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked mypy
uv run --locked pytest
uv run --locked --group docs \
  python docs/synthetic-environment-specification/generate.py --check
uv run --locked python -m tools.generate_lidar_wire --check
uv build --no-sources
```

`ajin-edge-platform` 구현과의 직접 호환성은 고정한 source checkout으로 확인한다.

```bash
uv run --locked python -m tools.verify_edge_platform_contract \
  --edge-platform-root /path/to/ajin-edge-platform
```

검증기는 외부 Proto와 로컬 계약의 일치, `lidar-processing` 설정 loader의 수락, 두 sensor frame의
ingest, 단면 coverage와 최종 `GOOD` 측정을 확인한다. 단계 4의 Rust 후보는 실제 `run` process와
두 UDS lane을 검증하는 별도 계약 검증을 통과한다. 이 검증은 미리 만든 release binary를 요구하며
정확한 명령과 판정 범위는 [`edge-platform-integration/`](edge-platform-integration/)을 따른다.

## 배포

배포 대상은 실제 센서 운영 환경이 아니라 Raspberry Pi 5에서 `lidar-processing`과 연동하는 개발 및
검증 환경이다. Release는 `linux/arm64` OCI(Open Container Initiative) image와 digest 참조를
제공한다. 현재 0.9.0 image의 기본 실행 경로는 Python이며 Rust `run` 후보는 ARM64 공유 부하 검증을
통과한 뒤 기본 실행 경로로 전환한다.

Rust 후보는 Raspberry Pi 5에서 두 sensor scan 의미와 `lidar-processing`의 높이 및 적재율 변환을
통과했다. 짧은 사전 검증은 정식 60분 부하 수락을 대신하지 않으며 고정한 `lidar-processing`
source의 호환 선행 조건이 남아 있다. 현재 Release의 기본 실행 경로는 Python이다. 측정값과 남은
조건은
[`docs/performance.md`](docs/performance.md)와 [`docs/development-plan.md`](docs/development-plan.md)가
정본이다.

배포 입력은 다음 4개 경로로 구분한다.

| Host 입력 | Container 경로 | 역할 |
|---|---|---|
| `/opt/ajin/config/lidar-simulator/` | `/config/` | 공개 합성 JSON 3개 |
| `/etc/scrap-monitoring-lidar-simulator.env` | `--env-file` | 실행 경로와 검증 식별자 |
| `/opt/ajin/runtime/sockets/lidar-simulator/` | `/run/lidar/` | sensor별 gRPC UDS |
| `/opt/ajin/runtime/status/` | `/status/` | driver 호환 상태 파일 |

`.env`의 `SCRAP_LIDAR_GENERATOR_CONFIG=/config/generator.v2.json`은 container 경로다. Host의
`examples/environment.v1.json`, `examples/generator.v2.json`과
`examples/quality-profile.v1.json`을 첫 번째 경로에 복사한 뒤 directory 전체를 read-only로
mount한다.

Release 선택, Host 준비, 전체 Docker 명령과 반복 가능한 image 검증은
[`docs/deployment.md`](docs/deployment.md)를 따른다.

## 문서

| 문서 | 내용 |
|---|---|
| [`docs/project-spec.md`](docs/project-spec.md) | 제품 범위와 완료 조건 |
| [`docs/architecture.md`](docs/architecture.md) | 패키지와 외부 경계 |
| [`docs/configuration.md`](docs/configuration.md) | 설정 정본과 값 분류 |
| [`docs/sdk-compatibility.md`](docs/sdk-compatibility.md) | `ajin-edge-platform` driver와 SDK 출력 정합성 |
| [`edge-platform-integration/`](edge-platform-integration/) | 외부 Proto, 처리 설정과 수락 기준 |
| [`docs/observation.md`](docs/observation.md) | 적재 모델 관찰 stream |
| [`docs/visualizer-requirements.md`](docs/visualizer-requirements.md) | 별도 시각화 프로그램 요구사항 |
| [`docs/deployment.md`](docs/deployment.md) | 검증 image 배포와 실행 |
| [`docs/performance.md`](docs/performance.md) | 부하 측정 범위와 기준 |
| [`docs/dependencies.md`](docs/dependencies.md) | 직접 의존성과 라이선스 |
| [`docs/development-plan.md`](docs/development-plan.md) | 현재 상태와 Python에서 Rust로의 전환 계획 |
| [`docs/synthetic-environment-specification/`](docs/synthetic-environment-specification/) | 공개 합성 환경의 자기완결 Markdown, DOCX, PDF와 도면 묶음 |

## 이용 조건

이 Repository는 코드 검토와 참고를 위해 Public으로 제공하며 프로젝트 소스 코드에 별도
라이선스를 부여하지 않는다.
