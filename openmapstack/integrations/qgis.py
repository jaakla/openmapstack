"""Optional native vector-report QGIS delivery from declared analytical artifacts.

This bounded adapter supports simple vector symbols/opacity. Basemaps and richer
cartography use an explicit project adapter, as in the Tartu worked example.

Run in an interpreter with PyQGIS and OpenMapStack installed:
``python -m openmapstack.integrations.qgis project.yaml --target qgis``.
Reusable static and native QA remains in ``openmapstack.checks.qgis``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ..delivery import declaration_errors, metadata_json, targets
from ..project import load_project, project_path


def generate(root: Path, project: dict, target_id: str) -> Path:
    errors = declaration_errors(project, root)
    if errors:
        raise ValueError("; ".join(errors))
    target = next((t for t in targets(project) if t["id"] == target_id and t["kind"] == "qgis"), None)
    if target is None:
        raise ValueError("QGIS target is not selected")
    # PyQGIS is deliberately imported only when the integration is invoked.
    from qgis.core import (Qgis, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
                           QgsExpressionContextUtils, QgsProject, QgsRectangle,
                           QgsReferencedRectangle, QgsVectorLayer)
    from ..checks.qgis import (_qgis_application, _manifest_layer_files,
                               _acceptable_files, _is_client_local)

    _qgis_application()
    desktop = QgsProject()
    output = project_path(root, project["outputs"][target["output"]]["path"])
    output.parent.mkdir(parents=True, exist_ok=True)
    desktop.setFileName(str(output))
    desktop.setFilePathStorage(Qgis.FilePathType.Relative)
    desktop.setCrs(QgsCoordinateReferenceSystem(project["processing"]["analysis_crs"]))
    if not desktop.crs().isValid():
        raise ValueError("declared analysis CRS is not valid in QGIS")
    presentation = project["presentation"].get("map") or {}
    if presentation.get("basemap") or project["presentation"].get("primary_view") == "map":
        raise ValueError("map-primary/basemap delivery needs a richer explicit QGIS adapter")
    groups = {g["id"]: desktop.layerTreeRoot().addGroup(g.get("title") or g["id"])
              for g in presentation.get("layer_groups") or []}
    for group in presentation.get("layer_groups") or []:
        groups[group["id"]].setExpanded(group.get("default_open", True))
    files = _manifest_layer_files(project)
    layers = []
    try:
        # Manifest layers are bottom-to-top; QGIS tree entries are top-to-bottom.
        for spec in reversed(presentation.get("layers") or []):
            if _is_client_local(spec):
                continue
            paths = _acceptable_files(files, spec.get("source"))
            data = next((project_path(root, p) for p in paths
                         if Path(p).suffix.lower() in {".geojson", ".json", ".gpkg", ".fgb", ".shp"}), None)
            if data is None or not data.is_file():
                raise ValueError(f"no portable analytical datasource for {spec.get('source')}")
            source = str(data)
            if data.suffix.lower() == ".gpkg":
                layer_name = spec.get("table_name")
                if not layer_name:
                    raise ValueError("GeoPackage layer requires table_name in presentation mapping")
                source += "|layername=" + layer_name
            layer = QgsVectorLayer(source, spec.get("title") or spec["source"], "ogr")
            if not layer.isValid() or not layer.crs().isValid():
                raise ValueError(f"QGIS failed to load {spec['source']} with a valid CRS")
            style = spec.get("style") or {}
            if set(style) - {"opacity", "visual_priority"}:
                raise ValueError("richer vector styling needs an explicit QGIS adapter")
            symbol = layer.renderer().symbol()
            if symbol is not None:
                from qgis.PyQt.QtGui import QColor
                colors = {"primary_result": "#2a75a6", "user_override": "#ba691f",
                          "hypothetical": "#ba691f", "constraint": "#a75050"}
                symbol.setColor(QColor(colors.get(spec.get("semantic_role"), "#83958c")))
                layer.setOpacity(float(style.get("opacity", 0.75)))
            desktop.addMapLayer(layer, False)
            (groups.get(spec.get("group")) or desktop.layerTreeRoot()).addLayer(layer)
            layers.append(layer)
        if not layers:
            raise ValueError("QGIS delivery requires at least one declared portable layer")
        extent = QgsRectangle()
        extent.setMinimal()
        for layer in layers:
            transform = QgsCoordinateTransform(layer.crs(), desktop.crs(), desktop)
            extent.combineExtentWith(transform.transformBoundingBox(layer.extent()))
        extent.scale(1.1)
        desktop.viewSettings().setDefaultViewExtent(QgsReferencedRectangle(extent, desktop.crs()))
        QgsExpressionContextUtils.setProjectVariable(desktop, "openmapstack_delivery",
                                                     metadata_json(root, project, target))
        metadata = desktop.metadata()
        metadata.setTitle(project["project"]["title"])
        metadata.setAuthor(project["project"].get("author", {}).get("name", ""))
        lines = [project["interpretation"]["objective"]]
        for source in project["sources"].values():
            lines.append(f"{source['provider']}: {source.get('dataset')}; "
                         f"{source.get('version')}; {source.get('license')}")
        lines += [f"{a['statement']} {a['rationale']}" for a in project["interpretation"]["assumptions"]]
        lines += [str(w["statement"]) for w in project.get("warnings") or []]
        metadata.setAbstract("\n".join(lines))
        desktop.setMetadata(metadata)
        if not desktop.write():
            raise ValueError("QGIS could not write the selected archive")
        desktop.clear()
        if not desktop.read(str(output)) or not desktop.crs().isValid():
            raise ValueError("saved QGIS project failed to reload with a valid CRS")
        if any(not layer.isValid() or not layer.crs().isValid() for layer in desktop.mapLayers().values()):
            raise ValueError("saved QGIS project contains invalid layers or CRS")
        return output
    finally:
        desktop.clear()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("--target", default="qgis")
    args = parser.parse_args()
    path, project = load_project(args.project)
    print(generate(path.parent, project, args.target))


if __name__ == "__main__":
    main()
