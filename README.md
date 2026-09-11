# Scrap Monitoring LiDAR Generator

스크랩 적재 모니터링 개발을 위한 LiDAR(Light Detection and Ranging) 스캔 데이터 생성 프로그램이다.

## 문서

| 문서 | 내용 |
| --- | --- |
| [`docs/project-spec.md`](docs/project-spec.md) | 제품 범위, 외부 계약과 완료 조건 |
| [`docs/architecture.md`](docs/architecture.md) | 구현 경계, 의존 방향과 검증 구조 |
| [`docs/development-plan.md`](docs/development-plan.md) | 구현 순서, 산출물과 단계별 완료 조건 |
| [`docs/dependencies.md`](docs/dependencies.md) | 직접 의존성, 버전, 사용 목적과 라이선스 |
| [`docs/deployment.md`](docs/deployment.md) | OCI 이미지, 컨테이너 실행과 Release 절차 |
| [`docs/performance.md`](docs/performance.md) | 생성 구간별 부하 측정과 결과 해석 |
| [`contracts/v1/`](contracts/v1/) | 환경, 생성 실행, 품질 분포와 스캔 및 응답 계약 버전 1 |
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

생성 설정의 수신 주소와 입력 경로를 실행 환경에 맞게 지정한 뒤 다음 명령으로 실행한다.

```bash
uv run --locked scrap-monitoring-lidar-generator --config /path/to/generator.v1.json
```

프로그램은 센서 회전 완료 시각에 맞춰 스캔을 생성하고 TCP 수신 프로그램으로 전송한다. 실행마다 UUID(Universally Unique Identifier) 형식의 새로운 `run_id`를 만들며 SIGINT 또는 SIGTERM을 받으면 생성과 송신을 정상 종료하고 집계를 출력한다.

OCI(Open Container Initiative) 이미지의 build, 실행과 multi-platform Release 절차는 [`docs/deployment.md`](docs/deployment.md)를 따른다.

## 이용 조건

이 Repository는 코드 검토와 참고를 위해 Public으로 제공하며 프로젝트 소스 코드에 별도 라이선스를 부여하지 않는다.
