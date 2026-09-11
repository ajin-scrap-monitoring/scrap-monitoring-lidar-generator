# 성능 측정

## 측정 범위

성능 측정은 4개 구간으로 구성한다.

| 구간 | 측정 범위 |
| --- | --- |
| `scene_update` | 적재 표면과 시나리오 상태 갱신 |
| `scan_generation` | 광선 교차, 측정 왜곡과 품질 생성 |
| `serialization` | MessagePack body와 길이 prefix 생성 |
| `transport_wait` | loopback TCP 연결, frame 전송과 ACK 대기 |

benchmark는 생성 pacing을 제거하고 설정된 시뮬레이션 시간만큼의 스캔을 가능한 빠르게 처리한다. 수신 test double은 별도 process에서 실행하므로 생성 process의 CPU(Central Processing Unit) 시간에 수신 처리 부하가 포함되지 않는다. 진단 파일 기록, 연결 실패, 재전송과 실제 수신 프로그램의 처리 시간은 측정 범위에 포함하지 않는다.

## 실행

Python 3.14와 잠금된 개발 환경에서 실행한다.

```bash
uv run --locked python -m tests.performance.generation \
  --config /path/to/generator.v1.json \
  --scans-per-sensor 100
```

대상 장비의 지속 부하는 실제 실행 설정과 함께 충분한 스캔 수를 지정해 측정한다. 같은 조건을 3회 이상 실행하고 다른 edge process가 동작하는 상태와 생성 process만 동작하는 상태를 구분해 보존한다.

## 결과 해석

결과는 JSON(JavaScript Object Notation) 한 개로 표준 출력에 기록한다. `generated_points_per_simulated_second`와 `scan_wire_bytes_per_simulated_second`는 설정이 요구하는 측정점 및 전송량이다. `estimated_single_core_utilization_percent`는 생성 process의 CPU 시간을 처리한 시뮬레이션 시간으로 나눈 값이다. 100은 한 개 CPU core를 지속 점유하는 계산량을 의미하며 전체 장비 CPU 사용률이 아니다. `maximum_rss_bytes`는 process 시작 이후 운영체제가 관측한 최대 RSS(Resident Set Size)다.

`samples`는 각 계측 경계를 호출한 횟수다. `scan_generation`은 장면 갱신 사이의 부분 기준 스캔과 최종 측정 생성 호출을 모두 합산하므로 생성된 스캔 수와 일치하지 않을 수 있다.

단일 core 환산값만으로 배포 적합성을 판정하지 않는다. Raspberry Pi 5 8GB에서 데이터 변환 process와 나머지 edge 기능을 함께 실행하고 CPU, 최대 RSS, 온도, throttling, 전송 backlog와 지연 누적을 관찰해야 한다. 허용 기준은 이 동시 실행 결과와 장비에 남겨야 할 자원 여유를 기준으로 확정한다.
