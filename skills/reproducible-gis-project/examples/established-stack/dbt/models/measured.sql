select id, ST_GeomFromText(wkt) as geometry,
       ST_Area(ST_GeomFromText(wkt)) as area_m2
from {{ source('pinned', 'parcels') }}
