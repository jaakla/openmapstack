from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from openmapstack.checks import geodata

from .helpers import make_workspace, minimal_project, write_project


def _write_geojson(path, features, crs=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"type": "FeatureCollection", "features": features}
    if crs:
        payload["crs"] = {"type": "name", "properties": {"name": crs}}
    path.write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _point_feature(properties, coords=(0.0, 0.0)):
    return {
        "type": "Feature",
        "properties": properties,
        "geometry": {"type": "Point", "coordinates": list(coords)},
    }


class ReadQueryTests(unittest.TestCase):
    def test_file_path_is_escaped_for_sql_literal(self) -> None:
        workspace = make_workspace()
        self.assertEqual(
            geodata._read(None, workspace / "owner's.parquet"),
            f"read_parquet('{workspace.as_posix()}/owner''s.parquet')",
        )


class DuckdbUnavailableTests(unittest.TestCase):
    """Every geodata assertion must return not_testable, never an implicit
    pass, when DuckDB Spatial cannot be loaded."""

    def test_row_count_not_testable_without_duckdb(self) -> None:
        workspace = make_workspace()
        target = workspace / "data.geojson"
        _write_geojson(target, [_point_feature({"id": 1})])
        with patch.object(geodata, "_connect", return_value=None):
            result = geodata.row_count(workspace, path="data.geojson", equals=1)
        self.assertEqual(result.status, "not_testable")
        self.assertEqual(result.data.get("code"), "duckdb_unavailable")

    def test_geometry_all_valid_not_testable_without_duckdb(self) -> None:
        workspace = make_workspace()
        target = workspace / "data.geojson"
        _write_geojson(target, [_point_feature({"id": 1})])
        with patch.object(geodata, "_connect", return_value=None):
            result = geodata.geometry_all_valid(workspace, path="data.geojson")
        self.assertEqual(result.status, "not_testable")


class RowCountTests(unittest.TestCase):
    def test_missing_file_fails(self) -> None:
        workspace = make_workspace()
        result = geodata.row_count(workspace, path="missing.geojson", equals=1)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.data.get("code"), "file_missing")

    def test_equals_matches(self) -> None:
        workspace = make_workspace()
        target = workspace / "data.geojson"
        _write_geojson(target, [_point_feature({"id": 1}), _point_feature({"id": 2})])
        result = geodata.row_count(workspace, path="data.geojson", equals=2)
        self.assertEqual(result.status, "passed")

    def test_equals_mismatch_fails(self) -> None:
        workspace = make_workspace()
        target = workspace / "data.geojson"
        _write_geojson(target, [_point_feature({"id": 1})])
        result = geodata.row_count(workspace, path="data.geojson", equals=5)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.data.get("code"), "row_count_equals")

    def test_at_least_and_at_most(self) -> None:
        workspace = make_workspace()
        target = workspace / "data.geojson"
        _write_geojson(target, [_point_feature({"id": 1})])
        self.assertEqual(
            geodata.row_count(workspace, path="data.geojson", at_least=2).data.get("code"),
            "row_count_at_least",
        )
        self.assertEqual(
            geodata.row_count(workspace, path="data.geojson", at_most=0).data.get("code"),
            "row_count_at_most",
        )


class GeometryAllValidTests(unittest.TestCase):
    def test_valid_geometry_passes(self) -> None:
        workspace = make_workspace()
        target = workspace / "data.geojson"
        _write_geojson(target, [_point_feature({"id": 1})])
        result = geodata.geometry_all_valid(workspace, path="data.geojson")
        self.assertEqual(result.status, "passed")

    def test_invalid_polygon_fails(self) -> None:
        workspace = make_workspace()
        target = workspace / "bad.geojson"
        bowtie = {
            "type": "Feature",
            "properties": {"id": 1},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0, 0], [2, 2], [2, 0], [0, 2], [0, 0]]],
            },
        }
        _write_geojson(target, [bowtie])
        result = geodata.geometry_all_valid(workspace, path="bad.geojson")
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.data.get("code"), "invalid_geometry")


class DuplicateAndNullIdsTests(unittest.TestCase):
    def test_no_duplicates_passes(self) -> None:
        workspace = make_workspace()
        target = workspace / "data.geojson"
        _write_geojson(target, [_point_feature({"id": 1}), _point_feature({"id": 2})])
        result = geodata.no_duplicate_ids(workspace, path="data.geojson", id_field="id")
        self.assertEqual(result.status, "passed")

    def test_duplicates_fail(self) -> None:
        workspace = make_workspace()
        target = workspace / "data.geojson"
        _write_geojson(target, [_point_feature({"id": 1}), _point_feature({"id": 1})])
        result = geodata.no_duplicate_ids(workspace, path="data.geojson", id_field="id")
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.data.get("code"), "duplicate_ids")

    def test_null_ids_fail(self) -> None:
        workspace = make_workspace()
        target = workspace / "data.geojson"
        _write_geojson(target, [_point_feature({"id": None}), _point_feature({"id": 2})])
        result = geodata.no_null_ids(workspace, path="data.geojson", id_field="id")
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.data.get("code"), "null_ids")


class FeaturePresenceTests(unittest.TestCase):
    def test_feature_present_passes_when_found(self) -> None:
        workspace = make_workspace()
        target = workspace / "data.geojson"
        _write_geojson(target, [_point_feature({"id": "a"})])
        result = geodata.feature_present(workspace, path="data.geojson", id_field="id", id="a")
        self.assertEqual(result.status, "passed")

    def test_feature_present_fails_when_missing(self) -> None:
        workspace = make_workspace()
        target = workspace / "data.geojson"
        _write_geojson(target, [_point_feature({"id": "a"})])
        result = geodata.feature_present(workspace, path="data.geojson", id_field="id", id="b")
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.data.get("code"), "feature_missing")

    def test_feature_absent_passes_when_missing(self) -> None:
        workspace = make_workspace()
        target = workspace / "data.geojson"
        _write_geojson(target, [_point_feature({"id": "a"})])
        result = geodata.feature_absent(workspace, path="data.geojson", id_field="id", id="b")
        self.assertEqual(result.status, "passed")

    def test_feature_absent_fails_when_present(self) -> None:
        workspace = make_workspace()
        target = workspace / "data.geojson"
        _write_geojson(target, [_point_feature({"id": "a"})])
        result = geodata.feature_absent(workspace, path="data.geojson", id_field="id", id="a")
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.data.get("code"), "feature_present")

    def test_feature_absent_not_testable_when_file_missing(self) -> None:
        workspace = make_workspace()
        result = geodata.feature_absent(workspace, path="missing.geojson", id_field="id", id="a")
        self.assertEqual(result.status, "not_testable")


class FeatureFieldEqualsTests(unittest.TestCase):
    def test_matching_field_passes(self) -> None:
        workspace = make_workspace()
        target = workspace / "data.geojson"
        _write_geojson(target, [_point_feature({"id": "a", "status": "closed"})])
        result = geodata.feature_field_equals(
            workspace, path="data.geojson", id_field="id", id="a", field="status", equals="closed"
        )
        self.assertEqual(result.status, "passed")

    def test_mismatched_field_fails(self) -> None:
        workspace = make_workspace()
        target = workspace / "data.geojson"
        _write_geojson(target, [_point_feature({"id": "a", "status": "active"})])
        result = geodata.feature_field_equals(
            workspace, path="data.geojson", id_field="id", id="a", field="status", equals="closed"
        )
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.data.get("code"), "field_value_mismatch")

    def test_missing_feature_fails(self) -> None:
        workspace = make_workspace()
        target = workspace / "data.geojson"
        _write_geojson(target, [_point_feature({"id": "a", "status": "active"})])
        result = geodata.feature_field_equals(
            workspace, path="data.geojson", id_field="id", id="zzz", field="status", equals="closed"
        )
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.data.get("code"), "feature_not_found")


class FieldRangeTests(unittest.TestCase):
    def test_within_range_passes(self) -> None:
        workspace = make_workspace()
        target = workspace / "data.geojson"
        _write_geojson(target, [_point_feature({"area": 500})])
        result = geodata.field_range(workspace, path="data.geojson", field="area", min=0, max=1000)
        self.assertEqual(result.status, "passed")

    def test_out_of_range_fails(self) -> None:
        workspace = make_workspace()
        target = workspace / "data.geojson"
        _write_geojson(target, [_point_feature({"area": 5000})])
        result = geodata.field_range(workspace, path="data.geojson", field="area", min=0, max=1000)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.data.get("code"), "field_out_of_range")


class CrsNotUsedForMetricsTests(unittest.TestCase):
    def test_projected_crs_passes(self) -> None:
        workspace = make_workspace()
        write_project(workspace, minimal_project())
        result = geodata.crs_not_used_for_metrics(workspace)
        self.assertEqual(result.status, "passed")

    def test_geographic_analysis_crs_fails(self) -> None:
        workspace = make_workspace()
        project = minimal_project()
        project["processing"]["analysis_crs"] = "EPSG:4326"
        project["processing"]["steps"][1]["operation"] = "distance_filter"
        write_project(workspace, project)
        result = geodata.crs_not_used_for_metrics(workspace)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.data.get("code"), "forbidden_analysis_crs")

    def test_step_level_forbidden_crs_fails(self) -> None:
        workspace = make_workspace()
        project = minimal_project()
        project["processing"]["steps"][1]["operation"] = "buffer"
        project["processing"]["steps"][1]["crs"] = "EPSG:3857"
        write_project(workspace, project)
        result = geodata.crs_not_used_for_metrics(workspace)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.data.get("code"), "forbidden_step_crs")

    def test_missing_analysis_crs_fails(self) -> None:
        workspace = make_workspace()
        project = minimal_project()
        del project["processing"]["analysis_crs"]
        write_project(workspace, project)
        result = geodata.crs_not_used_for_metrics(workspace)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.data.get("code"), "analysis_crs_missing")

    def test_geographic_crs_is_allowed_for_nonmetric_storage_step(self) -> None:
        workspace = make_workspace()
        project = minimal_project()
        project["processing"]["analysis_crs"] = "EPSG:4326"
        project["processing"]["steps"][1]["crs"] = "EPSG:4326"
        write_project(workspace, project)
        result = geodata.crs_not_used_for_metrics(workspace)
        self.assertEqual(result.status, "passed")


class DatasetCrsTests(unittest.TestCase):
    def test_actual_dataset_crs_passes(self) -> None:
        workspace = make_workspace()
        _write_geojson(
            workspace / "data.geojson",
            [_point_feature({"id": 1}, coords=(6500000, 650000))],
            crs="EPSG:3301",
        )
        result = geodata.dataset_crs_is(workspace, path="data.geojson", expected="EPSG:3301")
        self.assertEqual(result.status, "passed", result.detail)

    def test_actual_dataset_crs_mismatch_fails(self) -> None:
        workspace = make_workspace()
        _write_geojson(workspace / "data.geojson", [_point_feature({"id": 1})], crs="EPSG:4326")
        result = geodata.dataset_crs_is(workspace, path="data.geojson", expected="EPSG:3301")
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.data.get("code"), "dataset_crs_mismatch")

    def test_actual_dataset_crs_is_not_testable_without_duckdb(self) -> None:
        workspace = make_workspace()
        _write_geojson(workspace / "data.geojson", [_point_feature({"id": 1})])
        with patch.object(geodata, "_connect", return_value=None):
            result = geodata.dataset_crs_is(workspace, path="data.geojson", expected="EPSG:4326")
        self.assertEqual(result.status, "not_testable")


def _project_with_storage_crs(workspace, storage_crs):
    project = minimal_project()
    if storage_crs is None:
        project["processing"].pop("storage_crs", None)
    else:
        project["processing"]["storage_crs"] = storage_crs
    write_project(workspace, project)


def _write_plain_geoparquet(path):
    """GeoParquet with no `crs` key, as DuckDB writes it; readers take OGC:CRS84."""
    con = geodata._connect()
    con.execute(
        f"COPY (SELECT 1 AS id, ST_Point(26.7, 58.3) AS geometry) TO '{path.as_posix()}' (FORMAT parquet)"
    )


class DatasetCrsMatchesStorageCrsTests(unittest.TestCase):
    """Live case 070 declared `storage_crs: EPSG:4326` and wrote CRS84 GeoParquet."""

    def test_data_in_the_declared_projected_crs_passes(self) -> None:
        workspace = make_workspace()
        _project_with_storage_crs(workspace, "EPSG:3301")
        _write_geojson(workspace / "data.geojson", [_point_feature({"id": 1}, coords=(660000, 6470000))], crs="EPSG:3301")
        result = geodata.dataset_crs_matches_storage_crs(workspace, path="data.geojson")
        self.assertEqual(result.status, "passed", result.detail)

    def test_declared_epsg_4326_accepts_geoparquet_crs84(self) -> None:
        workspace = make_workspace()
        _project_with_storage_crs(workspace, "EPSG:4326")
        _write_plain_geoparquet(workspace / "candidates.parquet")
        result = geodata.dataset_crs_matches_storage_crs(workspace, path="candidates.parquet")
        self.assertEqual(result.status, "passed", result.detail)

    def test_data_that_contradicts_the_declaration_fails(self) -> None:
        workspace = make_workspace()
        _project_with_storage_crs(workspace, "EPSG:3301")
        _write_plain_geoparquet(workspace / "candidates.parquet")
        result = geodata.dataset_crs_matches_storage_crs(workspace, path="candidates.parquet")
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.data.get("code"), "dataset_crs_mismatch")

    def test_an_undeclared_storage_crs_fails(self) -> None:
        workspace = make_workspace()
        _project_with_storage_crs(workspace, None)
        _write_plain_geoparquet(workspace / "candidates.parquet")
        result = geodata.dataset_crs_matches_storage_crs(workspace, path="candidates.parquet")
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.data.get("code"), "storage_crs_missing")


# Tartu, in lon/lat; points are (lon, lat) unless a test swaps them.
_TARTU_AOI = {"bbox": [26.65, 58.32, 26.80, 58.42], "crs": "EPSG:4326"}
_TARTU = (26.72, 58.38)


def _aoi_workspace(features, crs="EPSG:4326", aoi=_TARTU_AOI):
    workspace = make_workspace()
    project = minimal_project()
    if aoi is not None:
        project["project"]["aoi"] = aoi
    write_project(workspace, project)
    _write_geojson(workspace / "layer.geojson", features, crs=crs)
    return workspace


class LayerExtentWithinAoiTests(unittest.TestCase):
    def test_tied_swap_evidence_keeps_the_generic_diagnosis(self) -> None:
        for inside in ([], [_point_feature({"id": 3}, coords=_TARTU)]):
            with self.subTest(partial=bool(inside)):
                workspace = _aoi_workspace(inside + [
                    _point_feature({"id": 1}, coords=_TARTU[::-1]),
                    _point_feature({"id": 2}, coords=(24.75, 59.44)),
                ])
                result = geodata.layer_extent_within_aoi(workspace, path="layer.geojson")
                self.assertEqual(result.status, "warning" if inside else "failed")
                self.assertEqual(result.data["code"], "features_outside_aoi" if inside else "extent_outside_aoi")
                self.assertEqual(result.data["features_swapped_inside"], 1)

    def test_strict_majority_of_outside_features_suggests_a_swap(self) -> None:
        workspace = _aoi_workspace([
            _point_feature({"id": 1}, coords=_TARTU),
            _point_feature({"id": 2}, coords=_TARTU[::-1]),
            _point_feature({"id": 3}, coords=(58.37, 26.73)),
            _point_feature({"id": 4}, coords=(24.75, 59.44)),
        ])
        result = geodata.layer_extent_within_aoi(workspace, path="layer.geojson")
        self.assertEqual((result.status, result.data["code"]), ("warning", "axis_swap_suspected"))

    def test_unrecognized_aoi_crs_fails(self) -> None:
        for crs in ("EPSG:bogus", "EPSG:999999", "not a CRS"):
            with self.subTest(crs=crs):
                workspace = _aoi_workspace([_point_feature({"id": 1}, coords=_TARTU)],
                    aoi={"bbox": _TARTU_AOI["bbox"], "crs": crs})
                result = geodata.layer_extent_within_aoi(workspace, path="layer.geojson")
                self.assertEqual((result.status, result.data["code"]), ("failed", "aoi_invalid"), result.detail)

    def test_aoi_coordinates_outside_the_crs_domain_fail(self) -> None:
        workspace = _aoi_workspace([_point_feature({"id": 1}, coords=_TARTU)],
            aoi={"bbox": [26.65, 100, 26.80, 101], "crs": "EPSG:4326"})
        result = geodata.layer_extent_within_aoi(workspace, path="layer.geojson")
        self.assertEqual((result.status, result.data["code"]), ("failed", "aoi_invalid"))

    def test_environmental_transform_failure_remains_not_testable(self) -> None:
        workspace = _aoi_workspace([_point_feature({"id": 1}, coords=_TARTU)])
        con = geodata._connect()
        self.assertIsNotNone(con)

        class UnavailableTransform:
            def execute(self, query, args=None):
                if "ST_Transform" in query and "ST_GeomFromText" in query:
                    raise RuntimeError("required datum grid unavailable")
                return con.execute(query, args)

            def close(self):
                con.close()

        with patch.object(geodata, "_connect", return_value=UnavailableTransform()):
            result = geodata.layer_extent_within_aoi(workspace, path="layer.geojson")
        self.assertEqual((result.status, result.data["code"]), ("not_testable", "aoi_transform_failed"))

    def test_local_layer_inside_the_aoi_passes(self) -> None:
        workspace = _aoi_workspace([_point_feature({"id": 1}, coords=_TARTU)])
        result = geodata.layer_extent_within_aoi(workspace, path="layer.geojson")
        self.assertEqual(result.status, "passed", result.detail)
        self.assertEqual(result.data["features_outside"], 0)

    def test_projected_layer_is_compared_in_its_own_crs(self) -> None:
        # Tartu in L-EST97; the lon/lat AOI is transformed, not the data.
        workspace = _aoi_workspace([_point_feature({"id": 1}, coords=(659000, 6474000))], crs="EPSG:3301")
        result = geodata.layer_extent_within_aoi(workspace, path="layer.geojson")
        self.assertEqual(result.status, "passed", result.detail)
        self.assertEqual(result.data["layer_crs"], "EPSG:3301")

    def test_lat_lon_swapped_layer_fails_as_axis_swap(self) -> None:
        # (58.38, 26.72) is legal as lon/lat, so ranges and the label pass.
        workspace = _aoi_workspace([_point_feature({"id": 1}, coords=_TARTU[::-1])])
        result = geodata.layer_extent_within_aoi(workspace, path="layer.geojson")
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.data.get("code"), "axis_swap_suspected")

    def test_swapped_projected_layer_fails_as_axis_swap(self) -> None:
        workspace = _aoi_workspace([_point_feature({"id": 1}, coords=(6474000, 659000))], crs="EPSG:3301")
        result = geodata.layer_extent_within_aoi(workspace, path="layer.geojson")
        self.assertEqual(result.data.get("code"), "axis_swap_suspected")

    def test_metres_labelled_as_degrees_fail_outside_the_aoi(self) -> None:
        workspace = _aoi_workspace([_point_feature({"id": 1}, coords=(659000, 6474000))], crs="EPSG:4326")
        result = geodata.layer_extent_within_aoi(workspace, path="layer.geojson")
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.data.get("code"), "extent_outside_aoi")

    def test_degrees_labelled_as_metres_fail_outside_the_aoi(self) -> None:
        workspace = _aoi_workspace([_point_feature({"id": 1}, coords=_TARTU)], crs="EPSG:3301")
        result = geodata.layer_extent_within_aoi(workspace, path="layer.geojson")
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.data.get("code"), "extent_outside_aoi")

    def test_some_features_outside_is_a_warning_naming_the_count(self) -> None:
        workspace = _aoi_workspace([
            _point_feature({"id": 1}, coords=_TARTU),
            _point_feature({"id": 2}, coords=(26.73, 58.37)),
            _point_feature({"id": 3}, coords=(24.75, 59.44)),  # Tallinn, 160 km away
        ])
        result = geodata.layer_extent_within_aoi(workspace, path="layer.geojson")
        self.assertEqual(result.status, "warning")
        self.assertEqual(result.data.get("code"), "features_outside_aoi")
        self.assertEqual((result.data["features_outside"], result.data["features_checked"]), (1, 3))

    def test_margin_admits_a_buffered_fetch_and_zero_margin_does_not(self) -> None:
        # 0.05 degrees east of a 0.15-degree-wide AOI.
        workspace = _aoi_workspace([_point_feature({"id": 1}, coords=(26.85, 58.38))])
        self.assertEqual(geodata.layer_extent_within_aoi(workspace, path="layer.geojson").status, "passed")
        strict = geodata.layer_extent_within_aoi(workspace, path="layer.geojson", margin=0)
        self.assertEqual(strict.status, "failed")

    def test_explicit_aoi_arguments_override_the_manifest(self) -> None:
        workspace = _aoi_workspace([_point_feature({"id": 1}, coords=_TARTU)], aoi=None)
        result = geodata.layer_extent_within_aoi(
            workspace, path="layer.geojson", aoi_bbox=[-74.3, 40.5, -73.7, 40.95], aoi_crs="EPSG:4326")
        self.assertEqual(result.data.get("code"), "extent_outside_aoi")

    def test_undeclared_aoi_is_not_testable_not_passed(self) -> None:
        workspace = _aoi_workspace([_point_feature({"id": 1}, coords=_TARTU)], aoi=None)
        result = geodata.layer_extent_within_aoi(workspace, path="layer.geojson")
        self.assertEqual(result.status, "not_testable")
        self.assertEqual(result.data.get("code"), "aoi_undeclared")

    def test_inverted_or_incomplete_aoi_fails(self) -> None:
        for aoi in ({"bbox": [26.8, 58.32, 26.65, 58.42], "crs": "EPSG:4326"},
                    {"bbox": [26.65, 58.32, 26.8], "crs": "EPSG:4326"},
                    {"bbox": [26.65, 58.32, 26.80, 58.42]}):
            workspace = _aoi_workspace([_point_feature({"id": 1}, coords=_TARTU)], aoi=aoi)
            result = geodata.layer_extent_within_aoi(workspace, path="layer.geojson")
            self.assertEqual(result.data.get("code"), "aoi_invalid", aoi)

    def test_empty_layer_is_not_testable(self) -> None:
        workspace = _aoi_workspace([])
        result = geodata.layer_extent_within_aoi(workspace, path="layer.geojson")
        self.assertEqual(result.status, "not_testable")

    def test_missing_layer_fails_and_missing_runtime_is_not_testable(self) -> None:
        workspace = _aoi_workspace([_point_feature({"id": 1}, coords=_TARTU)])
        missing = geodata.layer_extent_within_aoi(workspace, path="absent.geojson")
        self.assertEqual(missing.data.get("code"), "file_missing")
        with patch.object(geodata, "_connect", return_value=None):
            result = geodata.layer_extent_within_aoi(workspace, path="layer.geojson")
        self.assertEqual(result.status, "not_testable")
        self.assertEqual(result.data.get("code"), "duckdb_unavailable")


if __name__ == "__main__":
    unittest.main()
