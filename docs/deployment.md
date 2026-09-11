# 배포와 실행

## 배포 산출물

배포 산출물은 3개 구성 요소로 이루어진다.

| 구성 요소 | 형식 | 대상 |
| --- | --- | --- |
| Python package | source distribution, wheel | Python 3.14 환경 |
| OCI image | multi-platform image | `linux/amd64`, `linux/arm64` |
| GitHub Release | version tag와 package asset | 검증된 `main` commit |

OCI(Open Container Initiative) 이미지는 CPython 3.14.4와 `uv.lock`의 runtime 의존성을 정확히 고정한다. 빌드 단계의 uv와 실행 단계의 CPython base image는 multi-platform manifest digest로 고정한다. 최종 이미지에는 uv와 개발 의존성을 포함하지 않으며 UID(User Identifier)와 GID(Group Identifier) 10001인 비 root 사용자로 실행한다.

## 로컬 이미지 검증

현재 CPU(Central Processing Unit) architecture용 이미지를 빌드하고 CLI(Command-Line Interface)를 확인한다.

```bash
docker build --tag scrap-monitoring-lidar-generator:local .
docker run --rm scrap-monitoring-lidar-generator:local --help
```

## 컨테이너 실행

생성 설정은 이미지에 포함하지 않고 읽기 전용 bind mount로 전달한다. 진단 출력을 활성화한 설정은 컨테이너의 `/data/diagnostics`를 사용하고 쓰기 가능한 host 디렉토리를 mount한다.

```bash
docker run --rm \
  --name scrap-monitoring-lidar-generator \
  --mount type=bind,src=/path/to/config,dst=/config,readonly \
  --mount type=bind,src=/path/to/diagnostics,dst=/data/diagnostics \
  ghcr.io/ajin-scrap-monitoring/scrap-monitoring-lidar-generator:v0.1.0 \
  --config /config/generator.v1.json
```

컨테이너 네트워크에서 접근 가능한 수신 주소를 생성 설정에 사용한다. Docker Engine의 `--cpus`와 `--memory`로 생성 프로그램의 자원 상한을 지정할 수 있다. 대상 Raspberry Pi 5의 공유 부하 측정 결과가 확정되기 전에는 Repository가 기본 자원 상한을 정하지 않는다.

Docker Engine은 SIGTERM을 전달하며 프로그램은 진행 중인 생성과 송신 작업을 종료한 뒤 마지막 집계를 표준 출력에 기록한다. 전송 계약 오류와 설정 오류는 표준 오류에 기록한다. `docker logs`로 두 stream을 확인한다.

## Release

Release workflow는 원격 `main` 이력에 포함된 commit의 `vMAJOR.MINOR.PATCH` tag만 처리한다. workflow는 전체 소스 검증과 package build를 실행하고 `linux/amd64` 및 `linux/arm64` 이미지를 GitHub Container Registry에 version tag로 게시한 뒤 package 파일을 GitHub Release asset으로 게시한다. 이미지는 Software Bill of Materials(SBOM)와 provenance attestation을 포함한다.

프로젝트 버전과 tag 버전을 일치시킨 검증 완료 commit에만 release tag를 생성한다. 게시된 tag, image tag와 Release asset은 변경하지 않는다.
