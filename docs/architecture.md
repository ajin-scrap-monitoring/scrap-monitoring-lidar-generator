# 구현 아키텍처

## 설계 목표

생성 프로그램은 결정론적인 장면 및 측정 계산과 비결정적인 입출력을 분리한다. 같은 설정, 난수 seed와 시뮬레이션 시각은 전송 상태와 무관하게 같은 스캔 열을 만든다. 센서별 회전 완료와 전송 lane은 독립적이며 모든 센서는 하나의 장면 상태를 관측한다.

## 소스 구성 요소

소스 패키지는 7개 구성 요소와 1개 조립 진입점으로 구성한다.

| 경로 | 책임 |
| --- | --- |
| `configuration/` | 환경 및 실행 설정 읽기, 엄격한 검증과 내부 모델 변환 |
| `geometry/` | 벡터, 경계 다각형, 표면과 광선의 최초 교차 계산 |
| `scenario/` | 적재 및 수거 상태, 표면 변화와 시간 기반 사건 전이 |
| `measurement/` | 센서 회전, 측정점 시각, 기준 거리, 왜곡과 품질 생성 |
| `transport/` | MessagePack 직렬화, 스캔 송신, ACK(Acknowledgement), 버퍼와 재시도 |
| `observation/` | 읽기 전용 적재 모델 snapshot의 version 1 JSON Lines TCP stream과 latest-only 비동기 출력 |
| `runtime/` | 시뮬레이션 시각 진행, 센서 작업과 전송 작업의 생명주기 조정 |
| `cli.py` | CLI(Command-Line Interface) 설정 로딩, 의존성 조립, 시작과 정상 종료 처리 |

의존 방향은 다음과 같다.

```text
cli -> configuration -> geometry
cli -> runtime
runtime -> scenario -> geometry
runtime -> measurement -> geometry
runtime -> transport -> measurement
measurement -> scenario
runtime -> observation -> scenario
```

`geometry`는 다른 프로젝트 패키지를 참조하지 않는다. `configuration`은 공간 입력의 의미 검증에 `geometry`를 사용한다. `scenario`는 측정과 전송을 참조하지 않고, `measurement`는 전송을 참조하지 않는다. `transport`는 장면 상태를 변경하지 않는다. `runtime`과 `cli.py`만 장기 실행 객체를 조립하고 생명주기를 제어한다.

`observation`은 `scenario`가 제공하는 읽기 전용 snapshot을 기존 scan 계약과 별도 TCP stream으로 계속 전송한다. publisher는 연결마다 정적 장면 header를 1회 보내고 기본 1초마다 동적 표면을 보낸다. 전송 시각을 먼저 검사한 뒤 snapshot을 복사하고 최신 observation 1개만 보관한다. 연결 실패, 재연결과 느린 수신기는 scan 생성 및 전송 생명주기와 분리된다. 3D 표시, 기록과 MP4 생성은 별도 시각화 Repository가 담당한다.

## 시간과 재현성

시간은 시뮬레이션 시각, UTC(Coordinated Universal Time) 기준 시각과 단조 증가 시각의 3종으로 분리한다. `runtime`은 시뮬레이션 시각 순서로 장면과 센서 사건을 처리한다. 동일 시각의 사건은 센서 식별자와 사건 순서로 안정적으로 정렬한다. UTC 기준 시각은 스캔 대표 시각 계산에만 사용하고, 단조 증가 시각은 연결과 재시도 제한 시간 계산에만 사용한다.

시나리오 시간 비율은 설정한 평균 적재 시간을 24시간으로 나눈 값이다. 투입 및 수거 속도 변화와 시간 기반 측정 왜곡의 간격 및 지속시간에는 이 비율을 곱하고, 초당 사건 발생률에는 역수를 적용한다. 공간 거리, 반경, 부피 비율과 측정점별 발생 확률은 시간 비율로 바꾸지 않는다.

난수는 실행 seed에서 책임별 및 센서별 하위 난수 흐름을 안정적으로 파생한다. 장면 변화, 측정 오차, 품질 값과 전송 재시도 지연은 서로 다른 흐름을 사용한다. 한 기능의 난수 소비량 변화가 다른 기능의 결과를 바꾸지 않게 한다.

`measurement`는 오차 적용 전 기준 거리와 최종 측정값을 별도 결과로 만든다. 검증용 기준값은 진단 경로에서만 사용하며 외부 스캔에는 포함하지 않는다. 진단 sink는 완료된 스캔마다 해당 시점의 시나리오 상태, 전체 높이장, 기준점과 생성 입력의 SHA-256(Secure Hash Algorithm 256-bit) 지문을 받는다. 로컬 진단 version 2 JSON Lines 파일은 실행 `run_id`, 실행 시작 UTC 시각과 wire scan과 같은 `captured_at`을 기록한다. 센서별 설정 개수까지만 기록하고 새 순번으로 생성하여 기존 파일을 덮어쓰지 않는다. 입력 지문은 생성 결과를 결정하는 설정값만 포함하며 입력 경로와 진단 설정은 제외한다. 전송 지연, 연결 실패와 버퍼 폐기는 시뮬레이션 상태를 초기화하거나 되돌리지 않는다.

## 외부 경계

`configuration`은 UTF-8 JSON(JavaScript Object Notation)을 중복 key와 비유한 숫자까지 검사한 뒤 내부 모델로 변환한다. 공개 예시와 자동 검증은 현장 값에서 파생되지 않은 합성 입력만 사용한다.

입력 계약은 `contracts/v1/`에서 JSON Schema Draft 2020-12로 관리한다. 공통 환경, 스캔 및 응답 계약은 생성 프로그램과 수신 프로그램이 공유하고, 생성 실행 설정 및 품질 분포 계약은 생성 프로그램만 사용한다. `configuration`은 각 계약의 구조 규칙과 JSON Schema로 표현할 수 없는 다각형, 방향벡터, 구간 순서 및 참조 입력 사이의 규칙을 함께 검증한다.

`transport`만 외부 스캔 계약, MessagePack 표현과 TCP(Transmission Control Protocol) framing을 안다. 송신할 때 4 byte unsigned big-endian 본문 길이를 붙이며 연결별 decoder는 분할되거나 결합된 수신 byte에서 본문을 복원한다. ACK(Acknowledgement)와 오류 응답은 `run_id`, `sensor_id`, `scan_id` 조합으로 스캔을 참조한다. 내부 계산 모델은 전송 표현에 의존하지 않는다.

`transport.ScanMessageFactory`는 실행 식별자와 시뮬레이션 시각 0에 대응하는 UTC Unix 마이크로초를 최종 측정 스캔에 결합한다. 첫 측정점의 경과 시각은 가장 가까운 마이크로초로 반올림하며 정확히 절반이면 미래 방향으로 정한다. `ScanMessage`는 기존 측정 배열을 복사하지 않고 참조한다. MessagePack codec은 공개 버전 1의 스캔, ACK와 오류 본문을 엄격하게 처리하며 framing은 설정한 최대 본문 크기를 독립적으로 적용한다.

`transport.UnackedFrameBuffer`는 길이 접두부를 포함한 완성 frame과 최초 적재 단조 시각을 함께 보관한다. 보관 시간과 전체 byte 상한을 적용해 가장 오래된 frame부터 폐기하며 ACK는 정확히 일치하는 스캔 식별자 하나만 제거한다. `transport.ReconnectBackoff`는 전송 전용 seed 흐름에서 full jitter를 만들고 연속 실패마다 지연 상한을 증가시키며 정상 ACK 뒤에만 상한을 초기화한다.

`transport.AsyncFramedTcpConnection`은 연결, frame 전송과 body 수신의 제한 시간을 단조 증가 시계로 적용한다. 수신 frame 상태는 TCP 연결마다 분리하고 제한 시간, 연결 중단 또는 잘못된 frame 뒤에는 연결을 닫아 부분 byte를 폐기한다. 상위 송신 상태 machine은 buffer와 backoff를 이 adapter에 결합한다.

`transport.AsyncScanSender`는 센서 하나의 스캔 생성 호출을 네트워크 대기와 분리하고 지속 연결에서 가장 오래된 미응답 frame부터 전송한다. 현재 전송 스캔과 일치하는 ACK만 완료 처리하며 연결 오류, timeout과 일시 오류는 같은 frame을 재전송한다. 입력 오류는 해당 스캔만 폐기한다.

`transport.AsyncMultiSensorScanSender`는 설정된 센서마다 `AsyncScanSender`와 TCP 연결을 하나씩 구성한다. 한 센서의 ACK 대기와 재연결은 다른 센서 lane의 전달을 막지 않는다. 설정의 `buffer_max_bytes`는 센서 식별자 순서로 균등 분할하여 모든 lane의 합산 보관량이 설정 상한을 넘지 않게 한다. 환경, version 또는 응답 규격 오류가 한 lane에서 발생하면 전체 lane을 buffer 만료가 계속되는 전송 중단 상태로 전환하고 최초 오류를 센서 식별자와 함께 즉시 보고한다.

## 적재 표면 상태

`scenario.HeightField`는 경계 다각형의 bounding box를 일정한 간격의 node 격자로 덮고, 각 cell 안의 높이를 bilinear 보간한다. 경계 다각형과 격자 cell의 교차 면적을 node별 적분 가중치로 계산하여 경계 밖 영역을 부피에서 제외한다.

표면 갱신은 중심과 확산 반경을 입력받는 국소 Gaussian kernel을 사용한다. 높이가 상단 또는 바닥에 먼저 도달한 node를 제한한 뒤 남은 node로 변화량을 재분배한다. 요청량이 전체 가용 부피를 넘는 경우에는 적용량과 미적용량을 분리하여 반환한다.

충전 중 각 표면 갱신은 활성 투입구의 확산 범위 안에서 국소 요철 하나를 만든다. 요철은 유한 반경 안의 중심부와 주변부 높이를 반대 방향으로 바꾸어 적분 부피를 0으로 유지한다. 설정 높이를 그대로 적용하면 바닥 또는 상단을 벗어나는 경우 전체 형상을 같은 비율로 축소한다. 요철 위치, 높이와 반경은 회차 및 속도 계획과 분리한 난수 흐름에서 선택한다.

`scenario.ScenarioSimulator`는 적재와 수거의 2개 상태를 관리한다. 회차 시작 시 목표 적재 시간과 임계 부피를 고정하고, 임계 부피에 도달한 시각에 투입을 중단한 뒤 수거 상태로 전환한다. 수거 시작 시 목표 수거 시간을 고정하고, 해당 시각에 높이장을 완전히 비운 뒤 다음 적재 회차를 즉시 시작한다.

회차 내부 속도는 평균 1을 기준으로 양과 음의 `sin^2` lobe를 쌍으로 구성한다. 각 쌍의 편차 적분값은 0이고 전체 속도 적분값은 목표 부피와 일치한다. 회차 목표와 속도 곡선은 실행 seed에서 파생한 서로 다른 난수 흐름을 사용한다.

높이장은 설정한 전역 시뮬레이션 시각 간격과 상태 전환 시각에 갱신한다. 적재 위치는 활성 부피 비율 이후 각 투입구 주변 격자의 면적 가중 평균 높이를 비교하여 더 낮은 위치로 전환한다. 수거는 별도 공간 경로 설정을 요구하지 않고 점유된 높이장 전체를 같은 높이 변화량으로 낮춘다.

`geometry.RaySurface`는 장면이 구체적인 표면 구현을 참조하지 않고 최초 광선 교차를 요청하는 Protocol이다. 높이장은 광선 경로를 격자 cell 구간으로 나누고 각 구간의 bilinear 높이와 광선 사이의 이차식을 풀며, 경계 다각형 내부의 가장 가까운 해만 반환한다. `EnvironmentScene`은 같은 공간 경계와 높이 범위를 사용하는 표면만 결합한다.

scalar 광선 교차는 수치 정확성의 기준 구현이다. 스캔 생성 경로는 `RayBatch`의 원점과 방향 배열을 사용하여 다각형 포함, 바닥, 외벽, 고정 표면 및 높이장 교차를 묶음으로 계산한다. batch 결과는 입력 각도 순서를 유지한 `ReferencePoint`로 변환한다.

## 센서 회전과 측정 시각

`measurement.SensorRotationScheduler`는 센서마다 독립된 회전 상태와 `scan_id`를 관리한다. 각 센서는 실행 seed와 `sensor_id`에서 안정적으로 파생한 시작 각도를 사용하며, 다른 센서의 scheduler 진행 여부가 해당 센서의 결과를 바꾸지 않는다.

측정점은 `sample_index / sample_rate_hz`의 시뮬레이션 시각에 생성한다. 회전 구간은 `rotation_index / rotation_rate_hz`부터 다음 회전 경계 직전까지이며, 경계 시각의 측정점은 다음 스캔에 포함한다. 각 구간의 끝 sample index를 유리수 연산으로 계산하므로 빈도의 비율이 정수가 아니어도 스캔별 측정점 수는 실제 구간에 따라 달라지고 장기 측정 빈도는 유지된다.

`ScheduledScan`은 논리적인 회전 시작 및 완료 시각과 측정 순서의 각도 및 시뮬레이션 시각 배열을 구분한다. 외부 스캔의 `captured_at`은 이 배열의 첫 측정점 시각을 실행 기준 UTC(Coordinated Universal Time)에 더해 계산한다.

`runtime.ReferenceGenerationRuntime`은 모든 센서의 진행 중인 회전과 하나의 `ScenarioSimulator`를 시뮬레이션 시각 순서로 조정한다. 높이장 갱신 시각 직전까지의 측정점을 현재 장면에서 sensor별 batch로 계산하고, 같은 시각의 높이장 갱신을 먼저 적용한 뒤 해당 시각의 측정점을 계산한다. 측정 사건 관찰자는 각 구간에서 장면보다 먼저 같은 종료 시각까지 진행한다. 가장 이른 회전 완료 시각마다 완료된 스캔만 반환하며 동시에 완료된 스캔은 `sensor_id` 순서로 정렬한다.

`measurement.SpatialDistortionTimeline`은 모든 센서가 공유하는 낙하물, 빈틈과 수거 가림 사건을 시뮬레이션 시간순으로 생성한다. 낙하물 후보 사건은 투입 속도 상한의 Poisson process에서 만들고 해당 시각의 실제 투입 속도 비율로 선별한다. 사건 중심은 활성 투입구의 더미 확산 범위 안에서 선택하며, 기준 표면 교차점의 평면 투영이 사건 반경 안에 있고 측정점 시각이 사건의 반개구간 안에 있을 때 거리 감소 후보를 만든다.

빈틈은 재료가 있는 표면에 서로 겹치지 않는 원형 영역으로 배치하며 활성 영역의 합이 설정한 표면 투영 면적 비율에 도달하도록 유지한다. 각 빈틈은 설정 수명이 끝나거나 생성 시점보다 국소 표면이 설정 높이만큼 상승하거나 수거가 시작되면 종료한다. 빈틈의 거리 증가 후보는 정적 장면의 바닥이나 외벽 및 최대 측정 거리를 통과하지 않을 때만 사용한다. 완료되지 않은 가장 오래된 회전보다 먼저 끝난 공간 사건은 제거한다.

수거 가림은 수거 회차가 시작된 뒤 설정 간격으로 생성하며 수거 종료 시각을 넘기지 않는다. 각 사건의 중심은 가림 반경을 포함한 이동 선분 전체가 경계 다각형 안에 남는 시작점과 끝점 사이를 선형 이동한다. 측정점마다 해당 시각의 중심을 계산하고 기준 표면 교차점이 가림 반경 안에 있을 때 거리 감소 후보를 만든다. 낙하물, 수거 가림 및 반사 경로 오류의 후보는 합산하지 않고 센서에서 가장 가까운 거리 하나를 사용한다.

`measurement.MeasurementGenerator`는 공간 사건과 반사 경로 오류가 만든 거리 후보 중 센서에서 가장 가까운 하나를 선택한다. 거리 감소량은 설정 범위와 측정 가능한 거리의 교집합에서 선택하며 벽, 바닥과 무효 측정은 이 오류에서 제외한다. 센서별 지속 무효 구간은 이전 구간이 끝난 뒤 다음 비활성 간격을 시작하며 활성 구간은 시작 시각을 포함하고 종료 시각을 제외한다. 활성 구간의 거리를 0으로 만든 뒤 남은 유효 거리에 설정 한계 안의 절단 정규분포 오차를 적용하고 측정 범위를 다시 검사한다. 범위 밖 결과와 기준 무효 거리는 거리 0으로 만들며, 최종 거리 유효성에 맞는 센서별 누적 품질 분포에서 8비트 값을 선택한다. 최종 각도, 거리와 품질은 읽기 전용 배열로 보관한다.

`MeasurementResult`는 같은 회전 일정의 `TimedReferenceScan`과 `TimedMeasuredScan`을 별도 필드로 유지한다. `runtime.MeasurementGenerationRuntime`은 시각 순서로 완료된 기준 스캔에 센서별 측정 생성기를 적용한다.

`runtime.run_scan_generation`은 실행 시작 단조 시각에 회전 완료 경과 시각을 더한 절대 deadline으로 생성 속도를 조절한다. `runtime.run_generator_application`은 실행별 UUID(Universally Unique Identifier)와 UTC 기준 시각을 만들고 측정, 진단 및 비동기 송신의 수명주기를 함께 관리한다. CLI는 `--config`로 실행 설정을 받고 SIGINT와 SIGTERM에서 생성과 송신을 정상 종료한다.

종료 집계는 생성, 적재, 전송, ACK, 거부, 시간 만료, 용량 폐기, 크기 초과, 연결 실패와 미응답 frame 및 byte를 구분한다. 첫 집계 줄은 기존 필드를 유지하고 두 번째 `transport` 줄이 전체 전송 상태를 제공한다.

`runtime.PerformanceRecorder`는 명시적으로 주입한 benchmark 실행에서만 장면 갱신과 스캔 생성 시간을 누적한다. recorder를 주입하지 않은 생성 실행은 성능 시계를 읽지 않는다. 직렬화와 전송 대기는 외부 adapter 경계를 사용하는 benchmark가 같은 recorder에 기록한다.

관찰 stream의 형식, bounded latest-only 정책과 별도 장비 경계는 [`docs/observation.md`](observation.md)에서 관리한다. 관찰 레코드는 생성기의 현재 적재물 표면 모양을 보존하며 일반 scan으로 재구성하지 않는다.

## 검증 구조

자동 검증은 4개 계층으로 구성한다.

| 경로 | 검증 범위 |
| --- | --- |
| `tests/unit/` | 설정 규칙, 수치 계산, 상태 전이와 오류 처리의 작은 단위 |
| `tests/integration/` | 장면에서 스캔 생성까지의 연결과 전송 장애 복구 |
| `tests/contract/` | JSON 및 MessagePack schema, 수신 프로그램과 공유하는 합성 fixture |
| `tests/performance/` | 지속 생성량, 지연, 메모리와 Linux ARM64(64-bit Arm architecture) 실행 부하 |
| `tests/edge/` | Docker Engine만 사용하는 digest image의 scan 및 관찰 TCP 검증 |

단위 및 통합 검증은 외부 네트워크와 UTC 시각에 의존하지 않는다. 전송 검증은 local loopback과 event loop의 단조 시각을 사용한다. 계약 fixture는 사람이 검토할 수 있는 원본과 인코딩 결과를 함께 관리한다. 성능 검증은 기능 회귀 검사와 분리하고 측정 환경 및 명령을 결과와 함께 기록한다.

## Repository 구조

```text
.github/
  workflows/
    ci.yml
    release.yml
.dockerignore
.python-version
Dockerfile
contracts/
  v1/
  observation/
    v1/
docs/
  architecture.md
  deployment.md
  dependencies.md
  development-plan.md
  observation.md
  visualizer-requirements.md
  performance.md
  project-spec.md
  internal/
examples/
pyproject.toml
src/
  scrap_monitoring_lidar_generator/
    configuration/
    geometry/
    scenario/
    measurement/
    transport/
    observation/
    runtime/
      performance.py
    __main__.py
    cli.py
tests/
  unit/
  integration/
  contract/
  edge/
    receiver.py
    check_result.py
    run.sh
  performance/
    generation.py
uv.lock
```

`docs/internal/`은 공개 구조의 입력이나 fixture 저장소가 아니라 읽기 전용 기준 자료 경계다.
