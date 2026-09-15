# 성능 측정

이 문서는 Raspberry Pi 5에서 시뮬레이터와 `lidar-processing`을 함께 실행할 때의 입력 부하,
단기 기능 검증과 장기 판정 방법을 정의한다.

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

## 단기 image 검증

다음 명령은 현재 commit으로 만든 ARM64 image의 공개 설정, 두 sensor의 측정 의미, UDS 2개,
상태 진행과 정상 종료를 검사한다.

```bash
tests/edge/verify-rust-image.sh \
  "$IMAGE_REF" \
  examples \
  "$(git rev-parse HEAD)"
```

이 검증은 기능 smoke test이며 `lidar-processing`과 공유하는 장기 자원 상한을 판정하지 않는다.

## ARM64 단기 통합 검증

Rust source revision `65730a13697162dc42741effd0241ac3aa942e2d`의 로컬 ARM64 image ID
`sha256:458a01ac36990ba36f9d15b145d44faeeeb2d7f83ac3b158a476c1021306a53f`를 Raspberry Pi 5
8 GB에서 검증했다. `lidar-processing`은 고정한 외부 source의 검증 호환 revision
`55b2e9d9401682c237a42945d9f548a4c912951f`과 로컬 ARM64 image ID
`sha256:c737833bc97958e2a6c0807276cce3e0f9b46fa6f020a8b890bb279092deb042`를 사용했다. 재현
source와 patch는 `edge-platform-integration/`에 고정되어 있고 두 image는 registry에 게시하지
않았다.

측정은 600초 평균 적재 주기, 관찰 no-op, 진단 비활성, 2초 준비와 30초 측정 조건을 사용했다. 이
구간은 filling 방향과 공유 자원 여유를 검증하며 수거 전이, 관찰 추가 부하와 지속 안정성은 장기
matrix에서 검증한다. CPU와 RSS는 1초 간격 30개 표본, frame 완료 지연은 300개 전체 batch를
사용했다.

| 항목 | 관측값 |
| --- | --- |
| 완료 batch | 300/300 |
| 생성기 CPU P95 | 33.17 percent |
| 생성기 RSS P95 | 5.73 MiB |
| 공동 frame 완료 지연 P99 | 20.00 ms |
| 처리기 CPU P95 | 22.30 percent |
| 처리기 RSS P95 | 66.39 MiB |
| 처리 measurement | 26개, sensor별 및 융합 `GOOD` 26개 |
| 처리 measurement 최대 간격 | 1.188 s |
| 처리 measurement 최대 전달 지연 | 192.469 ms |
| 생성기 scan 의미 | 두 sensor 모두 통과 |
| 처리 결과 의미 | 높이, quality, sensor별 및 융합 적재율 범위와 filling 증가 통과 |
| 유실 및 생명주기 | 모든 sequence 및 frame 유실, 재시작, OOM과 thermal throttling 0회 |
| 장비 최고 온도 | 59.5 C |

장비 kernel은 cgroup v2 memory controller를 활성화하지 않았다. 생성기와 처리기 RSS는 각
cgroup의 process별 `/proc/<pid>/smaps_rollup`을 합산했고 `memory.current`는 누락 진단값으로
기록했다. 기존 상주 생성기와 처리기는 검증 전후 같은 container로 복구했으며 restart count와
`vcgencmd get_throttled` 결과는 각각 0과 `0x0`이었다.

## ARM64 약식 지속 검증

Release 0.10.0 source revision `896696667a9f186042bf3deaf6b71ecb5deb7b3a`와 ARM64 image digest
`sha256:d235e4d3492d8327c5483b87754770313fd2f2b1e06efca969b6b8eba02aeeb9`를 Raspberry Pi 5
8 GB에서 30분 동안 실행했다. 평균 적재 주기는 600초이고 실제 관찰 stream은 별도 장비의
시각화 프로그램이 계속 수신했다.

| 항목 | 관측값 |
| --- | --- |
| 생성기 CPU | 257개 표본, 평균 26.36 percent, P95 31.80 percent, 최대 35.73 percent |
| 생성기 메모리 | 실행 파일 process `VmHWM` 6.47 MiB |
| 두 sensor 진행 | 완료 후 직접 상태에서 각각 sequence 22,266, lifetime 평균 10.00 scan/s |
| 생성 상태 | 두 sensor 모두 `HEALTHY`, `sdk_errors` 0 |
| 관찰 수신 | sequence 217부터 2,016까지 1,799개 증가, 누락과 거부 0 |
| 관찰 연결 | 같은 run과 connection 유지, render 오류 0 |
| 장비 온도 | 257개 표본, 평균 57.27 C, 최대 62.25 C |
| 장비 상태 | Container restart 0회, OOM 0회, thermal throttling 0회 |

누적 `frame_loss`는 구독자의 소비 속도에 따라 latest-two에서 덮어쓴 frame을 집계하므로 현재
교체 예정인 처리 구성 요소의 값은 simulator 생성 실패 판정에서 제외했다. 약식 수집기의 상태
파일 표본은 host 권한 문제로 유효하지 않아 완료 후 파일을 직접 확인했다. 이 검증은 simulator의
지속 생성과 관찰 전송에 이상이 없음을 확인하지만 frame 완료 지연, RSS P95와 처리 결과를 포함한
선택적 장기 검증 결과를 대신하지 않는다.

## 공유 부하 측정 규칙

장기 공유 부하 결과는 없다. 아래 규칙은 필요할 때 지속 안정성을 재현하는 선택적 절차이며 현재
후속 작업으로 예약하지 않는다. 수치 기준은
[`development-plan.md`](development-plan.md#release-전-단기-합격)가 정본이다. 단기 통합 검증의
관측값은 이 절차의 결과를 대신하지 않는다.

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
매핑되면 중복 집계될 수 있다. Linux가 memory controller를 활성화한 경우 cgroup
`memory.current`를 별도 진단값으로 기록하며, 이 값의 부재는 RSS 판정을 무효화하지 않는다. RSS
field의 의미는
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
