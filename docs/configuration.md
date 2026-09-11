# 설정 출처와 기본 프로파일

## 공개 설정의 정본

공개 합성 실행의 정본은 다음 3개 설정 파일의 조합이다.

| 파일 | 책임 | 분류 |
| --- | --- | --- |
| `examples/environment.v1.json` | 적재 공간 형상, 바닥과 상단 높이, 센서 위치와 방향 | 공개 합성 환경 |
| `examples/generator.v1.json` | 시나리오, 측정, 전송과 진단 실행값 | 공개 합성 실행 프로파일 |
| `examples/quality-profile.v1.json` | 센서별 유효 및 무효 측정 품질 분포 | 공개 합성 품질 fixture |

이 3개 파일의 값이 공개 실행의 단일 입력 정본이다. 공개 실행을 설명하는 문서와 예제 검증은 이 정본을 기준으로 하며, 단위 테스트는 필요한 경우 별도 축소 설정을 사용한다. 생성 실행 schema는 모든 조정값을 요구하며 암묵적인 사용자 설정 기본값을 제공하지 않는다.

공개 환경의 공간 및 센서 규격은 프로젝트용 합성 규격이다. 공개 품질 분포도 실제 센서 관측값이 아닌 결정론적 테스트용 분포다. 실제 센서 측정값, 품질 관측 원본, 운영 로그, 사설 주소와 자격 증명은 이 프로파일의 출처나 입력이 아니다.

## 출처 분류

### 센서 하드웨어 기준

`examples/generator.v1.json`의 다음 측정값은 RPLIDAR S2E의 기본 운용 모델을 반영한다.

| 설정 | 프로파일 값 | 출처와 적용 |
| --- | --- | --- |
| `measurement.sample_rate_hz` | 32,000 | [SLAMTEC S2 specification](https://www.slamtec.com/en/s2/spec)의 S2E sample rate |
| `measurement.rotation_rate_hz` | 10 | [SLAMTEC S2 specification](https://www.slamtec.com/en/s2/spec)의 S2E scan rate와 600 RPM 운용 기준 |
| `measurement.min_distance_m` | 0.05 | S2E 90% 반사율 측정 범위의 하한 및 version 1 입력 계약 |
| `measurement.max_distance_m` | 30 | S2E 90% 반사율 측정 범위의 상한 및 version 1 입력 계약 |

32,000회를 초당 10회전으로 나눈 명목 측정점 수는 회전당 3,200개다. 이 계산값은 생성 프로파일의 정수 비율을 설명하는 값이며 센서 수신 배열 길이나 전송 계약의 고정 제약이 아니다. 회전 scheduler는 회전 경계와 각 측정점의 시각으로 스캔을 나누고, 실제 배열 길이는 입력 배열의 길이로 처리한다.

생성기는 제조사 통신 packet이나 SDK 수신 형식을 재현하지 않는다. 생성기와 실제 수집 경로는 SDK 처리 이후의 각도, 거리, 품질과 시각을 version 1 공통 입력 계약으로 제공한다. `scan_id`는 하드웨어 packet 번호가 아니라 실행 중 센서별로 1부터 증가하는 스캔 sequence다.

### 합성 시나리오 기본값

시나리오 값은 적재와 수거의 시간 흐름 및 표면 변화를 재현하기 위한 프로젝트 합성값이다. 센서의 물리 기본값으로 해석하지 않는다. 기준 평균 적재 시간은 24시간이며 코드의 시나리오 시간 기준과 일치한다.

| 설정 묶음 | 프로파일 값 | 적용 의미 |
| --- | --- | --- |
| `scenario.mean_fill_duration_s` | 86,400 | 24시간 기준 적재 구간 |
| 적재 시간 및 속도 배수 | 0.8~1.2, 0.5~1.5 | 회차와 회차 내부의 합성 변동 |
| 적재 속도 변화 시간 | 300~900초 | 24시간 기준 5~15분 변화 |
| 수거 임계치 | 0.85~0.95 | 적재 부피 비율 기반 수거 시작 |
| 수거 시간 배수 | 0.03333333333333333~0.05 | 24시간 기준 48~72분 수거 |
| 수거 속도 및 변화 시간 | 0.3~1.7, 60~180초 | 수거 속도의 합성 변동 |
| 투입구 전환 | 활성 비율 0.5, 높이 차이 0.5m, 비교 반경 0.5m | 합성 표면 높이 비교 |
| 표면 확산 및 요철 | `examples/generator.v1.json`의 `surface` 객체 | 격자 해상도와 합성 표면 형상 |

`surface.roughness_radius_range_m`은 코드의 반경 단위다. 공개 프로파일은 국소 요철의 수평 크기 0.2~0.6m를 반경 0.1~0.3m로 표현한다. 투입구 좌표와 환경 형상은 별도의 공개 합성 환경 정본을 따른다.

### 합성 측정 오차와 왜곡

거리 noise와 원인별 distortion은 S2E의 출력 기본값이 아니다. 측정 오차와 적재 상황별 관측 이상을 검증하기 위한 합성 모델이다.

| 설정 묶음 | 프로파일 값 | 적용 의미 |
| --- | --- | --- |
| `distance_noise` | 표준편차 0.01m, 제한 0.03m | 평상시 거리 오차의 합성 모델 |
| `falling_material` | 초당 0.5개 후보, 반경 0.025~0.1m, 0.05~0.2초 | 낙하물 가림의 합성 모델 |
| `voids` | 면적 비율 0.03, 반경 0.015~0.06m, 60~300초 | 스크랩 사이 빈틈의 합성 모델 |
| `collection_occlusion` | 간격 20~40초, 반경 0.15~0.5m, 2~8초 | 수거 중 가림의 합성 모델 |
| `reflection_error` | 확률 0.001, 거리 감소 0.5~2m | 반사 경로 오류의 합성 모델 |
| `dropout` | 비활성, 범위는 schema 입력 유지용 | 기본 실행에서 사용하지 않는 장애 모델 |

실제 품질 관측 분포를 공개 품질 fixture에 복제하지 않는다. 합성 품질 fixture의 값은 설정 재현성과 계약 검증을 위한 선택값이다.

### 전송과 진단 정책

`transport`의 endpoint, frame 크기, buffer, timeout과 재접속 값은 센서 사양이 아닌 개발용 TCP 전송 정책이다. 전송 계약과 기본 frame 상한은 [`contracts/v1/README.md`](../contracts/v1/README.md)에서 정의한다. `diagnostics`와 `seed`는 검증 출력의 범위와 결정론을 제어하는 개발 정책이다.

운영 실행은 설정 파일에 명시한 전송 endpoint를 사용한다. 공개 예시의 `receiver` 주소는 합성 실행을 위한 container network 이름이며 실제 운영 주소를 나타내지 않는다.

## 코드 내부 기본값 감사

운영 loader가 읽는 공개 실행 설정은 모든 필드를 명시하므로 다음 코드 상수는 설정 파일을 대신하지 않는다.

| 코드 상수 | 값 | 성격 |
| --- | --- | --- |
| `scenario.time_scale.REFERENCE_MEAN_FILL_DURATION_S` | 86,400초 | 24시간 시나리오 기준 |
| `measurement.reference.DEFAULT_MIN_DISTANCE_M` | 0.05m | 직접 생성 API의 거리 기본값 |
| `measurement.reference.DEFAULT_MAX_DISTANCE_M` | 30m | 직접 생성 API의 거리 기본값 |
| `transport.framing.DEFAULT_MAX_MESSAGE_BODY_BYTES` | 1,048,576 byte | 직접 framing API의 개발용 frame 상한 |
| `geometry.intersections.DEFAULT_MIN_DISTANCE_M` | 1e-9m | 광선 교차 수치 epsilon, 센서 측정 하한 아님 |

전송 계약의 유효 거리 상수 0.05m와 30m는 메시지 검증 범위다. frame prefix가 표현할 수 있는 최대 길이와 각종 입력 검증 상한은 운영 프로파일의 기본값이 아니라 형식 안전성 제한이다.

직접 생성 API의 기본 인자는 테스트와 저수준 기하 계산을 위한 것이다. 실행 경로는 loader가 반환한 명시적 측정 및 전송 설정을 각 구성 요소에 전달하며, 이때 공개 프로파일과 계약의 하드웨어 및 정책 기준을 사용한다.

## 변경 기준

하드웨어 사양 또는 공통 계약이 바뀌면 공개 실행 프로파일, 이 문서의 출처 분류, 관련 테스트와 성능 결과를 같은 변경에서 갱신한다. 합성 시나리오 값을 조정하면 하드웨어 기준과 분리된 합성값임을 유지하고 결정론 및 성능 검증을 다시 실행한다. `docs/project-spec.md`와 기존 `docs/internal/**`는 이 절차의 변경 대상이 아니다.
