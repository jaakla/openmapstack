-- Approved-snapshot query: NYC taxi zone reference geometry.
-- Public-origin reference data; no tenant restriction applies.
-- Executed by the restricted reader through `openmapstack source snapshot`.

SELECT
    zone_id,
    h3_cell,
    borough,
    zone_name,
    zone_source,
    geom
FROM ops.taxi_zones
ORDER BY zone_id
