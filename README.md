# Scrap Monitoring LiDAR Generator

스크랩 적재 모니터링 개발을 위한 LiDAR(Light Detection and Ranging) 스캔 데이터 생성 프로그램이다.

## 문서

| 문서 | 내용 |
| --- | --- |
| [`docs/project-spec.md`](docs/project-spec.md) | 제품 범위, 외부 계약과 완료 조건 |
| [`docs/architecture.md`](docs/architecture.md) | 구현 경계, 의존 방향과 검증 구조 |
| [`docs/development-plan.md`](docs/development-plan.md) | 구현 순서, 산출물과 단계별 완료 조건 |
| [`docs/dependencies.md`](docs/dependencies.md) | 직접 의존성, 버전, 사용 목적과 라이선스 |
| [Organization 개발 운영 규칙](https://github.com/ajin-scrap-monitoring/.github/blob/main/GOVERNANCE.md) | Issue, 브랜치, Pull Request, CI(Continuous Integration)와 Release 기준 |

## 개발 환경

Python 3.14와 uv 0.12.12가 필요하다. 다음 명령으로 잠금 파일에 맞는 개발 환경을 구성하고 전체 검증을 실행한다.

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

현재 CLI는 설치 및 실행 기반만 제공한다. 스캔 생성 동작은 제품 명세의 구현 단계에 따라 추가한다.

## 이용 조건

이 Repository는 코드 검토와 참고를 위해 Public으로 제공하며 프로젝트 소스 코드에 별도 라이선스를 부여하지 않는다.
