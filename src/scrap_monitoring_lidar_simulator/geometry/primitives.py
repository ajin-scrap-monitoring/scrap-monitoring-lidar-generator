"""Finite vector, ray, and triangle primitives."""

import math
from dataclasses import dataclass
from typing import Self

_UNIT_VECTOR_TOLERANCE = 1e-6
_DEGENERATE_TOLERANCE = 1e-12


def _require_finite(*values: float) -> None:
    if not all(math.isfinite(value) for value in values):
        raise ValueError("geometry coordinates must be finite")


@dataclass(frozen=True, slots=True)
class Vec2:
    """A finite two-dimensional vector."""

    x: float
    y: float

    def __post_init__(self) -> None:
        _require_finite(self.x, self.y)

    def __sub__(self, other: Self) -> Self:
        return type(self)(self.x - other.x, self.y - other.y)

    def cross(self, other: Self) -> float:
        """Return the scalar two-dimensional cross product."""
        return self.x * other.y - self.y * other.x


@dataclass(frozen=True, slots=True)
class Vec3:
    """A finite three-dimensional vector."""

    x: float
    y: float
    z: float

    def __post_init__(self) -> None:
        _require_finite(self.x, self.y, self.z)

    def __add__(self, other: Self) -> Self:
        return type(self)(self.x + other.x, self.y + other.y, self.z + other.z)

    def __sub__(self, other: Self) -> Self:
        return type(self)(self.x - other.x, self.y - other.y, self.z - other.z)

    def __mul__(self, scalar: float) -> Self:
        _require_finite(scalar)
        return type(self)(self.x * scalar, self.y * scalar, self.z * scalar)

    def __rmul__(self, scalar: float) -> Self:
        return self * scalar

    def dot(self, other: Self) -> float:
        """Return the dot product."""
        return self.x * other.x + self.y * other.y + self.z * other.z

    def cross(self, other: Self) -> Self:
        """Return the cross product."""
        return type(self)(
            self.y * other.z - self.z * other.y,
            self.z * other.x - self.x * other.z,
            self.x * other.y - self.y * other.x,
        )

    def length(self) -> float:
        """Return the Euclidean length."""
        return math.hypot(self.x, self.y, self.z)

    def normalized(self) -> Self:
        """Return a unit vector with the same direction."""
        length = self.length()
        if length <= _DEGENERATE_TOLERANCE:
            raise ValueError("cannot normalize a zero-length vector")
        return type(self)(self.x / length, self.y / length, self.z / length)


@dataclass(frozen=True, slots=True)
class Ray:
    """A ray whose parameter is physical distance in meters."""

    origin: Vec3
    direction: Vec3

    def __post_init__(self) -> None:
        if not math.isclose(
            self.direction.length(),
            1.0,
            rel_tol=0.0,
            abs_tol=_UNIT_VECTOR_TOLERANCE,
        ):
            raise ValueError("ray direction must be a unit vector")

    def point_at(self, distance: float) -> Vec3:
        """Return the point at a non-negative distance along the ray."""
        if not math.isfinite(distance) or distance < 0:
            raise ValueError("ray distance must be a finite non-negative number")
        return self.origin + self.direction * distance


@dataclass(frozen=True, slots=True)
class Triangle:
    """A non-degenerate three-dimensional triangle."""

    a: Vec3
    b: Vec3
    c: Vec3

    def __post_init__(self) -> None:
        area_vector = (self.b - self.a).cross(self.c - self.a)
        if area_vector.length() <= _DEGENERATE_TOLERANCE:
            raise ValueError("triangle must be non-degenerate")
