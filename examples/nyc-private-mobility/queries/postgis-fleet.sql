-- Approved-snapshot query: current fleet positions.
-- Row-level security confines this to tenant_id = 'alpha' for the restricted
-- reader; only located vehicles contribute to zone coverage.
-- Executed by the restricted reader through `openmapstack source snapshot`.

SELECT
    z.h3_cell,
    source.position_id,
    source.tenant_id,
    source.vehicle_id,
    source.taxi_zone_id,
    source.status,
    source.recorded_at,
    source.geom
FROM ops.fleet_positions AS source
JOIN ops.taxi_zones AS z ON z.zone_id = source.taxi_zone_id
ORDER BY position_id
