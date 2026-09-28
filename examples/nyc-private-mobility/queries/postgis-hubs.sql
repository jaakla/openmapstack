-- Approved-snapshot query: existing Northstar hubs.
-- Row-level security confines this to tenant_id = 'alpha' for the restricted
-- reader; the snapshot therefore contains only alpha hubs.
-- Executed by the restricted reader through `openmapstack source snapshot`.

SELECT
    z.h3_cell,
    source.hub_id,
    source.tenant_id,
    source.taxi_zone_id,
    source.name,
    source.capacity,
    source.activated_on,
    source.geom
FROM ops.hubs AS source
JOIN ops.taxi_zones AS z ON z.zone_id = source.taxi_zone_id
ORDER BY hub_id
