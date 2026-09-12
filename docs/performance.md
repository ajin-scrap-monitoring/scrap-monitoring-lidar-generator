# 성능 측정

## 측정 범위

성능 측정은 4개 구간으로 구성한다.

| 구간 | 측정 범위 |
| --- | --- |
| `scene_update` | 적재 표면과 시나리오 상태 갱신 |
| `scan_generation` | 광선 교차, 측정 왜곡과 품질 생성 |
| `serialization` | MessagePack body와 길이 prefix 생성 |
| `transport_wait` | 센서별 loopback TCP 연결, frame 전송과 ACK 대기 |

benchmark는 생성 pacing을 제거하고 설정된 시뮬레이션 시간만큼의 스캔을 가능한 빠르게 처리한다. 송신 경로는 설정된 센서마다 독립된 지속 TCP 연결을 사용한다. 수신 test double은 별도 process에서 실행하므로 생성 process의 CPU(Central Processing Unit) 시간에 수신 처리 부하가 포함되지 않는다. 진단 파일 기록, 연결 실패, 재전송과 실제 수신 프로그램의 처리 시간은 측정 범위에 포함하지 않는다.

## 실행

Python 3.14.4와 잠금된 개발 환경에서 실행한다.

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

## Raspberry Pi 5 검증 결과

검증 대상은 `v0.1.0` ARM64 image `ghcr.io/ajin-scrap-monitoring/scrap-monitoring-lidar-generator@sha256:75877ace8dbda3fffa717fecf9e2b733a85e0610e9dd2e22227ca2bae70268c7`다. Raspberry Pi 5 8GB에서 Python 3.14.4와 aarch64 실행 환경을 사용했다. 공개 합성 설정의 센서 1개에서 고정 seed로 500회 스캔과 1,600,000개 측정점을 생성하고, 시뮬레이션 시간 50초를 동일 조건으로 4회 실행했다.

| 항목 | 관측 범위 |
| --- | --- |
| 생성 process wall time | 26.791-27.153초 |
| 생성 process CPU time | 23.754-23.969초 |
| 단일 core 환산 사용률 | 47.507-47.939퍼센트 |
| 최대 RSS | 49.7-50.1MiB |
| `scan_generation` 평균 | 18.641-18.828ms |
| `scene_update` 평균 | 0.146-0.149ms |
| `serialization` 평균 | 2.317-2.370ms |
| `transport_wait` 평균 | 6.237-6.535ms |
| 장비 온도 | 최대 62.25 C |
| 최대 load average | 0.69 |
| 최소 MemAvailable | 7551MB |
| throttling | `0x0` |

이 결과는 변경된 공개 합성 실행 프로파일을 외부에서 bind mount하고, 이미지 내부의 loopback 수신 test double과 생성 process를 사용한 단일 컨테이너 검증이다. 장비 온도, load average와 MemAvailable은 4회 중 한 실행에서 함께 관측했다. 실제 수신 프로그램 처리 시간, 다른 edge process와의 동시 자원 경합, 운영 네트워크와 장기 지속 실행은 포함하지 않는다. 따라서 실제 수신 프로그램 통합과 공유 부하 기준 확정 전의 기술 검증 결과로 사용한다.

## v0.2.0 관찰 stream 엣지 검증

관찰 stream이 포함된 `v0.2.0` ARM64 image를 Raspberry Pi 5 8GB에서 공개 합성 설정으로
실행했다. scan ACK와 관찰 JSON Lines 수신에는 같은 Docker network의 별도 test double
container를 사용했다. 생성 container에는 CPU 1 core 상한을 적용했다.

| 항목 | 관측값 |
| --- | --- |
| Image digest | `sha256:359681841a572ec37255a5db45baacd655d49d8cc5fa3529ca7cda181f0d1e68` |
| 생성 및 ACK | 486 scan, 미응답 0 |
| 관찰 전송 | 49 record, 폐기 0, 연결 실패 0 |
| scan 구성 | 회전당 3,200 point, 초당 10회전 |
| 관찰 구성 | 33 x 25 표면 격자, 시뮬레이션 시각 1초 주기 |
| 관찰 line 크기 | 첫 record 약 4.1KB, 표면 갱신 이후 약 19KB |
| 생성 process CPU | CPU 1 core 상한에서 48.9퍼센트 |
| 생성 process RSS | 53,136KiB |
| 장비 상태 | load average 0.56, MemAvailable 7,545MB, 58.7 C, throttling `0x0` |

관찰 수신 test double은 렌더링을 수행하지 않고 version 1 header와 observation을 decode했다.
관찰 수신은 scan ACK 처리와 독립적으로 진행됐으며 생성 종료 시 두 경로의 sequence가
연속적이었다. 현재 장비 kernel은 Docker memory cgroup 제한을 제공하지 않아 container
memory 상한은 적용할 수 없고 host process RSS로 관측했다.

이 결과는 ARM64 image에서 scan과 관찰 stream을 동시에 제공하는 기능 및 단기 부하
검증이다. 실제 scan 수신 프로그램, router를 지나는 별도 시각화 장비, 다른 edge process와
장기 동시 실행은 포함하지 않는다.
