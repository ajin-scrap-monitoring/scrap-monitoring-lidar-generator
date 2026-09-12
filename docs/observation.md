# 적재 모델 관찰 스트리밍

## 목적

생성기는 기존 scan 전송과 별도로 현재 적재물 표면 모양과 시나리오 상태를 개발 장비에
계속 전송한다. 일반 scan 데이터로 표면을 재구성하지 않고 생성기 내부의 읽기 전용
snapshot을 사용한다.

## 운영 경계

관찰 publisher는 생성기와 함께 항상 실행한다. 기본 주기는 시뮬레이션 시각 1초이며
전송 시각에만 적재 모델 snapshot을 복사한다. 수신기 연결 실패, 느린 수신기와 관찰
전송 오류는 scan 생성 및 기존 전송을 중단하거나 지연시키지 않는다.

publisher는 전송 대기 중인 최신 snapshot 1개만 보관한다. 새 snapshot이 들어오면 아직
전송하지 않은 이전 상태를 폐기한다. 연결이 끊어지면 제한된 지수 증가 대기 뒤에
재연결하고 과거 상태를 누적하거나 재전송하지 않는다. 엣지 장비는 관찰 파일과 영상을
생성하지 않는다.

운영 이미지에는 관찰 JSON Lines(JavaScript Object Notation Lines) producer만 포함한다.
Matplotlib, FFmpeg와 3D 렌더링 코드는 포함하지 않는다.

## 전송 계약

각 레코드는 `contracts/observation/v1/observation.schema.json`의 version 1 계약을 따른다.
TCP(Transmission Control Protocol) stream은 UTF-8 JSON 객체 하나와 줄바꿈 하나를
레코드 framing으로 사용한다. 레코드는 독립적으로 해석할 수 있으며 ACK(Acknowledgement)를
요구하지 않는다.

레코드는 `environment_id`, 실행 `run_id`, seed와 입력 SHA-256(Secure Hash Algorithm
256-bit) 지문, 시뮬레이션 시각, `filling` 또는 `collecting` 상태, 적재율과 현재 적재물
표면 격자를 담는다. 기존 scan 계약과 수신 프로그램에는 관찰 필드를 추가하지 않는다.

바닥, 외벽, 투입구와 센서 설치는 레코드에 복제하지 않는다. 시각화 프로그램은 공개 합성
환경 정본인 `examples/environment.v1.json`과 `examples/generator.v1.json`을 별도 입력으로
사용한다.

## 실행

생성기는 관찰 수신 endpoint를 명시적으로 받는다.

| 인자 | 기본값 | 의미 |
| --- | --- | --- |
| `--observation-host` | 필수 | 관찰 수신기의 TCP host |
| `--observation-port` | 필수 | 관찰 수신기의 TCP port |
| `--observation-interval-s` | `1.0` | 시뮬레이션 초 기준 전송 간격 |

```bash
uv run --locked scrap-monitoring-lidar-generator \
  --config /path/to/generator.v1.json \
  --observation-host visualizer-host \
  --observation-port 9100
```

관찰 간격은 0보다 크고 86,400초 이하여야 한다. 첫 snapshot은 처음 완료한 scan 시점의
현재 상태이며 이후 설정 간격을 지난 첫 scan 완료 시점의 상태를 전송한다. 시간 사이의
표면을 보간하지 않는다.

## 시각화 장비

[`scrap-monitoring-load-visualizer`](https://github.com/ajin-scrap-monitoring/scrap-monitoring-load-visualizer)는
TCP 관찰 stream을 수신하여 최신 적재 표면을 3D mesh로 표시한다. 선택한 수신 레코드를
bounded JSON Lines 파일로 기록하고 저장된 시각열을 결정론적으로 재생하여 MP4를 만든다.

생성된 JSON Lines, frame과 MP4는 기본적으로 Git에 추적하지 않는다. 실제 센서 측정값,
품질 관측 원본, 운영 로그, 사설 주소와 자격 증명은 관찰 출력과 예시에 포함하지 않는다.
