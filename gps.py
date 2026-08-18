"""
GPS path generator — produces realistic walking waypoints.

Uses the Haversine-based destination formula to move from a starting
coordinate along a slowly-drifting bearing, yielding (lat, lng) pairs
that look like a person walking through streets.
"""

import math
import random

EARTH_RADIUS_M = 6_371_000


def _destination(lat: float, lng: float, bearing_deg: float, dist_m: float):
    """Move *dist_m* metres from (lat, lng) along *bearing_deg*."""
    lat_r = math.radians(lat)
    lng_r = math.radians(lng)
    brg = math.radians(bearing_deg)
    ang = dist_m / EARTH_RADIUS_M

    new_lat = math.asin(
        math.sin(lat_r) * math.cos(ang)
        + math.cos(lat_r) * math.sin(ang) * math.cos(brg)
    )
    new_lng = lng_r + math.atan2(
        math.sin(brg) * math.sin(ang) * math.cos(lat_r),
        math.cos(ang) - math.sin(lat_r) * math.sin(new_lat),
    )
    return math.degrees(new_lat), math.degrees(new_lng)


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Distance in metres between two GPS points."""
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = rlat2 - rlat1
    dlng = math.radians(lng2 - lng1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlng / 2) ** 2
    )
    return EARTH_RADIUS_M * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


class PathWalker:
    """
    Generates a walking path starting near (start_lat, start_lng).

    Parameters
    ----------
    start_lat, start_lng : float
        Starting GPS coordinate center.
    stride_m : float
        Average stride length in metres (default 0.72, matches server's
        ≈0.7055 m/step observed in testing).
    speed_kmh : float
        Target walking speed in km/h. Must stay inside the server's
        [stableSpeed, maxSpeed] window (2.5 – 10 km/h).
    jitter_m : float
        Random start offset up to ±jitter_m metres (default 100.0m).
    """

    def __init__(
        self,
        start_lat: float = 36.736003,
        start_lng: float = 10.214625,
        stride_m: float = 0.72,
        speed_kmh: float | None = None,
        jitter_m: float = 100.0,
    ):
        if jitter_m > 0:
            offset_dist = random.uniform(0, jitter_m)
            offset_brg = random.uniform(0, 360)
            start_lat, start_lng = _destination(
                start_lat, start_lng, offset_brg, offset_dist
            )

        self.lat = start_lat
        self.lng = start_lng
        self.stride_m = stride_m
        # Random speed between 3.5 – 5.5 km/h (comfortable walking pace)
        self.speed_kmh = speed_kmh or round(random.uniform(3.5, 5.5), 2)
        self.bearing = random.uniform(0, 360)
        self.total_distance_m = 0.0
        self._step_count = 0

    @property
    def speed_ms(self) -> float:
        return self.speed_kmh / 3.6

    @property
    def step_interval_s(self) -> float:
        """Seconds between consecutive steps at the current speed."""
        return self.stride_m / self.speed_ms

    @property
    def cadence_spm(self) -> float:
        """Steps per minute at the current speed."""
        return 60.0 / self.step_interval_s

    def step(self) -> tuple[float, float]:
        """Advance one step. Returns (lat, lng)."""
        # Slight random jitter in stride (±15%) — mimics real-world variance
        actual_stride = self.stride_m * random.uniform(0.85, 1.15)

        # Gradually drift the bearing (like walking along winding streets)
        self.bearing = (self.bearing + random.gauss(0, 8)) % 360

        # Occasionally make a sharper turn (intersection)
        if random.random() < 0.03:
            self.bearing = (self.bearing + random.choice([-90, -45, 45, 90])) % 360

        self.lat, self.lng = _destination(
            self.lat, self.lng, self.bearing, actual_stride
        )
        self.total_distance_m += actual_stride
        self._step_count += 1
        return self.lat, self.lng
