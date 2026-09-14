# Scrap Monitoring LiDAR Generator

스크랩 적재 모니터링 개발을 위한 합성 LiDAR(Light Detection and Ranging) 생성기다. Raspberry
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
| 개발 | Python 3.14.4, uv 0.12.12 |
| 운영 | 64-bit ARM Linux, Docker Engine |
| 배포 검증 | Docker Engine, Git, GitHub CLI, Bash |

운영 장비는 Python, uv, compiler와 이미지 빌드 도구를 설치하지 않는다. GitHub Container
Registry에 게시된 `linux/arm64` 이미지를 digest로 받아 실행한다.

다음 명령은 새 checkout의 잠금 환경을 구성하고 담당자 처리 설정을 처음 생성한다.

```bash
uv sync --locked --all-groups
uv run --locked scrap-monitoring-lidar-generator-export-processing-config \
  --generator-config examples/generator.v2.json \
  --socket-dir /sockets \
  --site-id synthetic-site \
  --edge-id synthetic-edge \
  --config-revision synthetic-r1 \
  --output /tmp/processing.synthetic.json
uv run --locked pytest tests/integration/test_grpc_scan_server.py
```

첫 명령은 `/tmp/processing.synthetic.json`을 만들고 두 번째 명령은 sensor별 UDS(Unix Domain
Socket) 구독과 상태 출력을 검증한다.

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

### 담당자 처리 설정 생성

`lidar-processing`은 생성기의 환경 JSON을 직접 읽지 않는다. 다음 exporter가 공개 합성
환경을 담당자의 센서별 강체 변환, 50 mm 단면 ROI(Region of Interest), 높이 범위, 측정
필터와 융합 보정 형식으로 변환한다.

```bash
uv run --locked scrap-monitoring-lidar-generator-export-processing-config \
  --generator-config examples/generator.v2.json \
  --socket-dir /sockets \
  --site-id synthetic-site \
  --edge-id synthetic-edge \
  --config-revision synthetic-r1 \
  --output processing.synthetic.json
```

담당자 전체 배포 설정이 있으면 `--base-config`를 추가한다. exporter는 camera, deployment
revision과 다른 service version을 유지한다. 생성기가 대체하는 `lidar-driver-a`와
`lidar-driver-b`의 version은 현재 생성기 package version으로 갱신한다. 다른 생성기 image
version을 지정할 때만 `--driver-service-version`을 사용한다. `site_id`, `edge_id` 또는
`config_revision`이 다르면 실패한다. 생성된 calibration은 공개 합성 환경 전용 `demo`다.
실제 현장 calibration으로 사용하지 않는다.

`lidar-processing`에는 출력 파일을 read-only로 mount하고 같은 `SITE_ID`, `EDGE_ID`,
`CONFIG_REVISION`, `DEPLOYMENT_REVISION`을 주입한다. 담당자 설정 checksum은 최종 출력 파일에서
계산한다.

```bash
CONFIG_SHA256="$(sha256sum processing.synthetic.json | awk '{print $1}')"
```

계약 필드, 변환 기준, 처리 설정과 담당자 수락 명령은
[`edge-platform-integration/`](edge-platform-integration/)이 정본이다.

## 개발 및 검증

다음 명령은 전체 소스, 계약, 문서와 Python package를 검증한다.

```bash
uv run --locked rumdl check .
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked mypy
uv run --locked pytest
uv run --locked python -m tools.generate_lidar_wire --check
uv build --no-sources
```

담당자 구현과의 직접 호환성은 고정한 `ajin-edge-platform` checkout으로 확인한다.

```bash
uv run --locked python -m tools.verify_edge_platform_contract \
  --edge-platform-root /path/to/ajin-edge-platform
```

검증기는 담당자 Proto와 로컬 계약의 일치, 담당자 설정 loader의 수락, 두 sensor frame의
ingest, 단면 coverage와 최종 `GOOD` 측정을 확인한다.

## 배포

### Release 이미지 선택

다음 명령은 최신 GitHub Release의 source와 ARM64 이미지 digest를 함께 고정한다.

```bash
git clone https://github.com/ajin-scrap-monitoring/scrap-monitoring-lidar-generator.git
cd scrap-monitoring-lidar-generator

RELEASE_TAG="$(gh release view \
  --repo ajin-scrap-monitoring/scrap-monitoring-lidar-generator \
  --json tagName --jq .tagName)"
git switch --detach "$RELEASE_TAG"

RELEASE_DIR="$(mktemp -d)"
gh release download "$RELEASE_TAG" \
  --repo ajin-scrap-monitoring/scrap-monitoring-lidar-generator \
  --pattern oci-image.txt \
  --dir "$RELEASE_DIR"
IMAGE_REF="$(sed -n '1p' "$RELEASE_DIR/oci-image.txt")"
docker image pull "$IMAGE_REF"
```

`latest` tag는 사용하지 않는다. Release의 `oci-image.txt`가 기록한 digest를 배포 입력으로
사용한다.

### 배포 파일 준비

다음 4개 host 디렉토리를 준비한다.

```bash
CONFIG_DIR=/opt/ajin/config/lidar-generator
SOCKET_DIR=/opt/ajin/runtime/sockets/lidar-generator
STATUS_DIR=/opt/ajin/runtime/status
DIAGNOSTICS_DIR=/opt/ajin/runtime/diagnostics/lidar-generator

sudo install -d -m 0755 "$CONFIG_DIR"
sudo install -m 0644 \
  examples/environment.v1.json \
  examples/generator.v2.json \
  examples/quality-profile.v1.json \
  "$CONFIG_DIR/"
sudo install -d -m 0755 "$STATUS_DIR"
sudo install -d -o 10001 -g 10001 -m 0770 \
  "$SOCKET_DIR" \
  "$STATUS_DIR/lidar-driver-a" \
  "$STATUS_DIR/lidar-driver-b" \
  "$DIAGNOSTICS_DIR"
sudo install -o root -g root -m 0600 \
  .env.example /etc/scrap-monitoring-lidar-generator.env
sudoedit /etc/scrap-monitoring-lidar-generator.env
```

환경변수 파일 안의 컨테이너 경로는 다음 실행 명령의 mount 대상과 일치해야 한다.

```text
SCRAP_LIDAR_GENERATOR_CONFIG=/config/generator.v2.json
SCRAP_LIDAR_GENERATOR_GRPC_SOCKET_DIR=/run/lidar
SCRAP_LIDAR_GENERATOR_STATUS_DIR=/status
SCRAP_LIDAR_GENERATOR_DIAGNOSTICS_OUTPUT_PATH=/data/diagnostics
```

### 생성기 컨테이너 실행

```bash
sudo docker run --detach \
  --name scrap-monitoring-lidar-generator \
  --restart unless-stopped \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges=true \
  --init \
  --log-opt max-size=10m \
  --log-opt max-file=3 \
  --env-file /etc/scrap-monitoring-lidar-generator.env \
  --mount type=bind,src="$CONFIG_DIR",dst=/config,readonly \
  --mount type=bind,src="$SOCKET_DIR",dst=/run/lidar \
  --mount type=bind,src="$STATUS_DIR/lidar-driver-a",dst=/status/lidar-driver-a \
  --mount type=bind,src="$STATUS_DIR/lidar-driver-b",dst=/status/lidar-driver-b \
  --mount type=bind,src="$DIAGNOSTICS_DIR",dst=/data/diagnostics \
  "$IMAGE_REF"
```

`lidar-processing` 컨테이너는 같은 host `SOCKET_DIR`을 자신의 `/sockets`에 mount하고, exporter가
만든 처리 JSON의 `unix:/sockets/lidar_1.sock`과 `unix:/sockets/lidar_2.sock`을 구독한다.
UDS 통신에는 Docker network와 TCP port가 필요하지 않다. 관찰 TCP 연결만 별도 시각화
장비까지의 router 경로를 사용한다.

### 이미지 검증

검증한 Release checkout에서 다음 명령을 실행한다.

```bash
VALIDATION_DIR="$(mktemp -d)"
tests/edge/run.sh \
  --image "$IMAGE_REF" \
  --config-dir examples \
  --duration-s 30 \
  --cpus 2 \
  --output-dir "$VALIDATION_DIR"
```

성공 출력은 `edge_validation=passed sensors=2`로 시작한다. 검증기는 센서별 gRPC 구독,
sequence 연속성, Proto 정규화 범위, observation 전달과 두 driver 상태 파일을 검사한다.
`VALIDATION_DIR`에는 로그, Docker 통계, container inspect와 상태 snapshot이 남는다. 이
산출물은 운영 정보를 포함할 수 있으므로 Git에 추가하지 않는다. CPU 2 core는 검증 시작값이며
운영 자원 상한이 아니다.

### 운영 확인

```bash
sudo docker container inspect scrap-monitoring-lidar-generator \
  --format '{{.State.Status}} {{.State.ExitCode}} {{.Image}}'
sudo docker logs --tail 20 scrap-monitoring-lidar-generator
sudo docker image inspect "$IMAGE_REF" --format '{{index .RepoDigests 0}}'
sudo find "$SOCKET_DIR" "$STATUS_DIR" -maxdepth 2 \( -type f -o -type s \)
```

정상 실행은 센서별 첫 scan을 `scan_hz` 기준으로 사용한 뒤 초당 약 10 frame을 각 UDS에
게시한다. 프로그램 재시작은 새 센서별 `instance_id`와 sequence 1로 구분한다. 구독 연결의
중단과 재연결은 적재 모델을 초기화하지 않으며 생성기 프로세스 재시작은 빈 적재 공간에서 새
실행을 시작한다.

## 문서

| 문서 | 내용 |
|---|---|
| [`docs/project-spec.md`](docs/project-spec.md) | 제품 범위와 완료 조건 |
| [`docs/architecture.md`](docs/architecture.md) | 패키지와 외부 경계 |
| [`docs/configuration.md`](docs/configuration.md) | 설정 정본과 값 분류 |
| [`docs/sdk-compatibility.md`](docs/sdk-compatibility.md) | 담당자 driver와 SDK 출력 정합성 |
| [`edge-platform-integration/`](edge-platform-integration/) | 담당자 Proto, 처리 설정과 수락 기준 |
| [`docs/observation.md`](docs/observation.md) | 적재 모델 관찰 stream |
| [`docs/visualizer-requirements.md`](docs/visualizer-requirements.md) | 별도 시각화 프로그램 요구사항 |
| [`docs/deployment.md`](docs/deployment.md) | 이미지, 배포와 검증 상세 |
| [`docs/performance.md`](docs/performance.md) | 부하 측정 범위와 기준 |
| [`docs/dependencies.md`](docs/dependencies.md) | 직접 의존성과 라이선스 |
| [`docs/development-plan.md`](docs/development-plan.md) | 현재 완료 상태와 후속 검증 |

## 이용 조건

이 Repository는 코드 검토와 참고를 위해 Public으로 제공하며 프로젝트 소스 코드에 별도
라이선스를 부여하지 않는다.
