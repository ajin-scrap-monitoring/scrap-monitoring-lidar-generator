"""Tests for the regular-grid surface state."""

import numpy as np
import pytest

from scrap_monitoring_lidar_generator.geometry import Polygon2, Vec2
from scrap_monitoring_lidar_generator.scenario import HeightField


@pytest.fixture
def square_boundary() -> Polygon2:
    return Polygon2((Vec2(0.0, 0.0), Vec2(2.0, 0.0), Vec2(2.0, 1.0), Vec2(0.0, 1.0)))


def test_empty_height_field_matches_floor_and_polygon_capacity(
    square_boundary: Polygon2,
) -> None:
    surface = HeightField(
        square_boundary,
        floor_z_m=-1.0,
        top_z_m=3.0,
        cell_size_m=0.5,
    )

    assert surface.shape == (3, 5)
    assert surface.surface_area_m2 == pytest.approx(2.0)
    assert surface.capacity_m3 == pytest.approx(8.0)
    assert surface.volume_m3 == pytest.approx(0.0)
    assert surface.fill_ratio == pytest.approx(0.0)
    assert surface.height_at(Vec2(0.7, 0.3)) == pytest.approx(-1.0)


def test_add_volume_is_local_and_preserves_requested_volume(
    square_boundary: Polygon2,
) -> None:
    surface = HeightField(
        square_boundary,
        floor_z_m=0.0,
        top_z_m=4.0,
        cell_size_m=0.25,
    )

    change = surface.add_volume(
        1.25,
        center=Vec2(0.5, 0.5),
        spread_radius_m=0.35,
    )

    assert change.requested_m3 == pytest.approx(1.25)
    assert change.applied_m3 == pytest.approx(1.25)
    assert change.unapplied_m3 == pytest.approx(0.0)
    assert surface.volume_m3 == pytest.approx(1.25)
    assert surface.fill_ratio == pytest.approx(1.25 / 8.0)
    assert surface.height_at(Vec2(0.5, 0.5)) > surface.height_at(Vec2(1.75, 0.5))
    assert np.min(surface.heights_m) >= surface.floor_z_m
    assert np.max(surface.heights_m) <= surface.top_z_m


def test_height_query_uses_bilinear_interpolation(square_boundary: Polygon2) -> None:
    surface = HeightField(
        square_boundary,
        floor_z_m=0.0,
        top_z_m=4.0,
        cell_size_m=0.5,
    )
    surface.add_volume(1.0, center=Vec2(0.0, 0.0), spread_radius_m=0.4)
    heights_m = surface.heights_m

    expected_height_m = float(np.mean(heights_m[0:2, 0:2]))

    assert surface.height_at(Vec2(0.25, 0.25)) == pytest.approx(expected_height_m)


def test_mean_height_compares_area_around_positions(square_boundary: Polygon2) -> None:
    surface = HeightField(
        square_boundary,
        floor_z_m=0.0,
        top_z_m=4.0,
        cell_size_m=0.1,
    )
    surface.add_volume(0.5, center=Vec2(0.4, 0.5), spread_radius_m=0.15)

    near_height_m = surface.mean_height_within(Vec2(0.4, 0.5), 0.2)
    far_height_m = surface.mean_height_within(Vec2(1.6, 0.5), 0.2)

    assert near_height_m > far_height_m


def test_add_and_remove_preserve_volume_in_concave_boundary() -> None:
    boundary = Polygon2(
        (
            Vec2(0.0, 0.0),
            Vec2(2.0, 0.0),
            Vec2(2.0, 1.0),
            Vec2(1.0, 1.0),
            Vec2(1.0, 2.0),
            Vec2(0.0, 2.0),
        )
    )
    surface = HeightField(boundary, floor_z_m=-2.0, top_z_m=2.0, cell_size_m=0.3)

    added = surface.add_volume(4.5, center=Vec2(0.5, 0.5), spread_radius_m=0.4)
    removed = surface.remove_volume(1.75, center=Vec2(0.5, 1.5), spread_radius_m=0.5)

    assert surface.surface_area_m2 == pytest.approx(3.0)
    assert surface.capacity_m3 == pytest.approx(12.0)
    assert added.applied_m3 == pytest.approx(4.5)
    assert removed.applied_m3 == pytest.approx(1.75)
    assert surface.volume_m3 == pytest.approx(2.75)


def test_accepts_clockwise_boundary_with_collinear_vertex() -> None:
    boundary = Polygon2(
        (
            Vec2(0.0, 0.0),
            Vec2(0.0, 1.0),
            Vec2(1.0, 1.0),
            Vec2(2.0, 1.0),
            Vec2(2.0, 0.0),
        )
    )
    surface = HeightField(boundary, floor_z_m=0.0, top_z_m=3.0, cell_size_m=0.3)

    change = surface.add_volume(7.0, center=Vec2(1.0, 0.5), spread_radius_m=0.4)

    assert surface.surface_area_m2 == pytest.approx(2.0)
    assert surface.capacity_m3 == pytest.approx(6.0)
    assert change.applied_m3 == pytest.approx(6.0)
    assert change.unapplied_m3 == pytest.approx(1.0)


def test_addition_reports_capacity_overflow(square_boundary: Polygon2) -> None:
    surface = HeightField(
        square_boundary,
        floor_z_m=0.0,
        top_z_m=2.0,
        cell_size_m=0.4,
    )

    change = surface.add_volume(5.5, center=Vec2(1.0, 0.5), spread_radius_m=0.2)

    assert change.applied_m3 == pytest.approx(4.0)
    assert change.unapplied_m3 == pytest.approx(1.5)
    assert surface.volume_m3 == pytest.approx(surface.capacity_m3)
    assert surface.fill_ratio == pytest.approx(1.0)
    assert np.all(surface.heights_m == surface.top_z_m)


def test_removal_reports_missing_volume_and_restores_floor(square_boundary: Polygon2) -> None:
    surface = HeightField(
        square_boundary,
        floor_z_m=1.0,
        top_z_m=3.0,
        cell_size_m=0.4,
    )
    surface.add_volume(1.0, center=Vec2(0.5, 0.5), spread_radius_m=0.3)

    change = surface.remove_volume(2.25, center=Vec2(1.5, 0.5), spread_radius_m=0.3)

    assert change.applied_m3 == pytest.approx(1.0)
    assert change.unapplied_m3 == pytest.approx(1.25)
    assert surface.volume_m3 == pytest.approx(0.0, abs=1e-12)
    assert np.all(surface.heights_m == surface.floor_z_m)


def test_uniform_removal_preserves_requested_volume(square_boundary: Polygon2) -> None:
    surface = HeightField(
        square_boundary,
        floor_z_m=0.0,
        top_z_m=3.0,
        cell_size_m=0.2,
    )
    surface.add_volume(2.0, center=Vec2(0.5, 0.5), spread_radius_m=0.3)

    partial = surface.remove_volume_uniformly(0.75)
    remainder = surface.remove_volume_uniformly(2.0)

    assert partial.applied_m3 == pytest.approx(0.75)
    assert remainder.applied_m3 == pytest.approx(1.25)
    assert remainder.unapplied_m3 == pytest.approx(0.75)
    assert surface.volume_m3 == pytest.approx(0.0, abs=1e-12)
    assert np.all(surface.heights_m == surface.floor_z_m)


def test_same_operations_produce_same_height_field(square_boundary: Polygon2) -> None:
    surfaces = [
        HeightField(square_boundary, floor_z_m=0.0, top_z_m=3.0, cell_size_m=0.2) for _ in range(2)
    ]
    for surface in surfaces:
        surface.add_volume(2.0, center=Vec2(0.5, 0.4), spread_radius_m=0.4)
        surface.remove_volume(0.3, center=Vec2(1.5, 0.7), spread_radius_m=0.25)

    np.testing.assert_array_equal(surfaces[0].heights_m, surfaces[1].heights_m)
    assert surfaces[0].volume_m3 == surfaces[1].volume_m3


def test_height_array_is_an_independent_copy(square_boundary: Polygon2) -> None:
    surface = HeightField(
        square_boundary,
        floor_z_m=0.0,
        top_z_m=2.0,
        cell_size_m=0.5,
    )

    exposed = surface.heights_m
    exposed[:] = 2.0

    assert surface.volume_m3 == pytest.approx(0.0)


@pytest.mark.parametrize(
    ("floor", "top", "cell_size"),
    [
        (0.0, 0.0, 0.5),
        (1.0, 0.0, 0.5),
        (float("nan"), 1.0, 0.5),
        (0.0, float("inf"), 0.5),
        (0.0, 1.0, 0.0),
        (0.0, 1.0, float("inf")),
    ],
)
def test_rejects_invalid_height_field_bounds(
    square_boundary: Polygon2,
    floor: float,
    top: float,
    cell_size: float,
) -> None:
    with pytest.raises(ValueError):
        HeightField(
            square_boundary,
            floor_z_m=floor,
            top_z_m=top,
            cell_size_m=cell_size,
        )


@pytest.mark.parametrize("volume", [-1.0, float("nan"), float("inf")])
def test_rejects_invalid_volume(square_boundary: Polygon2, volume: float) -> None:
    surface = HeightField(
        square_boundary,
        floor_z_m=0.0,
        top_z_m=2.0,
        cell_size_m=0.5,
    )

    with pytest.raises(ValueError, match="volume"):
        surface.add_volume(volume, center=Vec2(0.5, 0.5), spread_radius_m=0.5)


def test_rejects_invalid_change_location(square_boundary: Polygon2) -> None:
    surface = HeightField(
        square_boundary,
        floor_z_m=0.0,
        top_z_m=2.0,
        cell_size_m=0.5,
    )

    with pytest.raises(ValueError, match="inside"):
        surface.add_volume(1.0, center=Vec2(3.0, 0.5), spread_radius_m=0.5)
    with pytest.raises(ValueError, match="radius"):
        surface.remove_volume(1.0, center=Vec2(0.5, 0.5), spread_radius_m=0.0)

    with pytest.raises(ValueError, match="comparison center"):
        surface.mean_height_within(Vec2(3.0, 0.5), 0.5)
    with pytest.raises(ValueError, match="comparison radius"):
        surface.mean_height_within(Vec2(0.5, 0.5), 0.0)
