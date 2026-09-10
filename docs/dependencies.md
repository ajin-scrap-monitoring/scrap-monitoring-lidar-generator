# 외부 의존성

## 적용 범위

현재 직접 사용하는 외부 의존성은 4개다. 애플리케이션 package 의존성은 아직 없으며 아래 항목은 Python 실행 환경과 임시 CI(Continuous Integration)에 사용한다.

| 의존성 | 버전 | 사용 목적 | 출처 | 라이선스 |
| --- | --- | --- | --- | --- |
| CPython | `3.14.x` | 애플리케이션 실행 및 Python 구문 검사 | [Python](https://www.python.org/downloads/) | Python-2.0 |
| `actions/checkout` | `v6` | GitHub Actions 실행 환경의 Repository checkout | [GitHub](https://github.com/actions/checkout) | MIT |
| `actions/setup-python` | `v6` | GitHub Actions의 Python 3.14 설치 | [GitHub](https://github.com/actions/setup-python) | MIT |
| `markdownlint-cli2` | `0.23.2` | Markdown 형식 검사 | [GitHub](https://github.com/DavidAnson/markdownlint-cli2) | MIT |

애플리케이션 또는 개발 의존성을 추가하거나 버전을 변경하면 같은 Pull Request에서 이 표와 잠금 파일을 갱신한다.
