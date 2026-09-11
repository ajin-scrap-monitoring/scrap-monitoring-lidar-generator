"""Shared simulation-time events that cause spatial measurement distortions."""

import math
from dataclasses import dataclass
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

from scrap_monitoring_lidar_generator.geometry import Polygon2, SensorFrame, Vec2
from scrap_monitoring_lidar_generator.geometry.scene import HitKind
from scrap_monitoring_lidar_generator.measurement._randomness import (
    derive_global_seed,
    require_seed,
)
from scrap_monitoring_lidar_generator.measurement.models import TimedReferenceScan
from scrap_monitoring_lidar_generator.scenario import FillPlan, ScenarioPhase, ScenarioSimulator

type FloatArray = NDArray[np.float64]
type FloatRange = tuple[float, float]

_TIME_TOLERANCE_S = 1e-12


@dataclass(frozen=True, slots=True)
class FallingMaterialSettings:
    """Controls for fill-rate-proportional falling-material events."""

    event_rate_per_s: float
    radius_m_range: FloatRange
    duration_s_range: FloatRange
    distance_reduction_m_range: FloatRange
    inlet_positions: tuple[Vec2, ...]
    placement_radius_m: float

    def __post_init__(self) -> None:
        _require_non_negative(self.event_rate_per_s, "falling material event rate")
        _require_positive_range(self.radius_m_range, "falling material radius")
        _require_positive_range(self.duration_s_range, "falling material duration")
        _require_positive_range(
            self.distance_reduction_m_range,
            "falling material distance reduction",
        )
        if not self.inlet_positions:
            raise ValueError("falling material must contain at least one inlet position")
        _require_positive(self.placement_radius_m, "falling material placement radius")


@dataclass(frozen=True, slots=True)
class FallingMaterialEvent:
    """One shared falling-material region over a half-open time interval."""

    cycle_index: int
    started_at_s: float
    ends_at_s: float
    center: Vec2
    radius_m: float
    distance_reduction_m: float

    def __post_init__(self) -> None:
        if (
            isinstance(self.cycle_index, bool)
            or not isinstance(self.cycle_index, int)
            or self.cycle_index < 0
        ):
            raise ValueError("falling material cycle index must be a non-negative integer")
        if not math.isfinite(self.started_at_s) or self.started_at_s < 0.0:
            raise ValueError("falling material start must be finite and non-negative")
        if not math.isfinite(self.ends_at_s) or self.ends_at_s <= self.started_at_s:
            raise ValueError("falling material end must be finite and after its start")
        _require_positive(self.radius_m, "falling material radius")
        _require_positive(self.distance_reduction_m, "falling material distance reduction")


class SpatialDistanceResolver(Protocol):
    """Resolve shared spatial events for one sensor reference scan."""

    def resolve_distances(
        self,
        reference: TimedReferenceScan,
        *,
        frame: SensorFrame,
        min_distance_m: float,
    ) -> FloatArray:
        """Return one spatially resolved distance for every reference point."""
        ...


class SpatialDistortionTimeline:
    """Generate and retain shared spatial events needed by pending rotations."""

    __slots__ = (
        "_advanced_to_s",
        "_boundary",
        "_falling_events",
        "_falling_phase_key",
        "_falling_rng",
        "_falling_settings",
        "_next_falling_candidate_at_s",
    )

    def __init__(
        self,
        *,
        boundary: Polygon2,
        falling_material: FallingMaterialSettings | None,
        seed: int,
    ) -> None:
        require_seed(seed, "spatial distortion seed")
        if falling_material is not None and any(
            not boundary.contains(position) for position in falling_material.inlet_positions
        ):
            raise ValueError("falling material inlet positions must lie inside the boundary")
        self._boundary = boundary
        self._falling_settings = falling_material
        self._falling_rng = np.random.Generator(
            np.random.PCG64(derive_global_seed(seed, "falling-material"))
        )
        self._falling_events: list[FallingMaterialEvent] = []
        self._falling_phase_key: tuple[int, float] | None = None
        self._next_falling_candidate_at_s = math.inf
        self._advanced_to_s = 0.0

    @property
    def advanced_to_s(self) -> float:
        """Return the latest simulation time through which events were generated."""
        return self._advanced_to_s

    @property
    def falling_events(self) -> tuple[FallingMaterialEvent, ...]:
        """Return retained events in start-time order for diagnostics and tests."""
        return tuple(self._falling_events)

    def advance_to(self, elapsed_s: float, *, scenario: ScenarioSimulator) -> None:
        """Generate events before an interval end from the scenario state at its start."""
        if not math.isfinite(elapsed_s) or elapsed_s < self._advanced_to_s:
            raise ValueError("spatial distortion time must be finite and monotonic")
        if not math.isclose(
            scenario.elapsed_s,
            self._advanced_to_s,
            rel_tol=0.0,
            abs_tol=_TIME_TOLERANCE_S,
        ):
            raise ValueError("spatial distortion and scenario times must match")
        plan = scenario.phase_plan
        if elapsed_s > plan.ends_at_s + _TIME_TOLERANCE_S:
            raise ValueError("spatial distortion interval must not cross a phase boundary")

        settings = self._falling_settings
        snapshot = scenario.snapshot
        if settings is not None and snapshot.phase is ScenarioPhase.FILLING:
            if not isinstance(plan, FillPlan):
                raise RuntimeError("filling scenario must expose a fill plan")
            self._advance_falling_material(
                plan=plan,
                inlet_index=snapshot.current_inlet_index,
                through_s=elapsed_s,
            )
        self._advanced_to_s = elapsed_s

    def resolve_distances(
        self,
        reference: TimedReferenceScan,
        *,
        frame: SensorFrame,
        min_distance_m: float,
    ) -> FloatArray:
        """Resolve retained foreground events against one completed reference scan."""
        if reference.completed_at_s > self._advanced_to_s + _TIME_TOLERANCE_S:
            raise ValueError("spatial events must be advanced through the completed scan")
        return resolve_falling_material_distances(
            reference,
            frame=frame,
            events=self._falling_events,
            min_distance_m=min_distance_m,
        )

    def discard_before(self, elapsed_s: float) -> None:
        """Discard events that cannot overlap any pending measurement point."""
        if not math.isfinite(elapsed_s) or elapsed_s < 0.0:
            raise ValueError("spatial distortion retention time must be finite and non-negative")
        if elapsed_s > self._advanced_to_s + _TIME_TOLERANCE_S:
            raise ValueError("spatial distortion retention time must not exceed generated time")
        self._falling_events = [
            event for event in self._falling_events if event.ends_at_s > elapsed_s
        ]

    def _advance_falling_material(
        self,
        *,
        plan: FillPlan,
        inlet_index: int | None,
        through_s: float,
    ) -> None:
        settings = self._falling_settings
        if settings is None or settings.event_rate_per_s == 0.0:
            return
        if inlet_index is None:
            raise RuntimeError("filling scenario must expose an active inlet")
        phase_key = (plan.cycle_index, plan.started_at_s)
        maximum_factor = max(
            (1.0, *(1.0 + segment.deviation for segment in plan.rate_profile.segments))
        )
        maximum_rate_per_s = settings.event_rate_per_s * maximum_factor
        if self._falling_phase_key != phase_key:
            if not math.isclose(
                self._advanced_to_s,
                plan.started_at_s,
                rel_tol=0.0,
                abs_tol=_TIME_TOLERANCE_S,
            ):
                raise ValueError(
                    "falling material timeline must observe a fill phase from its start"
                )
            self._falling_phase_key = phase_key
            self._next_falling_candidate_at_s = plan.started_at_s + float(
                self._falling_rng.exponential(1.0 / maximum_rate_per_s)
            )

        while self._next_falling_candidate_at_s < through_s:
            candidate_at_s = self._next_falling_candidate_at_s
            factor = plan.rate_profile.factor_at(candidate_at_s - plan.started_at_s)
            if float(self._falling_rng.random()) < factor / maximum_factor:
                duration_s = float(self._falling_rng.uniform(*settings.duration_s_range))
                ends_at_s = min(plan.ends_at_s, candidate_at_s + duration_s)
                if ends_at_s > candidate_at_s:
                    self._falling_events.append(
                        FallingMaterialEvent(
                            cycle_index=plan.cycle_index,
                            started_at_s=candidate_at_s,
                            ends_at_s=ends_at_s,
                            center=self._sample_falling_center(inlet_index),
                            radius_m=float(self._falling_rng.uniform(*settings.radius_m_range)),
                            distance_reduction_m=float(
                                self._falling_rng.uniform(
                                    *settings.distance_reduction_m_range,
                                )
                            ),
                        )
                    )
            self._next_falling_candidate_at_s += float(
                self._falling_rng.exponential(1.0 / maximum_rate_per_s)
            )

    def _sample_falling_center(self, inlet_index: int) -> Vec2:
        settings = self._falling_settings
        if settings is None:
            raise RuntimeError("falling material settings are unavailable")
        inlet = settings.inlet_positions[inlet_index]
        for _ in range(32):
            distance_m = settings.placement_radius_m * math.sqrt(float(self._falling_rng.random()))
            angle_rad = float(self._falling_rng.random()) * math.tau
            candidate = Vec2(
                inlet.x + distance_m * math.cos(angle_rad),
                inlet.y + distance_m * math.sin(angle_rad),
            )
            if self._boundary.contains(candidate):
                return candidate
        return inlet


def resolve_falling_material_distances(
    reference: TimedReferenceScan,
    *,
    frame: SensorFrame,
    events: tuple[FallingMaterialEvent, ...] | list[FallingMaterialEvent],
    min_distance_m: float,
) -> FloatArray:
    """Choose the nearest active falling-material candidate for each surface hit."""
    minimum_distance_m = _require_positive(min_distance_m, "minimum measurement distance")
    reference_distances_m = np.fromiter(
        (point.distance_m for point in reference.scan.points),
        dtype=np.float64,
        count=len(reference.scan.points),
    )
    resolved_distances_m = reference_distances_m.copy()
    if not events:
        return resolved_distances_m

    surface_hits = np.fromiter(
        (point.hit_kind is HitKind.SURFACE for point in reference.scan.points),
        dtype=np.bool_,
        count=len(reference.scan.points),
    )
    rays = frame.ray_batch_at(reference.schedule.angles_deg)
    hit_x_m = rays.origins_m[:, 0] + rays.directions[:, 0] * reference_distances_m
    hit_y_m = rays.origins_m[:, 1] + rays.directions[:, 1] * reference_distances_m
    point_times_s = reference.schedule.point_elapsed_times_s

    for event in events:
        active = (
            surface_hits & (point_times_s >= event.started_at_s) & (point_times_s < event.ends_at_s)
        )
        if not bool(np.any(active)):
            continue
        inside_region = (
            np.square(hit_x_m - event.center.x) + np.square(hit_y_m - event.center.y)
            <= event.radius_m * event.radius_m
        )
        candidate_distance_m = reference_distances_m - event.distance_reduction_m
        selected = active & inside_region & (candidate_distance_m >= minimum_distance_m)
        resolved_distances_m[selected] = np.minimum(
            resolved_distances_m[selected],
            candidate_distance_m[selected],
        )
    return resolved_distances_m


def _require_non_negative(value: float, name: str) -> float:
    if isinstance(value, bool) or not math.isfinite(value) or value < 0.0:
        raise ValueError(f"{name} must be finite and non-negative")
    return value


def _require_positive(value: float, name: str) -> float:
    if isinstance(value, bool) or not math.isfinite(value) or value <= 0.0:
        raise ValueError(f"{name} must be finite and positive")
    return value


def _require_positive_range(value: FloatRange, name: str) -> FloatRange:
    lower, upper = value
    _require_positive(lower, name)
    _require_positive(upper, name)
    if upper < lower:
        raise ValueError(f"{name} range must be ordered")
    return value
