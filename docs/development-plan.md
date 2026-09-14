# 개발 계획

## 현재 상태

현재 제품 경계는 5개다.

| 경계 | 상태 | 정본 |
| --- | --- | --- |
| 공개 합성 환경과 적재 시나리오 | 구현 및 자동 검증 완료 | `examples/`, `docs/configuration.md` |
| RPLIDAR S2E 호환 scan 생성 | 구현 및 자동 검증 완료 | `docs/sdk-compatibility.md` |
| `ajin-edge-platform` gRPC over UDS scan 출력 | 구현 및 외부 source 직접 검증 완료 | `contracts/lidar/v1/`, `edge-platform-integration/` |
| 적재 모델 관찰 stream | 생성기 출력 구현 완료 | `docs/observation.md` |
| ARM64 image 배포 | 자동 build 및 image 검증 구성 완료 | `docs/deployment.md` |

현재 source version은 0.9.0이다. 생성기는 하나의 적재 모델에서 정확히 2개 sensor scan을
만들고 `ajin-edge-platform`의 `LidarScanSource.SubscribeScans`와 같은 sensor별 gRPC(Google Remote Procedure
Call) over UDS(Unix Domain Socket) endpoint를 제공한다. `lidar-processing`은 생성기의 환경
JSON을 읽지 않으며 exporter가 같은 공개 합성 환경에서 `lidar-processing` 설정을 만든다.

scan 계약 전환의 완료 조건은 다음과 같다.

- 외부 Proto와 고정 source metadata의 일치.
- HQ node 이후 각도, 거리와 quality 정수 변환의 일치.
- 첫 scan 생략, 완료 시각, scan rate, instance와 sequence 의미의 일치.
- sensor별 latest-two buffer, 구독자 제한과 gRPC 오류 표현의 일치.
- `ajin-edge-platform` 상태 파일 이름과 정상 상태 표현의 일치.
- 실제 `lidar-processing` 설정 loader와 `ProcessingEngine`의 두 sensor frame 수락.
- UDS, 관찰과 상태를 포함한 digest image 검증.

자동 검증은 위 조건을 포함한다. 실제 배포 장비의 장기 동시 부하는 외부 구성 요소가 모두
준비된 뒤 별도 검증한다.

## 다음 작업

다음 작업은 3개다.

| 순서 | 작업 | 선행 조건 | 완료 기준 |
| --- | --- | --- | --- |
| 1 | 합성 edge 통합 검증 | `lidar-processing` image 준비 | 두 UDS 구독, 두 상태 파일과 처리 `GOOD` 상태 |
| 2 | Raspberry Pi 5 장기 공유 부하 검증 | 생성기와 높이 계산 process 동시 실행 | CPU, 메모리, sequence gap과 처리 지연 상한 확정 |
| 3 | 실제 S2E 비교 검증 | sensor 2대와 비공개 관측 저장소 준비 | 실제 SDK frame과 생성 frame의 의미 및 분포 차이 기록 |

첫 번째 작업은 공개 합성 환경과 `edge-platform-integration/`의 exporter를 사용한다. 실제 현장
설정과 적재율 보정은 이 Repository의 작업 범위가 아니다. 두 번째 작업은
생성기만 자원을 독점한다고 가정하지 않고 `lidar-processing`과 다른 검증용 edge process를 함께
실행한다. 성능 상한은 측정 전에 임의로 확정하지 않는다. 세 번째 작업의 원시 frame,
실제 quality 분포, 사설 주소와 운영 로그는 공개 fixture가 아니라 `docs/internal/`의 별도
산출물로 관리한다.

별도 시각화 Repository의 실시간 3D 표시와 MP4 생성은 이 Repository의 다음 구현 작업이
아니다. 생성기 측 관찰 계약과 latest-only 비차단 출력은 완료되어 있다.

## 변경 원칙

외부 Proto 또는 처리 설정이 바뀌면 고정 source commit, 로컬 Proto, 생성 binding, exporter,
인계 묶음과 직접 호환 검증을 하나의 변경에서 갱신한다. 합성 환경이 바뀌면 공개 JSON 정본과
exporter fixture를 함께 갱신한다. `docs/project-spec.md`와 기존 `docs/internal/**`은 사용자의
명시적 요청 없이 수정하지 않는다.

각 변경은 Organization 개발 운영 규칙의 Issue, branch, Pull Request와 Release 절차를 따른다.
Issue와 Pull Request 제목 summary는 명사구로 끝낸다.
