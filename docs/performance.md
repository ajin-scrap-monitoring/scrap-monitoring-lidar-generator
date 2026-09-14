# 성능 측정

## 측정 범위

현재 benchmark는 4개 구간을 측정한다.

| 구간 | 측정 범위 |
| --- | --- |
| `scene_update` | 적재 표면과 시나리오 상태 갱신 |
| `scan_generation` | 광선 교차, 측정 왜곡과 quality 생성 |
| `serialization` | 담당자 Proto ScanFrame 생성과 직렬화 |
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
완료하기 위한 시작값이고 운영 할당 기준이 아니다.

## 운영 성능 판정

운영 성능 검증 구성 요소는 5개다.

1. 합성 생성기.
2. SDK 변환기 2개를 대체하거나 함께 비교하는 검증 경로.
3. `lidar-processing`.
4. edge platform의 나머지 상시 process.
5. 관찰 수신기가 연결된 router 경로.

Raspberry Pi 5 8GB에서 다섯 조건의 장기 실행을 함께 측정하기 전에는 운영 CPU와 memory
상한을 확정하지 않는다. 측정 항목은 다음과 같다.

- 생성기와 각 process의 CPU 및 RSS.
- 장비 load average, 온도와 throttling.
- sensor별 sequence gap과 gRPC 재연결 횟수.
- `lidar-processing`의 frame age, 계산 지연과 상태.
- 관찰 publisher의 연결 실패와 폐기 수.
- Docker restart와 OOM(Out Of Memory) 발생 여부.

0.8.0의 운영 상한은 아직 확정되지 않았다. gRPC UDS와 실제 담당자 처리 process를 사용한
장기 공유 부하 결과가 운영 상한의 정본이 된다. 원시 로그, 사설 주소와 실제 sensor 자료는
Git에 추가하지 않는다.
