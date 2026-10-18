"""JSON-safe exports of measured laser ranges; no inferred scene geometry."""
import math


def lidar_snapshot(ranges, angles, max_range, sample_t, now):
    values=[float(r) if math.isfinite(r) and 0<r<=max_range else None for r in ranges]
    valid=[r for r in values if r is not None]
    return dict(ranges_m=values,angles_rad=[float(a) for a in angles],max_range_m=max_range,
                sample_t=sample_t,age_s=max(0,now-sample_t),ray_count=len(values),
                valid_count=len(valid),nearest_m=min(valid) if valid else None)
