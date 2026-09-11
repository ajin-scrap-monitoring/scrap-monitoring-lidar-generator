# 외부 의존성

## 적용 범위

현재 직접 사용하는 외부 의존성은 19개다. NumPy와 `msgpack`은 애플리케이션 runtime의 수치 연산과 직렬화에 사용하며 나머지 항목은 Python 실행, 빌드, 개발 검증, CI(Continuous Integration)와 Release에 사용한다.

| 의존성 | 버전 | 사용 목적 | 출처 | 라이선스 |
| --- | --- | --- | --- | --- |
| CPython | `3.14.x`, OCI image `3.14.4-slim-bookworm` | 애플리케이션 실행 및 Python 구문 검사 | [Python](https://www.python.org/downloads/) | Python-2.0 |
| `uv` | `0.12.12` | 환경 구성, 의존성 잠금과 명령 실행 | [Astral](https://github.com/astral-sh/uv) | MIT OR Apache-2.0 |
| `uv_build` | `0.12.12` | Python source distribution과 wheel 빌드 | [Astral](https://docs.astral.sh/uv/concepts/build-backend/) | MIT OR Apache-2.0 |
| NumPy | `>=2.5.3,<2.6` | 높이장 배열, 부피와 표면 변화 수치 연산 | [NumPy](https://numpy.org/) | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| `msgpack` | `>=1.2.2,<1.3` | 스캔 및 응답 본문의 MessagePack 인코딩과 디코딩 | [msgpack-python](https://github.com/msgpack/msgpack-python) | Apache-2.0 |
| jsonschema | `>=4.26.0,<5` | JSON Schema 계약과 합성 fixture 검증 | [Python JSON Schema](https://python-jsonschema.readthedocs.io/) | MIT |
| Ruff | `>=0.16.6,<0.17` | Python 형식 및 정적 검사 | [Astral](https://docs.astral.sh/ruff/) | MIT |
| mypy | `>=2.3.1,<3` | Python 정적 type 검사 | [mypy](https://www.mypy-lang.org/) | MIT |
| pytest | `>=9.1.1,<10` | Python 자동 테스트 | [pytest](https://docs.pytest.org/) | MIT |
| `types-jsonschema` | `>=4.26.0.20260518,<5` | jsonschema 사용 코드의 정적 type 검사 | [typeshed](https://github.com/python/typeshed) | Apache-2.0 |
| `actions/checkout` | `v6.1.0` | GitHub Actions 실행 환경의 Repository checkout | [GitHub](https://github.com/actions/checkout) | MIT |
| `actions/setup-python` | `v6.3.0` | GitHub Actions의 Python 3.14 설치 | [GitHub](https://github.com/actions/setup-python) | MIT |
| `astral-sh/setup-uv` | `v10.0.1` | GitHub Actions의 uv 설치 및 cache 구성 | [Astral](https://github.com/astral-sh/setup-uv) | MIT |
| `docker/setup-qemu-action` | `v4.3.0` | GitHub Actions의 ARM64 image emulation 구성 | [Docker](https://github.com/docker/setup-qemu-action) | Apache-2.0 |
| `docker/setup-buildx-action` | `v4.3.0` | GitHub Actions의 multi-platform image builder 구성 | [Docker](https://github.com/docker/setup-buildx-action) | Apache-2.0 |
| `docker/login-action` | `v4.6.0` | GitHub Container Registry 인증 | [Docker](https://github.com/docker/login-action) | Apache-2.0 |
| `docker/build-push-action` | `v7.3.0` | OCI image build, attestation과 registry 게시 | [Docker](https://github.com/docker/build-push-action) | Apache-2.0 |
| `softprops/action-gh-release` | `v3.0.3` | package asset을 포함한 GitHub Release 게시 | [GitHub](https://github.com/softprops/action-gh-release) | MIT |
| rumdl | `>=0.2.70,<0.3` | Markdown 형식 검사 | [GitHub](https://github.com/rvben/rumdl) | MIT |

애플리케이션 또는 개발 의존성을 추가하거나 버전을 변경하면 같은 Pull Request에서 이 표와 잠금 파일을 갱신한다.
