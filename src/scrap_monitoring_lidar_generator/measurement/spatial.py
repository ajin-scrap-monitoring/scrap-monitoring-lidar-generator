"""Shared simulation-time events that cause spatial measurement distortions."""

import math
from dataclasses import dataclass, replace
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

from scrap_monitoring_lidar_generator.geometry import EnvironmentScene, Polygon2, SensorFrame, Vec2
from scrap_monitoring_lidar_generator.geometry.intersections import validate_distance_bounds
from scrap_monitoring_lidar_generator.geometry.scene import HitKind
from scrap_monitoring_lidar_generator.measurement._randomness import (
    derive_global_seed,
    require_seed,
)
from scrap_monitoring_lidar_generator.measurement.models import TimedReferenceScan
from scrap_monitoring_lidar_generator.scenario import (
    CollectionPlan,
    FillPlan,
    HeightField,
    ScenarioPhase,
    ScenarioSimulator,
)

type FloatArray = NDArray[np.float64]
type FloatRange = tuple[float, float]

_TIME_TOLERANCE_S = 1e-12
_HEIGHT_TOLERANCE_M = 1e-12
_MAX_VOID_EVENT_ATTEMPTS = 8
_MAX_VOID_CENTER_ATTEMPTS = 32


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


@dataclass(frozen=True, slots=True)
class VoidSettings:
    """Coverage and lifetime controls for persistent surface voids."""

    surface_area_ratio: float
    radius_m_range: FloatRange
    duration_s_range: FloatRange
    cover_height_increase_m: float
    distance_increase_m_range: FloatRange

    def __post_init__(self) -> None:
        _require_ratio(self.surface_area_ratio, "void surface area ratio")
        _require_positive_range(self.radius_m_range, "void radius")
        _require_positive_range(self.duration_s_range, "void duration")
        _require_non_negative(self.cover_height_increase_m, "void cover height increase")
        _require_positive_range(self.distance_increase_m_range, "void distance increase")


@dataclass(frozen=True, slots=True)
class VoidEvent:
    """One persistent surface region with a possibly shortened lifetime."""

    cycle_index: int
    started_at_s: float
    expires_at_s: float
    ends_at_s: float
    center: Vec2
    surface_height_at_start_m: float
    radius_m: float
    distance_increase_m: float

    def __post_init__(self) -> None:
        _require_non_negative_integer(self.cycle_index, "void cycle index")
        if not math.isfinite(self.started_at_s) or self.started_at_s < 0.0:
            raise ValueError("void start must be finite and non-negative")
        if not math.isfinite(self.expires_at_s) or self.expires_at_s <= self.started_at_s:
            raise ValueError("void expiry must be finite and after its start")
        if (
            not math.isfinite(self.ends_at_s)
            or self.ends_at_s <= self.started_at_s
            or self.ends_at_s > self.expires_at_s
        ):
            raise ValueError("void end must be after its start and no later than expiry")
        if not math.isfinite(self.surface_height_at_start_m):
            raise ValueError("void starting surface height must be finite")
        _require_positive(self.radius_m, "void radius")
        _require_positive(self.distance_increase_m, "void distance increase")


@dataclass(frozen=True, slots=True)
class CollectionOcclusionSettings:
    """Schedule and shape controls for moving collection occlusions."""

    event_interval_s_range: FloatRange
    radius_m_range: FloatRange
    duration_s_range: FloatRange
    distance_reduction_m_range: FloatRange

    def __post_init__(self) -> None:
        _require_positive_range(self.event_interval_s_range, "collection event interval")
        _require_positive_range(self.radius_m_range, "collection occlusion radius")
        _require_positive_range(self.duration_s_range, "collection occlusion duration")
        _require_positive_range(
            self.distance_reduction_m_range,
            "collection occlusion distance reduction",
        )


@dataclass(frozen=True, slots=True)
class CollectionOcclusionEvent:
    """One collection-only occlusion moving along a straight center path."""

    cycle_index: int
    started_at_s: float
    ends_at_s: float
    start_center: Vec2
    end_center: Vec2
    radius_m: float
    distance_reduction_m: float

    def __post_init__(self) -> None:
        _require_non_negative_integer(self.cycle_index, "collection occlusion cycle index")
        if not math.isfinite(self.started_at_s) or self.started_at_s < 0.0:
            raise ValueError("collection occlusion start must be finite and non-negative")
        if not math.isfinite(self.ends_at_s) or self.ends_at_s <= self.started_at_s:
            raise ValueError("collection occlusion end must be finite and after its start")
        _require_positive(self.radius_m, "collection occlusion radius")
        _require_positive(
            self.distance_reduction_m,
            "collection occlusion distance reduction",
        )

    def center_at(self, elapsed_s: float) -> Vec2:
        """Return the linearly interpolated center during the event lifetime."""
        if not math.isfinite(elapsed_s) or not self.started_at_s <= elapsed_s <= self.ends_at_s:
            raise ValueError("collection occlusion time must be within the event")
        progress = (elapsed_s - self.started_at_s) / (self.ends_at_s - self.started_at_s)
        return Vec2(
            self.start_center.x + progress * (self.end_center.x - self.start_center.x),
            self.start_center.y + progress * (self.end_center.y - self.start_center.y),
        )


class SpatialDistanceResolver(Protocol):
    """Resolve shared spatial events for one sensor reference scan."""

    def resolve_distances(
        self,
        reference: TimedReferenceScan,
        *,
        frame: SensorFrame,
        min_distance_m: float,
        max_distance_m: float,
    ) -> FloatArray:
        """Return one spatially resolved distance for every reference point."""
        ...


class SpatialDistortionTimeline:
    """Generate and retain shared spatial events needed by pending rotations."""

    __slots__ = (
        "_advanced_to_s",
        "_boundary",
        "_collection_events",
        "_collection_phase_key",
        "_collection_rng",
        "_collection_settings",
        "_falling_events",
        "_falling_phase_key",
        "_falling_rng",
        "_falling_settings",
        "_next_collection_event_at_s",
        "_next_falling_candidate_at_s",
        "_static_scene",
        "_void_events",
        "_void_rng",
        "_void_settings",
        "_x_bounds_m",
        "_y_bounds_m",
    )

    def __init__(
        self,
        *,
        boundary: Polygon2,
        static_scene: EnvironmentScene,
        falling_material: FallingMaterialSettings | None,
        voids: VoidSettings | None = None,
        collection_occlusion: CollectionOcclusionSettings | None = None,
        seed: int,
    ) -> None:
        require_seed(seed, "spatial distortion seed")
        if falling_material is not None and any(
            not boundary.contains(position) for position in falling_material.inlet_positions
        ):
            raise ValueError("falling material inlet positions must lie inside the boundary")
        if static_scene.boundary != boundary or static_scene.dynamic_surface is not None:
            raise ValueError("spatial distortion static scene must match the boundary")
        self._boundary = boundary
        self._static_scene = static_scene
        self._falling_settings = falling_material
        self._falling_rng = np.random.Generator(
            np.random.PCG64(derive_global_seed(seed, "falling-material"))
        )
        self._falling_events: list[FallingMaterialEvent] = []
        self._falling_phase_key: tuple[int, float] | None = None
        self._next_falling_candidate_at_s = math.inf
        self._collection_settings = collection_occlusion
        self._collection_rng = np.random.Generator(
            np.random.PCG64(derive_global_seed(seed, "collection-occlusion"))
        )
        self._collection_events: list[CollectionOcclusionEvent] = []
        self._collection_phase_key: tuple[int, float] | None = None
        self._next_collection_event_at_s = math.inf
        self._void_settings = voids
        self._void_rng = np.random.Generator(np.random.PCG64(derive_global_seed(seed, "voids")))
        self._void_events: list[VoidEvent] = []
        self._x_bounds_m = (
            min(vertex.x for vertex in boundary.vertices),
            max(vertex.x for vertex in boundary.vertices),
        )
        self._y_bounds_m = (
            min(vertex.y for vertex in boundary.vertices),
            max(vertex.y for vertex in boundary.vertices),
        )
        self._advanced_to_s = 0.0

    @property
    def advanced_to_s(self) -> float:
        """Return the latest simulation time through which events were generated."""
        return self._advanced_to_s

    @property
    def falling_events(self) -> tuple[FallingMaterialEvent, ...]:
        """Return retained events in start-time order for diagnostics and tests."""
        return tuple(self._falling_events)

    @property
    def void_events(self) -> tuple[VoidEvent, ...]:
        """Return retained void events in creation order for diagnostics and tests."""
        return tuple(self._void_events)

    @property
    def collection_events(self) -> tuple[CollectionOcclusionEvent, ...]:
        """Return retained collection occlusions in start-time order."""
        return tuple(self._collection_events)

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
        self._synchronize_voids(scenario)
        if settings is not None and snapshot.phase is ScenarioPhase.FILLING:
            if not isinstance(plan, FillPlan):
                raise RuntimeError("filling scenario must expose a fill plan")
            self._advance_falling_material(
                plan=plan,
                inlet_index=snapshot.current_inlet_index,
                through_s=elapsed_s,
            )
        if self._void_settings is not None and snapshot.phase is ScenarioPhase.FILLING:
            self._maintain_void_coverage(
                scenario=scenario,
                through_s=elapsed_s,
            )
        if self._collection_settings is not None and snapshot.phase is ScenarioPhase.COLLECTING:
            if not isinstance(plan, CollectionPlan):
                raise RuntimeError("collecting scenario must expose a collection plan")
            self._advance_collection_occlusion(plan=plan, through_s=elapsed_s)
        self._advanced_to_s = elapsed_s

    def resolve_distances(
        self,
        reference: TimedReferenceScan,
        *,
        frame: SensorFrame,
        min_distance_m: float,
        max_distance_m: float,
    ) -> FloatArray:
        """Resolve retained spatial events against one completed reference scan."""
        if reference.completed_at_s > self._advanced_to_s + _TIME_TOLERANCE_S:
            raise ValueError("spatial events must be advanced through the completed scan")
        void_distances_m = resolve_void_distances(
            reference,
            frame=frame,
            events=self._void_events,
            min_distance_m=min_distance_m,
            max_distance_m=max_distance_m,
            static_scene=self._static_scene,
        )
        falling_distances_m = resolve_falling_material_distances(
            reference,
            frame=frame,
            events=self._falling_events,
            min_distance_m=min_distance_m,
        )
        collection_distances_m = resolve_collection_occlusion_distances(
            reference,
            frame=frame,
            events=self._collection_events,
            min_distance_m=min_distance_m,
        )
        reference_distances_m = np.fromiter(
            (point.distance_m for point in reference.scan.points),
            dtype=np.float64,
            count=len(reference.scan.points),
        )
        foreground_distances_m = np.minimum(
            falling_distances_m,
            collection_distances_m,
        )
        foreground = foreground_distances_m < reference_distances_m
        void_distances_m[foreground] = np.minimum(
            void_distances_m[foreground],
            foreground_distances_m[foreground],
        )
        return void_distances_m

    def discard_before(self, elapsed_s: float) -> None:
        """Discard events that cannot overlap any pending measurement point."""
        if not math.isfinite(elapsed_s) or elapsed_s < 0.0:
            raise ValueError("spatial distortion retention time must be finite and non-negative")
        if elapsed_s > self._advanced_to_s + _TIME_TOLERANCE_S:
            raise ValueError("spatial distortion retention time must not exceed generated time")
        self._falling_events = [
            event for event in self._falling_events if event.ends_at_s > elapsed_s
        ]
        self._void_events = [event for event in self._void_events if event.ends_at_s > elapsed_s]
        self._collection_events = [
            event for event in self._collection_events if event.ends_at_s > elapsed_s
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

    def _advance_collection_occlusion(
        self,
        *,
        plan: CollectionPlan,
        through_s: float,
    ) -> None:
        settings = self._collection_settings
        if settings is None:
            return
        phase_key = (plan.cycle_index, plan.started_at_s)
        if self._collection_phase_key != phase_key:
            if not math.isclose(
                self._advanced_to_s,
                plan.started_at_s,
                rel_tol=0.0,
                abs_tol=_TIME_TOLERANCE_S,
            ):
                raise ValueError(
                    "collection occlusion timeline must observe a collection phase from its start"
                )
            self._collection_phase_key = phase_key
            self._next_collection_event_at_s = plan.started_at_s + float(
                self._collection_rng.uniform(*settings.event_interval_s_range)
            )

        while self._next_collection_event_at_s < through_s:
            event_at_s = self._next_collection_event_at_s
            radius_m = float(self._collection_rng.uniform(*settings.radius_m_range))
            path = self._sample_collection_path(radius_m)
            if path is not None:
                duration_s = float(self._collection_rng.uniform(*settings.duration_s_range))
                ends_at_s = min(plan.ends_at_s, event_at_s + duration_s)
                if ends_at_s > event_at_s:
                    self._collection_events.append(
                        CollectionOcclusionEvent(
                            cycle_index=plan.cycle_index,
                            started_at_s=event_at_s,
                            ends_at_s=ends_at_s,
                            start_center=path[0],
                            end_center=path[1],
                            radius_m=radius_m,
                            distance_reduction_m=float(
                                self._collection_rng.uniform(
                                    *settings.distance_reduction_m_range,
                                )
                            ),
                        )
                    )
            self._next_collection_event_at_s += float(
                self._collection_rng.uniform(*settings.event_interval_s_range)
            )

    def _sample_collection_path(self, radius_m: float) -> tuple[Vec2, Vec2] | None:
        for _ in range(64):
            start = self._sample_clear_point(radius_m)
            end = self._sample_clear_point(radius_m)
            if start is None or end is None:
                continue
            if _minimum_path_boundary_distance(start, end, self._boundary) >= radius_m:
                return start, end
        return None

    def _sample_clear_point(self, radius_m: float) -> Vec2 | None:
        for _ in range(16):
            candidate = Vec2(
                float(self._collection_rng.uniform(*self._x_bounds_m)),
                float(self._collection_rng.uniform(*self._y_bounds_m)),
            )
            if self._boundary.contains(candidate) and (
                _minimum_boundary_distance(candidate, self._boundary) >= radius_m
            ):
                return candidate
        return None

    def _synchronize_voids(self, scenario: ScenarioSimulator) -> None:
        settings = self._void_settings
        if settings is None:
            return
        at_s = self._advanced_to_s
        snapshot = scenario.snapshot
        for index, event in enumerate(self._void_events):
            if not event.started_at_s <= at_s < event.ends_at_s:
                continue
            close = snapshot.phase is ScenarioPhase.COLLECTING
            if not close:
                current_height_m = scenario.surface.height_at(event.center)
                height_increase_m = current_height_m - event.surface_height_at_start_m
                close = height_increase_m > _HEIGHT_TOLERANCE_M and (
                    height_increase_m + _HEIGHT_TOLERANCE_M >= settings.cover_height_increase_m
                )
            if close:
                self._void_events[index] = replace(event, ends_at_s=at_s)

    def _maintain_void_coverage(
        self,
        *,
        scenario: ScenarioSimulator,
        through_s: float,
    ) -> None:
        cursor_s = self._advanced_to_s
        while True:
            self._fill_void_coverage(scenario=scenario, at_s=cursor_s)
            next_expiry_s = min(
                (
                    event.ends_at_s
                    for event in self._void_events
                    if event.started_at_s <= cursor_s < event.ends_at_s
                ),
                default=math.inf,
            )
            if next_expiry_s >= through_s:
                return
            cursor_s = next_expiry_s

    def _fill_void_coverage(
        self,
        *,
        scenario: ScenarioSimulator,
        at_s: float,
    ) -> None:
        settings = self._void_settings
        if settings is None or settings.surface_area_ratio == 0.0:
            return
        target_area_m2 = scenario.surface.surface_area_m2 * settings.surface_area_ratio
        active_events = [
            event for event in self._void_events if event.started_at_s <= at_s < event.ends_at_s
        ]
        active_area_m2 = sum(math.pi * event.radius_m * event.radius_m for event in active_events)
        failed_attempts = 0
        while active_area_m2 < target_area_m2 and failed_attempts < _MAX_VOID_EVENT_ATTEMPTS:
            event = self._sample_void_event(
                scenario=scenario,
                at_s=at_s,
                active_events=active_events,
            )
            if event is None:
                failed_attempts += 1
                continue
            self._void_events.append(event)
            active_events.append(event)
            active_area_m2 += math.pi * event.radius_m * event.radius_m

    def _sample_void_event(
        self,
        *,
        scenario: ScenarioSimulator,
        at_s: float,
        active_events: list[VoidEvent],
    ) -> VoidEvent | None:
        settings = self._void_settings
        if settings is None:
            raise RuntimeError("void settings are unavailable")
        radius_m = float(self._void_rng.uniform(*settings.radius_m_range))
        center = self._sample_void_center(
            surface=scenario.surface,
            radius_m=radius_m,
            active_events=active_events,
        )
        if center is None:
            return None
        plan = scenario.phase_plan
        duration_s = float(self._void_rng.uniform(*settings.duration_s_range))
        expires_at_s = min(plan.ends_at_s, at_s + duration_s)
        if expires_at_s <= at_s:
            return None
        return VoidEvent(
            cycle_index=scenario.snapshot.cycle_index,
            started_at_s=at_s,
            expires_at_s=expires_at_s,
            ends_at_s=expires_at_s,
            center=center,
            surface_height_at_start_m=scenario.surface.height_at(center),
            radius_m=radius_m,
            distance_increase_m=float(self._void_rng.uniform(*settings.distance_increase_m_range)),
        )

    def _sample_void_center(
        self,
        *,
        surface: HeightField,
        radius_m: float,
        active_events: list[VoidEvent],
    ) -> Vec2 | None:
        for _ in range(_MAX_VOID_CENTER_ATTEMPTS):
            candidate = Vec2(
                float(self._void_rng.uniform(*self._x_bounds_m)),
                float(self._void_rng.uniform(*self._y_bounds_m)),
            )
            if not self._boundary.contains(candidate):
                continue
            if _minimum_boundary_distance(candidate, self._boundary) < radius_m:
                continue
            if any(
                math.hypot(
                    candidate.x - event.center.x,
                    candidate.y - event.center.y,
                )
                < radius_m + event.radius_m
                for event in active_events
            ):
                continue
            height_at = surface.height_at(candidate)
            if height_at <= surface.floor_z_m + _HEIGHT_TOLERANCE_M:
                continue
            return candidate
        return None


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


def resolve_collection_occlusion_distances(
    reference: TimedReferenceScan,
    *,
    frame: SensorFrame,
    events: tuple[CollectionOcclusionEvent, ...] | list[CollectionOcclusionEvent],
    min_distance_m: float,
) -> FloatArray:
    """Choose the nearest moving collection occlusion for each surface hit."""
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
        progress = (point_times_s - event.started_at_s) / (event.ends_at_s - event.started_at_s)
        center_x_m = event.start_center.x + progress * (event.end_center.x - event.start_center.x)
        center_y_m = event.start_center.y + progress * (event.end_center.y - event.start_center.y)
        inside_region = (
            np.square(hit_x_m - center_x_m) + np.square(hit_y_m - center_y_m)
            <= event.radius_m * event.radius_m
        )
        candidate_distance_m = reference_distances_m - event.distance_reduction_m
        selected = active & inside_region & (candidate_distance_m >= minimum_distance_m)
        resolved_distances_m[selected] = np.minimum(
            resolved_distances_m[selected],
            candidate_distance_m[selected],
        )
    return resolved_distances_m


def resolve_void_distances(
    reference: TimedReferenceScan,
    *,
    frame: SensorFrame,
    events: tuple[VoidEvent, ...] | list[VoidEvent],
    min_distance_m: float,
    max_distance_m: float,
    static_scene: EnvironmentScene,
) -> FloatArray:
    """Choose the nearest valid inner reflection for each active void region."""
    validate_distance_bounds(min_distance_m, max_distance_m)
    if min_distance_m == 0.0 or not math.isfinite(max_distance_m):
        raise ValueError("void resolution requires finite positive measurement bounds")
    if static_scene.dynamic_surface is not None:
        raise ValueError("void resolution requires a static environment scene")

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
    static_distances_m = static_scene.first_hit_batch(
        rays,
        min_distance_m=min_distance_m,
        max_distance_m=max_distance_m,
    ).distances_m
    point_times_s = reference.schedule.point_elapsed_times_s
    void_selected = np.zeros(reference_distances_m.size, dtype=np.bool_)

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
        candidate_distance_m = reference_distances_m + event.distance_increase_m
        valid_candidate = (candidate_distance_m <= max_distance_m) & (
            candidate_distance_m <= static_distances_m
        )
        selected = active & inside_region & valid_candidate
        closer = selected & (~void_selected | (candidate_distance_m < resolved_distances_m))
        resolved_distances_m[closer] = candidate_distance_m[closer]
        void_selected[selected] = True
    return resolved_distances_m


def _minimum_boundary_distance(point: Vec2, boundary: Polygon2) -> float:
    return min(_point_segment_distance(point, start, end) for start, end in boundary.edges)


def _minimum_path_boundary_distance(start: Vec2, end: Vec2, boundary: Polygon2) -> float:
    return min(
        _segment_distance(start, end, edge_start, edge_end)
        for edge_start, edge_end in boundary.edges
    )


def _segment_distance(
    first_start: Vec2, first_end: Vec2, second_start: Vec2, second_end: Vec2
) -> float:
    if _segments_intersect(first_start, first_end, second_start, second_end):
        return 0.0
    return min(
        _point_segment_distance(first_start, second_start, second_end),
        _point_segment_distance(first_end, second_start, second_end),
        _point_segment_distance(second_start, first_start, first_end),
        _point_segment_distance(second_end, first_start, first_end),
    )


def _segments_intersect(
    first_start: Vec2, first_end: Vec2, second_start: Vec2, second_end: Vec2
) -> bool:
    first_to_end = first_end - first_start
    second_to_start = second_start - first_start
    second_to_end = second_end - first_start
    second_edge = second_end - second_start
    first_from_second = first_start - second_start
    first_end_from_second = first_end - second_start
    first_cross_start = first_to_end.cross(second_to_start)
    first_cross_end = first_to_end.cross(second_to_end)
    second_cross_start = second_edge.cross(first_from_second)
    second_cross_end = second_edge.cross(first_end_from_second)
    tolerance = 1e-12
    if (
        first_cross_start * first_cross_end < -tolerance
        and second_cross_start * second_cross_end < -tolerance
    ):
        return True
    return (
        (
            abs(first_cross_start) <= tolerance
            and _point_on_segment(second_start, first_start, first_end)
        )
        or (
            abs(first_cross_end) <= tolerance
            and _point_on_segment(second_end, first_start, first_end)
        )
        or (
            abs(second_cross_start) <= tolerance
            and _point_on_segment(first_start, second_start, second_end)
        )
        or (
            abs(second_cross_end) <= tolerance
            and _point_on_segment(first_end, second_start, second_end)
        )
    )


def _point_on_segment(point: Vec2, start: Vec2, end: Vec2) -> bool:
    tolerance = 1e-12
    return (
        min(start.x, end.x) - tolerance <= point.x <= max(start.x, end.x) + tolerance
        and min(start.y, end.y) - tolerance <= point.y <= max(start.y, end.y) + tolerance
    )


def _point_segment_distance(point: Vec2, start: Vec2, end: Vec2) -> float:
    edge_x = end.x - start.x
    edge_y = end.y - start.y
    squared_length = edge_x * edge_x + edge_y * edge_y
    if squared_length == 0.0:
        return math.hypot(point.x - start.x, point.y - start.y)
    projection = min(
        1.0,
        max(
            0.0,
            ((point.x - start.x) * edge_x + (point.y - start.y) * edge_y) / squared_length,
        ),
    )
    closest_x = start.x + projection * edge_x
    closest_y = start.y + projection * edge_y
    return math.hypot(point.x - closest_x, point.y - closest_y)


def _require_non_negative_integer(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _require_non_negative(value: float, name: str) -> float:
    if isinstance(value, bool) or not math.isfinite(value) or value < 0.0:
        raise ValueError(f"{name} must be finite and non-negative")
    return value


def _require_ratio(value: float, name: str) -> float:
    result = _require_non_negative(value, name)
    if result > 1.0:
        raise ValueError(f"{name} must be between 0 and 1")
    return result


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
