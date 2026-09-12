# Scrap Monitoring LiDAR Generator

스크랩 적재 모니터링 개발을 위한 LiDAR(Light Detection and Ranging) 스캔 데이터 생성 프로그램이다.

## 문서

| 문서 | 내용 |
| --- | --- |
| [`docs/project-spec.md`](docs/project-spec.md) | 제품 범위, 외부 계약과 완료 조건 |
| [`docs/architecture.md`](docs/architecture.md) | 구현 경계, 의존 방향과 검증 구조 |
| [`docs/development-plan.md`](docs/development-plan.md) | 구현 순서, 산출물과 단계별 완료 조건 |
| [`docs/dependencies.md`](docs/dependencies.md) | 직접 의존성, 버전, 사용 목적과 라이선스 |
| [`docs/configuration.md`](docs/configuration.md) | 공개 설정 정본, 기본값 출처와 합성값 분류 |
| [`docs/deployment.md`](docs/deployment.md) | OCI 이미지, 컨테이너 실행과 Release 절차 |
| [`docs/performance.md`](docs/performance.md) | 생성 구간별 부하 측정과 결과 해석 |
| [`docs/height-calculation-integration.md`](docs/height-calculation-integration.md) | 한 회전 scan의 높이 계산 프로세스 인계 계약 |
| [`docs/observation.md`](docs/observation.md) | 적재 모델 관찰 stream과 외부 시각화 경계 |
| [`docs/visualizer-requirements.md`](docs/visualizer-requirements.md) | 별도 시각화 Repository 구현 요구사항 |
| [`contracts/v1/`](contracts/v1/) | 환경, 생성 실행, 품질 분포와 스캔 및 응답 계약 버전 1 |
| [`contracts/observation/v1/`](contracts/observation/v1/) | 적재 모델 관찰 출력 계약 버전 1 |
| [Organization 개발 운영 규칙](https://github.com/ajin-scrap-monitoring/.github/blob/main/GOVERNANCE.md) | Issue, 브랜치, Pull Request, CI(Continuous Integration)와 Release 기준 |

## 개발 환경

개발 및 검증에는 Python 3.14.4와 uv 0.12.12가 필요하다. 다음 명령으로 잠금 파일에 맞는 개발 환경을 구성하고 전체 검증을 실행한다.

```bash
uv sync --locked --all-groups
uv run --locked rumdl check .
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked mypy
uv run --locked pytest
uv run --locked scrap-monitoring-lidar-generator --help
uv build --no-sources
```

## 엣지 배포

엣지 배포에는 build 도구가 필요하지 않다. GitHub Release의 `oci-image.txt`에 기록된
불변 digest의 ARM64 OCI(Open Container Initiative) image와 실행 설정 3개를 전달한다.
배포 담당자가 준비해야 하는 입력은 다음 4개다.

| 입력 | 설정 위치 | 의미 |
| --- | --- | --- |
| 생성 실행 설정 | `generator.v1.json` | 시나리오, 측정, scan 송신과 진단 정책 |
| scan 수신 endpoint | `generator.v1.json`의 `transport.host`, `transport.port` | 높이 계산 process가 수신하는 기존 scan stream |
| 관찰 수신 endpoint | `--observation-host`, `--observation-port` | 별도 시각화 프로그램이 수신하는 적재 모델 stream |
| 불변 image | GitHub Release의 `oci-image.txt` | digest로 고정한 `linux/arm64` image |

`examples/`의 3개 JSON 파일을 같은 directory에 복사하고
`generator.v1.json`의 scan 수신 endpoint를 배포 환경 값으로 바꾼다. 진단을 사용하면
`diagnostics.output_path`를 container 내부의 `/data/diagnostics`로 지정한다. 실제 사설
주소와 자격 증명은 Repository에 commit하지 않는다.

생성기는 두 TCP(Transmission Control Protocol) server로 각각 outbound connection을
만든다. 두 host는 container network에서 해석되고 접근 가능해야 한다. 관찰 수신기가
연결되지 않아도 scan 생성과 기존 scan 송신은 계속된다.

개발 환경에서는 다음 명령으로 같은 실행 경로를 확인할 수 있다.

```bash
uv run --locked scrap-monitoring-lidar-generator \
  --config /path/to/generator.v1.json \
  --observation-host observation-receiver-host \
  --observation-port 9100
```

프로그램은 센서 회전 완료 시각에 맞춰 스캔을 생성하고 TCP 수신 프로그램으로 전송한다. 실행마다 UUID(Universally Unique Identifier) 형식의 새로운 `run_id`를 만들며 SIGINT 또는 SIGTERM을 받으면 생성과 송신을 정상 종료하고 집계를 출력한다.

OCI(Open Container Initiative) 이미지 선택, 설정 준비, container 실행과 ARM64 Release
절차는 [`docs/deployment.md`](docs/deployment.md)를 따른다.

엣지 생성기의 상시 적재 모델 관찰 stream과 별도 장비의 3D 시각화는 [`docs/observation.md`](docs/observation.md)를 따른다.

## 이용 조건

이 Repository는 코드 검토와 참고를 위해 Public으로 제공하며 프로젝트 소스 코드에 별도 라이선스를 부여하지 않는다.
