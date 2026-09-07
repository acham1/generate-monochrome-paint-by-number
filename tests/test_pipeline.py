"""Invariants the paint-by-numbers pipeline must hold."""

from __future__ import annotations

import numpy as np
import pytest
from pathlib import Path
from PIL import Image

from monochrome import cli
from monochrome import mesh
from monochrome import regions as R
from monochrome import render, tone
from monochrome.pipeline import convert


def gradient_photo(tmp_path, size=(220, 180)):
    """A synthetic photo with smooth tone ramps plus a distinct dark blob."""
    w, h = size
    xs = np.linspace(0, 255, w, dtype=np.float32)
    img = np.tile(xs, (h, 1))
    img[h // 4 : h // 2, w // 4 : w // 2] = 10.0  # something to find
    path = tmp_path / "photo.png"
    Image.fromarray(img.astype(np.uint8)).save(path)
    return path


class TestParseCrop:
    def test_none_passes_through(self):
        assert tone.parse_crop(None) is None

    def test_parses_four_fractions(self):
        assert tone.parse_crop("0,0.25,0.5,1") == (0.0, 0.25, 0.5, 1.0)

    @pytest.mark.parametrize(
        "spec",
        [
            "0,0,1",  # too few
            "0,0,1,1,1",  # too many
            "0.5,0,0.5,1",  # zero width
            "0.6,0,0.4,1",  # inverted
            "-0.1,0,1,1",  # out of range
            "0,0,1,1.2",  # out of range
        ],
    )
    def test_rejects_bad_boxes(self, spec):
        with pytest.raises(ValueError):
            tone.parse_crop(spec)


class TestTone:
    def test_load_lightness_is_unit_range_and_downscaled(self, tmp_path):
        light = tone.load_lightness(gradient_photo(tmp_path), working_px=100)
        assert light.dtype == np.float32
        assert 0.0 <= light.min() and light.max() <= 1.0
        assert max(light.shape) == 100

    def test_lightness_is_perceptual_not_luma(self, tmp_path):
        """Mid-gray sRGB sits well above L* 0.5; that gap is the whole point."""
        path = tmp_path / "mid.png"
        Image.fromarray(np.full((8, 8), 128, dtype=np.uint8)).save(path)
        light = tone.load_lightness(path, working_px=0)
        assert 0.5 < light.mean() < 0.56  # sRGB 128 is about L* 53.6

    def test_lightness_to_srgb_round_trips(self):
        from skimage import color

        values = np.linspace(0.0, 1.0, 11)
        srgb = tone.lightness_to_srgb(values)
        assert np.all(np.diff(srgb) > 0), "must stay monotonic"
        assert srgb[0] == pytest.approx(0.0, abs=1e-6)
        assert srgb[-1] == pytest.approx(1.0, abs=1e-6)
        # Mid perceptual gray is much lighter than mid sRGB.
        assert srgb[5] == pytest.approx(0.4663, abs=0.005)
        back = color.rgb2lab(np.stack([srgb] * 3, axis=-1).reshape(-1, 1, 3))[:, 0, 0] / 100
        assert back == pytest.approx(values, abs=1e-4)

    def test_lightness_to_srgb_preserves_shape(self):
        assert tone.lightness_to_srgb(np.zeros((3, 4))).shape == (3, 4)

    def test_crop_selects_the_requested_fraction(self, tmp_path):
        path = gradient_photo(tmp_path, size=(200, 100))
        full = tone.load_lightness(path, working_px=0)
        half = tone.load_lightness(path, working_px=0, crop=(0.5, 0.0, 1.0, 1.0))
        assert half.shape == (full.shape[0], full.shape[1] // 2)
        # The right half of a left-to-right ramp is the brighter half.
        assert half.mean() > full.mean()

    @pytest.mark.parametrize("mode", ["kmeans", "quantile", "uniform"])
    def test_quantize_labels_and_values_are_ordered(self, tmp_path, mode):
        opts = tone.ToneOptions(levels=5, working_px=120, smooth=0, mode=mode)
        gray = tone.load_lightness(gradient_photo(tmp_path), opts.working_px)
        level_map, values = tone.quantize(gray, opts)
        assert values.shape == (5,)
        assert np.all(np.diff(values) > 0), "level values must run dark to light"
        assert level_map.min() >= 0 and level_map.max() <= 4

    def test_ramp_palette_spans_black_to_white_inclusive(self):
        fitted = np.array([0.05, 0.23, 0.39, 0.55, 0.70, 0.87], dtype=np.float32)
        ramp = tone.paint_palette(fitted, "ramp")
        assert ramp[0] == pytest.approx(0.0)
        assert ramp[-1] == pytest.approx(1.0)
        assert np.diff(ramp) == pytest.approx(np.full(5, 0.2))

    def test_fitted_palette_is_a_passthrough(self):
        fitted = np.array([0.05, 0.5, 0.9], dtype=np.float32)
        assert tone.paint_palette(fitted, "fitted") is fitted

    def test_unknown_palette_is_rejected(self):
        with pytest.raises(ValueError):
            tone.paint_palette(np.zeros(3), "rainbow")

    def test_quantize_rejects_too_few_levels(self, tmp_path):
        opts = tone.ToneOptions(levels=1)
        gray = tone.load_lightness(gradient_photo(tmp_path), 100)
        with pytest.raises(ValueError):
            tone.quantize(gray, opts)


class TestSubjectWeight:
    def test_absent_subject_means_no_weighting(self):
        assert R.subject_weight((40, 40), None, 10.0, 0.0) is None

    def test_boost_of_one_means_no_weighting(self):
        assert R.subject_weight((40, 40), (0, 0, 0.5, 0.5), 1.0, 0.0) is None

    def test_inside_is_cheap_and_outside_is_expensive(self):
        weight = R.subject_weight((100, 100), (0.4, 0.4, 0.6, 0.6), 8.0, 0.0)
        assert weight[50, 50] == pytest.approx(1.0)
        assert weight[5, 5] == pytest.approx(8.0)

    def test_feather_softens_the_edge(self):
        hard = R.subject_weight((100, 100), (0.4, 0.4, 0.6, 0.6), 8.0, 0.0)
        soft = R.subject_weight((100, 100), (0.4, 0.4, 0.6, 0.6), 8.0, 0.15)
        # Just outside the box the feathered map has not yet reached full boost.
        assert soft[50, 66] < hard[50, 66]


class TestRegions:
    def build(self, tmp_path, **kw):
        topts = tone.ToneOptions(levels=4, working_px=160, smooth=0)
        gray = tone.load_lightness(gradient_photo(tmp_path), topts.working_px)
        level_map, _ = tone.quantize(gray, topts)
        ropts = R.RegionOptions(**kw)
        region_map, region_levels = R.merge_small(level_map, ropts)
        return region_map, region_levels, ropts

    def test_region_ids_are_dense_and_labelled(self, tmp_path):
        region_map, region_levels, _ = self.build(tmp_path, min_area=50)
        ids = np.unique(region_map)
        assert ids.tolist() == list(range(len(ids)))
        assert region_levels.shape == (len(ids),)

    def test_no_region_survives_below_the_minimum(self, tmp_path):
        region_map, _, ropts = self.build(tmp_path, min_area=120)
        counts = np.bincount(region_map.ravel())
        # A whole level can be smaller than min_area only if it has no
        # neighbour to merge into, which cannot happen on a connected image.
        assert counts.min() >= ropts.min_area

    def test_raising_the_minimum_never_adds_regions(self, tmp_path):
        loose, _, _ = self.build(tmp_path, min_area=40)
        tight, _, _ = self.build(tmp_path, min_area=400)
        assert len(np.unique(tight)) <= len(np.unique(loose))

    def test_label_point_lies_inside_its_own_region(self, tmp_path):
        region_map, region_levels, ropts = self.build(tmp_path, min_area=80)
        found = R.build_regions(region_map, region_levels, ropts)
        assert found
        for rid, region in enumerate(found):
            x, y = region.label_xy
            assert region_map[round(y), round(x)] == rid
            assert region.label_radius > 0
            assert region.outlines, "every region needs at least one outline"

    def test_outlines_stay_within_the_image(self, tmp_path):
        region_map, region_levels, ropts = self.build(tmp_path, min_area=80)
        h, w = region_map.shape
        for region in R.build_regions(region_map, region_levels, ropts):
            for poly in region.outlines:
                assert poly[:, 0].min() >= -1 and poly[:, 0].max() <= w
                assert poly[:, 1].min() >= -1 and poly[:, 1].max() <= h


class TestLabel:
    """The page label must not leak the source filename by default."""

    def test_number_mode_takes_trailing_digits(self):
        assert cli._label(Path("Engagement Photos-16.jpg"), 0, "number") == "16"
        assert cli._label(Path("DSC_0421.jpg"), 0, "number") == "0421"

    def test_number_mode_falls_back_to_position(self):
        assert cli._label(Path("portrait.jpg"), 4, "number") == "5"

    def test_name_and_none_modes(self):
        assert cli._label(Path("portrait.jpg"), 0, "name") == "portrait"
        assert cli._label(Path("portrait.jpg"), 0, "none") == ""


class TestLayout:
    def test_landscape_photo_turns_the_page_landscape(self):
        wide = render.plan_layout((400, 800), "letter")
        tall = render.plan_layout((800, 400), "letter")
        assert wide.page_w > wide.page_h
        assert tall.page_h > tall.page_w

    def test_drawing_fits_inside_the_margins(self):
        lay = render.plan_layout((800, 400), "letter")
        assert lay.off_x >= render.MARGIN - 1e-6
        assert lay.off_x + 400 * lay.scale <= lay.page_w - render.MARGIN + 1e-6
        assert lay.off_y + lay.draw_h <= lay.key_y + 1e-6

    def test_numeral_never_collapses_or_runs_away(self):
        tiny = render.Region(level=0, area=1, label_radius=0.1)
        huge = render.Region(level=0, area=10**6, label_radius=500.0)
        assert render.numeral_size(tiny, 1.0, "1") == pytest.approx(render.MIN_NUMERAL)
        assert render.numeral_size(huge, 1.0, "12") == pytest.approx(render.MAX_NUMERAL)


class TestConvert:
    def test_writes_all_three_outputs(self, tmp_path):
        result = convert(
            gradient_photo(tmp_path),
            tmp_path / "out",
            tone.ToneOptions(levels=4, working_px=140, smooth=0),
            R.RegionOptions(min_area=80),
        )
        assert result.region_count > 0
        assert {p.suffix for p in result.outputs} == {".png", ".svg", ".pdf"}
        for path in result.outputs:
            assert path.exists() and path.stat().st_size > 0
        svg = next(p for p in result.outputs if p.suffix == ".svg").read_text()
        assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
        assert svg.count("<path") >= result.region_count

    def test_page_never_names_the_source_photo(self, tmp_path):
        """These get printed and handed over; the source is often the surprise."""
        photo = tmp_path / "Engagement Photos-16.png"
        photo.write_bytes(gradient_photo(tmp_path).read_bytes())
        result = convert(
            photo,
            tmp_path / "out",
            tone.ToneOptions(levels=4, working_px=140, smooth=0),
            R.RegionOptions(min_area=80),
            label="16",
        )
        svg = next(p for p in result.outputs if p.suffix == ".svg").read_text()
        assert "Engagement" not in svg
        assert ">16  ·  4 tones" in svg

    def test_subject_box_reduces_the_region_count(self, tmp_path):
        photo = gradient_photo(tmp_path)
        topts = tone.ToneOptions(levels=5, working_px=180, smooth=0)
        uniform = convert(photo, tmp_path / "a", topts, R.RegionOptions(min_area=60))
        focused = convert(
            photo,
            tmp_path / "b",
            topts,
            R.RegionOptions(min_area=60, background_boost=20.0),
            subject=(0.25, 0.25, 0.5, 0.5),
        )
        assert focused.region_count <= uniform.region_count


class TestMesh:
    """The relief has to be a closed solid, or a slicer cannot print it."""

    @staticmethod
    def _edge_counts(triangles):
        from collections import Counter

        edges = Counter()
        for tri in np.round(np.asarray(triangles, dtype=np.float64), 6):
            for i in range(3):
                a, b = tuple(tri[i]), tuple(tri[(i + 1) % 3])
                edges[(a, b) if a < b else (b, a)] += 1
        return edges

    @staticmethod
    def _volume(triangles):
        """Signed volume by the divergence theorem; positive means outward."""
        v = np.asarray(triangles, dtype=np.float64)
        return np.einsum("ij,ij->i", v[:, 0], np.cross(v[:, 1], v[:, 2])).sum() / 6.0

    @pytest.mark.parametrize(
        "field",
        [
            [[1.0]],
            [[1.0, 2.0], [3.0, 4.0]],
            [[5.0, 5.0, 5.0], [5.0, 1.0, 5.0], [5.0, 5.0, 5.0]],
            [[1.0, 1.0, 1.0], [1.0, 9.0, 1.0], [1.0, 1.0, 1.0]],
        ],
    )
    def test_surface_has_no_holes(self, field):
        """Every edge shared by at least two faces means nothing is left open."""
        counts = self._edge_counts(mesh.build(np.array(field), 1.0))
        assert min(counts.values()) >= 2
        assert not [e for e, n in counts.items() if n % 2], "edges must pair up"

    @pytest.mark.parametrize("pixel_mm", [0.5, 1.0, 2.5])
    def test_volume_matches_the_columns_it_is_made_of(self, pixel_mm):
        field = np.array([[1.0, 2.0, 3.0], [4.0, 1.0, 2.0], [2.0, 3.0, 1.0]])
        volume = self._volume(mesh.build(field, pixel_mm))
        assert volume == pytest.approx(field.sum() * pixel_mm**2, rel=1e-6)

    def test_walls_are_split_so_corners_meet(self):
        """A staircase leaves open edges unless walls share a ladder of heights."""
        counts = self._edge_counts(mesh.build(np.array([[1.0, 2.0], [3.0, 4.0]]), 1.0))
        assert 1 not in counts.values()

    def test_height_field_spans_base_to_base_plus_relief(self):
        region_map = np.array([[0, 1], [2, 2]], dtype=np.int32)
        opts = mesh.MeshOptions(base_mm=2.0, relief_mm=6.0, px=64)
        heights = mesh.height_field(region_map, [0, 1, 2], [0.0, 0.5, 1.0], opts)
        assert heights.min() == pytest.approx(2.0)
        assert heights.max() == pytest.approx(8.0)

    def test_invert_raises_the_dark_tones(self):
        region_map = np.array([[0, 1]], dtype=np.int32)
        kwargs = dict(base_mm=1.0, relief_mm=4.0, px=64)
        normal = mesh.height_field(region_map, [0, 1], [0.0, 1.0], mesh.MeshOptions(**kwargs))
        flipped = mesh.height_field(
            region_map, [0, 1], [0.0, 1.0], mesh.MeshOptions(invert=True, **kwargs)
        )
        assert normal.min() == pytest.approx(flipped.min())
        assert normal.max() == pytest.approx(flipped.max())
        assert np.argmax(normal) != np.argmax(flipped)

    def test_stl_file_declares_the_triangles_it_holds(self, tmp_path):
        triangles = mesh.build(np.array([[1.0, 2.0], [3.0, 1.0]]), 1.0)
        path = tmp_path / "relief.stl"
        mesh.write_stl(triangles, path)
        raw = path.read_bytes()
        assert len(raw) == 84 + 50 * len(triangles)
        assert int(np.frombuffer(raw[80:84], dtype="<u4")[0]) == len(triangles)


class TestBorder:
    """A frame is applied to the height field, so the mesh code stays unaware."""

    def _heights(self, **kw):
        region_map = np.zeros((80, 80), dtype=np.int32)
        opts = mesh.MeshOptions(width_mm=80.0, base_mm=2.0, relief_mm=6.0, px=80, **kw)
        return mesh.height_field(region_map, [0], [0.0], opts), opts

    def test_off_by_default(self):
        heights, _ = self._heights()
        assert heights.max() == pytest.approx(2.0), "a flat dark picture stays flat"

    def test_frame_stands_above_the_lightest_tone(self):
        heights, opts = self._heights(border_mm=5.0, border_rise_mm=3.0)
        top = opts.base_mm + opts.relief_mm + opts.border_rise_mm
        assert heights[0, 0] == pytest.approx(top)
        assert heights[40, 40] == pytest.approx(2.0), "the middle is untouched"

    def test_frame_width_follows_millimetres(self):
        # 80mm wide over 80 columns, so 5mm of frame is 5 columns.
        heights, _ = self._heights(border_mm=5.0)
        assert heights[40, 4] != pytest.approx(2.0)
        assert heights[40, 5] == pytest.approx(2.0)

    def test_gap_sits_between_frame_and_picture(self):
        heights, opts = self._heights(border_mm=4.0, border_gap_mm=3.0, border_rise_mm=1.0)
        top = opts.base_mm + opts.relief_mm + opts.border_rise_mm
        assert heights[40, 0] == pytest.approx(top), "frame keeps its full width"
        assert heights[40, 3] == pytest.approx(top)
        assert heights[40, 5] == pytest.approx(opts.base_mm), "gutter is recessed"
        assert heights[40, 40] == pytest.approx(opts.base_mm)

    def test_frame_never_swallows_the_whole_picture(self):
        heights, _ = self._heights(border_mm=500.0)
        assert heights.shape == (80, 80)

    def test_bordered_surface_is_still_closed(self):
        heights, opts = self._heights(border_mm=6.0, border_gap_mm=2.0)
        counts = TestMesh._edge_counts(mesh.build(heights, opts.width_mm / heights.shape[1]))
        assert min(counts.values()) >= 2
        assert not [e for e, n in counts.items() if n % 2]
