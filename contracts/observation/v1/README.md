# 적재 모델 관찰 계약 버전 1

관찰 출력은 한 줄에 하나의 `load_model_observation` JSON Lines(JavaScript Object Notation Lines) 객체를 전송한다. TCP stream은 UTF-8 JSON 객체와 줄바꿈을 레코드 framing으로 사용하며 ACK(Acknowledgement)를 요구하지 않는다. 이 계약은 기존 `contracts/v1/`의 scan, ACK와 오류 전송 계약에 포함되지 않는다.

레코드는 생성기가 계산한 읽기 전용 적재 모델 상태와 시나리오 상태를 담는다. `surface`의 `x_coordinates_m`, `y_coordinates_m`와 `heights_m`는 y-major 격자이며, 경계, 투입구와 센서 설치는 레코드에 복제하지 않고 공개 합성 환경 설정을 입력으로 사용한다.

`observation_version`은 1이다. 생성기는 최신 전송 대기 레코드 1개만 보관하며 연결 실패나 느린 수신기 때문에 이전 상태를 누적하지 않는다. 레코드의 `environment_id`, `seed`와 `input_fingerprint_sha256`은 재생에 사용하는 설정과 일치해야 한다.
