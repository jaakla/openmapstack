select * from {{ ref('measured') }}
where area_m2 >= {{ var('minimum_area_m2') }}
