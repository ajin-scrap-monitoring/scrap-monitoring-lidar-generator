"""Tests for simple polygons."""

import pytest

from scrap_monitoring_lidar_generator.geometry import Polygon2, Vec2


def test_calculates_signed_area_for_both_windings() -> None:
    counter_clockwise = Polygon2((Vec2(0.0, 0.0), Vec2(4.0, 0.0), Vec2(4.0, 3.0), Vec2(0.0, 3.0)))
    clockwise = Polygon2(tuple(reversed(counter_clockwise.vertices)))

    assert counter_clockwise.signed_area == pytest.approx(12.0)
    assert clockwise.signed_area == pytest.approx(-12.0)


def test_contains_points_in_concave_polygon() -> None:
    polygon = Polygon2(
        (
            Vec2(0.0, 0.0),
            Vec2(4.0, 0.0),
            Vec2(4.0, 4.0),
            Vec2(2.0, 2.0),
            Vec2(0.0, 4.0),
        )
    )

    assert polygon.contains(Vec2(1.0, 1.0))
    assert polygon.contains(Vec2(3.5, 3.0))
    assert not polygon.contains(Vec2(2.0, 3.0))
    assert not polygon.contains(Vec2(5.0, 1.0))


def test_boundary_inclusion_is_explicit() -> None:
    polygon = Polygon2((Vec2(0.0, 0.0), Vec2(2.0, 0.0), Vec2(0.0, 2.0)))
    boundary_point = Vec2(1.0, 0.0)

    assert polygon.contains(boundary_point)
    assert not polygon.contains(boundary_point, include_boundary=False)


@pytest.mark.parametrize(
    "vertices",
    [
        (Vec2(0.0, 0.0), Vec2(1.0, 0.0)),
        (Vec2(0.0, 0.0), Vec2(1.0, 0.0), Vec2(0.0, 0.0)),
        (Vec2(0.0, 0.0), Vec2(1.0, 0.0), Vec2(2.0, 0.0)),
        (
            Vec2(0.0, 0.0),
            Vec2(4.0, 0.0),
            Vec2(0.0, 3.0),
            Vec2(4.0, 3.0),
            Vec2(2.0, 4.0),
        ),
    ],
)
def test_rejects_invalid_polygon(vertices: tuple[Vec2, ...]) -> None:
    with pytest.raises(ValueError):
        Polygon2(vertices)
