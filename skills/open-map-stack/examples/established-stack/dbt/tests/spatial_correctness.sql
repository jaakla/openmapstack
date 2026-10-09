-- Independent oracle for the synthetic fixture, including the hole's net area.
select * from {{ ref('measured') }}
where not ST_IsValid(geometry) or area_m2 !=
  case id when 'small' then 10000 when 'medium' then 20000 when 'donut' then 30000 else -1 end
union all
select * from {{ ref('measured') }} where id is null
