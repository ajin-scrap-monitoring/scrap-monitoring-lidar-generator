# 개발 계획

## 진행 기준

개발은 5단계로 진행한다. 각 단계는 앞 단계의 계약과 검증 결과를 입력으로 사용하며, 완료 조건을 충족한 뒤 다음 단계로 넘어간다. 각 변경은 Organization [개발 운영 규칙](https://github.com/ajin-scrap-monitoring/.github/blob/main/GOVERNANCE.md)의 Issue, Project, 브랜치와 Pull Request 절차로 관리한다.

## 현재 상태

5개 단계의 현재 상태는 다음과 같다.

| 단계 | 상태 | 남은 조건 |
| --- | --- | --- |
| 0단계 | 완료 | 없음 |
| 1단계 | 완료 | 없음 |
| 2단계 | 완료 | 없음 |
| 3단계 | 생성 프로그램과 test double 검증 완료 | 실제 수신 프로그램과의 계약 검증 |
| 4단계 | ARM64 OCI image, v0.1.0 Release와 Raspberry Pi 5 단일 컨테이너 loopback 성능 검증 완료 | 실제 수신 프로그램 통합, 다른 edge process와의 공유 부하 검증 및 허용 기준 |
| 관찰 stream | 상시 TCP publisher와 외부 시각화 경계 구현 완료 | 실제 장비와 별도 시각화 장비의 연속 연결 검증 |

## 다음 작업

남은 작업은 3개다.

1. 실제 수신 프로그램에 version 1 계약과 합성 fixture를 적용하고 생성 프로그램과 통합 검증한다.
2. Raspberry Pi 5 8GB에서 실제 실행 설정으로 생성 프로그램 단독 및 다른 edge process와의 동시 부하를 각각 측정하고 허용 CPU, 메모리, 지연 및 지속 실행 기준을 확정한다.
3. Raspberry Pi 5 생성기의 관찰 stream을 별도 시각화 장비에서 수신하고 재연결, 최신 상태 표시와 장시간 부하를 검증한다.

관찰 stream은 다음 기준이다.

| 항목 | 현재 결정 |
| --- | --- |
| 관찰 출력 형식 | 연결별 정적 header와 기본 1초 주기 동적 observation의 JSON Lines 계약 |
| 엣지 출력 경로 | 항상 실행하는 비동기 TCP publisher |
| snapshot 주기와 보관량 | 기본 1초, 최신 대기 상태 1개 |
| 장애 처리 | ACK 없는 best-effort 전송, bounded 재연결과 이전 상태 폐기 |
| 별도 장비 기능 | 별도 시각화 Repository의 실시간 3D 표시, bounded 기록과 MP4 생성 |
| 엣지 의존성 | JSON producer만 포함하며 렌더러와 FFmpeg 제외 |

구현 상세와 실행 명령은 [`docs/observation.md`](observation.md)가 정본이다. 관찰 stream은 기존 scan 전송 계약을 변경하지 않는다.

## 착수 전 확정 항목

다음 5개 항목은 관련 구현을 시작하기 전에 확정한다.

| 항목 | 필요한 결정 | 차단 단계 |
| --- | --- | --- |
| 공통 환경 설정 | schema 소유 위치, 호환성 규칙과 식별자 변경 기준 | 1단계 |
| 스캔 계약 | `scan_id`, `captured_at`와 측정점의 정확한 자료형 및 허용 범위 | 1단계 |
| 생성 실행 설정 | 시나리오, 왜곡, 품질 및 진단 설정의 JSON(JavaScript Object Notation) schema | 2단계 |
| 전송 protocol 및 설정 | message framing, ACK와 오류 응답, message 크기 상한, 연결 및 복구 설정 schema | 3단계 |
| 성능 기준 | 대상 장비의 허용 CPU, 메모리, 지연과 지속 실행 시간 | 4단계 |

확정한 외부 계약은 수신 프로그램과 공유하는 versioned schema 및 합성 fixture로 관리한다. 현장 설정과 실측 자료는 계약 fixture에 포함하지 않는다.

## 0단계: Repository 운영 기반

산출물은 Python 3.14 및 uv 기반 프로젝트 metadata, 잠금 파일, 직접 의존성 및 라이선스 문서, 개발 명령, `CI` workflow와 공개 합성 입력 골격이다. CI(Continuous Integration)는 build, format 검사, lint, type 검사와 빠른 자동 테스트를 하나의 필수 `CI` job으로 집계한다.

완료 조건은 Organization Repository 설정, CodeQL default setup과 4개 ruleset의 활성 상태, 최신 `main` 기준 작업 브랜치, 재현 가능한 로컬 및 CI 검증이다.

## 1단계: 계약과 기준 기하

산출물은 공통 환경 설정 및 스캔 schema, 엄격한 설정 loader, 좌표와 다각형 model, 고정된 바닥 및 표면의 광선 교차, 왜곡 없는 기준 스캔이다. 계산 kernel은 단순한 기준 구현을 먼저 만들고 합성 형상의 기대값으로 검증한다.

완료 조건은 유효 및 무효 설정 검증, 방향벡터와 경계 조건 검증, 최초 교차와 열린 방향의 무효 거리 검증, 동일 입력의 기준 스캔 재현이다.

## 2단계: 시나리오와 측정

산출물은 생성 실행 설정 및 품질 분포 schema와 엄격한 loader, 적재 및 수거 상태 전이, 점진적인 표면 갱신, 센서별 독립 회전, 거리 오차, 무효 측정, 원인별 가림 및 품질 생성이다. 기준 표면과 기준 거리는 왜곡된 측정 결과와 분리한다.

완료 조건은 부피 및 표면 경계 보존, 상태 전환, 센서별 회전 경계, 측정점 순서, 원인별 왜곡, 같은 설정과 seed의 전체 스캔 열 재현이다.

## 3단계: 전송과 복구

산출물은 수신 프로그램과 확정한 전송 설정, MessagePack codec, 송수신 상태 machine, 제한된 미확인 buffer, timeout, 지수 증가 대기와 무작위 지연, ACK 및 오류 처리다. 수신 프로그램의 test double과 실제 수신 프로그램을 모두 계약 검증 대상으로 사용한다.

완료 조건은 message 분할 및 연속 수신, 연결 중단과 재접속, 선택적 ACK, 재전송, buffer 만료 및 용량 초과, 중복 처리와 설정 불일치 검증이다.

## 4단계: 배포와 성능

산출물은 잠금된 의존성을 사용하는 OCI(Open Container Initiative) 이미지, Linux ARM64(64-bit Arm architecture) build, 실행 및 관측 문서, 지속 부하 결과와 Release workflow다. 기능 검증과 함께 생성, 장면 갱신, 직렬화 및 전송 대기 구간을 각각 측정한다.

완료 조건은 대상 엣지 환경의 지속 생성, 수신 프로그램과의 동시 실행, CPU(Central Processing Unit), 메모리 및 지연 기준 충족, 필요한 외부 라이선스 고지, 원격 `main` 이력의 검증된 commit을 대상으로 한 release다.

## 변경 단위

각 단계는 계약, 계산 kernel, 상태 전이, adapter와 검증을 독립적으로 검토할 수 있는 Issue와 Pull Request로 나눈다. Pull Request는 관련 자동 검증과 정본 문서 변경을 함께 포함한다. 성능 최적화는 기준 구현의 정확성 검증과 profile 결과가 확보된 뒤 별도 변경으로 수행한다.
