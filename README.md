# Scrap Monitoring LiDAR Generator

스크랩 적재 모니터링 개발을 위한 LiDAR(Light Detection and Ranging) 스캔 데이터 생성 프로그램이다.
Raspberry Pi 5의 엣지 컨테이너에서 센서 2대의 합성 scan을 높이 계산 프로세스로 보내고, 별도 시각화 프로그램에 적재 모델 관찰 stream을 제공한다.

## 주요 기능

- LiDAR 2대의 독립 회전 및 scan 생성
- 적재와 수거에 따른 안식각 기반 결정론적 합성 표면 및 측정 왜곡
- 센서별 독립 TCP(Transmission Control Protocol) 전송과 ACK(Acknowledgement) 재시도
- 별도 시각화 프로그램용 적재 모델 관찰 stream

## 빠른 시작

Python 3.14.4와 uv 0.12.12가 필요하다. 다음 명령은 잠금된 개발 환경을 구성하고 공개 합성 설정으로 센서별 scan 1개를 생성하여 JSON(JavaScript Object Notation) 성능 결과를 출력한다.

```bash
uv sync --locked --all-groups
uv run --locked python -m tests.performance.generation \
  --config examples/generator.v1.json \
  --scans-per-sensor 1
```

## 설정

실행 입력은 JSON(JavaScript Object Notation) 생성 모델과 배포 설정의 2개 범주로 구성한다.

| 계층 | 책임 | 제공 방법 |
| --- | --- | --- |
| 생성 모델 | 환경, 센서, 시나리오, 측정, 품질과 전송 정책 | version 1 JSON 파일 3개 |
| 실행 override | 평균 적재 주기, 수거 기준 임계치, 설정 경로, 외부 endpoint와 진단 출력 위치 | CLI 인자 또는 환경변수 |

CLI(Command-Line Interface) 인자, 환경변수, JSON, 코드 기본값 순서로 값을 선택한다. 앞선
계층에 값이 있으면 뒤의 계층 값은 사용하지 않는다. 평균 적재 주기, 수거 기준 임계치,
scan endpoint와 진단 설정은 환경변수가 없으면 JSON 값을 사용한다. 관찰 host와 port는
JSON에 포함되지 않으므로 CLI 또는 환경변수로 반드시 제공한다.

| 환경변수 | CLI 인자 | 필수 여부 및 fallback |
| --- | --- | --- |
| `SCRAP_LIDAR_GENERATOR_CONFIG` | `--config` | 둘 중 하나 필수 |
| `SCRAP_LIDAR_GENERATOR_MEAN_FILL_DURATION_S` | `--mean-fill-duration-s` | `scenario.mean_fill_duration_s` |
| `SCRAP_LIDAR_GENERATOR_COLLECTION_THRESHOLD_CENTER_RATIO` | `--collection-threshold-center-ratio` | `scenario.collection_threshold_range` |
| `SCRAP_LIDAR_GENERATOR_SCAN_HOST` | `--scan-host` | `transport.host` |
| `SCRAP_LIDAR_GENERATOR_SCAN_PORT` | `--scan-port` | `transport.port` |
| `SCRAP_LIDAR_GENERATOR_OBSERVATION_HOST` | `--observation-host` | 둘 중 하나 필수 |
| `SCRAP_LIDAR_GENERATOR_OBSERVATION_PORT` | `--observation-port` | 둘 중 하나 필수 |
| `SCRAP_LIDAR_GENERATOR_OBSERVATION_INTERVAL_S` | `--observation-interval-s` | 1초 |
| `SCRAP_LIDAR_GENERATOR_DIAGNOSTICS_ENABLED` | `--diagnostics-enabled` | `diagnostics.enabled` |
| `SCRAP_LIDAR_GENERATOR_DIAGNOSTICS_OUTPUT_PATH` | `--diagnostics-output-path` | `diagnostics.output_path` |

평균 적재 주기는 0초보다 큰 유한한 값이며 `.env.example`의 기본값은 24시간에 해당하는
86,400초다. 수거 기준 임계치는 0.05 초과, 0.95 이하의 적재율이며 회차별 실제 임계치는
기준값을 중심으로 `+-0.05` 범위에서 선택한다. 예시 기준값 0.90은 JSON의 0.85부터 0.95
범위와 동일하다. `SCRAP_LIDAR_GENERATOR_DIAGNOSTICS_ENABLED`는 `true` 또는 `false`만
허용한다. port는 1부터 65,535까지이며 관찰 주기는 0초보다 크고 86,400초 이하여야 한다.
상대 진단 경로는 생성 설정 파일의 directory를 기준으로 해석한다. 잘못된 값은 시작 전에
종료 코드 2와 `configuration error`로 거부한다.

센서 위치와 방향, 시나리오, 측정 사양과 품질 분포는 중첩 객체, 배열과 좌표를 포함하고 같은
설정 및 seed로 재현돼야 하므로 JSON으로 관리한다. 환경변수는 평균 적재 주기, 수거 기준
임계치와 장비마다 바뀌는 endpoint 및 경로만 덮어쓴다. 공개 합성 입력 3개와 각 값의 출처 및 분류는
[`docs/configuration.md`](docs/configuration.md)가 정본이다.

## 개발 및 검증

빠른 시작에서 구성한 개발 환경으로 전체 검증을 실행한다.

```bash
uv run --locked rumdl check .
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked mypy
uv run --locked pytest
uv run --locked scrap-monitoring-lidar-generator --help
uv build --no-sources
```

## 배포

엣지 장비에는 64-bit ARM Linux, Docker Engine과 외부 설정만 필요하다. Repository clone,
Python, uv와 compiler는 운영 실행에 필요하지 않다. 검증 도구를 실행하는 장비에는 Git,
GitHub CLI와 Bash도 필요하다.

### 최신 Release 이미지 검증

다음 명령은 Repository를 새로 받고, 최신 게시 Release와 같은 source revision의 검증
도구로 ARM64 이미지를 30초 동안 검사한다. Docker 사용 권한이 없는 계정은 `docker`
명령과 `tests/edge/run.sh`를 권한이 있는 계정으로 실행한다.

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

VALIDATION_DIR="$(mktemp -d)"
tests/edge/run.sh \
  --image "$IMAGE_REF" \
  --config-dir examples \
  --duration-s 30 \
  --cpus 2 \
  --output-dir "$VALIDATION_DIR"
```

성공하면 마지막에 `edge_validation=passed sensors=2`가 출력된다. 검증기는 임시 scan 및
관찰 수신기를 실행하고 센서별 수신, ACK(Acknowledgement), 중복, sequence 누락, 폐기와
연결 실패를 검사한다. 결과는 `VALIDATION_DIR`의 `generator.log`, `receiver.log`,
`docker-stats.jsonl`과 `container-inspect.json`에 남는다. CPU 2 core는 검증 시작값이며
운영 자원 기준이 아니다.

### 운영 설정 준비

검증한 Release checkout의 공개 JSON 3개를 배포 장비의 같은 directory에 배치한다.
진단 directory는 이미지의 실행 사용자 UID(User Identifier)와 GID(Group Identifier)
10001이 쓸 수 있어야 한다.

```bash
CONFIG_DIR=/opt/scrap-monitoring-lidar-generator/config
DIAGNOSTICS_DIR=/var/lib/scrap-monitoring-lidar-generator/diagnostics

sudo install -d -m 0755 "$CONFIG_DIR"
sudo install -m 0644 \
  examples/environment.v1.json \
  examples/generator.v1.json \
  examples/quality-profile.v1.json \
  "$CONFIG_DIR/"
sudo install -d -o 10001 -g 10001 -m 0700 "$DIAGNOSTICS_DIR"
```

배포 장비에서 [`.env.example`](.env.example)을 장비 전용 환경변수 파일로 복사한다. 파일의
`SCRAP_LIDAR_GENERATOR_SCAN_HOST=height-calculation`은 아래의 공용 Docker network에서
높이 계산 container가 사용하는 이름 또는 network alias다.
`<height-calculation-listen-port>`는 유효한 port가 아니므로 높이 계산 프로세스의 실제 TCP
수신 port로 반드시 바꾼다. 이 프로젝트는 scan 전용 관례 port를 정하지 않는다.
`visualizer.example`은 container에서 router를 거쳐 접근 가능한 시각화 프로그램의 실제
DNS(Domain Name System) 이름 또는 IP 주소로 바꾼다. 경로와 나머지 값도 배포 환경에 맞게
확인한다. 장비 전용 파일은 Git에 추가하지 않는다.

```bash
sudo install -o root -g root -m 0600 \
  .env.example \
  /etc/scrap-monitoring-lidar-generator.env
sudoedit /etc/scrap-monitoring-lidar-generator.env
```

`.env.example`과 장비 전용 파일에는 크레덴셜을 넣지 않는다. 현재 생성기의 scan 및 관찰
계약에는 인증 입력이 없으며 환경변수 10개는 평균 적재 주기, 수거 기준 임계치, endpoint,
port, 경로와 동작 설정만 제공한다.

### 운영 container 실행

`IMAGE_REF`는 앞에서 검증한 Release asset의 digest 참조를 사용한다. tag나 `latest`는
사용하지 않는다. 실행 구성 요소는 생성기 container, 높이 계산 container와 별도 장비의
관찰 수신 프로그램 3개다. 생성기와 높이 계산 container는 같은 사용자 정의 Docker
network에 연결한다. 높이 계산 container는 `height-calculation` 이름 또는 network alias로
scan TCP server를 열어야 한다. 관찰 수신 프로그램은 이 Docker network에 참여할 필요가
없다.

```bash
EDGE_NETWORK=scrap-monitoring-edge
sudo docker network inspect "$EDGE_NETWORK" >/dev/null 2>&1 || \
  sudo docker network create "$EDGE_NETWORK"

sudo docker run --detach \
  --name scrap-monitoring-lidar-generator \
  --network "$EDGE_NETWORK" \
  --restart unless-stopped \
  --log-opt max-size=10m \
  --log-opt max-file=3 \
  --env-file /etc/scrap-monitoring-lidar-generator.env \
  --mount type=bind,src="$CONFIG_DIR",dst=/config,readonly \
  --mount type=bind,src="$DIAGNOSTICS_DIR",dst=/data/diagnostics \
  "$IMAGE_REF"
```

생성기는 scan 수신 server에 센서별 outbound TCP(Transmission Control Protocol) 연결을
만들고 관찰 수신 server에 별도 연결을 만든다. scan endpoint는 공용 Docker network의
내부 DNS로 해석한다. 관찰 endpoint는 container에서 router를 거쳐 접근할 수 있어야 한다.
관찰 연결 실패는 scan 생성과 기존 scan 송신을 중단시키지 않는다.

실행 상태, 적용 이미지와 종료 집계는 다음 명령으로 확인한다.

```bash
sudo docker container inspect scrap-monitoring-lidar-generator \
  --format '{{.State.Status}} {{.State.ExitCode}} {{.Image}}'
sudo docker logs --tail 20 scrap-monitoring-lidar-generator
sudo docker image inspect "$IMAGE_REF" --format '{{index .RepoDigests 0}}'
```

프로그램은 실행마다 UUID(Universally Unique Identifier) 형식의 `run_id`를 만들고 SIGINT
또는 SIGTERM에서 생성과 송신을 정상 종료한 뒤 전체 전송 집계를 출력한다. 이미지 선택,
자원 상한, 결과 해석과 Release 절차의 상세 기준은
[`docs/deployment.md`](docs/deployment.md)를 따른다.

엣지 생성기의 상시 적재 모델 관찰 stream과 별도 장비의 3D 시각화는 [`docs/observation.md`](docs/observation.md)를 따른다.

## 문서

| 문서 | 내용 |
| --- | --- |
| [`docs/project-spec.md`](docs/project-spec.md) | 제품 범위, 외부 계약과 완료 조건 |
| [`docs/architecture.md`](docs/architecture.md) | 구현 경계, 의존 방향과 검증 구조 |
| [`docs/development-plan.md`](docs/development-plan.md) | 구현 순서, 산출물과 단계별 완료 조건 |
| [`docs/dependencies.md`](docs/dependencies.md) | 직접 의존성, 버전, 사용 목적과 라이선스 |
| [`docs/configuration.md`](docs/configuration.md) | 공개 설정 정본, 기본값 출처와 합성값 분류 |
| [`docs/deployment.md`](docs/deployment.md) | OCI 이미지, 컨테이너 실행과 Release 절차 |
| [`docs/performance.md`](docs/performance.md) | 생성 구간별 부하 측정과 결과 해석 |
| [`docs/height-calculation-integration.md`](docs/height-calculation-integration.md) | 한 회전 scan의 높이 계산 프로세스 인계 계약 |
| [`docs/observation.md`](docs/observation.md) | 적재 모델 관찰 stream과 외부 시각화 경계 |
| [`docs/visualizer-requirements.md`](docs/visualizer-requirements.md) | 별도 시각화 Repository 구현 요구사항 |
| [`contracts/v1/`](contracts/v1/) | 환경, 생성 실행, 품질 분포와 스캔 및 응답 계약 버전 1 |
| [`contracts/observation/v1/`](contracts/observation/v1/) | 적재 모델 관찰 출력 계약 버전 1 |
| [Organization 개발 운영 규칙](https://github.com/ajin-scrap-monitoring/.github/blob/main/GOVERNANCE.md) | Issue, 브랜치, Pull Request, CI(Continuous Integration)와 Release 기준 |

## 이용 조건

이 Repository는 코드 검토와 참고를 위해 Public으로 제공하며 프로젝트 소스 코드에 별도 라이선스를 부여하지 않는다.
