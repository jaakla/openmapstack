# Coordinate-order anchor

`anchor.json` is a named-coordinate test input near Tallinn. Both 24.7536 and
59.4370 are legal as either longitude or latitude, so a range check cannot
detect a swap. Case 018's ordered oracle pairs are fixed independently of the
generator and submitted output; the shared EPSG:4326 label cannot choose an
interface's tuple order.

Boundary contracts used by the oracle:

- [GeoJSON positions](https://www.rfc-editor.org/rfc/rfc7946#section-3.1.1): longitude, latitude.
- [MapLibre LngLat](https://maplibre.org/maplibre-gl-js/docs/API/classes/LngLat/): longitude, latitude.
- [Leaflet LatLng](https://leafletjs.com/reference.html#latlng): latitude, longitude.
- [Leaflet GeoJSON](https://leafletjs.com/reference.html#geojson-coordstolatlng): the input retains GeoJSON order; the library converts it to LatLng.

`gen.py` writes the healthy control or swaps exactly one of those boundaries.
Mutations 929–932 keep the other three pairs healthy and leave the source bytes
unchanged. No network, GIS runtime, or model account is required. These artifacts
measure interface order; they do not prove map rendering or live-agent adherence.
