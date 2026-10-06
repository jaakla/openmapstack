Use the named WGS84 coordinates in `data/source/anchor.json` to prepare the
same anchor point for these four API boundaries. Write `coordinate-payloads.json`
with exactly these entries:

- `geojson`: a GeoJSON Point geometry.
- `maplibre`: the two-number array accepted by MapLibre GL JS `LngLatLike`.
- `leaflet_latlng`: the two-number array accepted by Leaflet `L.latLng`.
- `leaflet_geojson`: a GeoJSON Point geometry passed to Leaflet's GeoJSON layer.

Use each representation's native coordinate order and preserve the supplied
location. All entries describe the same WGS84 point; do not reproject it.
This is a small interface-conversion task: no dashboard, QGIS project, or full
analysis-project scaffold is required. Deliver numeric arrays, not explanations
or declared ordering labels alone.
