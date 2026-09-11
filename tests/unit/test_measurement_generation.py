"""Tests for deterministic distance noise and quality generation."""

from collections.abc import Iterable

import numpy as np
import pytest
from numpy.typing import NDArray

from scrap_monitoring_lidar_generator.geometry import HitKind, SensorFrame, Vec3
from scrap_monitoring_lidar_generator.measurement import (
    MeasurementGenerator,
    ReferencePoint,
    ReferenceScan,
    ScheduledScan,
    SensorDropoutScheduler,
    SpatialDistanceResolver,
    TimedReferenceScan,
)


def _frequencies(*entries: tuple[int, int]) -> tuple[int, ...]:
    result = [0] * 256
    for quality, frequency in entries:
        result[quality] = frequency
    return tuple(result)


def _reference(
    distances_m: Iterable[float],
    *,
    sensor_id: str = "sensor-a",
    scan_id: int = 1,
    hit_kinds: Iterable[HitKind | None] | None = None,
) -> TimedReferenceScan:
    distances = tuple(float(distance_m) for distance_m in distances_m)
    count = len(distances)
    kinds = (
        tuple(hit_kinds)
        if hit_kinds is not None
        else tuple(HitKind.WALL if distance_m > 0.0 else None for distance_m in distances)
    )
    if len(kinds) != count:
        raise ValueError("test hit kinds must match distance count")
    angles_deg = np.arange(count, dtype=np.float64) * (360.0 / count)
    schedule = ScheduledScan(
        sensor_id=sensor_id,
        scan_id=scan_id,
        rotation_started_at_s=float(scan_id - 1),
        completed_at_s=float(scan_id),
        angles_deg=angles_deg,
        point_elapsed_times_s=float(scan_id - 1) + np.arange(count, dtype=np.float64) / count,
    )
    return TimedReferenceScan(
        schedule=schedule,
        scan=ReferenceScan(
            sensor_id=sensor_id,
            points=tuple(
                ReferencePoint(angle_deg=float(angle_deg), distance_m=distance_m, hit_kind=kind)
                for angle_deg, distance_m, kind in zip(
                    angles_deg,
                    distances,
                    kinds,
                    strict=True,
                )
            ),
        ),
    )


def _generator(
    *,
    sensor_id: str = "sensor-a",
    seed: int = 123,
    noise_enabled: bool = False,
    standard_deviation_m: float = 0.1,
    limit_m: float = 0.2,
    valid_frequencies: tuple[int, ...] | None = None,
    invalid_frequencies: tuple[int, ...] | None = None,
    reflection_error_enabled: bool = False,
    reflection_error_probability: float = 0.0,
    reflection_error_reduction_range_m: tuple[float, float] = (0.1, 0.2),
    dropout_scheduler: SensorDropoutScheduler | None = None,
    spatial_distortions: SpatialDistanceResolver | None = None,
    sensor_frame: SensorFrame | None = None,
) -> MeasurementGenerator:
    return MeasurementGenerator(
        sensor_id=sensor_id,
        min_distance_m=1.0,
        max_distance_m=10.0,
        noise_enabled=noise_enabled,
        noise_standard_deviation_m=standard_deviation_m,
        noise_limit_m=limit_m,
        valid_quality_frequencies=valid_frequencies or _frequencies((64, 1)),
        invalid_quality_frequencies=invalid_frequencies or _frequencies((7, 1)),
        reflection_error_enabled=reflection_error_enabled,
        reflection_error_probability=reflection_error_probability,
        reflection_error_reduction_range_m=reflection_error_reduction_range_m,
        dropout_scheduler=dropout_scheduler,
        seed=seed,
        spatial_distortions=spatial_distortions,
        sensor_frame=sensor_frame,
    )


def test_preserves_reference_and_generates_final_arrays() -> None:
    reference = _reference((2.0, 0.0, 10.0))

    result = _generator().generate(reference)

    assert result.reference is reference
    assert result.measured.schedule is reference.schedule
    assert result.sensor_id == "sensor-a"
    assert result.scan_id == 1
    assert result.measured.scan.distances_m.tolist() == [2.0, 0.0, 10.0]
    assert result.measured.scan.qualities.tolist() == [64, 7, 64]
    assert not result.measured.scan.angles_deg.flags.writeable
    assert not result.measured.scan.distances_m.flags.writeable
    assert not result.measured.scan.qualities.flags.writeable


def test_revalidates_measurement_range_without_noise() -> None:
    reference = _reference((0.5, 1.0, 10.0, 11.0))

    measured = _generator().generate(reference).measured.scan

    assert measured.distances_m.tolist() == [0.0, 1.0, 10.0, 0.0]
    assert measured.qualities.tolist() == [7, 64, 64, 7]


def test_noise_is_truncated_and_can_invalidate_a_boundary_distance() -> None:
    interior_reference = _reference((5.0,) * 4_000)
    boundary_reference = _reference((1.0,) * 1_000, scan_id=2)
    generator = _generator(noise_enabled=True, standard_deviation_m=1.0, limit_m=0.2)

    interior = generator.generate(interior_reference).measured.scan
    boundary = generator.generate(boundary_reference).measured.scan

    errors_m = interior.distances_m - 5.0
    assert bool(np.all(np.abs(errors_m) <= 0.2))
    assert not bool(np.any(np.abs(errors_m) == 0.2))
    assert abs(float(np.mean(errors_m))) < 0.01
    assert bool(np.any(boundary.distances_m == 0.0))
    assert bool(np.any(boundary.distances_m > 1.0))
    assert bool(np.all(boundary.qualities[boundary.distances_m == 0.0] == 7))
    assert bool(np.all(boundary.qualities[boundary.distances_m > 0.0] == 64))


def test_quality_sampling_follows_configured_frequencies() -> None:
    reference = _reference((5.0,) * 8_000)
    generator = _generator(
        valid_frequencies=_frequencies((10, 1), (20, 3)),
    )

    qualities = generator.generate(reference).measured.scan.qualities

    assert set(int(value) for value in np.unique(qualities)) == {10, 20}
    assert 0.72 < float(np.mean(qualities == 20)) < 0.78


def test_same_seed_reproduces_consecutive_measurement_scans() -> None:
    generators = [_generator(noise_enabled=True, seed=456) for _ in range(2)]
    references = [_reference((5.0,) * 100, scan_id=index) for index in (1, 2)]

    first_sequence = [generators[0].generate(reference) for reference in references]
    second_sequence = [generators[1].generate(reference) for reference in references]

    for first, second in zip(first_sequence, second_sequence, strict=True):
        assert np.array_equal(first.measured.scan.distances_m, second.measured.scan.distances_m)
        assert np.array_equal(first.measured.scan.qualities, second.measured.scan.qualities)
    assert not np.array_equal(
        first_sequence[0].measured.scan.distances_m,
        first_sequence[1].measured.scan.distances_m,
    )


def test_noise_configuration_does_not_change_quality_random_stream() -> None:
    reference = _reference((5.0,) * 1_000)
    valid_frequencies = _frequencies((10, 1), (20, 1), (30, 1))
    without_noise = _generator(valid_frequencies=valid_frequencies)
    with_noise = _generator(
        noise_enabled=True,
        standard_deviation_m=0.1,
        limit_m=0.2,
        valid_frequencies=valid_frequencies,
    )

    plain = without_noise.generate(reference).measured.scan
    noisy = with_noise.generate(reference).measured.scan

    assert np.array_equal(plain.qualities, noisy.qualities)
    assert not np.array_equal(plain.distances_m, noisy.distances_m)


def test_reflection_error_only_shortens_eligible_surface_hits() -> None:
    reference = _reference(
        (5.0, 5.0, 5.0, 1.5, 0.0),
        hit_kinds=(HitKind.SURFACE, HitKind.WALL, HitKind.FLOOR, HitKind.SURFACE, None),
    )
    generator = _generator(
        reflection_error_enabled=True,
        reflection_error_probability=1.0,
        reflection_error_reduction_range_m=(1.0, 2.0),
    )

    measured = generator.generate(reference).measured.scan

    assert 3.0 <= measured.distances_m[0] <= 4.0
    assert measured.distances_m[1:].tolist() == [5.0, 5.0, 1.5, 0.0]
    assert measured.qualities.tolist() == [64, 64, 64, 64, 7]


def test_reflection_error_precedes_noise_without_changing_other_random_streams() -> None:
    reference = _reference(
        (5.0,) * 1_000,
        hit_kinds=(HitKind.SURFACE,) * 1_000,
    )
    plain_generator = _generator(
        noise_enabled=True,
        valid_frequencies=_frequencies((10, 1), (20, 1)),
    )
    reflection_generator = _generator(
        noise_enabled=True,
        valid_frequencies=_frequencies((10, 1), (20, 1)),
        reflection_error_enabled=True,
        reflection_error_probability=1.0,
        reflection_error_reduction_range_m=(1.0, 1.0),
    )

    plain = plain_generator.generate(reference).measured.scan
    reflected = reflection_generator.generate(reference).measured.scan

    np.testing.assert_allclose(
        reflected.distances_m,
        plain.distances_m - 1.0,
        rtol=0.0,
        atol=1e-15,
    )
    assert np.array_equal(reflected.qualities, plain.qualities)


def test_spatial_foreground_and_reflection_error_select_nearest_without_stacking() -> None:
    class FixedSpatialResolver:
        def resolve_distances(
            self,
            reference: TimedReferenceScan,
            *,
            frame: SensorFrame,
            min_distance_m: float,
        ) -> NDArray[np.float64]:
            del frame, min_distance_m
            return np.fromiter(
                (point.distance_m - 0.5 for point in reference.scan.points),
                dtype=np.float64,
                count=len(reference.scan.points),
            )

    reference = _reference((5.0,) * 100, hit_kinds=(HitKind.SURFACE,) * 100)
    frame = SensorFrame(
        origin_m=Vec3(0.0, 0.0, 5.0),
        u0=Vec3(0.0, 0.0, -1.0),
        u90=Vec3(1.0, 0.0, 0.0),
    )
    generator = _generator(
        reflection_error_enabled=True,
        reflection_error_probability=1.0,
        reflection_error_reduction_range_m=(0.25, 0.25),
        spatial_distortions=FixedSpatialResolver(),
        sensor_frame=frame,
    )

    measured = generator.generate(reference).measured.scan

    np.testing.assert_array_equal(measured.distances_m, np.full(100, 4.5))


def test_dropout_overrides_distance_and_uses_invalid_quality_distribution() -> None:
    reference = _reference((5.0,) * 8)
    dropout = SensorDropoutScheduler(
        sensor_id="sensor-a",
        event_interval_s_range=(0.25, 0.25),
        duration_s_range=(0.25, 0.25),
        seed=123,
    )

    measured = _generator(dropout_scheduler=dropout).generate(reference).measured.scan

    assert measured.distances_m.tolist() == [5.0, 5.0, 0.0, 0.0, 5.0, 5.0, 0.0, 0.0]
    assert measured.qualities.tolist() == [64, 64, 7, 7, 64, 64, 7, 7]


def test_rejects_dropout_scheduler_for_another_sensor() -> None:
    dropout = SensorDropoutScheduler(
        sensor_id="sensor-b",
        event_interval_s_range=(1.0, 1.0),
        duration_s_range=(1.0, 1.0),
        seed=123,
    )

    with pytest.raises(ValueError, match="dropout sensor identifiers"):
        _generator(sensor_id="sensor-a", dropout_scheduler=dropout)


def test_sensor_identifier_derives_an_independent_noise_stream() -> None:
    first = _generator(sensor_id="sensor-a", noise_enabled=True).generate(
        _reference((5.0,) * 100, sensor_id="sensor-a")
    )
    second = _generator(sensor_id="sensor-b", noise_enabled=True).generate(
        _reference((5.0,) * 100, sensor_id="sensor-b")
    )

    assert not np.array_equal(first.measured.scan.distances_m, second.measured.scan.distances_m)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"sensor_id": ""}, "sensor_id"),
        ({"noise_enabled": 1}, "boolean"),
        ({"noise_standard_deviation_m": -1.0}, "standard deviation"),
        ({"noise_limit_m": float("inf")}, "noise limit"),
        ({"reflection_error_enabled": 1}, "reflection error enabled"),
        ({"reflection_error_probability": 1.1}, "probability"),
        ({"reflection_error_reduction_range_m": (0.0, 1.0)}, "positive"),
        ({"reflection_error_reduction_range_m": (2.0, 1.0)}, "ordered"),
        ({"seed": True}, "seed"),
        ({"valid_quality_frequencies": (1,)}, "exactly 256"),
        ({"invalid_quality_frequencies": (0,) * 256}, "positive"),
    ],
)
def test_rejects_invalid_generator_settings(
    overrides: dict[str, object],
    message: str,
) -> None:
    arguments: dict[str, object] = {
        "sensor_id": "sensor-a",
        "min_distance_m": 1.0,
        "max_distance_m": 10.0,
        "noise_enabled": False,
        "noise_standard_deviation_m": 0.1,
        "noise_limit_m": 0.2,
        "valid_quality_frequencies": _frequencies((64, 1)),
        "invalid_quality_frequencies": _frequencies((7, 1)),
        "reflection_error_enabled": False,
        "reflection_error_probability": 0.0,
        "reflection_error_reduction_range_m": (0.1, 0.2),
        "dropout_scheduler": None,
        "seed": 123,
    }
    arguments.update(overrides)

    with pytest.raises(ValueError, match=message):
        MeasurementGenerator(**arguments)  # type: ignore[arg-type]


def test_rejects_reference_from_another_sensor() -> None:
    with pytest.raises(ValueError, match="sensor identifiers"):
        _generator(sensor_id="sensor-a").generate(_reference((5.0,), sensor_id="sensor-b"))


def test_rejects_spatial_resolver_without_sensor_frame() -> None:
    class UnusedSpatialResolver:
        def resolve_distances(
            self,
            reference: TimedReferenceScan,
            *,
            frame: SensorFrame,
            min_distance_m: float,
        ) -> NDArray[np.float64]:
            del frame, min_distance_m
            return np.fromiter(
                (point.distance_m for point in reference.scan.points),
                dtype=np.float64,
                count=len(reference.scan.points),
            )

    with pytest.raises(ValueError, match="configured together"):
        _generator(spatial_distortions=UnusedSpatialResolver())
