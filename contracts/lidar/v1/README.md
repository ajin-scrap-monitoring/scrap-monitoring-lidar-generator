# LiDAR scan 계약

`lidar.proto`는 `ajin-edge-platform`이 소유한 scan 계약의 고정 호환 사본이다. `upstream.json`은
정본 Repository, commit, 경로와 SHA-256을 기록한다.

외부 계약을 변경한 뒤에만 다음 파일을 함께 갱신한다.

- `lidar.proto`와 `upstream.json`.
- `src/scrap_monitoring_lidar_simulator/wire/`의 생성 binding.
- `edge-platform-integration/v1/lidar.proto`.
- scan 변환, gRPC server와 외부 구현 직접 호환 테스트.

생성 binding 검증 명령은 다음과 같다.

```bash
uv run --locked python -m tools.generate_lidar_wire --check
```
