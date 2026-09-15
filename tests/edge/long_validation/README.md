# Edge 장기 검증 도구

이 디렉토리는 Raspberry Pi 5에서 Rust simulator와 `lidar-processing`의 실제 연결, 데이터 변환,
출력 의미, 자원 사용량, frame 유실, 시나리오 전이와 관찰 출력 비용을 검증하는 실행 도구를
제공한다. 공개 결과에는 aggregate metric과 counter만 남기며 endpoint, raw scan, measurement
payload, 관찰 payload, 적재 표면과 높이 배열을 포함하지 않는다.

## 실행 구성

검증 구성 요소는 5개다.

| 구성 요소 | 역할 |
| --- | --- |
| Rust simulator | 두 sensor scan 생성과 frame 완료 지연 계측 |
| `lidar-processing` | 두 sensor scan 구독과 높이 measurement 생성 |
| helper | measurement 수신, 계약 및 의미 검증, 자원 및 상태 수집 |
| observation receiver | 외부 장비의 actual 관찰 record 검증과 수신 건수 집계 |
| runner | container 실행, 공통 시각 설정, 증거 수집과 종료 처리 |

helper는 processing image의 `ajin_edge.contracts.validate_measurement`를 사용한다. 모든 measurement는
고정된 schema, `site_id`, `edge_id`, `config_revision`, `calibration_version`, 정확히 두 sensor와
fused quality를 통과해야 집계된다. helper는 host process와 cgroup을 읽기 위해 `--pid=host`,
`--cgroupns=host`, read-only
`/proc`와 `/sys/fs/cgroup` mount를 사용한다. CPU와 process RSS는 필수 판정값이다. Linux가
memory controller를 활성화한 경우에만 cgroup `memory.current`를 선택적 진단값으로 기록한다.
계층별 `cpu.max`와 `memory.max`가 있으면 유효 자원 제한을 기록하고, 파일이 없는 cgroup root는
제한 없음으로 해석한다.

runner는 helper가 발행한 하나의 `CLOCK_MONOTONIC` 시작 시각을 simulator와 resource sampler에
적용한다. 준비 구간은 300초이고 측정 구간은 3,600초다. runner가 이 시간이나 sensor 수를 변경하는
인자는 제공하지 않는다. Simulator는 마지막 frame 생성 worker를 중단한 뒤 2초 동안 process와
상태 출력을 유지하여 마지막 1초 자원 표본과 process 종료가 경합하지 않게 한다.

Simulator는 준비와 측정을 합친 3,900초 전체에서 최대 64개의 phase 및 cycle 전이를 기록한다. 각
전이는 전환 직전과 직후의 인접 scan에서 두 sensor 공동 frame 완료 지연을 함께 기록한다. Helper는
전이 순서와 latency bracket을 검증하고 공개 결과에는 전이 횟수, 완료 cycle 수, 인접 latency 표본
수와 최대값만 보존한다.

Observation receiver는 3,901개 표면 record의 1초 cadence, 연속 sequence, grid와 높이 범위뿐 아니라
매 record의 surface update, payload 변화, phase별 부피 방향과 부피-적재율 정합성을 검증한다. 높이
배열은 보존하지 않고 전체 surface payload의 누적 SHA-256과 변화 횟수 및 부피 범위만 로컬 증거에
남긴다.

Simulator는 측정 구간에 초당 한 번씩 두 sensor의 기준 광선을 집계한다. 집계값은 허공, 바닥,
외벽, 현재 적재면 교차 수, 최종 유효 및 무효 sample 수, 기준 교차가 없는 유효 sample 수와 기준
scan 변화 횟수다. `lidar-processing` 결과는 높이 범위와 중앙값 및 P90 순서, sensor별 단면 적재율과
융합 적재율의 범위, filling 및 collecting 구간별 적재율 방향을 검증한다.

`run-result.v1`은 두 의미 판정을 독립적으로 기록한다. Simulator만 실패하면 `simulator`,
simulator가 통과하고 `lidar-processing`만 실패하면 `lidar-processing`, 둘 다 실패하거나 증거가
불완전하면 `indeterminate`, 둘 다 통과하면 `none`을 `semantics.failure_domain`에 기록한다.

## 선행 조건

- Raspberry Pi 5 Model B 8 GB와 정상 동작하는 냉각 장치
- cgroup v2 CPU controller를 사용하는 Linux와 Docker Engine
- 검증 계정에서 성공하는 `docker info` 또는 passwordless `sudo -n /usr/bin/docker info`
- 검증 계정에서 성공하는 `vcgencmd get_throttled`와 온도 파일 읽기
- `vcgencmd get_throttled` 결과 `0x0`
- Candidate workflow가 같은 source revision에서 만든 검증 source archive와 그 정확한 추출본
- `linux/arm64`, runtime UID 10001, registry digest와 source revision label을 갖춘 simulator image
- `SOURCE.json`의 `validation_image` source와 일치하고 UDS(Unix Domain Socket)
  gRPC(Google Remote Procedure Call) authority 및 runtime sequence 집계 호환성을 포함한
  `lidar-processing` 검증 image
- actual 실행 전에 TCP port 17000으로 접근 가능한 별도 장비의 observation receiver

Simulator image는 `repository@sha256:<digest>` 형식으로 지정한다. Processing image는 같은 registry
digest 형식 또는 홈서버에서 만든 archive를 load한 뒤 확인한 `sha256:<image-id>` 형식으로 지정한다.
각 `--source-commit` 값은 image의 `org.opencontainers.image.revision` label과 같은 40자리 lowercase
commit SHA여야 한다. Processing source commit은 `SOURCE.json`의 `validation_image.commit`과 같아야
한다. Edge는 image를 pull 또는 load하고 실행할 뿐이며 Git, compiler와 build tool을 설치하지
않는다. Runner는 Git archive의 PAX(Portable Archive Interchange) commit 표식, 압축 및 확장 크기,
전체 파일 내용, 실행 mode, symlink와 추가 파일 부재를 확인한다. 결과 디렉토리와 archive는 추출
directory 바깥에 둔다.

Candidate workflow artifact의 `edge-validation-source.tar.gz`와
`edge-validation-source.sha256`을 검증 제어 장비에서 내려받는다. 두 파일과
`edge-candidate-image.txt`, `edge-candidate-source-sha.txt`를 edge로 전달하고 source archive를 새
directory에 추출한다.

```bash
SOURCE_ARCHIVE="$HOME/edge-validation-source.tar.gz"
SOURCE_ARCHIVE_SHA256="$(sed -n '1p' "$HOME/edge-validation-source.sha256")"
VALIDATION_REPOSITORY="$HOME/edge-validation-source"

test "$(sha256sum "$SOURCE_ARCHIVE" | awk '{print $1}')" = "$SOURCE_ARCHIVE_SHA256"
mkdir "$VALIDATION_REPOSITORY"
tar -xzf "$SOURCE_ARCHIVE" -C "$VALIDATION_REPOSITORY"
cd "$VALIDATION_REPOSITORY"
```

edge에서는 네 case 모두 같은 비특권 계정으로 실행한다. Runner가 만든 결과 디렉토리와 handoff
디렉토리도 이 계정이 소유하므로 observation 결과를 넘기기 위해 root 권한을 사용하지 않는다.
다음 사전 검사를 edge의 Repository root에서 실행한다.

```bash
GENERATOR_SOURCE_COMMIT="<generator-commit>"
DOCKER_COMMAND="$PWD/tests/edge/sudo-docker"

test -s "$SOURCE_ARCHIVE"
"$DOCKER_COMMAND" info >/dev/null
test "$(vcgencmd get_throttled)" = "throttled=0x0"
test -r /sys/class/thermal/thermal_zone0/temp
```

검증용 공개 설정은 Repository의 다음 파일을 그대로 사용한다.

- `examples/generator.v2.json`
- `examples/environment.v1.json`
- `examples/quality-profile.v1.json`
- `edge-platform-integration/v1/processing.synthetic.json`

## 공통 실행 변수

edge의 검증 source 추출본에서 image digest, source commit, source archive와 결과 root를 준비한다.
Archive와 결과 root는 Repository 밖의 공백 없는 사용자 쓰기 가능 경로로 정한다. 사설 주소와
자격 증명은 Repository 파일에 기록하지 않는다.

```bash
GENERATOR_IMAGE="ghcr.io/example/simulator@sha256:<generator-digest>"
GENERATOR_SOURCE_COMMIT="<generator-commit>"
SOURCE_ARCHIVE="$HOME/edge-validation-source.tar.gz"
PROCESSING_IMAGE="sha256:<processing-image-id>"
PROCESSING_SOURCE_COMMIT="55b2e9d9401682c237a42945d9f548a4c912951f"
RESULT_ROOT="$HOME/lidar-long-validation"
COOLING="active cooler"
DOCKER_COMMAND="$PWD/tests/edge/sudo-docker"

mkdir -p "$RESULT_ROOT"
```

공식 matrix는 다음 네 case를 사용한다. 각 `CASE_ROOT`는 실행 전에 존재하지 않아야 한다.

| `CASE_ID` | `FILL_DURATION_S` | 관찰 모드 |
| --- | --- | --- |
| `86400-actual` | `86400` | actual |
| `86400-noop` | `86400` | noop |
| `600-actual` | `600` | actual |
| `600-noop` | `600` | noop |

## Actual 관찰 실행

Actual 절차는 receiver 장비의 terminal과 edge terminal을 하나씩 사용한다. `86400-actual`과
`600-actual`마다 새 receiver 디렉토리와 새 case 디렉토리로 이 절차를 반복한다.

Receiver terminal에서 simulator image와 같은 commit의 clean Repository checkout으로 이동한다.
`FILL_DURATION_S`는 실행할 actual case와 같은 값으로 지정한다. Receiver는 background에서 시작하고
`ready.json`이 생성될 때까지 생존 여부를 확인한다.

```bash
cd /absolute/path/to/clean/repository

GENERATOR_SOURCE_COMMIT="<generator-commit>"
FILL_DURATION_S=86400
RECEIVER_ROOT="$(mktemp -d)"

uv run --frozen python -m tests.edge.long_validation.observation_receiver \
  --bind-host 0.0.0.0 \
  --port 17000 \
  --ready-file "$RECEIVER_ROOT/ready.json" \
  --stop-file "$RECEIVER_ROOT/stop" \
  --output "$RECEIVER_ROOT/observation-result.json" \
  --generator-source-commit "$GENERATOR_SOURCE_COMMIT" \
  --mean-fill-duration-s "$FILL_DURATION_S" &
RECEIVER_PID=$!

cleanup_receiver() {
  if kill -0 "$RECEIVER_PID" 2>/dev/null; then
    kill "$RECEIVER_PID"
    wait "$RECEIVER_PID" || true
  fi
}
trap cleanup_receiver EXIT INT TERM

while [ ! -s "$RECEIVER_ROOT/ready.json" ] && kill -0 "$RECEIVER_PID" 2>/dev/null; do
  sleep 0.2
done
test -s "$RECEIVER_ROOT/ready.json" || {
  wait "$RECEIVER_PID" || true
  exit 1
}
cat "$RECEIVER_ROOT/ready.json"
```

`ready.json`을 확인한 다음 edge terminal에서 actual runner를 background로 시작한다.
`OBSERVATION_HOST`는 edge에서 TCP port 17000으로 접근 가능한 receiver 주소다. 다음 loop는 runner의
handoff 요청을 기다리며 runner가 먼저 종료되면 그 종료를 그대로 보고한다.

```bash
CASE_ID="86400-actual"
FILL_DURATION_S=86400
CASE_ROOT="$RESULT_ROOT/$CASE_ID"
OBSERVATION_HOST="<receiver-address>"

tests/edge/long_validation/run-case.sh \
  --run-id "$CASE_ID" \
  --output-dir "$CASE_ROOT" \
  --generator-image "$GENERATOR_IMAGE" \
  --generator-source-commit "$GENERATOR_SOURCE_COMMIT" \
  --source-archive "$SOURCE_ARCHIVE" \
  --processing-image "$PROCESSING_IMAGE" \
  --processing-source-commit "$PROCESSING_SOURCE_COMMIT" \
  --mean-fill-duration-s "$FILL_DURATION_S" \
  --observation-mode actual \
  --observation-host "$OBSERVATION_HOST" \
  --observation-port 17000 \
  --docker-command "$DOCKER_COMMAND" \
  --cooling "$COOLING" &
RUNNER_PID=$!

while [ ! -f "$CASE_ROOT/handoff/observation-handoff-request.json" ] \
  && kill -0 "$RUNNER_PID" 2>/dev/null; do
  sleep 2
done

if [ -f "$CASE_ROOT/handoff/observation-handoff-request.json" ]; then
  echo "observation handoff requested"
  if wait "$RUNNER_PID"; then RUNNER_STATUS=0; else RUNNER_STATUS=$?; fi
else
  if wait "$RUNNER_PID"; then RUNNER_STATUS=0; else RUNNER_STATUS=$?; fi
  echo "runner ended before handoff: $RUNNER_STATUS"
fi

echo "runner exit: $RUNNER_STATUS"
```

Handoff 요청이 표시되면 receiver terminal에서 stop 파일을 만들고 receiver의 성공 종료를 기다린다.
완료된 결과만 edge로 전송한다. `EDGE_CASE_ROOT`는 edge terminal의 `CASE_ROOT`와 같아야 하고
`EDGE_TARGET`은 SSH(Secure Shell) 접속 대상이다.

```bash
touch "$RECEIVER_ROOT/stop"
if wait "$RECEIVER_PID"; then
  RECEIVER_STATUS=0
else
  RECEIVER_STATUS=$?
fi
trap - EXIT INT TERM
test "$RECEIVER_STATUS" -eq 0
test -s "$RECEIVER_ROOT/observation-result.json"

EDGE_TARGET="<edge-ssh-target>"
EDGE_CASE_ROOT="/absolute/path/to/lidar-long-validation/86400-actual"
EDGE_PARTIAL="$EDGE_CASE_ROOT/handoff/.observation-result.partial"
EDGE_STAGED="$EDGE_CASE_ROOT/handoff/observation-result.staged.json"

ssh "$EDGE_TARGET" "test ! -e '$EDGE_PARTIAL' && test ! -e '$EDGE_STAGED'"
scp "$RECEIVER_ROOT/observation-result.json" "$EDGE_TARGET:$EDGE_PARTIAL"
ssh "$EDGE_TARGET" \
  "set -eu;" \
  "test -f '$EDGE_PARTIAL';" \
  "test ! -e '$EDGE_STAGED';" \
  "chmod 0644 '$EDGE_PARTIAL';" \
  "mv -- '$EDGE_PARTIAL' '$EDGE_STAGED'"
```

전송은 runner가 무시하는 같은 handoff 디렉토리의 partial 파일을 먼저 완성한다. 마지막 `mv`는 같은
filesystem 안에서 staged 이름을 원자적으로 공개한다. `observation-result.staged.json`에 직접
전송하지 않는다. Receiver 결과의 `run_id`는 simulator가 만든 runtime UUID이며 `CASE_ID`와 다르다.
Runner는 이 값을 raw runtime telemetry의 UUID와 비교하므로 결과를 수동으로 수정하지 않는다.

Edge terminal의 `wait`는 staged 결과가 검증될 때 종료된다. `RUNNER_STATUS`는 합격에 0, 합격선 미달에
1, 실행 또는 증거 오류에 2다. `600-actual`은 두 terminal에서 `FILL_DURATION_S=600`,
`CASE_ID=600-actual`과 이에 맞는 `EDGE_CASE_ROOT`를 사용하여 같은 절차를 반복한다.

## Noop 관찰 실행

Noop case는 receiver, observation host와 handoff가 없다. Edge terminal에서 다음 함수를 정의하고 두
case를 순서대로 실행한다.

```bash
run_noop_case() {
  FILL_DURATION_S="$1"
  CASE_ID="$2"
  CASE_ROOT="$RESULT_ROOT/$CASE_ID"

  if tests/edge/long_validation/run-case.sh \
    --run-id "$CASE_ID" \
    --output-dir "$CASE_ROOT" \
    --generator-image "$GENERATOR_IMAGE" \
    --generator-source-commit "$GENERATOR_SOURCE_COMMIT" \
    --source-archive "$SOURCE_ARCHIVE" \
    --processing-image "$PROCESSING_IMAGE" \
    --processing-source-commit "$PROCESSING_SOURCE_COMMIT" \
    --mean-fill-duration-s "$FILL_DURATION_S" \
    --observation-mode noop \
    --docker-command "$DOCKER_COMMAND" \
    --cooling "$COOLING"; then
    RUNNER_STATUS=0
  else
    RUNNER_STATUS=$?
  fi
  echo "$CASE_ID runner exit: $RUNNER_STATUS"
}

run_noop_case 86400 86400-noop
run_noop_case 600 600-noop
```

SIGTERM 또는 SIGINT로 중단한 실행은 결과로 사용하지 않고 새 출력 디렉토리에서 다시 시작한다.

## Matrix 실행과 판정

공통 실행 변수에 정의한 네 case는 각각 300초 준비와 3,600초 측정을 사용하므로 전체 측정에는 최소
4시간 20분이 필요하다. Edge는 검증 image 실행과 증거 수집만 담당한다. 네 aggregate 결과를 clean
Repository checkout과 `uv`가 있는 receiver 또는 개발 장비로 복사하여 판정한다. 다음 경로에는
공백을 사용하지 않는다.

```bash
EDGE_TARGET="<edge-ssh-target>"
EDGE_RESULT_ROOT="/absolute/path/to/lidar-long-validation"
MATRIX_ROOT="$(mktemp -d)"

for CASE_ID in 86400-actual 86400-noop 600-actual 600-noop; do
  mkdir "$MATRIX_ROOT/$CASE_ID"
  scp \
    "$EDGE_TARGET:$EDGE_RESULT_ROOT/$CASE_ID/run-result.v1.json" \
    "$MATRIX_ROOT/$CASE_ID/run-result.v1.json"
done

uv run --frozen python -m tests.edge.long_validation \
  --run-result "$MATRIX_ROOT/86400-actual/run-result.v1.json" \
  --run-result "$MATRIX_ROOT/86400-noop/run-result.v1.json" \
  --run-result "$MATRIX_ROOT/600-actual/run-result.v1.json" \
  --run-result "$MATRIX_ROOT/600-noop/run-result.v1.json" \
  --output "$MATRIX_ROOT/matrix-result.v1.json"
```

판정기는 성공에 0, 합격선 미달에 1, 입력 계약 오류에 2를 반환한다. percentile은 보간 없이
`ceil(percentile * N)`의 1-based 위치를 고르는 nearest-rank 방식이다.

| 항목 | 합격선 |
| --- | --- |
| Simulator CPU P95 | 75 percent 이하 |
| Simulator RSS P95 | 128 MiB 이하 |
| 두 sensor 공동 frame 완료 지연 P99 | 70 ms 이하 |
| Actual과 noop CPU P95 차이 | 5 percentage point 이하 |
| Producer 및 consumer frame loss | 0 |
| Container restart, OOM, thermal throttling | 0 |
| 두 sensor와 fused processing 상태 | 모든 수신 measurement `GOOD` |
| Simulator scan 의미 | 두 sensor별 허공, 정적 구조와 적재면 교차, 유효 및 무효 sample과 기준 scan 변화 확인 |
| Processing 결과 의미 | 높이 및 적재율 범위 정합성, 융합 범위와 phase별 방향 일치 |
| Processing measurement 전달 지연 | 최대 2,000 ms 이하 |
| 600초 시나리오 | 완전한 적재 및 수거 cycle 최소 5회 |
| 86,400초 시나리오 | 시나리오 전이 0회 |
| Actual과 noop 시나리오 schedule | 평균 적재 주기별 digest 일치 |
| 전환 인접 공동 frame 완료 지연 | 각 표본 70 ms 이하 |

1초 절대 deadline으로 수집한 자원 및 service status 표본은 실행별로 각각 정확히 3,600개여야 한다.
sensor별 및 두 sensor 공동 frame 완료 지연은 각각 정확히 36,000개여야 한다. measurement는 측정
구간에서 연속 수신되어야 하며 처음, 중간과 마지막 경계의 최대 간격은 2초 이하여야 한다.
의미 검증용 기준 scan은 sensor별로 정확히 3,600개여야 한다. Processing 추세는 phase 전후 1초를
제외하고 처음과 마지막 3개 값의 중앙값 차이를 사용한다. 최소 8개 measurement가 있는 phase
구간만 판정하며 방향 일치 구간이 80 percent 이상이어야 한다. 600초 설정은 filling과 collecting
구간을 모두 요구하고 86,400초 설정은 filling 구간을 요구한다.
공개 설정의 phase duration 범위에서 600초 설정은 3,900초 동안 최소 5개 완전 cycle을 마치며,
86,400초 설정의 첫 적재 phase는 이 구간보다 길다.

## 산출물 경계

공개 가능한 정본은 다음 두 schema를 통과한 aggregate 결과다.

- `schemas/run-result.v1.schema.json`
- `schemas/matrix-result.v1.schema.json`

각 case의 `control.json`, raw Rust telemetry, helper ready 정보, observation receiver 결과, lifecycle
evidence, status 파일과 container log는 검증 장비의 로컬 증거다. 이 파일에는 endpoint나 장비 정보가
포함될 수 있으므로 Git에 추가하지 않는다.
