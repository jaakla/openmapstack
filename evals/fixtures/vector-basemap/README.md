# Synthetic vector basemap tile

`land.mvt` is an independently constructed MVT v2 tile containing a full-tile
polygon in the `land` source layer, with extent 4096. It has no external data
or provider dependency. Rebuild it with `python make_land_tile.py`.

The Tartu browser regression serves this tile through offline style/TileJSON
fixtures to exercise the real MapLibre renderer, theme switching, overlay
preservation and explicit-provider configuration. The basemap checker tests
separately prove that metadata-only requests and missing attribution fail.
These fixtures verify rendering/checker behavior; they do not prove that a
live agent follows the changed skill wording.
