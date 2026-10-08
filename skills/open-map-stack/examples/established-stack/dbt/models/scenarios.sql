select 'canonical' as scenario, {{ var('minimum_area_m2') }} as minimum_area_m2,
       count(*) as feature_count, coalesce(sum(area_m2), 0) as area_m2
from {{ ref('candidates') }}
union all
select 'exploratory', {{ var('exploratory_area_m2') }}, count(*), coalesce(sum(area_m2), 0)
from {{ ref('measured') }} where area_m2 >= {{ var('exploratory_area_m2') }}
