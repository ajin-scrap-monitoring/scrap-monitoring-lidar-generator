# [Bug] LiDAR 처리 scan 보관 경계의 frame loss

## 오류 내용

`lidar-processing`은 sensor별 최근 10개 scan을 보관하고 1초마다 계산한다. 10 Hz 입력에서 계산
경계 사이 11개 scan이 들어오면 연속된 sequence인데도 가장 오래된 scan이 제거되고
`frame_loss`가 증가한다.

## 재현 방법

1. 각 sensor의 첫 scan을 넣고 한 번 계산한다.
2. 다음 계산 전에 각 sensor에 연속된 scan 11개를 넣는다.
3. 결과가 `GOOD`이어도 `frame_loss`가 sensor당 1개씩 증가하는지 확인한다.

## 예상 동작

정상적인 10 Hz 스케줄 지터를 수용하도록 보관 상한을 조정하고, 보관 회전과 실제 입력 sequence
유실을 서로 다른 counter로 기록한다.
