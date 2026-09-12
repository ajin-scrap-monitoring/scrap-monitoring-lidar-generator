# 높이 계산 프로세스 연동 계약

## 적용 범위

한 회전 scan 전달 경로는 다음 3개 구성 요소로 이루어진다.

1. TCP(Transmission Control Protocol) client인 LiDAR(Light Detection and Ranging) 생성기
2. 길이 접두부와 MessagePack 본문으로 구성된 version 1 scan 계약
3. TCP server인 높이 계산 프로세스

이 문서는 높이 계산 프로세스가 생성기의 한 회전 scan을 수신하고 처리 완료를 응답하는 경계를 정의한다. 높이 계산 알고리즘, SDK(Software Development Kit) 변환기와 시각화 구현은 이 Repository의 범위가 아니다.

이 생성기는 version 1 scan의 기준 producer다. 실제 LiDAR를 읽는 SDK 변환기도 같은 version 1 scan 계약을 producer 경계로 사용할 수 있으며, 높이 계산 프로세스는 producer 구현과 무관하게 같은 consumer 계약을 적용한다.

wire 형식, field, 자료형, 허용 범위와 센서 좌표 변환의 정본은 [`contracts/v1/`](../contracts/v1/)이다. 높이 계산 프로세스는 `environment.schema.json`, `scan.schema.json`, `ack.schema.json`, `error.schema.json`과 `fixtures/`를 같은 version 단위로 사용한다. 이 문서는 해당 계약을 복제하지 않고 소비자의 구현 책임과 통합 완료 조건만 정의한다.

## 연결 역할과 설정

생성기는 `generator.v1.json`의 `transport.host`와 `transport.port`가 가리키는 높이 계산 프로세스에 설정된 센서마다 outbound 연결을 하나씩 만든다. 높이 계산 프로세스는 해당 endpoint에서 센서 수만큼의 동시 연결을 수락한다. 한 연결에는 해당 센서의 scan만 전송된다. 배포 담당자는 다음 설정을 두 프로세스에 일치시킨다.

| 설정 | 생성기 입력 | 높이 계산 프로세스 입력 |
| --- | --- | --- |
| endpoint | `transport.host`, `transport.port` | listen host와 port |
| 본문 상한 | `transport.max_message_body_bytes` | 같은 MessagePack 본문 상한 |
| 환경 | 환경 설정의 `environment_id` | 허용할 `environment_id` |
| 멱등성 보존 | `transport.buffer_max_age_s` | 이 값 이상의 완료 key 보존 시간 |

실제 host, port, 사설 주소와 자격 증명은 배포 설정으로 제공하며 Repository에 기록하지 않는다. version 1 TCP 계약은 인증과 암호화를 제공하지 않으므로 신뢰할 수 있는 배포 network 안에서 사용한다.

scan 본문은 적재 공간 형상과 sensor 설치값을 반복해서 포함하지 않는다. 배포 담당자는 `environment.schema.json`을 따르는 같은 환경 설정을 생성기와 높이 계산 프로세스에 제공한다. 높이 계산 프로세스는 scan의 `environment_id`로 환경 설정을 선택하고 `sensor_id`로 `p0_m`, `u0`, `u90`을 선택한다. 서로 다른 환경 내용을 같은 `environment_id`로 운영하지 않는다.

## frame 수신

높이 계산 프로세스는 TCP packet 경계가 아니라 4 byte unsigned big-endian 길이 접두부를 기준으로 frame을 복원한다. 접두부와 본문은 여러 packet으로 나뉠 수 있고 여러 frame이 한 packet에 결합될 수 있다. 연결별 decoder는 불완전한 frame만 보관한다.

높이 계산 프로세스는 다음 순서로 한 frame을 처리한다.

1. 본문 길이 검증
2. MessagePack 객체 decode
3. version 1 field와 자료형 검증
4. `environment_id`와 `sensor_id` 검증
5. 멱등성 key 예약 또는 기존 처리 조회
6. 높이 계산과 결과 보존
7. 멱등성 완료 기록과 ACK 송신

길이가 0이거나 설정한 상한을 초과하면 높이 계산 프로세스는 현재 연결을 닫고 부분 상태를 폐기한다. 식별자를 안전하게 추출할 수 없는 잘못된 MessagePack 본문도 응답 없이 연결을 닫는다. 식별자를 포함한 유효한 frame 구조에서 scan 내용이 계약을 위반하면 `invalid_scan`을 응답한다.

## 처리 완료와 ACK

높이 계산 프로세스는 bytes 수신이나 MessagePack decode만으로 ACK를 보내지 않는다. 높이 계산 결과와 멱등성 완료 기록을 보존한 뒤 해당 scan의 `run_id`, `sensor_id`, `scan_id`를 포함한 ACK를 보낸다. 결과 보존과 멱등성 완료 기록은 하나의 실패 경계에서 완료되어 수신 프로세스 재시작 뒤에도 완료 여부를 판별할 수 있어야 한다.

생성기의 각 센서 lane은 한 번에 가장 오래된 미응답 scan 하나를 보내고 해당 ACK를 기다린다. ACK 제한 시간, 연결 중단 또는 `temporary_unavailable` 응답이 발생하면 해당 센서의 연결을 다시 만들고 보관 중인 같은 frame을 재전송한다. 다른 센서 lane은 이 ACK 대기와 재연결을 기다리지 않는다. TCP 연결은 `run_id`의 경계가 아니며 재연결된 scan의 식별자는 바뀌지 않는다.

## 처리 용량과 지연

현재 실행 설정은 모든 센서에 같은 측정 빈도와 회전 빈도를 적용한다. 센서 수를 `N`으로 둘 때 높이 계산 프로세스가 지속적으로 처리해야 하는 입력량은 다음과 같다.

```text
aggregate_scans_per_second = N * rotation_rate_hz
aggregate_points_per_second = N * sample_rate_hz
per_lane_ack_cycle_seconds < 1 / rotation_rate_hz
```

공개 합성 프로파일의 센서 1개는 초당 10 scan과 32,000 point를 만든다. 같은 프로파일의 센서가 2개면 합산 입력은 초당 20 scan과 64,000 point다. 각 lane의 수신, 높이 계산, 결과 보존과 ACK 완료 시간은 장기적으로 한 회전 주기보다 짧아야 하며, 높이 계산 프로세스의 전체 처리 용량도 합산 입력량 이상이어야 한다.

생성기의 bounded buffer는 일시적인 지연과 재연결만 흡수한다. 높이 계산 프로세스의 지속 처리량이 합산 입력량보다 작으면 오래된 scan이 보존 시간 또는 센서별 byte 할당량에 따라 폐기된다. 실제 수신 프로그램과 다른 process를 함께 실행한 부하 검증 전에는 Repository가 허용 지연이나 자원 상한을 확정하지 않는다.

## 멱등성과 순서

높이 계산 프로세스는 `(run_id, sensor_id, scan_id)` 조합을 멱등성 key로 사용한다. 처리 상태는 다음 3개다.

| 상태 | 수신 처리 |
| --- | --- |
| 미수신 | key 예약 후 높이 계산 1회 실행 |
| 처리 중 | 중복 계산 금지, 기존 완료 대기 또는 `temporary_unavailable` 응답 |
| 완료 | 결과 재계산과 중복 보존 없이 같은 ACK 응답 |

같은 key에 다른 본문이 도착하면 높이 계산 프로세스는 저장된 결과를 변경하지 않고 `invalid_scan`을 응답한다. 본문의 byte 비교 또는 안정적인 digest 보존 중 하나를 사용할 수 있다. 완료 key와 본문 판별 정보는 생성기의 `buffer_max_age_s` 이상 보존한다.

`scan_id`는 같은 `run_id`와 `sensor_id` 안에서만 증가한다. 여러 sensor의 순서는 서로 독립적이며 전체 sensor를 아우르는 전역 순서를 만들지 않는다. 생성기 buffer가 frame을 폐기하면 번호가 누락될 수 있으므로 높이 계산 프로세스는 누락을 관측 대상으로 기록할 수 있지만 해당 번호를 기다리며 다음 scan 처리를 중단하지 않는다. 새로운 `run_id`의 `scan_id`는 1부터 다시 시작한다.

## 오류 응답

오류 code와 생성기의 처리는 [`contracts/v1/README.md`](../contracts/v1/README.md)의 수신 응답 절을 따른다. 높이 계산 프로세스는 다음 기준으로 code를 선택한다.

| 조건 | 응답 |
| --- | --- |
| 일시적인 처리 용량 부족 또는 같은 frame의 처리 중 중복 | `temporary_unavailable` |
| 식별 가능한 scan 내용 오류 또는 같은 key의 다른 본문 | `invalid_scan` |
| 허용 환경과 다른 `environment_id` | `environment_mismatch` |
| 지원하지 않는 `protocol_version` | `unsupported_version` |
| 식별 불가능한 MessagePack 또는 잘못된 frame 길이 | 응답 없이 연결 종료 |

`invalid_scan`은 대상 scan의 식별자 3개를 모두 포함한다. `temporary_unavailable`은 결과가 보존되지 않았거나 처리 완료를 확정할 수 없을 때만 사용한다. 높이 계산 프로세스는 ACK를 보낸 결과를 이후 오류로 되돌리지 않는다.

## 소비자 구현 인계물

높이 계산 프로세스 Repository는 다음 항목을 현재 계약 revision에 고정한다.

- `contracts/v1/scan.schema.json`, `ack.schema.json`, `error.schema.json`
- `contracts/v1/environment.schema.json`
- `contracts/v1/fixtures/environment.v1.json`
- `contracts/v1/fixtures/scan.v1.json`과 `scan.v1.msgpack.hex`
- `contracts/v1/fixtures/ack.v1.json`과 `ack.v1.msgpack.hex`
- `contracts/v1/fixtures/error.v1.json`과 `error.v1.msgpack.hex`
- 이 Repository의 source commit 또는 Release version

계약 파일을 복사해 관리하면 원본 source revision을 함께 기록한다. field 의미, 자료형 또는 wire 표현을 바꾸려면 생성기와 높이 계산 프로세스가 함께 지원하는 새로운 계약 version을 추가한다.

## 통합 검증

높이 계산 프로세스의 계약 검증은 다음 10개 항목을 포함한다.

1. JSON fixture와 MessagePack fixture의 동일 문서 decode
2. 접두부와 본문이 분할된 frame 복원
3. 결합된 여러 frame의 순차 복원
4. 정상 scan의 높이 계산 1회, 결과 보존과 일치하는 ACK
5. ACK 유실 뒤 동일 frame 재수신의 계산 및 보존 1회와 ACK 재응답
6. 처리 중 동일 key 재수신의 동시 계산 방지
7. 같은 key와 다른 본문의 `invalid_scan` 응답
8. sensor별 시퀀스 누락 뒤 다음 scan 처리
9. 환경 및 version 불일치 오류 응답
10. 잘못된 길이와 식별 불가능한 본문 수신 뒤 연결 상태 폐기

실제 통합 실행은 생성기 집계의 생성 scan 수, ACK 수, 미응답 수, 폐기 수와 높이 계산 프로세스의 센서별 수신 수, 고유 처리 수, 중복 수, 오류 수를 함께 확인한다. 한 센서의 ACK를 의도적으로 지연한 동안 다른 센서가 계속 전달되는지도 검증한다. 공개 예시의 회전당 측정점 수는 현재 실행 profile의 결과이며 높이 계산 프로세스가 고정 배열 길이로 가정하는 계약이 아니다.

통합 완료 조건은 정상 구간의 고유 scan마다 높이 계산 결과가 한 번만 보존되고 ACK가 확인되며, 의도적으로 만든 ACK 유실, 재연결, 시퀀스 누락과 오류 조건에서 두 프로세스가 이 문서의 상태 전이를 유지하는 것이다.
