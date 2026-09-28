-- Approved-snapshot query: customer account presence per zone.
-- The restricted reader has column-level grants only: contact_name,
-- contact_email and contract_value are withheld by the backend and can
-- never reach this snapshot. Row-level security limits rows to
-- tenant_id = 'alpha'.
-- Executed by the restricted reader through `openmapstack source snapshot`.

SELECT
    z.h3_cell,
    source.account_id,
    source.tenant_id,
    source.taxi_zone_id,
    source.account_name,
    source.is_active
FROM ops.customer_accounts AS source
JOIN ops.taxi_zones AS z ON z.zone_id = source.taxi_zone_id
ORDER BY account_id
