"""Deterministic fill and collection cycle state transitions."""

import hashlib
import math
import random
from dataclasses import dataclass
from enum import StrEnum

from scrap_monitoring_lidar_generator.geometry import Vec2
from scrap_monitoring_lidar_generator.scenario.height_field import HeightField
from scrap_monitoring_lidar_generator.scenario.rate_profile import (
    SmoothRateProfile,
    create_smooth_rate_profile,
)

type FloatRange = tuple[float, float]

_MAX_SEED = 18_446_744_073_709_551_615
_STATE_TOLERANCE = 1e-12


class ScenarioPhase(StrEnum):
    """Mutually exclusive phases of one scenario cycle."""

    FILLING = "filling"
    COLLECTING = "collecting"


@dataclass(frozen=True, slots=True)
class ScenarioSettings:
    """Scenario-domain settings independent of the configuration representation."""

    mean_fill_duration_s: float
    fill_duration_factor_range: FloatRange
    fill_rate_factor_range: FloatRange
    fill_rate_change_duration_s_range: FloatRange
    collection_threshold_range: FloatRange
    collection_duration_factor_range: FloatRange
    collection_rate_factor_range: FloatRange
    collection_rate_change_duration_s_range: FloatRange
    inlet_positions: tuple[Vec2, ...]
    inlet_switch_activation_ratio: float
    inlet_switch_height_difference_m: float
    inlet_comparison_radius_m: float
    surface_update_interval_s: float
    pile_spread_radius_m: float
    roughness_height_range_m: FloatRange
    roughness_radius_range_m: FloatRange

    def __post_init__(self) -> None:
        _require_positive(self.mean_fill_duration_s, "mean fill duration")
        fill_duration_range = _require_range(
            self.fill_duration_factor_range,
            "fill duration factor",
            positive=True,
        )
        if not math.isclose(sum(fill_duration_range), 2.0, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("fill duration factor range must be centered on 1")
        _require_average_factor_range(self.fill_rate_factor_range, "fill rate factor")
        _require_range(
            self.fill_rate_change_duration_s_range,
            "fill rate change duration",
            positive=True,
        )
        threshold_range = _require_range(
            self.collection_threshold_range,
            "collection threshold",
            positive=True,
        )
        if threshold_range[1] > 1.0:
            raise ValueError("collection threshold range must not exceed 1")
        _require_range(
            self.collection_duration_factor_range,
            "collection duration factor",
            positive=True,
        )
        _require_average_factor_range(self.collection_rate_factor_range, "collection rate factor")
        _require_range(
            self.collection_rate_change_duration_s_range,
            "collection rate change duration",
            positive=True,
        )
        if not self.inlet_positions:
            raise ValueError("scenario must contain at least one inlet position")
        if len(set(self.inlet_positions)) != len(self.inlet_positions):
            raise ValueError("scenario inlet positions must be unique")
        _require_ratio(self.inlet_switch_activation_ratio, "inlet switch activation ratio")
        _require_non_negative(
            self.inlet_switch_height_difference_m,
            "inlet switch height difference",
        )
        _require_positive(self.inlet_comparison_radius_m, "inlet comparison radius")
        _require_positive(self.surface_update_interval_s, "surface update interval")
        _require_positive(self.pile_spread_radius_m, "pile spread radius")
        _require_range(self.roughness_height_range_m, "roughness height")
        _require_range(self.roughness_radius_range_m, "roughness radius", positive=True)


@dataclass(frozen=True, slots=True)
class FillPlan:
    """Fixed target and rate profile for one fill phase."""

    cycle_index: int
    started_at_s: float
    duration_s: float
    target_fill_ratio: float
    target_volume_m3: float
    rate_profile: SmoothRateProfile

    @property
    def ends_at_s(self) -> float:
        """Return the absolute simulation time of the fill transition."""
        return self.started_at_s + self.duration_s

    @property
    def average_rate_m3_per_s(self) -> float:
        """Return the phase average input rate."""
        return self.target_volume_m3 / self.duration_s


@dataclass(frozen=True, slots=True)
class CollectionPlan:
    """Fixed target and rate profile for one collection phase."""

    cycle_index: int
    started_at_s: float
    duration_s: float
    starting_volume_m3: float
    rate_profile: SmoothRateProfile

    @property
    def ends_at_s(self) -> float:
        """Return the absolute simulation time of the collection transition."""
        return self.started_at_s + self.duration_s

    @property
    def average_rate_m3_per_s(self) -> float:
        """Return the phase average removal rate."""
        return self.starting_volume_m3 / self.duration_s


type PhasePlan = FillPlan | CollectionPlan


@dataclass(frozen=True, slots=True)
class ScenarioSnapshot:
    """Observable scenario state at the latest requested simulation time."""

    elapsed_s: float
    surface_updated_at_s: float
    cycle_index: int
    phase: ScenarioPhase
    phase_started_at_s: float
    phase_ends_at_s: float
    phase_duration_s: float
    rate_factor: float
    target_fill_ratio: float
    surface_fill_ratio: float
    surface_volume_m3: float
    current_inlet_index: int | None


class ScenarioSimulator:
    """Advance one shared height field through deterministic scenario cycles."""

    def __init__(
        self,
        surface: HeightField,
        settings: ScenarioSettings,
        *,
        seed: int,
    ) -> None:
        if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= _MAX_SEED:
            raise ValueError("scenario seed must be an unsigned 64-bit integer")
        empty_tolerance_m3 = max(1.0, surface.capacity_m3) * _STATE_TOLERANCE
        if surface.volume_m3 > empty_tolerance_m3:
            raise ValueError("scenario surface must be empty at simulation start")
        if any(not surface.boundary.contains(position) for position in settings.inlet_positions):
            raise ValueError("scenario inlet positions must lie inside the surface boundary")

        self._surface = surface
        self._settings = settings
        self._cycle_rng = random.Random(_derive_seed(seed, "cycle-plan"))
        self._rate_rng = random.Random(_derive_seed(seed, "rate-profile"))
        self._roughness_rng = random.Random(_derive_seed(seed, "surface-roughness"))
        self._elapsed_s = 0.0
        self._surface_updated_at_s = 0.0
        self._next_update_index = 1
        self._cycle_index = 0
        self._phase = ScenarioPhase.FILLING
        self._current_inlet_index = 0
        self._fill_plan = self._create_fill_plan(started_at_s=0.0)
        self._collection_plan: CollectionPlan | None = None

    @property
    def surface(self) -> HeightField:
        """Return the shared mutable height field observed by sensors."""
        return self._surface

    @property
    def elapsed_s(self) -> float:
        """Return the latest simulation time processed or requested."""
        return self._elapsed_s

    @property
    def next_surface_event_elapsed_s(self) -> float:
        """Return the next scheduled update or phase transition time."""
        next_update_s = self._next_update_index * self._settings.surface_update_interval_s
        return min(next_update_s, self.phase_plan.ends_at_s)

    @property
    def phase_plan(self) -> PhasePlan:
        """Return the fixed plan for the active phase."""
        if self._phase is ScenarioPhase.FILLING:
            return self._fill_plan
        if self._collection_plan is None:
            raise RuntimeError("collection phase has no plan")
        return self._collection_plan

    @property
    def snapshot(self) -> ScenarioSnapshot:
        """Return immutable observable state without advancing time."""
        plan = self.phase_plan
        phase_elapsed_s = min(plan.duration_s, self._elapsed_s - plan.started_at_s)
        return ScenarioSnapshot(
            elapsed_s=self._elapsed_s,
            surface_updated_at_s=self._surface_updated_at_s,
            cycle_index=self._cycle_index,
            phase=self._phase,
            phase_started_at_s=plan.started_at_s,
            phase_ends_at_s=plan.ends_at_s,
            phase_duration_s=plan.duration_s,
            rate_factor=plan.rate_profile.factor_at(phase_elapsed_s),
            target_fill_ratio=self._fill_plan.target_fill_ratio,
            surface_fill_ratio=self._surface.fill_ratio,
            surface_volume_m3=self._surface.volume_m3,
            current_inlet_index=(
                self._current_inlet_index if self._phase is ScenarioPhase.FILLING else None
            ),
        )

    def advance_to(self, elapsed_s: float) -> ScenarioSnapshot:
        """Process all surface and phase events through an absolute simulation time."""
        if not math.isfinite(elapsed_s) or elapsed_s < self._elapsed_s:
            raise ValueError("scenario elapsed time must be finite and monotonic")

        while True:
            plan = self.phase_plan
            next_update_s = self._next_update_index * self._settings.surface_update_interval_s
            next_event_s = min(next_update_s, plan.ends_at_s)
            if next_event_s > elapsed_s:
                break

            self._apply_phase_change(next_event_s)
            self._surface_updated_at_s = next_event_s
            update_due = next_event_s == next_update_s
            transition_due = next_event_s == plan.ends_at_s
            if update_due:
                self._next_update_index += 1
            if transition_due:
                self._transition_phase(next_event_s)
            elif self._phase is ScenarioPhase.FILLING:
                self._switch_inlet_if_needed()

        self._elapsed_s = elapsed_s
        return self.snapshot

    def _create_fill_plan(self, *, started_at_s: float) -> FillPlan:
        duration_factor = self._cycle_rng.uniform(*self._settings.fill_duration_factor_range)
        duration_s = self._settings.mean_fill_duration_s * duration_factor
        target_fill_ratio = self._cycle_rng.uniform(*self._settings.collection_threshold_range)
        return FillPlan(
            cycle_index=self._cycle_index,
            started_at_s=started_at_s,
            duration_s=duration_s,
            target_fill_ratio=target_fill_ratio,
            target_volume_m3=self._surface.capacity_m3 * target_fill_ratio,
            rate_profile=create_smooth_rate_profile(
                duration_s=duration_s,
                factor_range=self._settings.fill_rate_factor_range,
                change_duration_s_range=self._settings.fill_rate_change_duration_s_range,
                rng=self._rate_rng,
            ),
        )

    def _create_collection_plan(self, *, started_at_s: float) -> CollectionPlan:
        duration_factor = self._cycle_rng.uniform(*self._settings.collection_duration_factor_range)
        duration_s = self._settings.mean_fill_duration_s * duration_factor
        starting_volume_m3 = self._surface.volume_m3
        return CollectionPlan(
            cycle_index=self._cycle_index,
            started_at_s=started_at_s,
            duration_s=duration_s,
            starting_volume_m3=starting_volume_m3,
            rate_profile=create_smooth_rate_profile(
                duration_s=duration_s,
                factor_range=self._settings.collection_rate_factor_range,
                change_duration_s_range=self._settings.collection_rate_change_duration_s_range,
                rng=self._rate_rng,
            ),
        )

    def _apply_phase_change(self, through_s: float) -> None:
        plan = self.phase_plan
        interval_start_s = max(0.0, self._surface_updated_at_s - plan.started_at_s)
        interval_end_s = min(plan.duration_s, through_s - plan.started_at_s)
        integrated_factor_s = plan.rate_profile.integrated_factor_between(
            interval_start_s,
            interval_end_s,
        )

        if isinstance(plan, FillPlan):
            if through_s == plan.ends_at_s:
                requested_m3 = max(0.0, plan.target_volume_m3 - self._surface.volume_m3)
            else:
                requested_m3 = plan.average_rate_m3_per_s * integrated_factor_s
            change = self._surface.add_volume(
                requested_m3,
                center=self._settings.inlet_positions[self._current_inlet_index],
                spread_radius_m=self._settings.pile_spread_radius_m,
            )
            self._apply_local_roughness()
        else:
            if through_s == plan.ends_at_s:
                requested_m3 = self._surface.volume_m3
            else:
                requested_m3 = min(
                    self._surface.volume_m3,
                    plan.average_rate_m3_per_s * integrated_factor_s,
                )
            change = self._surface.remove_volume_uniformly(requested_m3)

        tolerance_m3 = max(1.0, self._surface.capacity_m3) * _STATE_TOLERANCE
        if change.unapplied_m3 > tolerance_m3:
            raise RuntimeError("scenario surface could not apply the planned volume change")

    def _apply_local_roughness(self) -> None:
        peak_delta_m = self._roughness_rng.uniform(*self._settings.roughness_height_range_m)
        if peak_delta_m == 0.0:
            return
        radius_m = self._roughness_rng.uniform(*self._settings.roughness_radius_range_m)
        center = self._sample_roughness_center()
        self._surface.apply_local_roughness(
            center=center,
            radius_m=radius_m,
            peak_delta_m=peak_delta_m,
        )

    def _sample_roughness_center(self) -> Vec2:
        inlet = self._settings.inlet_positions[self._current_inlet_index]
        for _ in range(32):
            distance_m = self._settings.pile_spread_radius_m * math.sqrt(
                self._roughness_rng.random()
            )
            angle_rad = self._roughness_rng.random() * math.tau
            candidate = Vec2(
                inlet.x + distance_m * math.cos(angle_rad),
                inlet.y + distance_m * math.sin(angle_rad),
            )
            if self._surface.boundary.contains(candidate):
                return candidate
        return inlet

    def _transition_phase(self, transitioned_at_s: float) -> None:
        tolerance_m3 = max(1.0, self._surface.capacity_m3) * _STATE_TOLERANCE
        if self._phase is ScenarioPhase.FILLING:
            if not math.isclose(
                self._surface.volume_m3,
                self._fill_plan.target_volume_m3,
                rel_tol=0.0,
                abs_tol=tolerance_m3,
            ):
                raise RuntimeError("fill phase did not reach its planned threshold volume")
            self._phase = ScenarioPhase.COLLECTING
            self._collection_plan = self._create_collection_plan(started_at_s=transitioned_at_s)
            return

        if self._surface.volume_m3 > tolerance_m3:
            raise RuntimeError("collection phase did not empty the surface")
        self._cycle_index += 1
        self._phase = ScenarioPhase.FILLING
        self._current_inlet_index = 0
        self._collection_plan = None
        self._fill_plan = self._create_fill_plan(started_at_s=transitioned_at_s)

    def _switch_inlet_if_needed(self) -> None:
        if self._surface.fill_ratio < self._settings.inlet_switch_activation_ratio:
            return
        heights_m = tuple(
            self._surface.mean_height_within(
                position,
                self._settings.inlet_comparison_radius_m,
            )
            for position in self._settings.inlet_positions
        )
        lowest_index = min(range(len(heights_m)), key=lambda index: (heights_m[index], index))
        current_height_m = heights_m[self._current_inlet_index]
        lowest_height_m = heights_m[lowest_index]
        if (
            lowest_index != self._current_inlet_index
            and current_height_m > lowest_height_m
            and current_height_m - lowest_height_m
            >= self._settings.inlet_switch_height_difference_m
        ):
            self._current_inlet_index = lowest_index


def _derive_seed(seed: int, stream_name: str) -> int:
    payload = seed.to_bytes(8, byteorder="big") + stream_name.encode("ascii")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], byteorder="big")


def _require_range(value: FloatRange, name: str, *, positive: bool = False) -> FloatRange:
    lower, upper = value
    if not math.isfinite(lower) or not math.isfinite(upper) or upper < lower:
        raise ValueError(f"{name} range must contain finite ordered values")
    if positive and lower <= 0.0:
        raise ValueError(f"{name} range must be positive")
    return lower, upper


def _require_average_factor_range(value: FloatRange, name: str) -> FloatRange:
    result = _require_range(value, name, positive=True)
    if not result[0] <= 1.0 <= result[1]:
        raise ValueError(f"{name} range must include 1")
    return result


def _require_positive(value: float, name: str) -> float:
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError(f"{name} must be a finite positive number")
    return value


def _require_non_negative(value: float, name: str) -> float:
    if not math.isfinite(value) or value < 0.0:
        raise ValueError(f"{name} must be a finite non-negative number")
    return value


def _require_ratio(value: float, name: str) -> float:
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be between 0 and 1")
    return value
