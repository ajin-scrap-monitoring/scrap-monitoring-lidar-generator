# 개발 계획

## 현재 상태

현재 제품 경계는 5개다.

| 경계 | 상태 | 정본 |
| --- | --- | --- |
| 공개 합성 환경과 적재 시나리오 | Rust 기본 구현, Python 기준 구현 | `examples/`, `docs/configuration.md` |
| RPLIDAR S2E 호환 scan 생성 | Rust 기본 구현, Python 기준 구현 | `docs/sdk-compatibility.md` |
| `lidar-processing` gRPC over UDS scan 출력 | Rust live frame과 실제 container 수락 완료 | `contracts/lidar/v1/`, `edge-platform-integration/` |
| 적재 모델 관찰 stream | Rust 기본 구현, Python 기준 구현 | `docs/observation.md` |
| ARM64 image 배포 | Rust 단일 실행 image와 자동 검증 | `docs/deployment.md` |

현재 source와 Release version은 0.10.0이다. 기본 실행 경로와 OCI(Open Container Initiative)
image entrypoint는 Rust다. 하나의 적재 모델에서 정확히 2개 sensor scan을 만들고 sensor별
gRPC(Google Remote Procedure Call) over UDS(Unix Domain Socket) endpoint를 제공한다.
`lidar-processing`은 생성기의 환경 JSON을 직접 읽지 않으며 exporter가 같은 공개 합성 환경에서
처리 설정을 만든다.

Rust binary는 공개 JSON 3개와 배포 override를 읽고 하나의 적재 모델, sensor별 고정 계산 worker
2개, UDS gRPC server 2개, 상태 writer, 관찰 publisher와 선택적 진단 writer를 하나의 생명주기로
실행한다. SIGINT와 SIGTERM 종료는 생성과 출력 queue를 닫고 UDS를 제거한다. Python 구현은 Rust
동등성 fixture, 외부 계약 검사와 개발 도구로 유지하며 Release image에 포함하지 않는다.

Raspberry Pi 5 짧은 공유 검증은 두 sensor 기준 광선 구성, 실제 `lidar-processing`의 sensor별 및
융합 `GOOD` 결과, 높이 및 적재율 범위, sequence 진행과 자원 여유를 확인한다. 검증 image는 고정한
외부 source에 UDS authority와 frame 손실 의미를 바로잡는 재현 가능한 patch를 적용한다. 외부
Repository의 원격에는 이 변경을 게시하지 않는다. 조건과 관측값은 `docs/performance.md`, 연결 경계는
[`../edge-platform-integration/`](../edge-platform-integration/)이 정본이다.

현재 CI(Continuous Integration)는 Rust 단위 및 통합 테스트, Python 기준선, 고정한 외부 계약,
ARM64 Rust image와 문서 산출물을 검증한다. Live 계약 검증은 Rust release binary의 `run` 명령을
실제로 시작하고 exporter 출력, sensor별 UDS 구독, 상태 파일, 관찰 연결, 종료 정리와
`lidar-processing` `ProcessingEngine`의 결과를 확인한다.

4개 case 장기 공유 부하 matrix는 0.10.0 Release 이후 별도 안정성 검증으로 수행한다. 장기 검증
도구는 simulator의 허공, 정적 구조, 적재면 교차와 최종 측정점 의미를 처리 높이 및 적재율 의미와
독립적으로 판정하고 실패 영역을 simulator, `lidar-processing` 또는 판정 불가로 구분한다. 장기
검증은 기본 실행 전환과 Release의 선행 조건이 아니다.

## 전환 범위

전환 목표는 4개다.

1. 같은 공개 설정에서 동일한 합성 환경과 상태 전이 유지.
2. 기존 scan, 관찰 stream과 상태 출력 계약 유지.
3. Raspberry Pi 5에서 `lidar-processing`과 함께 실행할 CPU 여유 확보.
4. Python runtime이 없는 단일 Rust 실행 파일 기반 ARM64 OCI(Open Container Initiative) image 제공.

전환 범위에 실제 센서 운영, `lidar-processing`의 높이 및 적재율 알고리즘 변경, 시각화 프로그램
구현, observation version 1 변경은 포함하지 않는다.

## 채택한 구조

전환 구조의 결정은 12개다.

| 항목 | 결정 |
| --- | --- |
| Repository | 현재 이력, Issue, 계약과 Release를 유지하는 기존 Repository 사용 |
| 배포 단위 | Rust 실행 파일 하나를 포함한 `linux/arm64` OCI image |
| 내부 공간 정본 | 오른손 World XYZ, meter, degree |
| 센서 scan 경계 | Sensor polar, millimeter, millidegree |
| 처리 좌표 경계 | Sensor별 Section XZ, millimeter |
| 좌표 변환 위치 | World 정본에서 처리 설정을 만드는 exporter 경계 |
| Scan 전송 | 기존 Proto의 sensor별 server-streaming gRPC over UDS endpoint 2개 |
| 관찰 전송 | 기존 JSON Lines TCP version 1, 기본 1초, latest-one 비차단 출력 |
| 시뮬레이션 상태 | 하나의 coordinator가 갱신하고 두 sensor worker가 읽는 event 구간별 불변 snapshot |
| 동시성 | 제어 및 I/O runtime과 sensor별 고정 CPU worker 2개 분리 |
| 계산 자료구조 | 배열 분리 구조와 HQ 각도별 광선 및 정적 교차 결과 사전 계산 |
| 결정론 | 명시적 simulation model version과 sensor 및 현상별 독립 난수 stream |

World XYZ와 Section XZ는 통일하지 않는다. Section XZ는 `lidar-processing`이 sensor별 높이 계산에
사용하는 2D 투영이라 World Y 정보를 보존하지 못한다. Rust 구현은 좌표 frame, 거리 단위, 각도
단위와 시각을 서로 다른 자료형으로 만들고 외부 경계에서만 변환한다.

외부 version 1 계약은 전환 중 변경하지 않는다. Scan Proto, sensor ID, UDS 파일 이름, 상태 파일
경로, 환경변수 이름, observation record와 sequence 의미를 그대로 유지한다. 고정 계약의 schema
ID에 포함된 기존 Repository 이름도 version 1에서는 바꾸지 않는다.

## 구현 단계

전환은 7단계다. 각 단계는 앞 단계의 완료 조건을 통과한 뒤 시작한다.

| 단계 | 상태 | 작업 | 산출물 | 완료 조건 |
| --- | --- | --- | --- | --- |
| 0 | 완료 | 기준선 고정 | Python golden fixture, 좌표 예제, noise 오류 회귀 검증, edge 측정 기록 | 계약 의미와 현재 성능 재현 |
| 1 | 완료 | Rust 기반 구성 | package, 설정 loader, 오류 모델, CI와 ARM64 build | 공개 JSON 3개 수락 및 오류 동등성 |
| 2 | 완료 | 적재 시뮬레이션 이식 | World 자료형, 표면, 적재 및 수거 상태 전이 | 고정 시각 snapshot과 부피 불변식 통과 |
| 3 | 완료 | LiDAR 측정 이식 | 회전, 광선 교차, 합성 왜곡, quality와 SDK 정수 변환 | 무잡음 geometry 및 ScanFrame golden 통과 |
| 4 | 완료 | 외부 출력 이식 | UDS gRPC server 2개, 상태 및 진단 writer, 관찰 publisher, exporter CLI | Live UDS frame과 고정 engine의 계약 수락 |
| 5 | 완료 | ARM64 단기 통합 검증 | digest image, scan 및 처리 의미와 자원 측정 | Release 전 단기 합격선 통과 |
| 6 | 완료 | 기본 구현 전환 | Rust image, 배포 및 사용자 문서 | 새 checkout 배포와 rollback 절차 검증 |

단계 0은 무잡음 scan의 교차 좌표를 명시한 tolerance로 비교하고 SDK 이후 wire 정수는 정확히
비교한다. Noise와 합성 왜곡은 고정 seed 재현 및 분포 허용범위로 비교한다. Python의
`random.Random`과 NumPy 난수열 자체는 외부 계약이 아니므로 Rust가 그 구현 세부를 복제하지
않는다. Python seeded fixture는 전환 기준선이고 Rust exact fixture는 model version 1의 별도
계약이다. Model version 1의 PRNG(Pseudorandom Number Generator), stream seed와 rate profile 계약은
[`simulation-model.md`](simulation-model.md)가 정본이다. 같은 Rust model version, 설정과 seed는 같은 지원 architecture에서
동일한 simulation snapshot과 측정 sample을 만든다. ScanFrame golden은 instance ID와 wall 및
monotonic clock을 주입해 비교한다.

단계 2와 3은 계산 경로를 먼저 완성하고 network를 연결하지 않는다. 고정 sensor 방향과 HQ 각도별
삼각함수 값, 바닥 및 외벽 교차는 설정 변경 시 한 번 계산한다. 회전당 측정점 수와 각도 배열은
sample rate와 rotation rate의 비정수 비율에서도 달라질 수 있으므로 scan index 기준으로
고정하지 않는다. 동적 적재 표면 교차만 snapshot 구간마다 계산한다.

Coordinator는 한 scan이 가로지르는 모든 표면 event의 직전과 직후를 구분한 불변 snapshot 구간을
worker에 전달한다. Event 시각보다 이른 sample은 이전 표면을 사용하고 event 시각의 sample부터 새
표면을 사용한다. 두 sensor worker는 같은 구간과 공유 distortion history를 읽으며 모든 미완료
scan의 가장 이른 sample 시각이 지난 뒤에만 history를 폐기한다. Worker 실행 순서는 상태 전이와
난수 소비 순서에 영향을 주지 않는다.

단계 4는 소유한 HQ 정수 측정 buffer를 외부 `ScanSample` 배열로 변환하고 관찰 encoder는 snapshot을
직접 순회한다. Frame 변환 할당과 구독 delivery의 `ScanFrame` 복사는 benchmark에서 별도 copy
budget으로 측정한다. 각 sensor는 최신 frame 2개, 관찰 publisher는 최신 snapshot 1개만 유지한다.
늦게 연결한 구독자 때문에 집계되는 server `frame_loss`와 연결 중 sequence gap을 구분한다. 성능
합격의 유실 판정은 연결 중 생성기 원인 sequence gap을 사용한다. 구독자와 관찰 수신기 연결 실패는
worker와 시뮬레이션 coordinator를 중단시키지 않는다.

Wire monotonic 시각은 Linux `CLOCK_MONOTONIC`의 host epoch를 사용한다. Rust process 시작 이후
경과 시각을 대신 기록하지 않는다. 단계 4는 scan server와 관찰 publisher뿐 아니라 상태 writer,
진단 JSON Lines version 2 writer와 합성 처리 설정 exporter CLI까지 이식한다. 관찰은 공개 실행에서
항상 활성화하고, 추가 CPU 측정은 production 설정을 바꾸지 않는 benchmark용 no-op publisher와
비교한다.

단계 4의 외부 계약 수락 명령과 필요한 고정 checkout은
[`../edge-platform-integration/`](../edge-platform-integration/)이 정본이다.

기본 image는 정적으로 link한 Rust 실행 파일과 라이선스 고지만 포함한다. Python 구현과 uv 환경은
기준 fixture 재생성, 외부 계약 검사와 문서 생성에만 사용한다.

## 동등성 판정

Python 기준선과 Rust 결과의 비교 규칙은 5개다.

| 경계 | 판정 |
| --- | --- |
| 설정 | 정상 입력의 최종 값, 수락 및 거부, 오류 분류, field path와 CLI exit code 일치 |
| 결정론적 수치 | 좌표, 거리와 높이는 absolute tolerance 1e-10 m, 부피는 `max(1, capacity) * 1e-10` 이내 |
| SDK 및 wire | HQ 각도와 거리, millidegree, millimeter, quality, decoded Proto field와 sample 순서 정확 일치 |
| 실행 식별자와 시각 | 고정 clock과 UUID를 주입한 fixture에서 정확 일치, live 실행에서는 의미와 범위 일치 |
| 확률 모델 | Rust model version 안에서 정확 재현, Python과는 사건 경계 및 분포 허용범위 비교 |

일반 float 비교는 `abs(actual - expected) <= max(1e-10, 1e-12 * max(abs(actual),
abs(expected)))`를 사용한다. 부피는 표에 적은 capacity 기반 absolute tolerance를 사용한다.
CPython JSON parser의 문법 오류 문장 전체와 Python 및 NumPy PRNG byte sequence는 호환 계약에
포함하지 않는다. 큰 음수 noise가 생긴 유효 거리는 거리 0과 무효 quality로 바꾸며 process를
중단하지 않는다. 이 동작은 기존 Python 결함을 기준선 고정 전에 바로잡는다.

Python fixture는 설정, 좌표, 높이장 연산, scripted 시나리오, Python seeded 시나리오, 회전과 HQ
변환, distortion event와 ScanFrame의 8개 범주로 고정한다. Fixture metadata는 입력 3개와 생성
도구의 SHA-256, 비교 규칙과 fixture schema version을 포함하고 wall clock, host path와 생성 시각은
포함하지 않는다. 저장된 Protobuf hex와 파일 hash는 fixture 무결성만 검증한다. Protobuf wire
serialization은 canonical 형식이 아니므로 Rust 동등성은 decode한 field 값과 sample 순서로 판정한다.

## 합격 기준

전환 합격 기준은 2개 계층이다.

### 기능 합격

- 공개 JSON과 환경변수 우선순위 및 검증 결과 일치.
- 중복 key, 비정상 숫자, field path와 CLI exit code의 오류 분류 일치.
- 두 sensor 각각의 10 Hz scan과 초당 32,000개 명목 sample 생성.
- 외부 Proto field, SDK 정수 변환, 첫 scan 생략과 완료 시각 의미 일치.
- sensor별 sequence, instance, latest-two와 구독 오류 동작 일치.
- 상태 파일의 `STARTING`, `HEALTHY`, service 경로와 원자 교체 동작 일치.
- observation header, snapshot, 1초 주기와 latest-one 장애 격리 동작 일치.
- `lidar-processing` 실제 loader, `ProcessingEngine`과 두 sensor `GOOD` 결과.
- 기준 scan의 허공, 정적 구조와 적재면 교차 및 시간 변화 확인.
- 처리 높이 범위, sensor별 및 융합 적재율 관계와 phase별 방향 확인.
- 같은 model version, 설정과 seed의 반복 실행 결과 일치.
- 큰 음수 거리 noise 결과의 거리 0 무효화와 process 지속.

### Release 전 단기 합격

Raspberry Pi 5 8GB에서 생성기, `lidar-processing`과 상태 수집 검증 process를 함께 실행한다.
Docker CPU 100 percent는 논리 core 하나로 해석한다.

| 항목 | 1차 합격선 |
| --- | --- |
| 지속 시간 | 준비 2초 이상, 측정 30초 이상 |
| 생성기 CPU | 측정 구간 P95 75 percent 이하 |
| 생성기 RSS | P95 128 MiB 이하 |
| 두 sensor frame 생성 지연 | P99 70 ms 이하 |
| 생성기 원인 sequence gap | 0 |
| 처리 상태 | 두 sensor와 융합 결과 `GOOD` 유지 |
| 데이터 의미 | 생성 scan과 처리 높이 및 적재율 검증 통과 |
| 장비 상태 | OOM, container restart와 thermal throttling 0회 |

합격선은 생성기가 공유 장비의 core 하나를 계속 독점하지 않고 100 ms 주기에 계산 여유를
남기는지 Release 전에 빠르게 판정한다. 측정 도구 자체의 부하는 별도 process로 기록하고 원시 로그,
사설 주소와 실제 sensor 자료는 Git에 추가하지 않는다.

### Release 후 장기 검증

기본 86,400초 적재 주기와 600초 가속 주기에서 실제 관찰 publisher와 no-op publisher를 각각
실행한다. 각 case는 5분 준비와 60분 측정으로 구성하며 전체 matrix는 4시간 20분이다. CPU, RSS,
frame 완료 지연, sequence gap, 처리 결과, 관찰 추가 부하와 장비 상태에 단기 합격선의 자원 및
상태 기준을 적용한다. 관찰 활성화 추가 CPU는 같은 적재 주기의 no-op 실행보다 5 percentage point
이하여야 한다. 이 matrix는 Release 이후 지속 안정성과 반복 cycle을 검증하는 후속 작업이다.

## 설정 version

Rust 기본 구현은 `generator.v2.json`, `environment.v1.json`과 `quality-profile.v1.json`을 읽는다.
난수 stream과 수치 모델 재현 경계는 Rust의 simulation model version 1이고 합성 안식각 35도와
경사 이완 반복 상한은 engine 정책이다. 이 값은 배포 override나 환경 형상 입력이 아니므로 기존
version 2 설정 계약에 중복하지 않는다.

## 프로젝트 식별자

프로젝트 이름은 `scrap-monitoring-lidar-simulator`다. Python distribution과 import package,
Rust crate와 binary, CLI(Command-Line Interface), OCI image title은 이 이름을 사용한다. 이
프로그램은 시간에 따라 변하는 적재 환경과 LiDAR 측정을 함께 모사하며 실제 S2E network 장비
자체를 구현하지 않는다.

GitHub Repository와 GHCR(GitHub Container Registry) package도
`scrap-monitoring-lidar-simulator` 이름을 사용한다. 이미 공개된 schema ID와 외부 version 1 하위
호환 식별자는 그대로 보존한다.

## 환경 규격 문서 인계

`docs/synthetic-environment-specification/`은 공개 JSON 정본에서 만든 자기완결 Markdown,
DOCX, PDF, PNG와 source fingerprint를 한 경계에 보관한다. 생성기 runtime과 ARM64 image는 이
디렉토리에 의존하지 않는다. 별도 문서 Repository로 이관할 때 생성 산출물과 provenance를 함께
옮기고 이 Repository의 중복 사본은 제거한 뒤 외부 정본 링크만 남긴다. 이관 전까지 수치가
충돌하면 `examples/`의 JSON을 우선한다.

## 변경 원칙

외부 Proto 또는 처리 설정이 바뀌면 고정 source commit, 로컬 Proto, 생성 binding, exporter,
인계 묶음과 직접 호환 검증을 하나의 변경에서 갱신한다. 합성 환경이 바뀌면 공개 JSON 정본과
파생 규격서를 함께 갱신한다. `docs/project-spec.md`와 기존 `docs/internal/**`은 사용자의 명시적
요청 없이 수정하지 않는다.

각 변경은 Organization 개발 운영 규칙의 Issue, branch, Pull Request와 Release 절차를 따른다.
Issue와 Pull Request 제목 summary는 명사구로 끝낸다.
