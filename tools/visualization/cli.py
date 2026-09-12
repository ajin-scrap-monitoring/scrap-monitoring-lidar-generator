"""Command-line interface for optional load-model rendering."""

import argparse
import math
import sys
from collections.abc import Sequence
from pathlib import Path

from scrap_monitoring_lidar_generator.configuration import ConfigurationError, load_generator_inputs
from scrap_monitoring_lidar_generator.runtime import generator_input_fingerprint
from tools.visualization.io import load_observation_records
from tools.visualization.renderer import (
    MAX_HEIGHT,
    MAX_PIXELS,
    MAX_WIDTH,
    VisualizationError,
    preview,
    render_mp4,
)
from tools.visualization.timeline import (
    DEFAULT_MAX_FRAMES,
    MAX_FPS,
    MIN_FPS,
    select_render_timeline,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the development-only visualization parser."""
    parser = argparse.ArgumentParser(
        prog="scrap-monitoring-lidar-visualize",
        description="Render load-model observation JSON Lines as an engineering MP4.",
    )
    parser.add_argument(
        "--config", required=True, type=Path, help="generator v1 JSON configuration"
    )
    parser.add_argument("--input", required=True, type=Path, help="observation JSON Lines file")
    parser.add_argument("--output", type=Path, help="MP4 output path")
    parser.add_argument("--preview", action="store_true", help="show an interactive preview")
    parser.add_argument("--start-time", type=_finite_float, help="first simulation time in seconds")
    parser.add_argument("--end-time", type=_finite_float, help="last simulation time in seconds")
    timing = parser.add_mutually_exclusive_group()
    timing.add_argument(
        "--duration", type=_positive_float, help="video playback duration in seconds"
    )
    timing.add_argument(
        "--time-scale", type=_positive_float, help="simulation seconds per video second"
    )
    parser.add_argument("--fps", type=_bounded_fps, default=10, help="output frames per second")
    parser.add_argument("--width", type=_positive_int, default=1280, help="output width in pixels")
    parser.add_argument("--height", type=_positive_int, default=720, help="output height in pixels")
    parser.add_argument("--camera", choices=("oblique", "top"), default="oblique")
    parser.add_argument(
        "--max-frames",
        type=_bounded_max_frames,
        default=DEFAULT_MAX_FRAMES,
        help="maximum frames allowed for one output",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Render an observation file outside the production package."""
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if arguments.preview and arguments.output is not None:
        parser.error("--preview and --output are mutually exclusive")
    if not arguments.preview and arguments.output is None:
        parser.error("--output is required unless --preview is selected")
    if (
        arguments.width > MAX_WIDTH
        or arguments.height > MAX_HEIGHT
        or arguments.width * arguments.height > MAX_PIXELS
    ):
        parser.error(f"resolution must not exceed {MAX_WIDTH}x{MAX_HEIGHT} pixels")
    try:
        inputs = load_generator_inputs(arguments.config)
        records = load_observation_records(arguments.input)
        _validate_input_identity(records[0], inputs)
        timeline = select_render_timeline(
            records,
            fps=arguments.fps,
            start_time_s=arguments.start_time,
            end_time_s=arguments.end_time,
            duration_s=arguments.duration,
            time_scale=arguments.time_scale,
            max_frames=arguments.max_frames,
        )
        if arguments.preview:
            preview(
                timeline,
                inputs=inputs,
                width=arguments.width,
                height=arguments.height,
                camera=arguments.camera,
            )
        else:
            render_mp4(
                timeline,
                inputs=inputs,
                output_path=arguments.output,
                width=arguments.width,
                height=arguments.height,
                camera=arguments.camera,
            )
    except (ConfigurationError, OSError, ValueError, VisualizationError) as error:
        print(f"visualization error: {error}", file=sys.stderr)
        return 2
    return 0


def _validate_input_identity(record: object, inputs: object) -> None:
    from scrap_monitoring_lidar_generator.configuration import GeneratorInputs
    from scrap_monitoring_lidar_generator.observation import ObservationRecord

    if not isinstance(record, ObservationRecord) or not isinstance(inputs, GeneratorInputs):
        raise ValueError("invalid visualization inputs")
    if record.environment_id != inputs.environment.environment_id:
        raise ValueError("observation environment_id does not match configuration")
    if record.seed != inputs.generator.seed:
        raise ValueError("observation seed does not match configuration")
    if record.input_fingerprint_sha256 != generator_input_fingerprint(inputs):
        raise ValueError("observation input fingerprint does not match configuration")


def _finite_float(value: str) -> float:
    try:
        result = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a number") from error
    if not math.isfinite(result):
        raise argparse.ArgumentTypeError("must be finite")
    return result


def _positive_float(value: str) -> float:
    result = _finite_float(value)
    if result <= 0.0:
        raise argparse.ArgumentTypeError("must be positive")
    return result


def _positive_int(value: str) -> int:
    try:
        result = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be an integer") from error
    if result <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return result


def _bounded_fps(value: str) -> int:
    result = _positive_int(value)
    if not MIN_FPS <= result <= MAX_FPS:
        raise argparse.ArgumentTypeError(f"must be between {MIN_FPS} and {MAX_FPS}")
    return result


def _bounded_max_frames(value: str) -> int:
    result = _positive_int(value)
    if result > DEFAULT_MAX_FRAMES:
        raise argparse.ArgumentTypeError(f"must not exceed {DEFAULT_MAX_FRAMES}")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
