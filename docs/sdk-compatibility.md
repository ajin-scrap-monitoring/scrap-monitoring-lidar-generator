# RPLIDAR SDK 출력 정합성

## 적용 범위

이 문서는 RPLIDAR S2E와 Slamtec RPLIDAR SDK 2.1.0이 제공하는 한 회전 scan을 version 1 입력 계약으로 변환하는 기준의 정본이다. 생성기는 이 변환 이후의 합성 데이터를 만들며 제조사 UDP packet과 SDK 내부 수신 동작을 재현하지 않는다.

정합성 기준은 다음 5개 요소로 구성된다.

1. 장비 기본 mode와 측정 빈도
2. 한 회전 scan 경계와 측정 순서
3. HQ 노드의 각도, 거리와 quality 표현
4. 첫 측정점 시각의 UTC 변환
5. 생성기에서 재현하지 않는 수신 계층 동작

## 기준 SDK와 장비 mode

기준 SDK는 [Slamtec RPLIDAR SDK 2.1.0 source commit](https://github.com/Slamtec/rplidar_sdk/tree/99478e5fb90de3b4a6db0080acacd373f8b36869)이다. S2E adapter는 UDP channel로 연결한 뒤 `getTypicalScanMode`가 반환한 mode를 `startScan(false, true, 0, &used_mode)`로 시작한다. adapter는 scan mode와 motor speed를 별도로 덮어쓰지 않는다.

공개 실행의 초당 32,000 sample, 초당 10회전과 0.05-30m 거리 범위는 [`examples/generator.v1.json`](../examples/generator.v1.json)에 명시한다. 각 값의 출처와 합성 설정의 분류는 [`configuration.md`](configuration.md)를 따른다. 한 회전당 3,200점은 두 빈도의 명목 비율이며 SDK 반환 배열이나 version 1 계약의 고정 길이가 아니다.

## Scan 경계와 순서

SDK adapter는 `grabScanDataHqWithTimeStamp`를 사용한다. [SDK API 계약](https://github.com/Slamtec/rplidar_sdk/blob/99478e5fb90de3b4a6db0080acacd373f8b36869/sdk/include/sl_lidar_driver.h)은 반환 배열의 첫 노드가 scan 시작이고 timestamp가 첫 측정점에 해당한다고 정의한다. 같은 API 계약은 반환된 각도가 오름차순이 아닐 수 있다고 명시한다.

adapter는 `ascendScanData`로 측정점을 재정렬하지 않는다. 생성기와 adapter는 생성 또는 수집 순서를 version 1 `points` 배열에 유지한다. SDK는 첫 노드의 수치 각도를 고정하지 않으므로 생성기는 seed와 `sensor_id`에서 결정론적으로 시작 각도를 정하고 소비자는 첫 각도를 0도로 가정하지 않는다. 생성기는 센서별 `scan_id`를 1부터 부여하며 SDK의 sync bit와 packet 번호를 외부 계약에 포함하지 않는다.

version 1 소비자는 측정점 배열 길이를 고정하지 않는다. 점 개수는 장비 회전과 SDK 수집 결과 또는 생성 scheduler 결과에 따라 달라질 수 있다.

## HQ 노드 변환

[SDK HQ 노드 구조체](https://github.com/Slamtec/rplidar_sdk/blob/99478e5fb90de3b4a6db0080acacd373f8b36869/sdk/include/sl_lidar_cmd.h)는 `angle_z_q14`, `dist_mm_q2`, `quality`, `flag`를 제공한다. adapter는 다음 변환을 적용한다.

```text
angle_deg = angle_z_q14 * 90 / 16384
distance_m = dist_mm_q2 / 4000
quality = quality
```

각도 표현은 한 회전 65,536단계이며 한 단계는 `360 / 65536`도다. 거리 표현은 1m당 4,000단계이며 한 단계는 0.00025m다. 생성기는 최종 출력 각도와 거리를 각 표현에서 가장 가까운 값으로 양자화한다. 거리 0은 그대로 유지한다.

`quality`는 SDK가 제공한 8-bit 값을 이동하거나 정규화하지 않고 유지한다. 거리 0과 quality 0은 동일한 판정이 아니다. 높이 계산 프로세스는 `distance_m > 0`인 측정만 공간 좌표로 변환하고 quality는 독립된 관측값으로 처리한다.

## Timestamp 변환

SDK 2.1.0의 Linux timestamp는 `CLOCK_MONOTONIC` 시간 영역의 마이크로초 값이다. adapter는 연결 실행 중 UTC clock과 monotonic clock의 offset을 구하고 첫 측정점 timestamp에 더해 version 1 `captured_at`을 만든다.

```text
clock_offset_us = utc_now_us - monotonic_now_us
captured_at = sdk_first_point_monotonic_us + clock_offset_us
```

두 clock을 읽는 사이의 오차를 줄이기 위해 monotonic clock을 UTC clock 전후에 읽고 중간값을 사용한다. system clock이 실행 중 조정될 수 있으므로 adapter는 offset 계산 시점과 정책을 명시하고 `captured_at`이 센서별 측정 순서에서 역행하지 않는지 검증한다.

생성기는 실행 시작 UTC 시각과 결정론적 simulation 경과 시각으로 같은 의미의 `captured_at`을 만든다. 송신 완료 시각이나 ACK 시각을 사용하지 않는다.

## 재현 경계

생성기는 다음 항목을 재현한다.

- 공개 프로파일의 명목 측정 및 회전 빈도
- 센서별 독립된 한 회전 scan과 증가하는 `scan_id`
- 생성 순서의 각도, 거리와 8-bit quality
- HQ 노드와 같은 각도 및 거리 표현 단위
- 첫 측정점 기준 `captured_at`
- 가변 길이 scan을 허용하는 version 1 계약

생성기는 다음 항목을 재현하지 않는다.

- 제조사 UDP packet과 capsule decode
- 장비별 motor 변동과 측정점 개수 분포
- 실제 환경의 무효 거리와 quality 분포
- SDK 호출 지연, network 지연과 수신 병합
- 장비 serial, firmware 상태와 사설 endpoint

재현하지 않는 항목이 필요한 장애 및 복구 검증은 합성 설정이나 수신 test double에서 원인을 명시해 주입한다. 실제 측정값을 공개 기본값이나 fixture로 복제하지 않는다.

## 의존성과 검증

운영 생성기는 SDK에 link하지 않는다. SDK는 adapter 구현과 정합성 연구의 외부 기준이며 [BSD-2-Clause license](https://github.com/Slamtec/rplidar_sdk/blob/99478e5fb90de3b4a6db0080acacd373f8b36869/LICENSE)를 따른다. SDK source, compiler와 build 산출물은 Raspberry Pi용 생성기 운영 image에 포함하지 않는다.

자동 검증은 다음 항목을 확인한다.

- 각도의 HQ Q14 표현 가능성
- 거리의 HQ Q2 표현 가능성
- 거리 0 보존
- 센서별 scan 순서와 가변 점 개수
- 같은 설정 및 seed의 결정론
- 양자화의 NumPy 배열 연산 경계

실제 장비 검증 결과와 원시 측정 자료는 `docs/internal/`의 별도 산출물로 관리한다.
