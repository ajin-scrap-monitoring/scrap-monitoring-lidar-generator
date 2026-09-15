# 성능 측정

## Python 기준선 측정 범위

현재 Python benchmark는 4개 구간을 측정한다.

| 구간 | 측정 범위 |
| --- | --- |
| `scene_update` | 적재 표면과 시나리오 상태 갱신 |
| `scan_generation` | 광선 교차, 측정 왜곡과 quality 생성 |
| `serialization` | 외부 Proto ScanFrame 생성과 직렬화 |
| `transport_wait` | local gRPC UDS 게시와 구독 완료 |

benchmark는 생성 pacing을 제거하고 설정된 scan 수를 가능한 빠르게 처리한다. 생성기 process
안에서 두 sensor server와 두 구독자를 함께 실행하므로 process CPU 시간에는 local gRPC 처리도
포함된다. 진단 파일, 관찰 TCP, 연결 실패, 실제 `lidar-processing` 계산과 다른 edge process는
포함하지 않는다.

## 부하 입력

공개 설정의 명목 입력은 다음과 같다.

| 항목 | sensor 1대 | sensor 2대 합계 |
| --- | --- | --- |
| 회전 | 초당 10 scan | 초당 20 scan |
| 측정점 | 초당 32,000 point | 초당 64,000 point |
| 명목 scan 크기 | 회전당 약 3,200 point | 고정 계약 아님 |
| 관찰 snapshot | 해당 없음 | 기본 초당 1 record |

실제 scan 배열 길이는 회전 scheduler가 나눈 측정점 수다. Proto byte 수는 distance, angle과
quality 값의 varint 길이에 따라 달라진다.

## 실행

```bash
uv run --locked python -m tests.performance.generation \
  --config examples/generator.v2.json \
  --scans-per-sensor 100
```

결과는 JSON(JavaScript Object Notation) 한 개다.

| 결과 | 의미 |
| --- | --- |
| `generated_points_per_simulated_second` | 설정이 요구하는 초당 합성 point |
| `scan_wire_bytes_per_simulated_second` | 직렬화한 frame의 초당 명목 byte |
| `estimated_single_core_utilization_percent` | process CPU 시간과 simulation 시간의 비율 |
| `maximum_rss_bytes` | 실행 process의 최대 RSS(Resident Set Size) |
| stage `mean_ms`, `maximum_ms` | 계측 경계의 평균 및 최대 처리 시간 |

단일 core 환산값 100은 CPU core 하나를 지속 점유하는 계산량이다. 전체 장비 CPU 사용률이나
Docker CPU 제한 사용률이 아니다. `scan_generation` stage 호출 수는 장면 갱신 경계로 나눈
부분 생성도 포함하므로 게시 frame 수와 다를 수 있다.

이 benchmark는 실행 중 표본의 P95와 P99를 계산하지 않는다. 평균 및 최대 stage 시간과 process의
최대 RSS는 Rust 공유 부하 합격에 사용하는 CPU 및 RSS P95와 완료 지연 P99를 대신하지 않는다.

## Image 기능 검증

다음 명령은 digest image를 CPU 2 core 상한에서 30초 실행한다.

```bash
VALIDATION_DIR="$(mktemp -d)"
tests/edge/run.sh \
  --image "$IMAGE_REF" \
  --config-dir examples \
  --duration-s 30 \
  --cpus 2 \
  --output-dir "$VALIDATION_DIR"
```

검증은 두 sensor의 gRPC UDS frame, sequence, observation과 상태 파일을 확인한다. 생성기와
test double의 `docker stats`는 결과 directory에 남는다. CPU 2 core는 자동 검증을 안정적으로
완료하기 위한 시작값이고 자원 상한이 아니다.

## Python edge 기준선

0.9.0 image와 실제 `lidar-processing`을 Raspberry Pi 5에서 함께 실행한 짧은 제어 검증 결과는
다음과 같다. 이 값은 장기 상한이 아니라 Rust 전환 전 기준선이다.

| 항목 | 관측값 |
| --- | --- |
| 기본 24시간 적재 주기 계산 | 100 ms scan 주기당 약 87.6 ms |
| 600초 가속 적재 주기 계산 | 100 ms scan 주기당 약 97.1 ms |
| 생성기 container CPU | 논리 core 하나 기준 약 98-99 percent |
| `lidar-processing` container CPU | 약 4-16 percent |
| 생성기 RSS | 약 72 MiB |
| `lidar-processing` RSS | 약 60 MiB |
| 짧은 데이터 변환 검증 | 두 sensor `GOOD`, 유효 coverage 확인 |
| 지속 실행 결과 | 약 53초 이후 생성 sequence와 상태 갱신 정지 관측 |

현재 Python 생성 경로는 scan deadline을 넘긴 뒤 다음 deadline 대기에서 event loop에 실행권을
명시적으로 양보하지 않을 수 있다. 계산 시간이 100 ms 주기에 근접하면 gRPC와 상태 갱신 task가
지연되므로 짧은 데이터 정합성 통과만으로 지속 실행을 판정할 수 없다.

## Rust ARM64 사전 검증

Rust 후보 source revision은 `23f9e5a5061735ff3c8b7d324792340ce94782d9`이고 검증 image digest는
`sha256:2aaf1d3a6fae800906e19a9d28a69e9063f40bd45d20bdfa0924ec5221b92fae`다. Raspberry Pi 5
8 GB에서 600초 평균 적재 주기와 진단 비활성 설정으로 짧은 검증을 수행했다.

생성기 단독 사전 검증 결과는 다음과 같다. CPU는 Docker의 약 2초 간격 표본 20개를 사용했고
frame 지연은 40초 측정 구간의 전체 400개 batch를 사용했다.

| 항목 | 관측값 |
| --- | --- |
| 완료 batch | 400/400 |
| 생성기 CPU P95 | 35.18 percent |
| 생성기 CPU 최대 | 35.46 percent |
| 공동 frame 완료 지연 P99 | 21.381 ms |
| 공동 frame 완료 지연 최대 | 23.895 ms |
| 계측 buffer overflow | 없음 |

Docker가 scratch runtime의 memory 사용량을 0 B로 반환했으므로 단독 검증의 RSS 값은 사용하지
않았다.

공유 사전 검증은 같은 Rust image, 고정한 `lidar-processing` source
`666ca6067a3bb86833b74140cb659049025d0dae`에 UDS authority option만 적용한 로컬 ARM64 image와
임시 계약 수신기를 함께 실행했다. 이 처리 image는 registry에 게시하지 않았다. CPU와 RSS는
cgroup v2 CPU 시간과 `smaps_rollup`을 약 1초 간격으로 읽은 29개 구간 및 표본을 사용했다. 준비
2초와 측정 30초의 짧은 실행이므로 정식 percentile 근거가 아니다.

| 항목 | 관측값 |
| --- | --- |
| 완료 batch | 300/300 |
| 생성기 CPU P95 | 37.79 percent |
| 생성기 CPU 최대 | 37.83 percent |
| 생성기 RSS P95 | 5.81 MiB |
| 생성기 RSS 최대 | 5.92 MiB |
| 공동 frame 완료 지연 P99 | 22.756 ms |
| 처리기 CPU P95 | 22.19 percent |
| 처리기 CPU 최대 | 93.31 percent |
| 처리기 RSS P95 | 67.38 MiB |
| 처리기 RSS 최대 | 67.38 MiB |
| 처리 measurement | 전체 29개, `GOOD` 27개 |
| 생성기 scan 의미 | 두 sensor 모두 통과 |
| 처리 결과 의미 | 높이 범위, sequence 증가와 융합 적재율 범위 통과 |
| 처리기 `frame_loss` | 82 |

`frame_loss`는 생성기 scan 의미나 server frame 누락에서 발생하지 않았다. 고정한 처리 구현은
sensor별 최근 10개 scan을 보관하고 1초마다 계산한다. 10 Hz에서 계산 경계 사이 11개 scan이
들어오면 첫 scan을 보관하지 못하고 다음 계산에서 sensor당 1개를 `frame_loss`로 기록한다. 같은
입력을 처리 엔진 단위 실행에 넣어 총 2 증가와 `GOOD` 결과를 함께 재현했다. 처리 image가 이
경계를 수정하거나 counter 의미를 실제 전송 유실과 분리하기 전에는 장기 matrix의 유실 0 조건을
통과할 수 없다.

현재 edge의 Rust 후보와 authority 수정 처리기는 함께 실행되며 두 driver와 처리 상태는
`HEALTHY`다. 상주 measurement uplink가 없는 실행의 `local_loss_count`는 downstream 부재를
나타내므로 사전 성능 결과에 포함하지 않는다.

## Rust 공유 부하 측정 규칙

Rust 장기 공유 부하 합격 결과는 아직 없다. 아래 규칙은 Rust 전환의 합격 측정에 적용하며 수치
합격선은 [`development-plan.md`](development-plan.md#성능-합격)가 정본이다. 위 사전 검증이나
Python 기준선의 관측값으로 Rust 합격 여부를 판정하지 않는다.

고정된 실행 명령, 증거 인계와 aggregate 결과 schema는
[`../tests/edge/long_validation/README.md`](../tests/edge/long_validation/README.md)를 따른다.

검증 구성 요소는 4개다.

| 구성 요소 | 위치와 역할 |
| --- | --- |
| 생성기 | Raspberry Pi 5 8GB의 digest 고정 ARM64 image |
| `lidar-processing` | 같은 장비에서 두 sensor frame 처리 |
| 상태 및 성능 수집기 | 같은 장비의 별도 process에서 상태와 자원 표본 수집 |
| 관찰 수신기 | 별도 장비에서 실제 TCP stream 수신 |

추가 edge process가 있으면 실행 목록과 개별 CPU 및 RSS를 기록한다. 생성기와 수집기는 다른
cgroup(Control Group)에 둔다. image digest, 공개 입력 fingerprint, seed, 환경변수 override,
장비 OS(Operating System)와 kernel, Docker 자원 제한, CPU governor 및 냉각 조건을 결과에
포함한다. 진단 파일 출력은 두 비교 실행에서 모두 비활성화한다.

### 실행 구간과 적재 설정

각 실행은 빈 모델에서 시작해 5분간 준비 실행한 뒤 모델과 sequence를 초기화하지 않고 60분간
측정한다. 자원 표본은 monotonic clock의 절대 deadline을 기준으로 1초마다 수집한다. 준비 구간
마지막 표본을 CPU 차분의 시작값으로 사용하고 측정 구간의 3,600개 차분과 RSS 표본을 판정에
사용한다. 누락 또는 읽기 실패 표본은 0으로 보정하지 않고 해당 실행을 불완전 측정으로 기록한다.

적재 설정별 판정 목적은 2개다.

| 평균 적재 주기 | 판정 목적 |
| --- | --- |
| 기본 86,400초 | 60분 steady-state 부하와 scan 및 처리 상태 유지 |
| 가속 600초 | 적재와 수거 전이, 반복 cycle 및 전이 구간 부하 |

기본 설정의 60분 측정으로 전체 적재 및 수거 cycle 완료를 판정하지 않는다. 가속 설정은 측정
구간의 cycle 수, 전이 시각과 전이 전후 지연을 함께 기록한다. 두 설정의 통계는 합치지 않는다.
공개 설정의 phase duration 상한을 적용하면 3,900초 전체 실행에서 기본 설정은 phase 전이가 없어야
하고, 가속 설정은 적재와 수거를 합친 완전한 cycle이 최소 5회 완료되어야 한다.

### CPU와 RSS

수집기는 생성기 container의 cgroup v2 `cpu.stat`에서 `usage_usec`를 읽는다. CPU 표본은
`100 * delta(usage_usec) / delta(monotonic_us)`로 계산한다. 분모에는 두 수집 시각의 실제 간격을
사용한다. 논리 core 하나를 계속 사용하면 100 percent이며 core 수나 CPU quota로 나누지 않는다.
`usage_usec`는 해당 cgroup과 하위 cgroup의 CPU 사용 시간을 포함한다. 값의 의미는
[Linux cgroup v2](https://docs.kernel.org/admin-guide/cgroup-v2.html#cpu-interface-files)를 따른다.

RSS 표본은 같은 cgroup과 하위 cgroup의 `cgroup.procs`에서 중복을 제거한 process ID별로
`/proc/<pid>/smaps_rollup`의 `Rss`를 읽어 합산한다. `kB` 값에 1,024를 곱해 byte로 변환하며
1 MiB는 1,048,576 byte다. 이 합계는 process별 상주 메모리 합이므로 공유 page가 여러 process에
매핑되면 중복 집계될 수 있다. cgroup `memory.current`와 Docker의 메모리 사용량은 별도
진단값으로 기록한다. RSS field의 의미는
[Linux process 메모리 통계](https://docs.kernel.org/filesystems/proc.html)를 따른다.

P95는 3,600개 유효 표본을 오름차순 정렬한 뒤 `ceil(0.95 * N)`번째 값을 선택한다. 순위는
1부터 시작하며 보간하지 않는다. 각 실행의 CPU와 RSS에 이 규칙을 따로 적용한다. 수집기와
`lidar-processing`의 CPU 및 RSS도 같은 간격으로 기록하되 생성기 통계에 합치지 않는다.

### Frame 완료 지연과 유실

Frame 지연은 예정된 scan 완료 deadline부터 sensor의 latest-two buffer 게시가 끝난 시각까지의
차이다. 두 시각은 Linux `CLOCK_MONOTONIC` 기준이며 scan 계산, 완료 대기열, frame 구성 및 게시
지연을 포함한다. 구독자의 수신 시각은 별도의 전달 지연으로 기록한다. 계측 표본은 유한 상한의
buffer에 수집하고 표본 누락이 있으면 해당 실행을 불완전 측정으로 기록한다.

수집기는 sensor별 frame 지연과 같은 deadline을 공유한 두 sensor의 지연 중 큰 값을 기록한다.
측정 구간에 예정 완료 deadline이 있는 모든 게시 frame이 대상이며 마지막 deadline의 frame도
게시 완료까지 추적한다. P99는 각 지연 표본을 오름차순 정렬한 뒤 `ceil(0.99 * N)`번째 값을
보간 없이 선택한다. 두 sensor 공동 완료 지연 P99를 합격선에 비교하고 sensor별 P99도 보고한다.
게시되지 않은 frame은 지연 표본에서 조용히 제외하지 않고 생성 또는 전달 실패로 기록한다.

Sequence gap은 sensor별 구독 연결의 첫 frame을 기준값으로 삼고, 같은 연결과 같은 `instance_id`의
후속 frame에서 `sequence - previous_sequence - 1`로 계산한다. 양수는 gap이며 중복 또는 역행은
별도 오류다. 늦은 첫 연결의 이전 sequence는 gap에 포함하지 않는다. 재연결과 instance 변경은
별도 사건으로 기록하고 새 기준값을 만든다. 재연결 구간의 유실 여부가 확인되지 않으면 유실 0으로
판정하지 않는다.

연결 중 gap은 생성기의 게시 sequence와 수집기의 수신 sequence를 대조해 원인을 분류한다.
생성기 원인 gap 0은 정상 수신 속도의 지속 구독에서 판정하며, server의 누적 `frame_loss`는 별도
보고한다. 의도적인 느린 구독자와 연결 장애 검증은 이 성능 실행에서 분리한다.

### 관찰 추가 부하

관찰 비교는 각 적재 설정에서 같은 image와 seed를 사용하는 두 실행으로 구성한다. 실제
publisher 실행은 기본 1초 cadence의 snapshot 생성, JSON Lines 직렬화 및 실제 수신기까지의 TCP
전송을 포함한다. 비교 실행은 benchmark 전용 no-op publisher를 주입해 관찰 snapshot 복사,
직렬화 및 전송을 생략한다. 공개 CLI(Command-Line Interface)와 배포 설정에는 관찰 비활성 옵션을
추가하지 않는다.

두 실행은 같은 모델 시작 상태, 5분 준비 구간, 60분 측정 구간과 나머지 실행 조건을 사용한다.
관찰 추가 CPU는 `실제 publisher 실행 CPU P95 - no-op 실행 CPU P95`로 계산한다. 합격선은
`docs/development-plan.md`를 따른다. 연결 실패와 폐기가 있는 실제 publisher 실행은 정상 전송
비교 결과로 사용하지 않는다. 같은 평균 적재 주기의 두 실행은 phase, cycle과 전환 시각으로 만든
시나리오 schedule digest도 같아야 한다. 음수 CPU 차이도 그대로 기록하며 두 실행의 장비 온도와
다른 process 부하를 함께 보고한다.

### 상태와 결과 기록

추가 확인 항목은 7개다.

| 항목 | 기록 내용 |
| --- | --- |
| 처리 결과 | 두 sensor와 융합 결과의 `GOOD` 유지, sensor sequence 연속성 및 측정 전달 지연 |
| 결과 의미 | Simulator 기준 광선 구성과 변화, 처리 높이 및 적재율 정합성, phase별 방향과 실패 영역 |
| 장비 상태 | load average, 온도와 thermal throttling |
| CPU 제한 | cgroup `nr_throttled`, `throttled_usec`의 증가량 |
| 연결 상태 | sensor별 재연결과 instance 변경 횟수 |
| 관찰 상태 | 연결 실패, 폐기 및 전송 record 수 |
| 생명주기 | Docker restart와 OOM(Out Of Memory) 발생 여부 |

준비 구간과 측정 구간의 오류 및 재시작을 함께 보존한다. 결과에는 실행별 표본 수, 누락 수,
백분위수와 불합격 원인을 기록한다. 원시 로그, 사설 주소와 실제 sensor 자료는 Git에 추가하지
않는다.

Helper는 각 처리 measurement의 `measured_at`과 수신 UTC 시각 차이를 전달 지연으로 기록한다. 측정
구간의 모든 fused measurement에 지연 표본이 하나씩 있어야 하며 최대값은 2,000 ms 이하여야 한다.
Sensor별 처리 sequence가 이전 값보다 작거나 같으면 반복 또는 역행으로 판정한다.

Simulator 의미 검증은 측정 구간에 sensor별 기준 scan을 초당 1개 집계한다. 허공, 바닥 및 외벽,
현재 적재면 교차와 최종 유효 및 무효 sample이 모두 있어야 하며 기준 교차가 없는 위치에 유효
sample을 만들면 실패다. 기준 scan은 측정 구간에서 한 번 이상 변해야 한다.

Processing 의미 검증은 `GOOD` measurement마다 sensor별 단면 적재율, 중앙 높이와 P90 높이가 모두
있는지 확인한다. 높이는 공개 합성 환경의 0-10,000 mm 범위여야 하고 P90은 중앙값 이상이어야 한다.
융합 적재율은 identity fusion map을 사용하는 두 sensor 단면 적재율 사이에 있어야 한다. Phase별
추세는 전환 경계 1초를 제외한 구간의 처음과 마지막 3개 적재율 중앙값으로 판정한다. Simulator와
processing 판정을 독립적으로 남기며 한쪽만 실패하면 해당 구성 요소를 실패 영역으로 기록한다.
