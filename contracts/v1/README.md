# 생성기 입력 계약 버전 1

## 적용 범위

이 디렉토리는 생성 프로그램 전용 설정 계약과 외부 공유 계약의 호환 진입 경로를 관리한다. 높이 계산 프로세스에 전달하는 환경, scan, ACK(Acknowledgement), 오류 schema와 fixture의 정본은 [`height-calculation-contract-proposal/`](../../height-calculation-contract-proposal/)이다.

| 파일 | 계약 |
| --- | --- |
| `generator.schema.json` | 생성 시나리오, 측정, 전송과 진단 실행 설정 |
| `quality-profile.schema.json` | 센서별 유효 및 무효 거리 품질 빈도 |
| `environment.schema.json` | 공유 계약 정본을 가리키는 호환 경로 |
| `scan.schema.json` | 공유 계약 정본을 가리키는 호환 경로 |
| `ack.schema.json` | 공유 계약 정본을 가리키는 호환 경로 |
| `error.schema.json` | 공유 계약 정본을 가리키는 호환 경로 |
| `fixtures/` | 공유 fixture 정본을 가리키는 호환 경로 |

JSON Schema의 `2020-12`는 schema 문법의 판이며 이 프로젝트의 계약 version과 구분한다. 외부 공유 계약의 field, 자료형, wire 표현과 소비자 책임은 전달 묶음에서만 정의한다.

## 생성 실행 설정

`generator.schema.json`은 생성 프로그램만 사용하는 시나리오, 측정, 전송과 진단 설정이다. 생성 프로그램은 TCP(Transmission Control Protocol) client이고 높이 계산 프로세스는 TCP server다. 생성 프로그램은 설정된 센서마다 같은 수신 endpoint에 독립된 지속 연결을 하나씩 만들고 해당 센서의 여러 scan을 전송한다.

설정의 `environment_path`, `quality_profile_path`와 진단 출력 경로가 상대 경로이면 생성 실행 설정 파일이 있는 디렉토리를 기준으로 해석한다. 모든 조정값은 설정에 명시하며 schema가 암묵적인 기본값을 제공하지 않는다.

`fill_duration_factor_range`는 회차별 적재 목표 시간을 평균 적재 시간에 대한 배수로 정한다. `collection_duration_factor_range`는 회차별 수거 목표 시간을 평균 적재 시간에 대한 배수로 정한다. 적재 및 수거 속도 배수 범위는 각 회차의 평균 속도를 기준으로 하며 이름이 `_s_range`로 끝나는 시간 범위는 시뮬레이션 초 단위다.

JSON Schema 검사와 함께 다음 10개 의미 규칙을 적용한다.

- 두 값으로 구성된 모든 범위의 최솟값 우선 순서
- 평균 1을 중심으로 대칭인 회차별 적재 시간 배수 범위
- 회차 평균 배수 1을 포함하는 적재 및 수거 속도 배수 범위
- 회전당 측정점이 존재하도록 회전 빈도 이상인 측정 빈도
- 최소 거리보다 큰 최대 거리
- 중복되지 않고 환경 경계 안에 있는 투입 위치
- 환경 설정과 정확히 일치하는 품질 분포의 센서 식별자 집합
- 중복되지 않는 품질 분포의 센서 식별자
- 환경 센서 수 이상인 미응답 buffer byte 상한
- 재연결 최대 지연 이하의 재연결 초기 지연

품질 빈도 객체의 key는 `0`부터 `255`까지의 정수 문자열이다. 객체에 없는 품질 값의 빈도는 0이며 유효 거리와 무효 거리 빈도 객체는 각각 하나 이상의 양의 빈도를 포함한다.
