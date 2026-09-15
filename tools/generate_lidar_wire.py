"""Generate or verify Python wire modules from the pinned LiDAR Proto."""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).parents[1]
_PROTO_DIRECTORY = _ROOT / "contracts" / "lidar" / "v1"
_TARGET_DIRECTORY = _ROOT / "src" / "scrap_monitoring_lidar_simulator" / "wire"
_GENERATED_NAMES = ("lidar_pb2.py", "lidar_pb2.pyi", "lidar_pb2_grpc.py")


def _generate(output: Path) -> None:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "grpc_tools.protoc",
            f"--proto_path={_PROTO_DIRECTORY}",
            f"--python_out={output}",
            f"--pyi_out={output}",
            f"--grpc_python_out={output}",
            "lidar.proto",
        ],
        check=True,
    )
    grpc_module = output / "lidar_pb2_grpc.py"
    text = grpc_module.read_text(encoding="utf-8")
    import_line = "import lidar_pb2 as lidar__pb2"
    if text.count(import_line) != 1:
        raise RuntimeError("generated gRPC module has an unexpected protobuf import")
    grpc_module.write_text(
        text.replace(import_line, "from . import lidar_pb2 as lidar__pb2"),
        encoding="utf-8",
    )


def _check(generated: Path) -> None:
    changed = [
        name
        for name in _GENERATED_NAMES
        if (_TARGET_DIRECTORY / name).read_bytes() != (generated / name).read_bytes()
    ]
    if changed:
        raise RuntimeError(f"generated LiDAR wire modules are stale: {', '.join(changed)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="lidar-wire-") as directory:
        generated = Path(directory)
        _generate(generated)
        if arguments.check:
            _check(generated)
        else:
            for name in _GENERATED_NAMES:
                (_TARGET_DIRECTORY / name).write_bytes((generated / name).read_bytes())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
