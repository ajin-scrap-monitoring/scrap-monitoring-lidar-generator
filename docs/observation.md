# 적재 모델 관찰과 시각화

## 목적

관찰 기능은 엣지 라이다 생성기가 현재 계산한 적재물 표면 모양과 시나리오 상태를 필요할 때 별도 장비에서 확인하는 개발용 경로다. 일반 scan 데이터로 표면을 추정하지 않으며 생성기 내부의 읽기 전용 snapshot을 사용한다.

## 운영 경계

기본 실행은 관찰 기능을 생성하지 않는다. 엣지 생성기는 기존 scan을 기존 수신 프로그램으로 전송하고, 운영 이미지에는 시각화 코드, Matplotlib과 FFmpeg를 포함하지 않는다.

관찰을 켜면 생성기는 기존 scan 전송과 별도로 JSON Lines(JavaScript Object Notation Lines) 파일을 기록한다. 관찰 레코드는 기존 scan 계약과 수신 프로그램에 전달되지 않는다. 파일 생성, 기록 지연과 관찰 입력 오류는 scan 생성 및 전송을 중단하지 않는다.

관찰 출력은 `contracts/observation/v1/observation.schema.json`의 version 1 계약을 따른다. 각 레코드는 `environment_id`, 실행 `run_id`, seed와 입력 SHA-256(Secure Hash Algorithm 256-bit) 지문, 시뮬레이션 시각과 `filling` 또는 `collecting` 상태, 적재율과 현재 적재물 표면 격자를 담는다. 바닥, 외벽, 투입구와 센서 설치는 레코드에 복제하지 않고 공개 합성 환경 정본인 `examples/environment.v1.json`과 `examples/generator.v1.json`을 재생 입력으로 사용한다.

## 엣지 기록

생성기 CLI(Command-Line Interface)는 다음 선택 인자를 제공한다.

| 인자 | 기본값 | 의미 |
| --- | --- | --- |
| `--observation-path` | 비활성 | 관찰 JSON Lines 출력 경로 |
| `--observation-interval-s` | `1.0` | 시뮬레이션 초 기준 snapshot 간격 |
| `--observation-max-records` | `300` | 한 실행에서 기록할 최대 레코드 수 |

관찰 recorder는 최대 32개 또는 설정한 최대 레코드 수 중 작은 값의 비동기 queue를 사용한다. queue가 가득 차면 오래된 대기 snapshot을 폐기하고 최신 상태를 유지한다. 한 실행의 파일 레코드 상한은 10,000개이며, 생성 루프는 디스크 기록을 기다리지 않는다. 출력 파일은 기존 파일을 덮어쓰지 않고 새 파일로 생성한다.

관찰 간격은 0보다 크고 86,400초 이하여야 하며, 최대 레코드 수는 1개 이상 10,000개 이하여야 한다.

관찰 출력의 첫 snapshot은 생성기가 처음 완료한 scan 시점 이후의 최신 상태이며, 이후 설정한 간격을 지난 첫 완료 시점의 상태를 기록한다. 시간 사이의 표면을 보간하지 않는다.

```bash
uv run --locked scrap-monitoring-lidar-generator \
  --config examples/generator.v1.json \
  --observation-path /data/observations/run.jsonl \
  --observation-interval-s 1 \
  --observation-max-records 300
```

## 별도 장비 재생과 렌더링

시각화 도구는 `tools/visualization/`에 있으며 production package와 엣지 이미지에 포함되지 않는다. 개발 환경에서 선택적 `visualization` dependency group을 설치한 뒤 JSON Lines 파일과 같은 공개 설정을 입력한다.

```bash
uv sync --locked --all-groups
uv run --locked python -m tools.visualization.cli \
  --config examples/generator.v1.json \
  --input /data/observations/run.jsonl \
  --output /data/rendered/load-model.mp4 \
  --time-scale 60 \
  --fps 10 \
  --width 1280 \
  --height 720 \
  --camera oblique
```

기본 카메라는 전체 적재 공간이 보이는 고정 사선 시점이다. `--camera top`은 상면 시점을 선택한다. `--start-time`, `--end-time`으로 시뮬레이션 구간을 선택하며 `--duration` 또는 `--time-scale`로 재생 속도를 정한다. `--preview`는 MP4 대신 개발 장비의 창에서 선택한 구간을 재생한다.

렌더러는 현재 적재물 표면을 삼각형 mesh로 만들고 공개 환경 설정의 바닥, 외벽, 투입구와 센서 위치 및 방향을 함께 표시한다. 프레임에는 시뮬레이션 시각, 적재율과 상태를 표시한다. 같은 설정, seed와 관찰 레코드를 사용하면 시간 선택과 mesh 값이 같다.

MP4 writer는 PATH에 설치된 외부 FFmpeg 실행 파일을 호출한다. Repository와 운영 이미지에는 FFmpeg binary를 포함하지 않으며, MP4 생성 장비가 FFmpeg를 별도로 설치하고 배포 조건을 확인한다. FFmpeg의 기본 LGPL(Lesser General Public License) 범위와 선택적 GPL(General Public License) 구성은 [FFmpeg 법적 안내](https://ffmpeg.org/legal.html)를 따른다.

## 상한과 검증

엣지 관찰 출력은 기본 비활성 상태에서 추가 파일과 렌더링 의존성을 사용하지 않는다. 활성 상태의 메모리 보류량은 bounded queue와 레코드 상한으로 제한하고, 출력 오류는 실행 요약의 `observation_error`로 보고한다.

렌더러는 기본 최대 3,000 프레임, 최대 60 FPS와 3,840 x 2,160 해상도 상한을 적용한다. 자동 검증은 시각 구간 선택, mesh 정점 및 면 생성, 같은 입력의 결정론, queue 및 기록 상한, 관찰 출력 장애 격리와 CLI 오류를 확인한다. 픽셀 전체를 고정하는 snapshot 검증은 사용하지 않는다.

생성된 JSON Lines, 프레임과 MP4는 기본적으로 Git에 추적하지 않는다. 실제 센서 측정값, 품질 관측 원본, 운영 로그, 사설 주소와 자격 증명은 관찰 출력과 예시에 포함하지 않는다.
