# 구현 아키텍처

## 설계 목표

생성 프로그램은 결정론적인 장면 및 측정 계산과 비결정적인 입출력을 분리한다. 같은 설정, 난수 seed와 시뮬레이션 시각은 전송 상태와 무관하게 같은 스캔 열을 만든다. 센서별 회전 완료와 전송은 독립적이며 모든 센서는 하나의 장면 상태를 관측한다.

## 소스 구성 요소

소스 패키지는 6개 구성 요소와 1개 조립 진입점으로 구성한다.

| 경로 | 책임 |
| --- | --- |
| `configuration/` | 환경 및 실행 설정 읽기, 엄격한 검증과 내부 모델 변환 |
| `geometry/` | 벡터, 경계 다각형, 표면과 광선의 최초 교차 계산 |
| `scenario/` | 적재 및 수거 상태, 표면 변화와 시간 기반 사건 전이 |
| `measurement/` | 센서 회전, 측정점 시각, 기준 거리, 왜곡과 품질 생성 |
| `transport/` | MessagePack 직렬화, 스캔 송신, ACK(Acknowledgement), 버퍼와 재시도 |
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
```

`geometry`는 다른 프로젝트 패키지를 참조하지 않는다. `configuration`은 공간 입력의 의미 검증에 `geometry`를 사용한다. `scenario`는 측정과 전송을 참조하지 않고, `measurement`는 전송을 참조하지 않는다. `transport`는 장면 상태를 변경하지 않는다. `runtime`과 `cli.py`만 장기 실행 객체를 조립하고 생명주기를 제어한다.

## 시간과 재현성

시간은 시뮬레이션 시각, UTC(Coordinated Universal Time) 기준 시각과 단조 증가 시각의 3종으로 분리한다. `runtime`은 시뮬레이션 시각 순서로 장면과 센서 사건을 처리한다. 동일 시각의 사건은 센서 식별자와 사건 순서로 안정적으로 정렬한다. UTC 기준 시각은 스캔 대표 시각 계산에만 사용하고, 단조 증가 시각은 연결과 재시도 제한 시간 계산에만 사용한다.

난수는 실행 seed에서 책임별 및 센서별 하위 난수 흐름을 안정적으로 파생한다. 장면 변화, 측정 오차, 품질 값과 전송 재시도 지연은 서로 다른 흐름을 사용한다. 한 기능의 난수 소비량 변화가 다른 기능의 결과를 바꾸지 않게 한다.

`measurement`는 오차 적용 전 기준 거리와 최종 측정값을 별도 결과로 만든다. 검증용 기준값은 진단 경로에서만 사용하며 외부 스캔에는 포함하지 않는다. 전송 지연, 연결 실패와 버퍼 폐기는 시뮬레이션 상태를 초기화하거나 되돌리지 않는다.

## 외부 경계

`configuration`은 UTF-8 JSON(JavaScript Object Notation)을 중복 key와 비유한 숫자까지 검사한 뒤 내부 모델로 변환한다. 공개 예시와 자동 검증은 현장 값에서 파생되지 않은 합성 입력만 사용한다.

외부 입력 계약은 `contracts/v1/`에서 JSON Schema Draft 2020-12로 관리한다. `configuration`은 환경 설정 계약의 구조 규칙과 JSON Schema로 표현할 수 없는 다각형 및 방향벡터 규칙을 함께 검증한다.

`transport`만 외부 스캔 계약과 MessagePack 표현을 안다. 정확한 message framing과 ACK 및 오류 응답은 수신 프로그램과 합의한 계약으로 고정한 뒤 구현한다. 내부 계산 모델은 전송 표현에 의존하지 않는다.

## 검증 구조

자동 검증은 4개 계층으로 구성한다.

| 경로 | 검증 범위 |
| --- | --- |
| `tests/unit/` | 설정 규칙, 수치 계산, 상태 전이와 오류 처리의 작은 단위 |
| `tests/integration/` | 장면에서 스캔 생성까지의 연결과 전송 장애 복구 |
| `tests/contract/` | JSON 및 MessagePack schema, 수신 프로그램과 공유하는 합성 fixture |
| `tests/performance/` | 지속 생성량, 지연, 메모리와 Linux ARM64(64-bit Arm architecture) 실행 부하 |

단위 및 통합 검증은 외부 네트워크와 실제 시각에 의존하지 않는다. 계약 fixture는 사람이 검토할 수 있는 원본과 인코딩 결과를 함께 관리한다. 성능 검증은 기능 회귀 검사와 분리하고 측정 환경 및 명령을 결과와 함께 기록한다.

## Repository 구조

```text
.github/
  workflows/
    ci.yml
.python-version
contracts/
  v1/
docs/
  architecture.md
  dependencies.md
  development-plan.md
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
    runtime/
    __main__.py
    cli.py
tests/
  unit/
  integration/
  contract/
  performance/
uv.lock
```

`docs/internal/`은 공개 구조의 입력이나 fixture 저장소가 아니라 읽기 전용 기준 자료 경계다.
