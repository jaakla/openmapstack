# Formats and CRS

Format selection, conversions, and coordinate reference system handling. Two of the most common sources of subtle errors in GIS work — handle deliberately, not by reflex.

## Format quick reference

### Vector formats

| Format | When to use | Avoid for |
|---|---|---|
| **GeoParquet** | Cloud analytics, columnar workloads, large datasets, anything you'll query with DuckDB or Sedona | Tiny payloads, contexts requiring random write |
| **FlatGeobuf** | Single-file streaming over HTTP with spatial indexing built in; ideal for serving large vector datasets to web clients without a DB | Heavy attribute-side analytics |
| **GeoPackage (.gpkg)** | Desktop interchange (QGIS-friendly), multi-layer single file, when SQLite features matter | Cloud-native pipelines |
| **GeoJSON** | Small API payloads (< few MB), human-readable debugging, web map static layers | Anything beyond a few thousand features |
| **GeoJSONSeq / NDJSON** | Streaming large datasets between tools via Unix pipes (e.g., `ogr2ogr` to `tippecanoe`) | Random access reads |
| **Shapefile** | Reading legacy data only | Producing new output (column truncation to 10 chars, 2GB limit, no UTF-8 by default, multi-file) |
| **GeoArrow** | In-memory interchange between processes (uses Arrow C Data Interface for zero-copy memory transfers) | Persistent storage (use GeoParquet) |
| **MVT (Mapbox Vector Tile)** | Wire format inside vector tiles — almost never authored directly | — |

### Spatial Parquet must retain metadata

Writing `ST_AsWKB(geometry)` as an ordinary Parquet BLOB does not by itself produce GeoParquet.
Use a GeoParquet writer and retain the `geo` metadata, primary geometry column, encoding and CRS.
Reopen the written artifact and verify its actual CRS and geometry, including empty outputs;
a correct CRS string in `project.yaml` cannot repair missing file metadata. Do not repair this
by assigning a CRS guessed from coordinate magnitudes. See the
[GeoParquet metadata contract](https://geoparquet.org/releases/v1.1.0/).

### Raster formats

| Format | When to use |
|---|---|
| **Cloud Optimized GeoTIFF (COG)** | Default for new raster output. Single file, internally tiled, with overviews. HTTP range-requestable. |
| **Zarr** | n-dimensional arrays (time series, climate ensembles), cloud-native chunked storage |
| **NetCDF (.nc)** | Climate/ocean data, multi-dim arrays, established tooling (CDO, xarray) |
| **GeoTIFF (non-COG)** | Legacy compatibility only — convert to COG with `gdal_translate -of COG` |
| **HDF5** | Scientific multi-dim, often the underlying container for satellite L1/L2 products |

### Point cloud formats

| Format | When to use |
|---|---|
| **COPC** | New cloud-optimized LAZ — preferred for new archives |
| **LAZ** | Standard compressed LiDAR; convert to COPC for cloud serving |
| **LAS** | Uncompressed; convert to LAZ or COPC immediately |
| **EPT** (Entwine Point Tile) | Web streaming of huge point clouds — being overtaken by COPC |

### Tile container formats

| Format | When to use |
|---|---|
| **PMTiles** | Modern default. Single file, range-request friendly, serves from S3 or any static host with no tile server. Both vector (MVT) and raster. |
| **MBTiles** | Legacy SQLite-based; convert to PMTiles with `pmtiles convert` for new web deployments |
| **XYZ tile directories** | Static hosting of millions of small files; convert to PMTiles instead |

## Common conversions

### Vector

```bash
# Anything → GeoParquet
ogr2ogr -f Parquet output.parquet input.shp

# GeoParquet validation
gpq validate output.parquet

# Anything → GeoPackage (multi-layer)
ogr2ogr -f GPKG output.gpkg input.shp -nln my_layer

# Shapefile → GeoParquet with reprojection
ogr2ogr -f Parquet -t_srs EPSG:3301 output.parquet input.shp

# Filter while converting
ogr2ogr -f Parquet \
  -where "population > 10000" \
  -sql "SELECT name, population, geometry FROM cities" \
  cities_large.parquet cities.shp

# GeoParquet → PostGIS
ogr2ogr -f PostgreSQL "PG:host=localhost dbname=gis user=postgres" \
  input.parquet -nln cities -lco GEOMETRY_NAME=geom -lco FID=id
```

### Raster

```bash
# Anything → COG (the canonical conversion)
gdal_translate -of COG \
  -co COMPRESS=DEFLATE -co PREDICTOR=2 \
  -co BLOCKSIZE=512 \
  input.tif output_cog.tif

# COG validation
rio cogeo validate output_cog.tif

# Reproject + COG in one step
gdalwarp -t_srs EPSG:3301 -of COG \
  -co COMPRESS=DEFLATE \
  -tr 10 10 -r bilinear \
  input.tif output_3301_cog.tif

# Build virtual mosaic without copying (useful for many tiles)
gdalbuildvrt mosaic.vrt tile_*.tif
gdal_translate -of COG mosaic.vrt full_cog.tif

# Add internal overviews if missing
gdaladdo -r average input.tif 2 4 8 16 32
```

### Point cloud

```bash
# LAS → LAZ
pdal translate input.las output.laz

# LAZ → COPC
pdal translate input.laz output.copc.laz \
  --writers.copc.filename=output.copc.laz

# Or via PDAL pipeline JSON for anything more complex
```

### Tiles

```bash
# MBTiles → PMTiles
pmtiles convert input.mbtiles output.pmtiles

# Inspect a PMTiles file
pmtiles show output.pmtiles

# Extract a bbox subset (useful for previews)
pmtiles extract input.pmtiles output.pmtiles \
  --bbox=24.5,59.3,25.0,59.5
```

## Coordinate Reference Systems

### The four CRS rules to internalize

1. **Storage:** retain the source CRS when preserving raw data. WGS84 (EPSG:4326) is common for interchange; verify each dataset rather than assuming its CRS.
2. **Web rendering:** EPSG:3857 is common for tiled basemaps; use the rendering stack’s actual CRS contract. Rendering coordinates do not establish analytical accuracy.
3. **Metric computation:** use an appropriate local projected CRS or explicit geodesic calculations. Never use degrees as metres. Metre units alone do not establish distance accuracy: check distortion over the actual area and for the operation.
4. **Equal-area requirements:** use a suitable equal-area projection (for example EPSG:3035 for continental European area comparisons) or geodesic area calculation. UTM is conformal, not equal-area; national grids have their own projection properties. A local grid can be adequate when its distortion meets the stated tolerance. Equal-area does not mean distance-preserving.

Projection properties: [PROJ Transverse Mercator](https://proj.org/en/stable/operations/projections/tmerc.html)
and [Lambert Azimuthal Equal Area](https://proj.org/en/stable/operations/projections/laea.html).

### Choosing a local projected CRS

| Region | EPSG | Notes |
|---|---|---|
| Estonia | 3301 | L-EST97; the Estonian standard |
| Continental Europe (area comparison) | 3035 | LAEA Europe — equal-area; not a generic distance recommendation |
| US | UTM zones (32613–32619), or 5070 (CONUS Albers, equal-area) | |
| Worldwide local | UTM zone matching your area | Compute zone: `floor((lon + 180) / 6) + 1` |
| Web rendering | 3857 | Mercator, NOT equal-area |
| Storage / interchange | 4326 | WGS84 lat/lon |

### Reprojection commands

```bash
# Vector
ogr2ogr -t_srs EPSG:3301 output.gpkg input.gpkg

# Raster
gdalwarp -t_srs EPSG:3301 -tr 10 10 -r bilinear input.tif output.tif
# -r options: near, bilinear, cubic, cubicspline, lanczos, average, mode
# Use bilinear/cubic for continuous data; near/mode for categorical
```

### Reprojection in Python

```python
# GeoPandas
gdf = gdf.to_crs(epsg=3301)

# Single point
from pyproj import Transformer
t = Transformer.from_crs("EPSG:4326", "EPSG:3301", always_xy=True)
x, y = t.transform(24.7536, 59.4370)  # lon, lat (always_xy=True)

# Raster (rioxarray)
da = da.rio.reproject("EPSG:3301", resampling=Resampling.bilinear)
```

The `always_xy=True` flag is critical: pyproj defaults to "authority order", which is lat,lon for EPSG:4326 — opposite of what most code expects. Always pass `always_xy=True` unless you know exactly why you'd want otherwise.


## Coordinate order: longitude/latitude vs latitude/longitude

Coordinate order is an interface contract, **not a property you can infer from the words “WGS84” or `EPSG:4326` alone**. A single library may use different orders for different representations (for example, Leaflet `LatLng` arrays are `lat,lng`, while GeoJSON handled by Leaflet still follows GeoJSON `lon,lat`).

### Default internal convention

For OpenMapStack code and intermediate numeric coordinate arrays, prefer **Cartesian GIS order `x,y`**. For geographic WGS84-like coordinates this means **`longitude, latitude` (`lon,lat`)**.

Use this convention deliberately at internal boundaries:

```text
geographic point: [lon, lat]
projected point:  [x, y]
bbox:             [minx, miny, maxx, maxy]
```

Prefer named fields when crossing an ambiguous API boundary:

```json
{"lon": 24.7536, "lat": 59.4370}
```

Do not rewrite a format or API that explicitly specifies the opposite order. Convert at the boundary and keep the internal representation explicit.

### Common contracts

| Representation / API | Coordinate order | Rule |
|---|---|---|
| **GeoJSON / RFC 7946** | `lon,lat[,alt]` | Positions are longitude/easting first, latitude/northing second. GeoJSON uses OGC:CRS84 semantics. |
| **KML `<coordinates>`** | `lon,lat[,alt]` | Longitude first. |
| **MapLibre GL JS `LngLatLike`** | `lon,lat` | Numeric arrays are `[lng, lat]`, matching GeoJSON. |
| **OpenLayers geographic coordinate arrays** | `lon,lat` | Uses Cartesian `x,y`; helpers such as `fromLonLat()` make the convention explicit. |
| **PostGIS `ST_MakePoint(x,y)` for geodetic data** | `lon,lat` | `x` is longitude and `y` is latitude. Set SRID separately. |
| **OSRM HTTP coordinates** | `lon,lat` | URL coordinate pairs are `{longitude},{latitude}`. |
| **Redis `GEOADD`** | `lon lat` | Command syntax is longitude then latitude. |
| **Leaflet `L.LatLng` / numeric LatLng arrays** | `lat,lng` | `L.latLng(latitude, longitude)` and `[lat, lng]`. **GeoJSON passed to Leaflet remains `lon,lat`.** |
| **Google Maps JavaScript `LatLng`** | `lat,lng` | Constructor is `LatLng(lat, lng)`; prefer `{lat: ..., lng: ...}` literals to remove ambiguity. |
| **Google encoded polyline** | `lat,lon` sequence | The encoding alternates latitude delta then longitude delta. Treat this as an encoding contract, not a general Google Maps rule. |
| **GeoRSS Simple** | `lat lon` | A point is a latitude-longitude pair. GeoRSS GML is CRS/axis-aware and must not be reduced to this rule. |
| **WKT / WKB geometry coordinates** | `x y [z m]` | They encode coordinate axes, not a universal `lon,lat` promise. Interpret axes using the CRS and the consuming library's axis-order behavior. |
| **Shapefile geometry coordinates** | `x,y` | Same principle as WKT/WKB: coordinate tuples are Cartesian axes; CRS metadata determines meaning. Do not label the format itself “lon,lat”. |
| **PROJ / pyproj transforms** | CRS-dependent unless forced | For GIS-style `x,y` / `lon,lat`, use `always_xy=True` in pyproj and the equivalent explicit axis-order option where available. |

### Critical distinctions

**1. `EPSG:4326` and `OGC:CRS84` are not interchangeable axis-order contracts.**

They describe the same WGS84 geographic datum/coordinates in common GIS use, but authority axis order for EPSG:4326 is latitude, longitude, while OGC:CRS84 is longitude, latitude. Software often normalizes this to traditional GIS `x,y`; some standards and services do not. Therefore:

- do not infer tuple order from `EPSG:4326` alone;
- normalize deliberately when moving between libraries;
- in pyproj, prefer `Transformer.from_crs(..., always_xy=True)` for GIS-style `lon,lat` input/output;
- in DuckDB Spatial pipelines, keep CRS labels and `always_xy` behavior consistent with the surrounding OpenMapStack CRS rules.

**2. A product does not necessarily have one coordinate order.**

The order belongs to the specific function, object, serialization, or endpoint. For example:

- Leaflet `LatLng`: `lat,lng`
- Leaflet GeoJSON input: GeoJSON `lon,lat`
- Google Maps JavaScript `LatLng`: `lat,lng`
- GeoJSON used by a Google/Leaflet/MapLibre integration: still GeoJSON `lon,lat`

Never write a rule such as “Leaflet uses lat/lon” or “Google uses lat/lon” without naming the actual interface.

**3. BBOX order is a separate contract.**

The usual GIS bbox is Cartesian:

```text
[minx, miny, maxx, maxy] = [west, south, east, north]
```

But protocol-specific exceptions exist. In particular, WMS 1.3.0 with `EPSG:4326` may use latitude/longitude axis order, while `CRS:84` preserves longitude/latitude order. Follow the service capabilities and request contract, not a generic bbox rule.

### Agent guardrails

When reading, generating, converting, geocoding, routing, or calling a map API:

1. **Identify the exact representation/API contract before constructing coordinate arrays.** Do not rely on product name or habit.
2. **Prefer named coordinate objects** (`lon`/`lat`, `lng`/`lat`, `x`/`y`) at external boundaries when the API permits them.
3. **Do not guess from numeric ranges alone.** A latitude outside `[-90,90]` proves an order is wrong, but values inside that range do not prove it is right.
4. **Validate against the expected area of interest.** For Estonia, a Tallinn test point around `[24.75, 59.44]` should land in Estonia; `[59.44, 24.75]` must be treated as a failed sanity check, even though both numbers are individually legal geographic coordinates.
5. **Test one known asymmetric anchor before bulk processing** when consuming an unfamiliar service, file, or library interface.
6. **Keep axis order separate from reprojection.** `set_crs`/SRID assignment declares what existing numbers mean; it does not swap axes or transform coordinates. `to_crs`/`ST_Transform` transforms coordinates but still needs the correct input axis contract.
7. **Preserve source order only at source boundaries.** Convert into the project’s explicit internal convention immediately, and convert back only when writing/calling an interface that requires a different order.
8. **For material pipelines, record the convention** in code/comments or manifest assumptions when raw coordinate arrays enter the pipeline from CSV, JSON, APIs, or manually supplied parameters.

### Quick diagnostic

If a point appears on the wrong continent or far outside the AOI:

```text
1. Inspect the raw pair and its source contract.
2. Check whether it is [lat,lon] vs [lon,lat].
3. Check the declared CRS and actual coordinate magnitude.
4. Check EPSG authority axis order vs traditional GIS x,y behavior.
5. Check whether the interface is GeoJSON-like, LatLng-like, or protocol-specific.
6. Swap axes only when the source contract or a known-point test proves that they are reversed.
```

Do **not** “fix” a suspicious layer by swapping coordinates just because the result looks more plausible. Treat an axis swap as a data transformation that requires evidence.

### Primary references

- GeoJSON RFC 7946: https://www.rfc-editor.org/rfc/rfc7946.html
- KML reference: https://developers.google.com/kml/documentation/kmlreference
- OpenLayers coordinate-order FAQ: https://openlayers.org/doc/faq.html
- Leaflet `LatLng` reference: https://leafletjs.com/reference
- MapLibre GL JS `LngLat`: https://maplibre.org/maplibre-gl-js/docs/API/classes/LngLat/
- Google Maps JavaScript `LatLng`: https://developers.google.com/maps/documentation/javascript/reference/coordinates
- Google encoded polyline algorithm: https://developers.google.com/maps/documentation/utilities/polylinealgorithm
- OSRM HTTP API: https://project-osrm.org/docs/v26.5.0/http
- Redis `GEOADD`: https://redis.io/docs/latest/commands/geoadd/
- OGC GeoRSS Encoding Standard: https://docs.ogc.org/cs/17-002r1/17-002r1.html
- PostGIS `ST_MakePoint`: https://postgis.net/docs/ST_MakePoint.html
- pyproj axis-order behavior: https://pyproj4.github.io/pyproj/stable/api/transformer.html

## OGC axis-order gotchas

OGC services are inconsistent enough that every request should be checked against capabilities metadata:

* WMS 1.1.1 uses `SRS`; WMS 1.3.0 uses `CRS`.
* WMS 1.3.0 with `EPSG:4326` may expect bbox as latitude,longitude order. `CRS:84` keeps longitude,latitude order.
* WFS output may arrive as GML with service CRS axis order, not GeoJSON-style lon/lat.
* WMTS tile matrix sets define their own origin, scale denominator, and CRS; do not assume XYZ/Web Mercator unless the matrix set says so.

When in doubt, request a tiny bbox around a known point and inspect the coordinates before running a full extract.

## CRS troubleshooting checklist

When a layer "looks wrong" (off by a continent, scaled wrong, rotated):

1. **Check declared CRS:** `ogrinfo -so file.gpkg` or `gdf.crs`
2. **Check actual coordinate values:** are they in degree range (-180 to 180) or projected (large meter values)?
3. **Lat/lon swap?** If coordinates declared as EPSG:4326 but the "longitude" is between -90 and 90, you may have an axis-order issue.
4. **Missing CRS metadata?** GeoJSON without a CRS member is assumed EPSG:4326. Older Shapefiles often lack `.prj`. Set explicitly: `gdf.set_crs("EPSG:4326", inplace=True)` (note: `set_crs` declares; `to_crs` reprojects).
5. **Datum shift needed?** Older datasets may use NAD27, ED50, or local datums. Don't ignore — the offset can be hundreds of meters. **Ensure PROJ grid files are installed** (e.g., `proj-data` package) and `PROJ_NETWORK=ON` is set in your environment; otherwise, datum shifts might silently fall back to less accurate parameters.

## DuckDB Spatial CRS strictness — the rule

DuckDB Spatial 1.5+ refuses to mix CRS *labels* even when the underlying coordinates are identical. `ST_Intersects(geom_epsg_4326, geom_ogc_crs84)` errors out with *“geometries of different coordinate reference systems (CRS)… ‘EPSG:4326’ which is not compatible with ‘OGC:CRS84’”*. This bites every multi-layer pipeline because GeoJSON readers tag `OGC:CRS84` (RFC 7946 default), GeoParquet may say `EPSG:4326`, and shapefiles preserve whatever the `.prj` declares.

The rule that prevents the whole class of failures:

1. **Pick one label and normalise on read.** `OGC:CRS84` is the cleanest choice because it matches the GeoJSON spec — but `EPSG:4326` is fine if you prefer it; just be consistent.
   ```sql
   -- on every read of a layer that doesn't already carry the chosen label
   SELECT ST_SetCRS(geom, 'OGC:CRS84') AS geom_4326 FROM ST_Read('layer.geojson');
   SELECT ST_SetCRS(geom_wkb, 'OGC:CRS84') AS geom FROM read_parquet('layer.parquet');
   ```
2. **Use the same string in `ST_Transform`.** The third argument is a label match against the source geometry, so:
   ```sql
   ST_Transform(geom, 'OGC:CRS84', 'EPSG:3301', always_xy := true)
   ```
   not `'EPSG:4326'` if the geometry was set to `OGC:CRS84`. `always_xy := true` is critical for the same reason it is in pyproj — Estonia's `EPSG:3301` reprojection silently goes wrong without it.
3. **Don't trust the writer to round-trip.** When you `COPY (...) TO '...parquet'`, the geometry column is written with its current CRS metadata; on re-read DuckDB reconstructs the column as `GEOMETRY('<that-label>')` (not BLOB) — see `processing.md`. So whatever label was applied at write-time is the label you get back.

Treat any pipeline that mixes GeoJSON, Overture parquet, and shapefile inputs as suspect until you've explicitly normalised CRS labels on every read.

## Anti-patterns to flag and fix

* `gdf.buffer(100)` on EPSG:4326 — buffer in degrees is meaningless. Reproject first, buffer, then reproject back.
* `gdf.area` on EPSG:4326 — gives squared degrees.
* Computing area in EPSG:3857 (Web Mercator) — projected area is not ground area, and distortion grows strongly with latitude. Use a suitable equal-area or geodesic method.
* Joining two layers without checking `gdf1.crs == gdf2.crs`.
* Assuming a Shapefile without `.prj` is in WGS84 — it might be in a national grid.
* Using `pyproj.Transformer` without `always_xy=True` and silently getting axis order wrong.
* Mixing `'EPSG:4326'` and `'OGC:CRS84'` CRS labels in one DuckDB Spatial pipeline (see "DuckDB Spatial CRS strictness" above) — the coordinates agree, the labels don't, and joins fail.

## Validity and topology

Geometries that are technically loadable but topologically broken are a frequent source of surprise downstream failures.

```python
from shapely.validation import make_valid

gdf["geometry"] = gdf.geometry.apply(make_valid)

# Or filter out invalid:
invalid = gdf[~gdf.is_valid]
```

In PostGIS:

```sql
-- Find invalid
SELECT id, ST_IsValidReason(geom) FROM layer WHERE NOT ST_IsValid(geom);

-- Repair in place
UPDATE layer SET geom = ST_MakeValid(geom) WHERE NOT ST_IsValid(geom);
```

Always validate after a reprojection, an overlay, or an import from Shapefile.
