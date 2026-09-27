"""Pedestrian-network evidence and parcel screening in the Tartu example."""

import importlib.util
import json
import sqlite3
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from openmapstack.checks.spatial import connect_spatial


EXAMPLE = Path(__file__).resolve().parents[1] / "examples/tartu-development"


def load_module(name):
    specification = importlib.util.spec_from_file_location(name, EXAMPLE / f"{name}.py")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class RoutingEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.routing = load_module("routing")
        cls.project = yaml.safe_load((EXAMPLE / "project.yaml").read_text())

    def test_request_models_walking_to_facilities_at_declared_thresholds(self):
        feature = {"properties": {"source_id": "school:test"},
                   "geometry": {"type": "Point", "coordinates": [26.72, 58.37]}}
        settings = self.project["processing"]["routing"]
        request = self.routing.isochrone_request(feature, settings)
        self.assertEqual(request["costing"], "pedestrian")
        self.assertTrue(request["reverse"])
        self.assertTrue(request["polygons"])
        self.assertEqual([contour["time"] for contour in request["contours"]], [15, 20, 25, 30, 40])
        self.assertEqual(request["costing_options"]["pedestrian"]["walking_speed"], 4.8)
        self.assertEqual(request["locations"][0]["radius"], 0)
        self.assertEqual(request["locations"][0]["search_cutoff"], 200)
        self.assertEqual(request["denoise"], 0)

    def test_facilities_near_the_network_boundary_are_rejected(self):
        feature = {"properties": {"source_id": "school:edge"},
                   "geometry": {"type": "Point", "coordinates": [26.3001, 58.3]}}
        with self.assertRaisesRegex(RuntimeError, "boundary"):
            self.routing.isochrone_request(feature, self.project["processing"]["routing"])

    def test_missing_duplicate_and_non_time_contours_are_rejected(self):
        response = {"features": [{"properties": {"contour": minute, "metric": "time"},
                                  "geometry": {"type": "Polygon"}} for minute in [15, 20, 25, 30, 40]]}
        self.assertEqual(len(self.routing.validate_response(response, [15, 20, 25, 30, 40], "school:test")), 5)
        for broken in [response["features"][:-1], response["features"] + response["features"][:1]]:
            with self.assertRaisesRegex(RuntimeError, "Incomplete"):
                self.routing.validate_response({"features": broken}, [15, 20, 25, 30, 40], "school:test")
        response["features"][0]["properties"]["metric"] = "distance"
        with self.assertRaisesRegex(RuntimeError, "Non-time"):
            self.routing.validate_response(response, [15, 20, 25, 30, 40], "school:test")

    def test_engine_warnings_are_not_silently_accepted(self):
        with self.assertRaisesRegex(RuntimeError, "warnings"):
            self.routing.validate_response({"warnings": [{"code": 1}]}, [25], "school:test")

    @unittest.skipUnless(importlib.util.find_spec("pyproj"), "pyproj unavailable")
    def test_facility_with_no_network_snap_fails_instead_of_becoming_a_buffer(self):
        facility = {"type": "Feature", "geometry": {"type": "Point", "coordinates": [26.72, 58.37]},
                    "properties": {"source_id": "school:test", "name": "Test school", "active_source": True}}
        from unittest.mock import Mock

        actor = Mock()
        actor.isochrone.return_value = {"features": []}
        with patch.object(self.routing, "prepare_graph", return_value=(actor, {})):
            with self.assertRaisesRegex(RuntimeError, "network snap"):
                self.routing.facility_isochrones(Path("."), {}, self.project["processing"]["routing"],
                                                 {"features": [facility]})

    def test_cached_network_must_match_the_pinned_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            network = root / "network.osm.pbf"
            network.write_bytes(b"network snapshot")
            source = {"pin": {"path": network.name, "sha256": self.routing.digest(network)},
                      "source_url": "https://example.invalid/snapshot.osm.pbf", "portal_page": "https://example.invalid",
                      "version": {"identifier": "test snapshot", "published_at": "2026-09-01"}}
            with patch("urllib.request.urlopen", side_effect=AssertionError("cached input must not use the network")):
                self.assertEqual(self.routing.fetch_network(root, source)["sha256"], source["pin"]["sha256"])
                network.write_bytes(b"different snapshot")
                with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
                    self.routing.fetch_network(root, source)

    @unittest.skipUnless(importlib.util.find_spec("osmium"), "optional routing extractor unavailable")
    def test_extraction_preserves_boundary_references_restrictions_and_barriers(self):
        import osmium

        xml = '''<osm version="0.6">
          <node id="10000000001" lat="58.37" lon="26.7"><tag k="barrier" v="gate"/></node>
          <node id="10000000002" lat="58.37" lon="26.0"/>
          <node id="10000000003" lat="58.37" lon="25.9"/>
          <way id="11"><nd ref="10000000001"/><nd ref="10000000002"/><tag k="highway" v="footway"/></way>
          <way id="12"><nd ref="10000000002"/><nd ref="10000000003"/><tag k="highway" v="footway"/></way>
          <relation id="21"><member type="way" ref="11" role="from"/>
            <member type="node" ref="10000000002" role="via"/>
            <member type="way" ref="12" role="to"/>
            <tag k="type" v="restriction"/><tag k="restriction:foot" v="no_right_turn"/>
          </relation></osm>'''
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.osm"
            target = Path(directory) / "extract.osm.pbf"
            source.write_text(xml)
            self.routing.extract_network(source, target, [26.3, 58.2, 27, 58.55])
            entities = [(entity.type_str(), entity.id, dict(entity.tags)) for entity in osmium.FileProcessor(str(target))]
            self.assertEqual(len(entities), 6)
            self.assertIn(("n", 10000000001, {"barrier": "gate"}), entities)
            self.assertIn(("w", 12, {"highway": "footway"}), entities)
            self.assertIn(("r", 21, {"type": "restriction", "restriction:foot": "no_right_turn"}), entities)


class ParcelNetworkScreeningTests(unittest.TestCase):
    def setUp(self):
        if importlib.util.find_spec("pyproj") is None:
            self.skipTest("pyproj unavailable")
        self.connection = connect_spatial()
        if self.connection is None:
            self.skipTest("DuckDB Spatial unavailable")
        self.addCleanup(self.connection.close)
        self.pipeline = load_module("pipeline")
        self.routing = load_module("routing")
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.pipeline.DERIVED = root
        self.pipeline.VALIDATION = root

    @staticmethod
    def rectangle(west, east):
        return {"type": "Polygon", "coordinates": [[[west, 58.37], [east, 58.37],
                [east, 58.372], [west, 58.372], [west, 58.37]]]}

    def test_geopackage_header_preserves_wkb_and_rejects_wrong_crs(self):
        payload = struct.pack('<BIdd', 1, 1, 659000, 6473000)
        for byte_order in ['little', 'big']:
            for envelope, length in enumerate([0, 32, 48, 48, 64]):
                flags = (envelope << 1) | (byte_order == 'little')
                header = b'GP\x00' + bytes([flags]) + (3301).to_bytes(4, byte_order)
                self.assertEqual(self.pipeline.cadastre_wkb(header + bytes(length) + payload), payload)
        for broken in [None, b'GP', b'GP\x00\x01' + (4326).to_bytes(4, 'little') + payload,
                       b'GP\x00\x21' + (3301).to_bytes(4, 'little') + payload,
                       b'GP\x00\x09' + (3301).to_bytes(4, 'little')]:
            with self.assertRaises(ValueError):
                self.pipeline.cadastre_wkb(broken)

    def test_cadastre_sqlite_reader_retains_rows_attributes_and_coordinates(self):
        path = Path(self.temporary.name) / 'source.gpkg'
        source = sqlite3.connect(path)
        self.addCleanup(source.close)
        source.execute('CREATE TABLE gpkg_geometry_columns (table_name, column_name, srs_id)')
        source.execute("INSERT INTO gpkg_geometry_columns VALUES ('Tartu maakond', 'geom', 3301)")
        source.execute('CREATE TABLE "Tartu maakond" (fid, tunnus, l_aadress, ov_nimi, ay_nimi, siht1, pindala, geom)')
        geometry = b'GP\x00\x01' + (3301).to_bytes(4, 'little') + struct.pack('<BIdd', 1, 1, 659000, 6473000)
        source.execute('INSERT INTO "Tartu maakond" VALUES (1, "test", "Address", "Tartu linn", "Tartu", "ARIMAA", 25000, ?)', [geometry])
        source.commit()
        for repetition in range(2):
            self.pipeline.load_cadastre(self.connection, path)
            self.assertEqual(self.connection.execute("SELECT typeof(geometry) FROM parcels_raw").fetchone()[0], "GEOMETRY('EPSG:3301')")
            self.assertEqual(self.connection.execute('SELECT cadastral_id, area_m2, ST_X(geometry), ST_Y(geometry) FROM parcels_raw').fetchall(),
                             [('test', 25000, 659000, 6473000)])

    def test_network_overlap_and_outage_determine_tiers_not_nearby_euclidean_points(self):
        contours = {"type": "FeatureCollection", "features": []}
        for amenity, active, east in [("school", True, 26.724), ("kindergarten", False, 26.722)]:
            for minute in [15, 20, 25, 30, 40]:
                contours["features"].append({"type": "Feature", "geometry": self.rectangle(26.72, east),
                    "properties": {"source_id": amenity, "amenity": amenity, "minutes": minute, "active": active}})
        for minute in [15, 20, 25, 30, 40]:
            contours["features"].append({"type": "Feature", "geometry": self.rectangle(26.72, 26.721),
                "properties": {"source_id": "other_kg", "amenity": "kindergarten", "minutes": minute, "active": True}})
        self.connection.execute("CREATE TABLE candidate_parcels (cadastral_id VARCHAR, suitability_tier VARCHAR, geometry GEOMETRY)")
        for identifier, west, east in [("both", 26.7202, 26.7208), ("outage", 26.7212, 26.7218),
                                      ("outside", 26.725, 26.726)]:
            self.connection.execute("INSERT INTO candidate_parcels VALUES (?, '', ST_Transform(ST_GeomFromGeoJSON(?), 'OGC:CRS84', 'EPSG:3301', always_xy := true))",
                                    [identifier, json.dumps(self.rectangle(west, east))])
        with patch.dict(sys.modules, routing=self.routing), patch.object(self.routing, "facility_isochrones", return_value=(contours, {})):
            self.pipeline.calculate_network_access(self.connection, {})
        rows = {row[0]: row[1:] for row in self.connection.execute(
            "SELECT cadastral_id, suitability_tier, isochrone_kg_effective_min, isochrone_kg_baseline_min FROM candidate_parcels"
        ).fetchall()}
        self.assertEqual(rows["both"], (self.pipeline.SUITABILITY_TIERS["candidates_tier1"], 15, 15))
        self.assertEqual(rows["outage"], (self.pipeline.SUITABILITY_TIERS["candidates_tier2"], None, 15))
        self.assertEqual(rows["outside"], (self.pipeline.SUITABILITY_TIERS["candidates_highway"], None, None))
        import pyproj

        easting, northing = pyproj.Transformer.from_crs(4326, 3301, always_xy=True).transform(26.7205, 58.371)
        self.assertTrue(self.connection.execute(
            "SELECT ST_Contains(geometry, ST_Point(?, ?)) FROM facility_isochrones WHERE source_id='school' LIMIT 1",
            [easting, northing],
        ).fetchone()[0])
        nearby = self.connection.execute("SELECT ST_Distance(geometry, ST_Transform(ST_Point(26.72,58.371), 'OGC:CRS84', 'EPSG:3301', always_xy := true)) FROM candidate_parcels WHERE cadastral_id='outside'").fetchone()[0]
        self.assertLess(nearby, 2000)

    def test_large_topology_repairs_are_rejected(self):
        contour = {"type": "FeatureCollection", "features": [{"type": "Feature",
            "properties": {"source_id": "school", "amenity": "school", "minutes": 25, "active": True},
            "geometry": {"type": "Polygon", "coordinates": [[[26.72,58.37], [26.73,58.38],
                [26.72,58.38], [26.73,58.37], [26.72,58.37]]]}}]}
        with patch.dict(sys.modules, routing=self.routing), patch.object(self.routing, "facility_isochrones", return_value=(contour, {})):
            with self.assertRaisesRegex(RuntimeError, "repair exceeds"):
                self.pipeline.calculate_network_access(self.connection, {})

    def test_smaller_contour_cannot_grant_access_missing_from_a_larger_one(self):
        contours = {"type": "FeatureCollection", "features": [{"type": "Feature",
            "properties": {"source_id": "school", "amenity": "school", "minutes": minute, "active": True},
            "geometry": self.rectangle(west, east)} for minute, west, east in [(15,26.72,26.722), (25,26.73,26.732)]]}
        self.connection.execute("CREATE TABLE candidate_parcels AS SELECT 'test' AS cadastral_id, '' AS suitability_tier, ST_Transform(ST_GeomFromGeoJSON(?), 'OGC:CRS84', 'EPSG:3301', always_xy := true) AS geometry", [json.dumps(self.rectangle(26.7201,26.7202))])
        with patch.dict(sys.modules, routing=self.routing), patch.object(self.routing, "facility_isochrones", return_value=(contours, {})):
            with self.assertRaisesRegex(RuntimeError, "not monotonic"):
                self.pipeline.calculate_network_access(self.connection, {})
