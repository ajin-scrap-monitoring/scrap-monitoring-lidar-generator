# 외부 의존성

## 적용 범위

현재 직접 사용하는 외부 의존성은 38개다. Python 및 문서 실행 경계는 26개, Rust library와
실행 기반은 toolchain 1개와 직접 crate 11개를 사용한다. NumPy, `grpcio`와 `protobuf`는 애플리케이션
runtime의 수치 연산과 외부 gRPC(Google Remote Procedure Call) 계약에 사용한다. 나머지
항목은 Python 실행, wire binding 생성, 빌드, 개발 검증, CI(Continuous Integration)와
Release에 사용한다.

| 의존성 | 버전 | 사용 목적 | 출처 | 라이선스 |
| --- | --- | --- | --- | --- |
| CPython | 호환 범위 `>=3.14,<3.15`, 개발 및 OCI image `3.14.4` | 애플리케이션 실행 및 Python 구문 검사 | [Python](https://www.python.org/downloads/) | Python-2.0 |
| `uv` | `0.12.12` | 환경 구성, 의존성 잠금과 명령 실행 | [Astral](https://github.com/astral-sh/uv) | MIT OR Apache-2.0 |
| `uv_build` | `0.12.12` | Python source distribution과 wheel 빌드 | [Astral](https://docs.astral.sh/uv/concepts/build-backend/) | MIT OR Apache-2.0 |
| NumPy | `>=2.5.3,<2.6` | 높이장 배열, 부피와 표면 변화 수치 연산 | [NumPy](https://numpy.org/) | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| `grpcio` | `>=1.83.1,<1.84` | sensor별 gRPC over UDS server와 구독 stream | [gRPC](https://github.com/grpc/grpc) | Apache-2.0 |
| `protobuf` | `>=7.36.1,<8` | 외부 ScanFrame wire message | [Protocol Buffers](https://github.com/protocolbuffers/protobuf) | BSD-3-Clause |
| jsonschema | `>=4.26.0,<5` | JSON Schema 계약과 합성 fixture 검증 | [Python JSON Schema](https://python-jsonschema.readthedocs.io/) | MIT |
| `grpcio-tools` | `>=1.83.1,<1.84` | 고정 Proto의 Python binding 재생성 | [gRPC](https://github.com/grpc/grpc) | Apache-2.0 |
| Ruff | `>=0.16.6,<0.17` | Python 형식 및 정적 검사 | [Astral](https://docs.astral.sh/ruff/) | MIT |
| mypy | `>=2.3.1,<3` | Python 정적 type 검사 | [mypy](https://www.mypy-lang.org/) | MIT |
| pytest | `>=9.1.1,<10` | Python 자동 테스트 | [pytest](https://docs.pytest.org/) | MIT |
| `types-jsonschema` | `>=4.26.0.20260518,<5` | jsonschema 사용 코드의 정적 type 검사 | [typeshed](https://github.com/python/typeshed) | Apache-2.0 |
| `types-grpcio` | `>=1.83.0.20260730,<1.84` | gRPC 사용 코드의 정적 type 검사 | [typeshed](https://github.com/python/typeshed) | Apache-2.0 |
| `actions/checkout` | `v6.1.0` | GitHub Actions 실행 환경의 Repository checkout | [GitHub](https://github.com/actions/checkout) | MIT |
| `actions/setup-python` | `v6.3.0` | GitHub Actions의 Python 3.14.4 설치 | [GitHub](https://github.com/actions/setup-python) | MIT |
| `astral-sh/setup-uv` | `v10.0.1` | GitHub Actions의 uv 설치 및 cache 구성 | [Astral](https://github.com/astral-sh/setup-uv) | MIT |
| `docker/setup-qemu-action` | `v4.3.0` | GitHub Actions의 ARM64 image emulation 구성 | [Docker](https://github.com/docker/setup-qemu-action) | Apache-2.0 |
| `docker/setup-buildx-action` | `v4.3.0` | GitHub Actions의 ARM64 image builder 구성 | [Docker](https://github.com/docker/setup-buildx-action) | Apache-2.0 |
| `docker/login-action` | `v4.6.0` | GitHub Container Registry 인증 | [Docker](https://github.com/docker/login-action) | Apache-2.0 |
| `docker/build-push-action` | `v7.3.0` | OCI image build, attestation과 registry 게시 | [Docker](https://github.com/docker/build-push-action) | Apache-2.0 |
| `softprops/action-gh-release` | `v3.0.3` | integration bundle과 image 참조를 포함한 GitHub Release 게시 | [GitHub](https://github.com/softprops/action-gh-release) | MIT |
| rumdl | `>=0.2.70,<0.3` | Markdown 형식 검사 | [GitHub](https://github.com/rvben/rumdl) | MIT |
| Pillow | `12.3.0` | 합성 환경 PNG 도면 생성 | [Pillow](https://python-pillow.github.io/) | HPND |
| `python-docx` | `1.2.0` | 합성 환경 DOCX 생성 | [python-docx](https://python-docx.readthedocs.io/) | MIT |
| LibreOffice | `7.4` | 합성 환경 DOCX의 PDF 변환 | [LibreOffice](https://www.libreoffice.org/) | MPL-2.0 |
| DejaVu Sans | `2.37` | 합성 환경 PNG 도면 글꼴 | [DejaVu Fonts](https://dejavu-fonts.github.io/) | Bitstream-Vera |

## Rust 의존성

Rust library와 설정 검증은 toolchain 1개와 직접 crate 12개를 사용한다. `rust-toolchain.toml`은
compiler, rustfmt와 Clippy를 고정하고 `Cargo.toml`과 `Cargo.lock`은 직접 및 전이 crate를
고정한다. `build.rs`는 vendored `protoc` 31.1을 사용하며 시스템 `protoc`에 의존하지 않는다.

| 의존성 | 버전 | 사용 목적 | 출처 | 라이선스 |
| --- | --- | --- | --- | --- |
| Rust | `1.96.0` | compiler, Cargo, rustfmt와 Clippy | [Rust](https://github.com/rust-lang/rust) | MIT OR Apache-2.0 |
| `clap` | `4.6.6` | 별도 설정 검증 CLI | [clap](https://docs.rs/crate/clap/4.6.6) | MIT OR Apache-2.0 |
| `num-bigint` | `0.4.8` | 큰 10진 rate의 정확한 회전 및 sample 비율 계산 | [num-bigint](https://docs.rs/crate/num-bigint/0.4.8) | MIT OR Apache-2.0 |
| `serde` | `1.0.229` | 중복 key 보존 검증을 위한 JSON visitor | [Serde](https://docs.rs/crate/serde/1.0.229) | MIT OR Apache-2.0 |
| `serde_json` | `1.0.151` | 정수 정밀도와 raw JSON을 보존하는 설정 parser | [Serde JSON](https://docs.rs/crate/serde_json/1.0.151) | MIT OR Apache-2.0 |
| `thiserror` | `2.0.20` | 분류와 입력 경로를 가진 설정 오류 | [thiserror](https://docs.rs/crate/thiserror/2.0.20) | MIT OR Apache-2.0 |
| `tonic` | `0.14.6` | 고정 Proto의 gRPC client와 server binding 기반 | [tonic](https://docs.rs/crate/tonic/0.14.6) | MIT |
| `tonic-prost` | `0.14.6` | tonic의 prost message codec | [tonic-prost](https://docs.rs/crate/tonic-prost/0.14.6) | MIT |
| `prost` | `0.14.4` | Protocol Buffers message와 직렬화 | [prost](https://docs.rs/crate/prost/0.14.4) | Apache-2.0 |
| `prost-build` | `0.14.4` | 명시적 compiler 경로를 사용한 message 생성 | [prost-build](https://docs.rs/crate/prost-build/0.14.4) | Apache-2.0 |
| `tonic-prost-build` | `0.14.6` | 빌드 시 고정 Proto의 service binding 생성 | [tonic-prost-build](https://docs.rs/crate/tonic-prost-build/0.14.6) | MIT |
| `protoc-bin-vendored` | `3.2.0`, 포함 compiler `31.1` | host별 고정 Protocol Buffers compiler | [crate](https://docs.rs/crate/protoc-bin-vendored/3.2.0), [compiler](https://github.com/protocolbuffers/protobuf/blob/v31.1/LICENSE) | wrapper MIT, compiler BSD-3-Clause |
| `sha2` | `0.11.0` | model stream seed 파생과 테스트의 고정 Proto SHA-256 검증 | [RustCrypto](https://docs.rs/crate/sha2/0.11.0) | MIT OR Apache-2.0 |

애플리케이션 또는 개발 의존성을 추가하거나 버전을 변경하면 같은 Pull Request에서 해당 표와 잠금 파일을 갱신한다.
