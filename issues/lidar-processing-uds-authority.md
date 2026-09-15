# [Bug] LiDAR 처리 UDS authority 누락

## 오류 내용

`lidar-processing`이 sensor별 UDS(Unix Domain Socket)에 연결할 때 gRPC(Google Remote Procedure
Call) channel authority를 지정하지 않아 tonic 기반 scan server가 구독 요청을 거부한다.

## 재현 방법

1. tonic 기반 scan server에서 `lidar_1.sock`과 `lidar_2.sock`을 제공한다.
2. 현재 `lidar-processing`으로 두 UDS endpoint를 구독한다.
3. 구독이 실패하고 처리 상태가 `RETRYING`과 `SENSOR_ABSENT`로 유지되는지 확인한다.

## 예상 동작

Sensor 구독 channel option에 `("grpc.default_authority", "localhost")`를 추가하여 두 UDS
endpoint를 정상적으로 구독한다.
