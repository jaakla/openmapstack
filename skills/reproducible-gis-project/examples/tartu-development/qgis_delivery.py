"""Optional Tartu desktop adapter; owns QGIS styling and native/XML generation.

This module consumes the existing analytical outputs. It does not own analysis
or require PyQGIS for the deterministic static builder. Native checks stay honest.
"""
from __future__ import annotations
import json
import logging
import os
import zipfile
from pathlib import Path
log = logging.getLogger("tartu-qgis-delivery")

def _xml_text(value: str) -> str:
    """Escape a string for an XML text node or a double-quoted attribute."""
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# Full CRS definitions, exactly as QGIS serialises them. A <spatialrefsys>
# carrying only <srid>/<authid> reads back as an INVALID CRS: QGIS then
# cannot build a transform for that layer, and every layer whose CRS differs
# from the map's destination CRS silently paints nothing. The project looks
# healthy -- layers valid, datasources resolving, render not blank -- while
# most of the analysis is missing from it, so the WKT is mandatory.
CRS_DEFINITIONS = {
    3301: {
        "wkt": 'PROJCS["Estonian Coordinate System of 1997",GEOGCS["EST97",DATUM["Estonia_1997",SPHEROID["GRS 1980",6378137,298.257222101,AUTHORITY["EPSG","7019"]],AUTHORITY["EPSG","6180"]],PRIMEM["Greenwich",0,AUTHORITY["EPSG","8901"]],UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],AUTHORITY["EPSG","4180"]],PROJECTION["Lambert_Conformal_Conic_2SP"],PARAMETER["latitude_of_origin",57.5175539305556],PARAMETER["central_meridian",24],PARAMETER["standard_parallel_1",59.3333333333333],PARAMETER["standard_parallel_2",58],PARAMETER["false_easting",500000],PARAMETER["false_northing",6375000],UNIT["metre",1,AUTHORITY["EPSG","9001"]],AUTHORITY["EPSG","3301"]]',
        "proj4": '+proj=lcc +lat_0=57.5175539305556 +lon_0=24 +lat_1=59.3333333333333 +lat_2=58 +x_0=500000 +y_0=6375000 +ellps=GRS80 +towgs84=0,0,0,0,0,0,0 +units=m +no_defs',
        "srsid": 1259,
        "description": 'Estonian Coordinate System of 1997',
        "projectionacronym": 'lcc',
        "ellipsoidacronym": 'EPSG:7019',
        "geographic": False,
    },
    4326: {
        "wkt": 'GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563,AUTHORITY["EPSG","7030"]],AUTHORITY["EPSG","6326"]],PRIMEM["Greenwich",0,AUTHORITY["EPSG","8901"]],UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],AUTHORITY["EPSG","4326"]]',
        "proj4": '+proj=longlat +datum=WGS84 +no_defs',
        "srsid": 3452,
        "description": 'WGS 84',
        "projectionacronym": 'longlat',
        "ellipsoidacronym": 'EPSG:7030',
        "geographic": True,
    },
    3857: {
        "wkt": 'PROJCS["WGS 84 / Pseudo-Mercator",GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563,AUTHORITY["EPSG","7030"]],AUTHORITY["EPSG","6326"]],PRIMEM["Greenwich",0,AUTHORITY["EPSG","8901"]],UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],AUTHORITY["EPSG","4326"]],PROJECTION["Mercator_1SP"],PARAMETER["central_meridian",0],PARAMETER["scale_factor",1],PARAMETER["false_easting",0],PARAMETER["false_northing",0],UNIT["metre",1,AUTHORITY["EPSG","9001"]],AXIS["Easting",EAST],AXIS["Northing",NORTH],EXTENSION["PROJ4","+proj=merc +a=6378137 +b=6378137 +lat_ts=0 +lon_0=0 +x_0=0 +y_0=0 +k=1 +units=m +nadgrids=@null +wktext +no_defs"],AUTHORITY["EPSG","3857"]]',
        "proj4": '+proj=merc +a=6378137 +b=6378137 +lat_ts=0 +lon_0=0 +x_0=0 +y_0=0 +k=1 +units=m +nadgrids=@null +wktext +no_defs',
        "srsid": 3857,
        "description": 'WGS 84 / Pseudo-Mercator',
        "projectionacronym": 'merc',
        "ellipsoidacronym": 'EPSG:7030',
        "geographic": False,
    },
}


def _tier_subset(tier_label: str) -> str:
    """OGR subset expression selecting exactly one suitability tier.

    Each tier is its own QGIS layer because the manifest presents each in its
    own layer group; the subset is what makes the split real rather than a
    legend label.
    """
    return f"\"suitability_tier\" = '{tier_label}'"


def _qgis_layer_specs(
    school_count: int, kindergarten_count: int, scenario_inactive_count: int,
    *, ANALYSIS_CRS: int, STORAGE_CRS: int, SUITABILITY_TIERS: dict
) -> dict[tuple[str, str], dict | None]:
    """Every QGIS layer, keyed by the (layer group, source) pair that declares
    it in ``presentation.map.layers``.

    Keying on the manifest's own coordinates is what keeps `project.qgz` a
    mirror of the dashboard instead of a parallel hand-maintained map: a
    layer or group added to project.yaml with nothing here fails the run
    (see `_qgis_layer_tree`) rather than shipping a QGIS project that shows
    less than the manifest claims.

    A value of None means the manifest layer deliberately has no QGIS
    counterpart, and says why.
    """
    candidates_gpkg = "data/derived/final-candidates.gpkg"
    tier_style = {
        # Same three tier colours the dashboard and spec section 5.3 use.
        "candidates_tier1": ("46,125,50,190", "165,214,167,255", "0.6", "0.75"),
        "candidates_tier2": ("245,127,23,165", "255,245,157,255", "0.5", "0.65"),
        "candidates_highway": ("69,90,100,100", "144,164,174,255", "0.3", "0.40"),
    }
    tier_names = {
        "candidates_tier1": "Tier 1 Candidate Parcels (Prime)",
        "candidates_tier2": "Tier 2 Candidate Parcels (Good)",
        "candidates_highway": "Tier 3 Candidate Parcels (Highway Access Only)",
    }
    specs: dict[tuple[str, str], dict | None] = {}
    for group, tier_label in SUITABILITY_TIERS.items():
        fill, outline, outline_width, alpha = tier_style[group]
        specs[(group, "candidate_parcels_geojson")] = {
            "id": f"{group}_layer",
            "name": tier_names[group],
            "file": candidates_gpkg,
            # layername= is mandatory for GeoPackage: without it GDAL binds no
            # geometry table and QGIS loads a non-spatial attribute table.
            "uri_options": f"layername=final-candidates|subset={_tier_subset(tier_label)}",
            "geometry": "Polygon",
            "srid": ANALYSIS_CRS,
            "renderer": {
                "type": "single",
                "symbol": {
                    "kind": "fill",
                    "alpha": alpha,
                    "props": {
                        "color": fill,
                        "outline_color": outline,
                        "outline_width": outline_width,
                    },
                },
            },
        }

    specs[("catchments", "education_catchments_geojson")] = {
        "id": "education_catchments_layer",
        "name": "Education 25-minute Walking Catchments",
        "file": "data/derived/education_catchments.json",
        "uri_options": "",
        "geometry": "Polygon",
        "srid": STORAGE_CRS,
        "renderer": {
            "type": "categorized",
            "attr": "type",
            "categories": [
                {
                    "value": "school_catchment",
                    "label": "Municipal schools: 25-minute pedestrian catchment",
                    "symbol": {
                        "kind": "fill",
                        "alpha": "0.12",
                        "props": {
                            "color": "25,118,210,30",
                            "outline_color": "66,165,245,180",
                            "outline_style": "dash",
                            "outline_width": "0.5",
                        },
                    },
                },
                {
                    "value": "kindergarten_catchment",
                    "label": "Municipal kindergartens: 25-minute pedestrian catchment",
                    "symbol": {
                        "kind": "fill",
                        "alpha": "0.10",
                        "props": {
                            "color": "245,124,0,25",
                            "outline_color": "255,167,38,180",
                            "outline_style": "dash",
                            "outline_width": "0.5",
                        },
                    },
                },
            ],
        },
    }

    specs[("education_pois", "education_pois_geojson")] = {
        "id": "education_pois_layer",
        "name": "Verified Municipal Schools & Kindergartens",
        "file": "data/derived/education_pois.json",
        # Mirrors the manifest layer's `map_class <> 'scenario_inactive'`
        # filter: the facility OVERRIDE-001 switches off belongs to the
        # override group, not to the verified-source group.
        "uri_options": "subset=\"map_class\" <> 'scenario_inactive'",
        "geometry": "Point",
        "srid": STORAGE_CRS,
        "renderer": {
            "type": "categorized",
            "attr": "map_class",
            "categories": [
                {
                    "value": "school",
                    "label": f"Verified municipal schools (n={school_count})",
                    "symbol": {
                        "kind": "marker",
                        "alpha": "1",
                        "props": {
                            "color": "66,165,245,255",
                            "outline_color": "255,255,255,255",
                            "outline_width": "0.4",
                            "size": "3.5",
                        },
                    },
                },
                {
                    "value": "kindergarten",
                    "label": f"Verified municipal kindergartens (n={kindergarten_count})",
                    "symbol": {
                        "kind": "marker",
                        "alpha": "1",
                        "props": {
                            "color": "255,167,38,255",
                            "outline_color": "255,255,255,255",
                            "outline_width": "0.4",
                            "size": "3.5",
                        },
                    },
                },
            ],
        },
    }

    specs[("infrastructure", "main_roads_geojson")] = {
        "id": "main_roads_layer",
        "name": "Official National Highways (ETAK)",
        "file": "data/derived/main_roads.json",
        "uri_options": "",
        "geometry": "Line",
        "srid": STORAGE_CRS,
        "renderer": {
            "type": "single",
            "symbol": {
                "kind": "line",
                "alpha": "0.8",
                "props": {
                    "line_color": "121,134,203,255",
                    "line_style": "solid",
                    "line_width": "0.8",
                },
            },
        },
    }

    specs[("user_overrides", "education_pois_geojson")] = {
        "id": "override_pois_layer",
        "name": "Scenario Facility Outage (OVERRIDE-001)",
        "file": "data/derived/education_pois.json",
        "uri_options": "subset=\"map_class\" = 'scenario_inactive'",
        "geometry": "Point",
        "srid": STORAGE_CRS,
        "renderer": {
            "type": "categorized",
            "attr": "map_class",
            "categories": [
                {
                    "value": "scenario_inactive",
                    "label": f"Scenario outage: excluded from analysis (n={scenario_inactive_count})",
                    "symbol": {
                        "kind": "marker",
                        "alpha": "1",
                        "props": {
                            "color": "120,120,120,255",
                            "outline_color": "229,57,53,255",
                            "outline_width": "0.8",
                            "size": "3.8",
                        },
                    },
                },
            ],
        },
    }

    specs[("user_overrides", "planned_roads")] = {
        "id": "planned_road_layer",
        "name": "Hypothetical Connector Road (OVERRIDE-002)",
        "file": "data/overrides/planned-road.geojson",
        "uri_options": "",
        "geometry": "Line",
        "srid": STORAGE_CRS,
        "renderer": {
            "type": "single",
            "symbol": {
                "kind": "line",
                "alpha": "1",
                "props": {
                    "line_color": "255,213,79,255",
                    "line_style": "dash",
                    "line_width": "1.0",
                },
            },
        },
    }

    # Browser-local drafts live in the viewer's localStorage until they are
    # exported as an override bundle and applied by this pipeline. There is no
    # file for QGIS to open, and inventing one would assert that unvalidated
    # sketches are part of the accepted run.
    specs[("user_overrides", "draft_overrides")] = None
    return specs


# The dashboard's CARTO Positron background is a MapLibre vector style, which
# QGIS cannot read; CARTO's raster XYZ equivalent answers unauthenticated
# requests with an "API KEY REQUIRED" watermark, so shipping it as the desktop
# default would put that watermark across every QGIS view of the analysis. The
# national Baaskaart is the authoritative Estonian background, needs no key,
# and is served natively in the project's own EPSG:3301.
BASEMAP_LAYERS = [
    {
        "id": "maaamet_basemap_layer",
        "name": "Maa- ja Ruumiamet: Baaskaart (WMS)",
        "datasource": "contextualWMSLegend=0&crs=EPSG:3301&dpiMode=7&featureCount=10&format=image/png&layers=BAASKAART&styles=&url=https://kaart.maaamet.ee/wms/alus",
        "srid": 3301,
        "checked": True,
    },
    {
        "id": "osm_basemap_layer",
        "name": "OpenStreetMap (XYZ)",
        "datasource": "type=xyz&url=https://tile.openstreetmap.org/{z}/{x}/{y}.png&zmax=19&zmin=0",
        "srid": 3857,
        "checked": False,
    },
]


def _qgis_layer_tree(specs: dict[tuple[str, str], dict | None], PROJECT: dict) -> list[dict]:
    """The QGIS layer tree, top-to-bottom, built from the manifest itself.

    Two rules from spec section 5.3 are enforced here rather than trusted:

    * every group in ``presentation.map.layer_groups`` becomes a real
      ``<layer-tree-group>``, so the .qgz cannot claim a different
      organisation than the dashboard does; and
    * the tree is built in *reverse* manifest order. ``presentation.map.layers``
      is ordered bottom-to-top the way a web map paints, while QGIS paints its
      first tree entry on top — copying the order across would bury the POI
      markers under the parcel fill.
    """
    map_decl = PROJECT["presentation"]["map"]
    titles = {group["id"]: group.get("title") or group["id"] for group in map_decl["layer_groups"]}
    open_state = {group["id"]: bool(group.get("default_open", True)) for group in map_decl["layer_groups"]}
    declared = [(layer["group"], layer["source"]) for layer in map_decl["layers"]]

    unknown = [key for key in declared if key not in specs]
    if unknown:
        raise RuntimeError(f"presentation.map.layers declares {unknown} with no QGIS counterpart")
    undeclared_group = sorted({group for group, _ in declared} - set(titles))
    if undeclared_group:
        raise RuntimeError(f"presentation.map.layers uses undeclared layer groups: {undeclared_group}")

    tree: list[dict] = []
    for group, source in reversed(declared):
        spec = specs[(group, source)]
        if spec is None:
            continue
        entry = next((e for e in tree if e["id"] == group), None)
        if entry is None:
            entry = {"id": group, "title": titles[group], "expanded": open_state[group], "layers": []}
            tree.append(entry)
        entry["layers"].append(spec)

    empty = [group for group in titles if not any(e["id"] == group for e in tree)]
    if empty:
        raise RuntimeError(f"manifest layer groups would be absent from the QGIS tree: {empty}")
    return tree


def _pyqgis_payload(tree: list[dict], PROJECT: dict, ANALYSIS_CRS: int) -> str:
    """The layer tree as JSON for the PyQGIS builder, with datasources
    rewritten to the container's mount point."""
    payload = {
        "title": PROJECT["project"]["title"],
        "crs": f"EPSG:{ANALYSIS_CRS}",
        "groups": [
            {
                "title": group["title"],
                "expanded": group["expanded"],
                "layers": [
                    {
                        **spec,
                        "uri": "/workspace/" + spec["file"]
                        + (f"|{spec['uri_options']}" if spec["uri_options"] else ""),
                    }
                    for spec in group["layers"]
                ],
            }
            for group in tree
        ],
        "basemaps": BASEMAP_LAYERS,
    }
    return json.dumps(payload)


PYQGIS_BUILDER = r'''
import json
from qgis.core import (
    QgsApplication, QgsProject, QgsVectorLayer, QgsRasterLayer,
    QgsCoordinateReferenceSystem, QgsCategorizedSymbolRenderer,
    QgsRendererCategory, QgsFillSymbol, QgsLineSymbol, QgsMarkerSymbol,
    QgsSingleSymbolRenderer,
)

PLAN = json.loads(r"""__PAYLOAD__""")
SYMBOL = {"fill": QgsFillSymbol, "line": QgsLineSymbol, "marker": QgsMarkerSymbol}


def build_symbol(spec):
    symbol = SYMBOL[spec["kind"]].createSimple(spec["props"])
    symbol.setOpacity(float(spec["alpha"]))
    return symbol


def build_renderer(spec):
    if spec["type"] == "single":
        return QgsSingleSymbolRenderer(build_symbol(spec["symbol"]))
    categories = [
        QgsRendererCategory(c["value"], build_symbol(c["symbol"]), c["label"])
        for c in spec["categories"]
    ]
    return QgsCategorizedSymbolRenderer(spec["attr"], categories)


QgsApplication.setPrefixPath("/usr", True)
qgs = QgsApplication([], False)
qgs.initQgis()

project = QgsProject.instance()
project.clear()
project.setTitle(PLAN["title"])
project.setCrs(QgsCoordinateReferenceSystem(PLAN["crs"]))
root = project.layerTreeRoot()
root.clear()

invalid = []
for group in PLAN["groups"]:
    node = root.addGroup(group["title"])
    node.setExpanded(group["expanded"])
    for spec in group["layers"]:
        layer = QgsVectorLayer(spec["uri"], spec["name"], "ogr")
        layer.setRenderer(build_renderer(spec["renderer"]))
        if not layer.isValid():
            invalid.append(spec["name"])
        project.addMapLayer(layer, False)
        node.addLayer(layer)

# The basemap group goes last so it paints underneath every analysis layer.
base = root.addGroup("Basemaps")
for spec in PLAN["basemaps"]:
    layer = QgsRasterLayer(spec["datasource"], spec["name"], "wms")
    if not layer.isValid():
        invalid.append(spec["name"])
    project.addMapLayer(layer, False)
    base.addLayer(layer)
    root.findLayer(layer.id()).setItemVisibilityChecked(bool(spec["checked"]))

if invalid:
    raise RuntimeError("Invalid QGIS layers: " + ", ".join(invalid))
if not project.write("/workspace/project.qgz"):
    raise RuntimeError("QGIS project write failed")
qgs.exitQgis()
'''


def _qgs_symbol_xml(index: int, symbol: dict) -> str:
    kind = symbol["kind"]
    symbol_layer_class = {"fill": "SimpleFill", "line": "SimpleLine", "marker": "SimpleMarker"}[kind]
    props = "".join(
        '<prop k="{}" v="{}"/>'.format(_xml_text(key), _xml_text(value))
        for key, value in symbol["props"].items()
    )
    return '<symbol type="{}" name="{}" alpha="{}"><layer class="{}" enabled="1">{}</layer></symbol>'.format(
        kind, index, symbol["alpha"], symbol_layer_class, props
    )


def _qgs_renderer_xml(renderer: dict) -> str:
    if renderer["type"] == "single":
        return (
            '<renderer-v2 type="singleSymbol" enableorderby="0">\n'
            "        <symbols>{}</symbols>\n"
            "      </renderer-v2>"
        ).format(_qgs_symbol_xml(0, renderer["symbol"]))
    categories = "".join(
        '<category value="{}" symbol="{}" label="{}" render="true"/>'.format(
            _xml_text(category["value"]), index, _xml_text(category["label"])
        )
        for index, category in enumerate(renderer["categories"])
    )
    symbols = "".join(
        _qgs_symbol_xml(index, category["symbol"])
        for index, category in enumerate(renderer["categories"])
    )
    return (
        '<renderer-v2 type="categorizedSymbol" attr="{}" enableorderby="0">\n'
        "        <categories>{}</categories>\n"
        "        <symbols>{}</symbols>\n"
        "      </renderer-v2>"
    ).format(_xml_text(renderer["attr"]), categories, symbols)


def _spatialrefsys_xml(srid: int, indent: str = "      ") -> str:
    """A complete <spatialrefsys> element for an EPSG code.

    A <maplayer> with no CRS at all is assumed to be in the project CRS and is
    never reprojected -- that is how a Web Mercator basemap ends up drawn
    ~1500 km from an EPSG:3301 analysis. An incomplete one is just as bad in a
    quieter way: see CRS_DEFINITIONS.
    """
    crs = CRS_DEFINITIONS[srid]
    fields = [
        ("wkt", crs["wkt"]),
        ("proj4", crs["proj4"]),
        ("srsid", crs["srsid"]),
        ("srid", srid),
        ("authid", f"EPSG:{srid}"),
        ("description", crs["description"]),
        ("projectionacronym", crs["projectionacronym"]),
        ("ellipsoidacronym", crs["ellipsoidacronym"]),
        ("geographicflag", "true" if crs["geographic"] else "false"),
    ]
    body = "".join(
        f"\n{indent}  <{tag}>{_xml_text(value)}</{tag}>" for tag, value in fields
    )
    return f'{indent}<spatialrefsys nativeFormat="Wkt">{body}\n{indent}</spatialrefsys>'


def _qgs_srs_xml(srid: int) -> str:
    return "<srs>\n{}\n      </srs>".format(_spatialrefsys_xml(srid, "        "))


def _qgs_vector_layer_xml(spec: dict) -> str:
    datasource = "./" + spec["file"] + (f"|{spec['uri_options']}" if spec["uri_options"] else "")
    return """    <maplayer type="vector" geometry="{geometry}" hasScaleBasedVisibilityFlag="0" readOnly="0" maxScale="0" minScale="1e+08" styleCategories="AllStyleCategories">
      <id>{id}</id>
      <datasource>{datasource}</datasource>
      <layername>{name}</layername>
      {srs}
      <provider encoding="UTF-8">ogr</provider>
      {renderer}
    </maplayer>
""".format(
        geometry=spec["geometry"],
        id=spec["id"],
        datasource=_xml_text(datasource),
        name=_xml_text(spec["name"]),
        srs=_qgs_srs_xml(spec["srid"]),
        renderer=_qgs_renderer_xml(spec["renderer"]),
    )


def _qgs_raster_layer_xml(spec: dict) -> str:
    return """    <maplayer type="raster" hasScaleBasedVisibilityFlag="0" maxScale="0" minScale="1e+08" styleCategories="AllStyleCategories">
      <id>{id}</id>
      <datasource>{datasource}</datasource>
      <layername>{name}</layername>
      {srs}
      <provider>wms</provider>
      <pipe><provider><resampling enabled="false"/></provider><rasterrenderer type="singlebandcolordata" opacity="1"/></pipe>
    </maplayer>
""".format(
        id=spec["id"],
        datasource=_xml_text(spec["datasource"]),
        name=_xml_text(spec["name"]),
        srs=_qgs_srs_xml(spec["srid"]),
    )


def _qgs_document(tree: list[dict], PROJECT: dict, ANALYSIS_CRS: int) -> str:
    """Serialise the layer tree to a .qgs document.

    This is the default builder: it needs no QGIS install, so the example
    reproduces anywhere Python and DuckDB run. Set
    OPENMAPSTACK_USE_QGIS_DOCKER=1 to have PyQGIS write the same tree.
    """
    tree_xml = ""
    for group in tree:
        layers = "".join(
            '      <layer-tree-layer id="{}" name="{}" providerKey="ogr" expanded="1" checked="Qt.Checked"/>\n'.format(
                spec["id"], _xml_text(spec["name"])
            )
            for spec in group["layers"]
        )
        tree_xml += '    <layer-tree-group name="{}" expanded="{}" checked="Qt.Checked">\n{}    </layer-tree-group>\n'.format(
            _xml_text(group["title"]), "1" if group["expanded"] else "0", layers
        )
    basemap_tree = "".join(
        '      <layer-tree-layer id="{}" name="{}" providerKey="wms" expanded="0" checked="{}"/>\n'.format(
            spec["id"], _xml_text(spec["name"]), "Qt.Checked" if spec["checked"] else "Qt.Unchecked"
        )
        for spec in BASEMAP_LAYERS
    )
    tree_xml += '    <layer-tree-group name="Basemaps" expanded="1" checked="Qt.Checked">\n{}    </layer-tree-group>\n'.format(
        basemap_tree
    )

    layers_xml = "".join(
        _qgs_vector_layer_xml(spec) for group in tree for spec in group["layers"]
    ) + "".join(_qgs_raster_layer_xml(spec) for spec in BASEMAP_LAYERS)

    return """<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis projectname="{project_id}" version="3.44.3">
  <homePath path=""/>
  <title>{title}</title>
  <autotransaction active="0"/>
  <evaluateDefaultValues active="0"/>
  <trust active="0"/>
  <projectCrs>
{project_crs}
  </projectCrs>
  <layer-tree-group>
    <customproperties/>
{tree}  </layer-tree-group>
  <mapcanvas>
    <units>meters</units>
    <extent>
      <xmin>645000</xmin>
      <ymin>6460000</ymin>
      <xmax>675000</xmax>
      <ymax>6490000</ymax>
    </extent>
    <rotation>0</rotation>
    <destinationsrs>
{project_crs}
    </destinationsrs>
  </mapcanvas>
  <projectlayers>
{layers}  </projectlayers>
  <!-- ProjectionsEnabled is not decoration: without it QGIS reads the project
       back with NO project CRS at all, however complete <projectCrs> is, and
       opens this metric Estonian analysis in whatever CRS the reader's
       defaults supply. Paths/Absolute=false keeps the relative datasources
       relative when a reader saves the project. -->
  <properties>
    <Measurement>
      <AreaUnits type="QString">m2</AreaUnits>
      <DistanceUnits type="QString">meters</DistanceUnits>
    </Measurement>
    <Paths>
      <Absolute type="bool">false</Absolute>
    </Paths>
    <SpatialRefSys>
      <ProjectionsEnabled type="int">1</ProjectionsEnabled>
    </SpatialRefSys>
  </properties>
</qgis>""".format(
        project_id=_xml_text(PROJECT["project"]["id"]),
        title=_xml_text(PROJECT["project"]["title"]),
        project_crs=_spatialrefsys_xml(ANALYSIS_CRS, "    "),
        tree=tree_xml,
        layers=layers_xml,
    )


def write_qgis_project(con, *, ROOT: Path, PROJECT: dict, ANALYSIS_CRS: int,
                       STORAGE_CRS: int, SUITABILITY_TIERS: dict, scenario_inactive_count: int) -> Path:
    """Generate a fully-styled QGIS project (.qgz) mirroring the dashboard.

    The layer tree comes from `presentation.map.layer_groups` /
    `presentation.map.layers` in project.yaml, so the QGIS companion cannot
    quietly reorganise what the manifest says the product contains.
    """
    import subprocess

    zpath = ROOT / "project.qgz"
    school_count = int(con.execute("SELECT COUNT(*) FROM schools").fetchone()[0])
    kindergarten_count = int(con.execute("SELECT COUNT(*) FROM kindergartens").fetchone()[0])

    tree = _qgis_layer_tree(
        _qgis_layer_specs(school_count, kindergarten_count, scenario_inactive_count,
                          ANALYSIS_CRS=ANALYSIS_CRS, STORAGE_CRS=STORAGE_CRS, SUITABILITY_TIERS=SUITABILITY_TIERS), PROJECT
    )

    # Optional: let a real QGIS binary write the same tree, for projects that
    # want QGIS's own serialisation rather than the standalone builder.
    if os.environ.get("OPENMAPSTACK_USE_QGIS_DOCKER") == "1":
        try:
            res = subprocess.run(
                [
                    "docker", "run", "--rm", "-u", f"{os.getuid()}:{os.getgid()}",
                    "-e", "QT_QPA_PLATFORM=offscreen", "-e", "HOME=/tmp",
                    "-v", f"{ROOT}:/workspace", "-w", "/workspace",
                    "qgis/qgis:3.44.3", "python3", "-c",
                    PYQGIS_BUILDER.replace("__PAYLOAD__", _pyqgis_payload(tree, PROJECT, ANALYSIS_CRS)),
                ],
                capture_output=True,
                text=True,
                timeout=180,
            )
            if res.returncode == 0 and zpath.exists():
                log.info("QGIS project compiled natively via PyQGIS: %s", zpath)
                return zpath
            log.warning("PyQGIS docker runner failed (%s), falling back to the XML builder", res.stderr.strip()[-500:])
        except Exception as exc:  # noqa: BLE001 - docker absent, image missing, timeout
            log.warning("PyQGIS docker runner unavailable (%s), using the XML builder", exc)
    else:
        log.info("Using deterministic standalone QGIS XML builder")

    xml = _qgs_document(tree, PROJECT, ANALYSIS_CRS)
    (ROOT / "project.qgs").write_text(xml)
    zip_info = zipfile.ZipInfo("project.qgs", date_time=(1980, 1, 1, 0, 0, 0))
    zip_info.compress_type = zipfile.ZIP_DEFLATED
    zip_info.external_attr = 0o100644 << 16
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(zip_info, xml.encode("utf-8"))
    log.info("QGIS project generated: %s (%d groups)", zpath, len(tree) + 1)
    return zpath
