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

## v0.2.1 엣지 검증

검증 대상은 `v0.2.1` ARM64 image다. source revision과 불변 image 참조는 다음과 같다.

```text
29e6e340f94ff37b294d00c42aaa101b556ab2c9
ghcr.io/ajin-scrap-monitoring/scrap-monitoring-lidar-generator@sha256:4bec6747d3ddb852b38d0f5dd190177d16cf815a35a53f3b6acc549ff434bd3b
```

Raspberry Pi 5 8GB에서 공개 합성 설정, scan ACK test double과 관찰 JSON Lines 수신 test
double을 같은 Docker network에서 실행했다. 생성 container에는 CPU 1 core 상한을
적용했다. 공개 합성 센서 1개 결과는 다음과 같다.

| 항목 | 관측값 |
| --- | --- |
| 검증 구간 | 시뮬레이션 시각 약 31.4초 |
| 생성 및 ACK | 314 scan, 미응답 0 |
| 생성 point | 1,004,800개 |
| 관찰 전송 | 32 record, 폐기 0, 연결 실패 0 |
| 관찰 수신 | header 1개, observation 32개, 마지막 시뮬레이션 시각 31초 |
| 기준 진단 | version 2 record 2개, 실행 식별자와 UTC 대표 시각 기록 |
| 생성 process CPU | CPU 1 core 상한에서 48.94퍼센트 |
| 수신 test double CPU | 6.15퍼센트 |
| 장비 상태 | load average 0.28, MemAvailable 7,737,168 kB, 59.0 C, throttling `0x0` |

### 센서 2개 추가 검증

공개 합성 환경을 센서 2개로 확장해 같은 image와 CPU 1 core 상한을 적용했다. 두 센서는
각각 독립 TCP 연결을 만들고 같은 수의 scan을 전달했다.

| 항목 | 관측값 |
| --- | --- |
| 검증 구간 | 시뮬레이션 시각 43초 |
| 생성 및 ACK | 860 scan 생성, 812 ACK, 종료 시 미응답 48개 |
| 수신 scan | 센서별 407개, 합계 814개 |
| 수신 point | 센서별 1,302,400개, 합계 2,604,800개 |
| 관찰 전송 | 42 record, 폐기 2, 연결 실패 0 |
| 기준 진단 | version 2 record 센서별 2개, 실행 식별자와 UTC 대표 시각 기록 |
| 생성 process CPU | CPU 1 core 상한에서 89.61퍼센트 |
| 수신 test double CPU | 12.99퍼센트 |
| 장비 상태 | load average 0.58, MemAvailable 7,781,104 kB, 59.5 C, throttling `0x0` |

수신 수와 생성기 ACK 집계의 2개 차이는 종료 시 수신기가 처리한 전송을 생성기가 ACK로
반영하기 전에 sender가 종료된 결과다. CPU 1 core 상한에서는 ACK 처리량이 초당 20 scan의
생성률보다 낮아 backlog가 증가했다. 센서 2개 운영에는 더 큰 생성 process CPU 상한과 실제
수신 프로그램을 포함한 지속 부하 검증이 필요하다.

두 검증의 관찰 수신 test double은 렌더링을 수행하지 않고 version 1 header와 observation을
decode했다. 현재 장비 kernel은 Docker memory cgroup 제한을 제공하지 않아 container memory
사용량과 상한은 검증하지 못했다. 실제 scan 수신 프로그램, router를 지나는 별도 시각화
장비, 다른 edge process와 장기 동시 실행은 포함하지 않는다.

## v0.3.0 엣지 검증

검증 대상은 source revision `3a84915f50ea639b03f78e85e063a6f2659931c0`의 `v0.3.0`
ARM64 image다. Release asset의 불변 image 참조는 다음과 같다.

```text
ghcr.io/ajin-scrap-monitoring/scrap-monitoring-lidar-generator@sha256:6f0e5ee11dc204c0244bcaeeb7a1553e6365251c67a106545d0ed81f98911414
```

Raspberry Pi 5 8GB에서 공개 합성 2센서 설정, scan ACK test double과 관찰 JSON Lines 수신
test double을 같은 Docker network에서 실행했다. 생성 container에는 CPU 2 core 상한을
적용하고 30초 wall-clock 구간을 같은 조건으로 3회 검증했다.

| 항목 | 관측 범위 |
| --- | --- |
| 시뮬레이션 시각 | 실행별 31.2초 |
| 생성 및 ACK | 실행별 624 scan, 미응답 0 |
| 센서별 수신 | 실행별 312 scan |
| 생성 point | 실행별 1,996,800개 |
| scan 중복 및 sequence 누락 | 0 |
| scan 폐기 및 연결 실패 | 0 |
| 관찰 송수신 | 실행별 32 record, 폐기 및 연결 실패 0 |
| 생성 process CPU | 79.63-80.19퍼센트 |
| 수신 test double CPU | 12.03-12.36퍼센트 |
| 검증 후 장비 상태 | load average 0.38, MemAvailable 7,822,752 kB, 54.0 C, throttling `0x0` |

현재 장비 kernel은 Docker memory cgroup 제한을 제공하지 않아 container memory 사용량과
상한은 검증하지 못했다. Release image는 장비에 digest로 pull되어 있으며 검증 container와
network는 매 실행 후 제거된다. 실제 scan 수신 프로그램, 별도 시각화 장비, 다른 edge
process와 장기 동시 실행은 포함하지 않는다.

## v0.3.1 엣지 검증

검증 대상은 source revision `57dfa2469c8f641b326174bf6b61f302d7969eff`의 `v0.3.1`
ARM64 image다. Release asset의 불변 image 참조는 다음과 같다.

```text
ghcr.io/ajin-scrap-monitoring/scrap-monitoring-lidar-generator@sha256:b4d818ad6535eb0d624a98c31df9739607c61740033a9737196693fdcdf8ac77
```

Raspberry Pi 5 8GB에서 공개 합성 2센서 설정, scan ACK test double과 관찰 JSON Lines 수신
test double을 같은 Docker network에서 실행했다. 생성 container에는 CPU 2 core 상한을
적용하고 30초 wall-clock 구간을 검증했다.

| 항목 | 관측값 |
| --- | --- |
| 시뮬레이션 시각 | 31.2초 |
| 생성 및 ACK | 624 scan, 미응답 0 |
| 센서별 수신 | 312 scan |
| 생성 point | 1,996,800개 |
| scan 중복 및 sequence 누락 | 0 |
| scan 폐기 및 연결 실패 | 0 |
| 관찰 송수신 | 32 record, 폐기 및 연결 실패 0 |
| 생성 process CPU | 82.50퍼센트 |
| 수신 test double CPU | 12.05퍼센트 |
| 검증 후 장비 상태 | load average 0.89, MemAvailable 7,767,760 kB, 57.3 C, throttling `0x0` |

두 container는 종료 코드 0이고 OOM(Out Of Memory) 종료가 없었다. 현재 장비 kernel은
Docker memory cgroup 제한을 제공하지 않아 container memory 사용량과 상한은 검증하지
못했다. Release image는 비 root 사용자로 실행됐으며 version `0.3.1`, source revision과
ARM64 metadata를 장비에서 확인했다. 검증 container와 network 및 원격 임시 파일은 제거했고
Release image는 장비에 digest로 pull된 상태다. 실제 scan 수신 프로그램, 별도 시각화 장비,
다른 edge process와 장기 동시 실행은 포함하지 않는다.
