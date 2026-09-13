# 높이 계산 프로세스 계약 제안

## 제안 상태

이 디렉토리는 LiDAR(Light Detection and Ranging) 생성기와 높이 계산 Python 프로세스 사이의 version 1 입력 계약을 전달하기 위한 자기완결 묶음이다. 높이 계산 Repository는 이 디렉토리만 받아 환경의 정확한 형상과 좌표 전제, schema, fixture, 전송 동작과 소비자 완료 조건을 검토할 수 있다.

현재 상태는 다음과 같다.

| 항목 | 상태 |
| --- | --- |
| Producer package version | `0.7.0` |
| 계약 version | `1` |
| 생성기 생산 및 송신 | 구현 및 자동 검증 완료 |
| SDK 변환 기준 | RPLIDAR SDK 2.1.0 HQ 노드 기준 확정 |
| Raspberry Pi 5 ARM64 생성기 검증 | LiDAR 2대 합성 입력 및 test double 검증 완료 |
| 높이 계산 소비자 채택 | 미확정 |
| 실제 높이 계산 통합 | 미실행 |
| 동시 실행 성능 기준 | 미확정 |

이 계약은 producer가 제시하는 구현 완료 제안이다. 높이 계산 프로세스가 아래 수락 항목과 통합 검증을 완료해야 양방향 계약이 확정된다.

## 디렉토리 구성

전달 묶음은 README를 포함한 다음 13개 파일로 구성된다.

| 경로 | 역할 |
| --- | --- |
| `README.md` | 전송 계약, 처리 규칙과 소비자 완료 조건 |
| `ENVIRONMENT.md` | 공개 합성 환경의 출처, 정확한 형상과 좌표 전제 |
| `v1/environment.schema.json` | 공통 환경과 센서 설치 설정 |
| `v1/scan.schema.json` | 한 회전 scan 본문 |
| `v1/ack.schema.json` | 처리 완료 응답 |
| `v1/error.schema.json` | 처리 실패 또는 거부 응답 |
| `v1/fixtures/environment.v1.json` | 공개 합성 환경 fixture |
| `v1/fixtures/scan.v1.json` | 사람이 검토하는 scan fixture |
| `v1/fixtures/scan.v1.msgpack.hex` | scan MessagePack 본문 fixture |
| `v1/fixtures/ack.v1.json` | 사람이 검토하는 ACK fixture |
| `v1/fixtures/ack.v1.msgpack.hex` | ACK MessagePack 본문 fixture |
| `v1/fixtures/error.v1.json` | 사람이 검토하는 오류 fixture |
| `v1/fixtures/error.v1.msgpack.hex` | 오류 MessagePack 본문 fixture |

JSON(JavaScript Object Notation) fixture는 사람이 검토하는 문서다. 같은 이름의 `.msgpack.hex` fixture는 JSON과 동일한 MessagePack 본문 byte를 16진수로 표현한다. 길이 접두부는 `.msgpack.hex`에 포함하지 않는다.

## 시스템 경계

한 회전 scan 전달 경로는 다음 3개 구성 요소로 이루어진다.

1. TCP(Transmission Control Protocol) client인 LiDAR 생성기 또는 SDK 변환기
2. 4 byte 길이 접두부와 MessagePack 본문으로 구성된 version 1 계약
3. TCP server인 높이 계산 Python 프로세스

생성기와 실제 LiDAR를 읽는 SDK 변환기는 같은 producer 계약을 사용한다. 높이 계산 프로세스는 producer 종류와 무관하게 동일한 검증, 좌표 변환, 멱등성과 응답 규칙을 적용한다. 높이 계산 알고리즘과 계산 결과의 외부 제공 방식은 이 계약의 범위가 아니다.

엣지 배포에서는 생성기와 높이 계산 프로세스를 별도 container로 실행하고 같은 사용자 정의 Docker network에 연결한다. 생성기는 설정으로 주입한 높이 계산 container 이름 또는 network alias와 수신 port를 사용한다. 두 값은 protocol에 고정하지 않는다.

## 연결과 설정

생성기는 LiDAR 2대에 대해 outbound TCP 연결을 하나씩 유지한다. 높이 계산 프로세스는 같은 endpoint에서 연결 2개를 동시에 수락한다. 한 연결에는 한 센서의 여러 scan만 순서대로 전송된다.

두 프로세스는 다음 설정을 일치시킨다.

| 항목 | Producer | Consumer |
| --- | --- | --- |
| Endpoint | 실행 시 주입한 host와 port | listen host와 port |
| 본문 상한 | `max_message_body_bytes` | 같은 MessagePack 본문 상한 |
| 환경 식별 | scan의 `environment_id` | 허용할 `environment_id` |
| 센서 식별 | scan의 `sensor_id` | 환경 설정의 `sensor_id` |
| 멱등성 보존 | `buffer_max_age_s` | 이 값 이상의 완료 key 보존 시간 |

실제 host, port, 사설 주소와 자격 증명은 배포 설정으로 제공한다. version 1 TCP 계약은 인증과 암호화를 제공하지 않으므로 신뢰할 수 있는 배포 network 안에서 사용한다.

## 환경 계약

scan 본문은 공간 형상과 센서 설치값을 반복하지 않는다. 두 프로세스는 `environment.schema.json`을 따르는 같은 환경 설정을 읽는다. 이 묶음은 정확한 공개 합성 환경을 `v1/fixtures/environment.v1.json`으로 제공하며, 값의 출처와 형상 해석은 [`ENVIRONMENT.md`](ENVIRONMENT.md)에 정의한다. 높이 계산 프로세스는 `environment_id`로 환경을 선택하고 `sensor_id`로 `p0_m`, `u0`, `u90`을 선택한다. 서로 다른 환경 내용을 같은 `environment_id`로 운영하지 않는다.

Schema는 센서 2대를 요구한다. Schema 검사와 함께 다음 5개 의미 규칙을 적용한다.

- JSON 객체의 중복 field와 비유한 숫자 거부
- 서로 다른 꼭짓점 3개 이상으로 구성된 자기 교차 없는 경계 다각형
- `floor_z_m`보다 큰 `top_z_m`
- 중복되지 않는 `sensor_id`
- 길이 오차 `1e-6` 이하의 단위벡터 `u0` 및 `u90`과 내적 절대값 `1e-6` 이하의 직교 관계

## Scan 본문

한 회전 scan의 MessagePack 객체는 다음 형태다.

```text
{
  "protocol_version": 1,
  "type": "scan",
  "environment_id": "...",
  "run_id": "...",
  "sensor_id": "...",
  "scan_id": 1,
  "captured_at": 1800000000000000,
  "points": [
    [359.9945068359375, 2.5, 64],
    [0.0, 0.0, 255]
  ]
}
```

`run_id`, `sensor_id`, `scan_id` 조합은 한 scan의 멱등성 key다. `scan_id`는 같은 실행과 센서 안에서 1부터 증가하는 64-bit 양의 정수다. 재전송과 재연결은 같은 key를 유지하며 새로운 실행은 새로운 `run_id`와 센서별 `scan_id` 1로 시작한다.

`captured_at`은 첫 측정점에 대응하는 UTC(Coordinated Universal Time) Unix 시각의 마이크로초 단위 64-bit 정수다. 송신 완료 시각이나 ACK 시각이 아니다.

각 측정점은 `[angle_deg, distance_m, quality]` 순서다. 각도와 거리는 MessagePack 64-bit 부동소수점이고 quality는 0부터 255 범위의 정수다. 측정점은 생성 또는 SDK 수집 순서를 유지하며 consumer는 오름차순 각도와 고정 배열 길이를 가정하지 않는다.

| 값 | 허용 범위와 의미 |
| --- | --- |
| `angle_deg` | `0 <= angle_deg < 360`의 유한한 각도 |
| `distance_m` | 무효 측정의 0 또는 `0.05 <= distance_m <= 30`의 유한한 거리 |
| `quality` | 거리 유효성과 독립된 0부터 255 범위의 정수 |

RPLIDAR SDK 2.1.0 HQ 노드를 사용하는 producer는 다음 변환을 적용한다.

```text
angle_deg = angle_z_q14 * 90 / 16384
distance_m = dist_mm_q2 / 4000
quality = quality
```

거리 0과 quality 0은 같은 판정이 아니다. 높이 계산 프로세스는 `distance_m > 0`인 측정만 공간 좌표로 변환한다.

## 좌표 변환

높이 계산 프로세스는 환경 설정의 센서 원점 `p0_m`과 방향벡터 `u0`, `u90`을 사용한다.

```text
angle_rad = angle_deg * pi / 180
direction = cos(angle_rad) * u0 + sin(angle_rad) * u90
point = p0_m + distance_m * direction
```

`distance_m = 0`인 측정은 높이 0이나 센서 원점의 공간점으로 변환하지 않고 무효 측정으로 제외한다.

## Frame 수신

각 TCP frame은 4 byte unsigned big-endian 본문 길이와 해당 길이의 MessagePack 본문으로 구성한다. 길이는 접두부를 제외한 본문 byte 수다. TCP packet 경계는 frame 경계가 아니다. 접두부와 본문은 여러 packet으로 나뉠 수 있고 여러 frame이 한 packet에 결합될 수 있다.

높이 계산 프로세스는 다음 순서로 한 frame을 처리한다.

1. 본문 길이 검증
2. MessagePack 객체 decode
3. version 1 field와 자료형 검증
4. `environment_id`와 `sensor_id` 검증
5. 멱등성 key 예약 또는 기존 처리 조회
6. 높이 계산과 결과 보존
7. 멱등성 완료 기록과 ACK 송신

길이가 0이거나 합의한 상한을 초과하면 현재 연결을 닫고 부분 상태를 폐기한다. 식별자를 안전하게 추출할 수 없는 MessagePack도 응답 없이 연결을 닫는다. 식별 가능한 scan이 계약을 위반하면 `invalid_scan`을 응답한다.

## ACK와 재전송

높이 계산 프로세스는 bytes 수신이나 decode 완료만으로 ACK(Acknowledgement)를 보내지 않는다. 높이 계산 결과와 멱등성 완료 기록을 보존한 뒤 `run_id`, `sensor_id`, `scan_id`가 일치하는 ACK를 보낸다.

전달 방식은 bounded at-least-once다. ACK 제한 시간, 연결 중단 또는 `temporary_unavailable` 응답이 발생하면 producer는 보관 한도 안에서 같은 frame을 재전송한다. consumer는 완료된 같은 key를 다시 받으면 계산과 보존을 반복하지 않고 같은 ACK를 보낸다.

같은 key에 다른 본문이 도착하면 기존 결과를 변경하지 않고 `invalid_scan`을 응답한다. 같은 key의 처리가 진행 중이면 중복 계산을 실행하지 않고 기존 완료를 기다리거나 `temporary_unavailable`을 응답한다. consumer는 완료 key와 본문 판별 정보를 producer의 `buffer_max_age_s` 이상 보존한다.

여러 센서의 순서는 서로 독립적이다. scan 번호가 누락돼도 다음 scan 처리를 중단하지 않는다. 한 센서의 ACK 지연이나 재연결은 다른 센서 연결의 처리를 막지 않는다.

## 오류 응답

오류 code는 다음 4개다.

| Code | 조건 | Producer 처리 |
| --- | --- | --- |
| `temporary_unavailable` | 일시적인 처리 용량 부족 또는 처리 중 중복 | 연결 종료 후 보관 한도 내 재시도 |
| `invalid_scan` | 식별 가능한 scan 오류 또는 같은 key의 다른 본문 | 해당 scan 폐기 후 다음 scan 진행 |
| `environment_mismatch` | 허용 환경과 다른 `environment_id` | 전체 scan 전송 중단과 설정 오류 보고 |
| `unsupported_version` | 지원하지 않는 `protocol_version` | 전체 scan 전송 중단과 호환 오류 보고 |

오류 응답은 선택적인 비어 있지 않은 `message`를 포함할 수 있다. 식별 필드는 세 개를 모두 포함하거나 모두 생략하며 `invalid_scan`은 세 식별 필드를 반드시 포함한다.

## 처리 용량

공개 합성 프로파일의 지속 입력은 LiDAR 2대, 센서별 초당 10 scan과 32,000 point다.

```text
aggregate_scans_per_second = 20
aggregate_points_per_second = 64000
per_lane_ack_cycle_seconds < 0.1
```

각 센서 연결의 수신, 높이 계산, 결과 보존과 ACK 완료 시간은 장기적으로 0.1초보다 짧아야 한다. consumer의 지속 처리량이 입력량보다 작으면 producer의 bounded buffer에서 오래된 scan이 폐기될 수 있다. 허용 CPU, 메모리와 지연 상한은 두 프로세스의 실제 통합 부하 측정으로 확정한다.

## 소비자 수락 항목

높이 계산 Repository는 다음 7개 항목을 명시적으로 수락하거나 변경을 제안한다.

1. LiDAR 2대의 센서별 독립 TCP 연결
2. 4 byte unsigned big-endian 길이 접두부와 MessagePack 본문
3. version 1 scan field, 자료형, 범위와 측정 순서
4. 공통 환경 설정과 좌표 변환
5. 처리 결과 보존 뒤 ACK를 보내는 완료 의미
6. `(run_id, sensor_id, scan_id)` 멱등성 key와 bounded at-least-once 전달
7. 오류 code와 producer의 재시도 또는 중단 처리

field 의미, 자료형 또는 wire 표현을 바꾸려면 양쪽 producer와 consumer가 함께 지원하는 새로운 계약 version을 추가한다.

## 소비자 검증

높이 계산 프로세스의 계약 검증은 다음 10개 항목을 포함한다.

1. JSON fixture와 MessagePack fixture의 동일 문서 decode
2. 접두부와 본문이 분할된 frame 복원
3. 결합된 여러 frame의 순차 복원
4. 정상 scan의 높이 계산 1회, 결과 보존과 일치하는 ACK
5. ACK 유실 뒤 동일 frame 재수신의 계산 및 보존 1회와 ACK 재응답
6. 처리 중 동일 key 재수신의 동시 계산 방지
7. 같은 key와 다른 본문의 `invalid_scan` 응답
8. 센서별 sequence 누락 뒤 다음 scan 처리
9. 환경 및 version 불일치 오류 응답
10. 잘못된 길이와 식별 불가능한 본문 수신 뒤 연결 상태 폐기

실제 통합 실행은 producer의 생성, ACK, 미응답과 폐기 수를 consumer의 센서별 수신, 고유 처리, 중복과 오류 수와 함께 확인한다. 한 센서의 ACK를 의도적으로 지연한 동안 다른 센서가 계속 처리되는지도 검증한다.

통합 완료 조건은 정상 구간의 고유 scan마다 결과가 한 번만 보존되고 ACK가 확인되며, ACK 유실, 재연결, sequence 누락과 오류 조건에서 두 프로세스가 이 문서의 상태 전이를 유지하는 것이다.

## 전달 기준

이 디렉토리 전체를 한 단위로 전달하고 source Repository의 Release version 또는 commit을 함께 기록한다. 전달 뒤 schema나 fixture 일부만 별도로 수정하지 않는다. 변경 제안은 producer Repository의 새 계약 version 또는 현재 version의 호환 가능한 명확화로 반영한다.

GitHub Release는 이 디렉토리만 포함한 `height-calculation-contract-proposal-v1.tar.gz` asset을 제공한다. 다음 명령은 최신 Release에서 전달 묶음을 가져온다.

```bash
RELEASE_TAG="$(gh release view \
  --repo ajin-scrap-monitoring/scrap-monitoring-lidar-generator \
  --json tagName --jq .tagName)"
gh release download "$RELEASE_TAG" \
  --repo ajin-scrap-monitoring/scrap-monitoring-lidar-generator \
  --pattern height-calculation-contract-proposal-v1.tar.gz
tar --extract --gzip --file height-calculation-contract-proposal-v1.tar.gz
```
