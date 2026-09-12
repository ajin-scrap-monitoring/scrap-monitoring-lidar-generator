# 배포와 실행

## 배포 산출물

배포 산출물은 3개 구성 요소로 이루어진다.

| 구성 요소 | 형식 | 대상 |
| --- | --- | --- |
| Python package | source distribution, wheel | Python 3.14 환경 |
| OCI image | Raspberry Pi 5용 ARM64 image | `linux/arm64` |
| GitHub Release | version tag, package asset와 `oci-image.txt` | 검증된 `main` commit |

OCI(Open Container Initiative) 이미지는 CPython 3.14.4와 `uv.lock`의 runtime 의존성을 정확히 고정한다. 빌드 단계의 uv와 실행 단계의 CPython base image는 manifest digest로 고정한다. Release 이미지는 Raspberry Pi 5의 `linux/arm64`만 대상으로 한다. 최종 이미지에는 uv와 개발 의존성을 포함하지 않으며 UID(User Identifier)와 GID(Group Identifier) 10001인 비 root 사용자로 실행한다.

## 로컬 이미지 검증

현재 CPU(Central Processing Unit) architecture용 이미지를 빌드하고 CLI(Command-Line Interface)를 확인한다.

```bash
docker build --tag scrap-monitoring-lidar-generator:local .
docker run --rm scrap-monitoring-lidar-generator:local --help
```

## 컨테이너 실행

배포 입력은 4개다.

| 입력 | 제공 방법 | 필수 조건 |
| --- | --- | --- |
| ARM64 image | Release asset `oci-image.txt`의 불변 참조 | `linux/arm64`, Public GHCR package |
| 생성 설정 directory | LiDAR 2대를 포함하는 `examples/`의 3개 JSON 파일을 기반으로 만든 외부 설정 | container의 `/config`에 읽기 전용 mount |
| scan 수신 endpoint | `generator.v1.json`의 `transport.host`, `transport.port` | container에서 접근 가능한 높이 계산 process의 TCP server |
| 관찰 수신 endpoint | `--observation-host`, `--observation-port` | container에서 접근 가능한 시각화 프로그램의 TCP server |

GitHub Container Registry(GHCR) image는 Public이므로 pull credential이 필요하지 않다.
Release asset에서 불변 image 참조를 가져와 image를 준비한다.

```bash
gh release download v0.2.1 \
  --repo ajin-scrap-monitoring/scrap-monitoring-lidar-generator \
  --pattern oci-image.txt \
  --dir /tmp/scrap-monitoring-lidar-generator-release
IMAGE_REF="$(sed -n '1p' /tmp/scrap-monitoring-lidar-generator-release/oci-image.txt)"
docker image pull "$IMAGE_REF"
```

엣지 장비에는 Repository clone, Python, uv와 compiler가 필요하지 않다. 배포 제어 장비가
`examples/environment.v1.json`, `examples/generator.v1.json`과
`examples/quality-profile.v1.json`을 설정 directory에 함께 배치한다. 상대 경로인
`environment_path`와 `quality_profile_path`는 `generator.v1.json`이 있는 directory를
기준으로 해석된다.

`generator.v1.json`의 `transport.host`와 `transport.port`를 실제 scan 수신 endpoint로
바꾼다. 진단을 사용하면 `diagnostics.output_path`를 `/data/diagnostics`로 바꾸고 host의
진단 directory를 UID(User Identifier)와 GID(Group Identifier) 10001이 쓸 수 있게
준비한다. 진단을 사용하지 않으면 `diagnostics.enabled`를 `false`로 바꾸고 진단 mount를
생략할 수 있다. 실제 사설 주소, 자격 증명과 운영 설정은 Git에 추가하지 않는다.

생성 설정은 이미지에 포함하지 않고 읽기 전용 bind mount로 전달한다. 다음 명령은 재부팅
후에도 container를 다시 시작하며 Docker log file의 크기를 제한한다.

```bash
docker run --detach \
  --name scrap-monitoring-lidar-generator \
  --restart unless-stopped \
  --log-opt max-size=10m \
  --log-opt max-file=3 \
  --mount type=bind,src=/path/to/config,dst=/config,readonly \
  --mount type=bind,src=/path/to/diagnostics,dst=/data/diagnostics \
  "$IMAGE_REF" \
  --config /config/generator.v1.json \
  --observation-host <observation-receiver-host> \
  --observation-port 9100
```

scan 수신 endpoint와 관찰 수신 endpoint는 서로 다른 설정이다. 두 TCP server는 같은
장비의 서로 다른 port일 수도 있고 서로 다른 장비일 수도 있다. 두 host에는 Docker
container 안에서 이름을 해석하고 router를 거쳐 접근할 수 있는 DNS(Domain Name System)
이름 또는 IP 주소를 사용한다. 공개 Repository에는 실제 사설 주소를 기록하지 않는다.

Docker Engine의 `--cpus`와 `--memory`로 생성 프로그램의 자원 상한을 지정할 수 있다.
대상 Raspberry Pi 5에서 다른 edge process와 함께 측정한 결과가 확정되기 전에는
Repository가 기본 자원 상한을 정하지 않는다.

Docker Engine은 SIGTERM을 전달하며 프로그램은 진행 중인 생성과 송신 작업을 종료한 뒤
마지막 집계를 표준 출력에 기록한다. 첫 줄은 실행 식별자와 생성, ACK 및 미응답 frame 수를
제공한다. `transport` 줄은 적재, 송신, ACK, 거부, 시간 만료, 용량 폐기, 크기 초과, 연결 실패,
미응답 frame 및 byte를 제공한다. `observation` 줄은 전송, 폐기와 연결 실패를 제공한다.
환경, version 또는 응답 규격 오류로 전체 scan 전송이
중단되면 원인 센서와 오류를 최초 발생 시 표준 오류에 기록한다. 시나리오 계산과 bounded
buffer 만료는 container가 종료될 때까지 계속된다. 설정 오류도 표준 오류에 기록한다.
`docker logs scrap-monitoring-lidar-generator`로 시작 및 종료 결과를 확인한다. 실행 중인
container와 적용 image digest는 다음 명령으로 확인한다.

```bash
docker container inspect scrap-monitoring-lidar-generator \
  --format '{{.State.Status}} {{.State.ExitCode}} {{.Image}}'
docker image inspect "$IMAGE_REF" --format '{{index .RepoDigests 0}}'
```

적재 모델 관찰은 [`docs/observation.md`](observation.md)의 별도 TCP stream을 사용한다. 운영 이미지는 JSON Lines snapshot을 계속 전송하지만 관찰 기록, 3D 렌더러와 FFmpeg를 포함하지 않는다.

## 반복 가능한 엣지 검증

`tests/edge/run.sh`는 Docker Engine만 설치된 Linux 장비에서 scan ACK와 관찰 JSON Lines 수신 test double을 실행한다. 검증 도구는 test double 코드를 대상 image에 읽기 전용 mount하므로 운영 image에 개발 도구나 추가 의존성을 포함하지 않는다. 검증 대상은 도구와 같은 source revision으로 만든 digest 고정 image여야 한다.

검증 입력은 5개다.

| 입력 | 기준 |
| --- | --- |
| image | `name@sha256:<digest>` 형식의 불변 참조 |
| 설정 directory | 정확히 2개 센서를 포함하는 3개 JSON 설정 |
| 실행 구간 | 양의 정수 wall-clock 초, 기본 30초 |
| CPU 상한 | 양의 Docker CPU 값, 개발 검증 기본 2 core |
| 결과 directory | 생략 시 `/tmp` 아래 임시 directory |

Repository의 검증 도구와 설정을 엣지 장비의 작업 directory에 전달한 뒤 다음 명령을 실행한다.

```bash
tests/edge/run.sh \
  --image "$IMAGE_REF" \
  --config-dir examples \
  --duration-s 30 \
  --cpus 2 \
  --output-dir /tmp/lidar-edge-validation
```

도구는 센서별 TCP 연결과 scan 수신, sequence gap, 전송 폐기 및 실패, 종료 시 최대 2개의 전송 중 frame, 관찰 header와 동적 snapshot을 검사한다. `generator.log`, `receiver.log`, `docker-stats.jsonl`과 `container-inspect.json`은 지정한 결과 directory에 남는다. 이 결과에는 실행 환경의 경로와 운영 정보가 포함될 수 있으므로 Git에 추가하지 않는다. CPU 2 core는 test double 검증의 시작값이며 실제 높이 계산 process와 다른 edge process를 포함한 운영 자원 기준이 아니다.

## Release

Release workflow는 원격 `main` 이력에 포함된 commit의 `vMAJOR.MINOR.PATCH` tag만 처리한다. workflow는 전체 소스 검증과 package build를 실행하고 `linux/arm64` 이미지를 GitHub Container Registry에 `MAJOR.MINOR.PATCH`와 `sha-<full-git-sha>` tag로 게시한다. 두 tag가 같은 manifest digest를 가리키는지, 실행 platform이 ARM64 하나인지와 package가 Public인지 검증한 뒤 package 파일과 불변 image 참조를 기록한 `oci-image.txt`를 GitHub Release asset으로 게시한다. 배포 환경은 tag 대신 검증한 manifest digest를 사용한다. 이미지는 Software Bill of Materials(SBOM)와 provenance attestation을 포함한다.

프로젝트 버전과 tag 버전을 일치시킨 검증 완료 commit에만 release tag를 생성한다. 게시된 tag, image tag와 Release asset은 변경하지 않는다.

배포할 version의 Release asset `oci-image.txt`를 불변 image 참조의 정본으로 사용한다.
장비별 검증 결과와 적용한 digest는 [`performance.md`](performance.md)에 기록한다.

## 현재 검증 기준

현재 ARM64 배포 기준은 `v0.2.1` Release다. Release asset의 image는 `linux/arm64` 단일
실행 platform과 Public package 상태를 확인했다. 센서별 독립 전송 lane, 전체 전송 중단
오류의 즉시 보고와 진단 version 2를 포함한다.

Raspberry Pi 5에서 scan ACK test double과 관찰 stream test double을 사용한 실행 검증을
통과했다. 고정된 센서 2개 구성은 CPU 1 core 상한에서 지속 가능한 ACK 처리량을 확보하지
못했으므로 반복 검증 도구로 더 큰 CPU 상한과 실제 수신 프로그램 조건을 확인해야 한다. source
revision, 불변 image digest와 장비별 관측값은 [`performance.md`](performance.md)가 정본이다.
실제 scan 수신 프로그램과 별도 시각화 장비의 endpoint가 확정되면 검증한 digest와 외부
운영 설정으로 상시 container를 배치한다.
