"""Invariants the paint-by-numbers pipeline must hold."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

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
    def test_load_gray_is_unit_range_and_downscaled(self, tmp_path):
        gray = tone.load_gray(gradient_photo(tmp_path), working_px=100)
        assert gray.dtype == np.float32
        assert 0.0 <= gray.min() and gray.max() <= 1.0
        assert max(gray.shape) == 100

    def test_crop_selects_the_requested_fraction(self, tmp_path):
        path = gradient_photo(tmp_path, size=(200, 100))
        full = tone.load_gray(path, working_px=0)
        half = tone.load_gray(path, working_px=0, crop=(0.5, 0.0, 1.0, 1.0))
        assert half.shape == (full.shape[0], full.shape[1] // 2)
        # The right half of a left-to-right ramp is the brighter half.
        assert half.mean() > full.mean()

    @pytest.mark.parametrize("mode", ["kmeans", "quantile", "uniform"])
    def test_quantize_labels_and_values_are_ordered(self, tmp_path, mode):
        opts = tone.ToneOptions(levels=5, working_px=120, smooth=0, mode=mode)
        gray = tone.load_gray(gradient_photo(tmp_path), opts.working_px)
        level_map, values = tone.quantize(gray, opts)
        assert values.shape == (5,)
        assert np.all(np.diff(values) > 0), "level values must run dark to light"
        assert level_map.min() >= 0 and level_map.max() <= 4

    def test_quantize_rejects_too_few_levels(self, tmp_path):
        opts = tone.ToneOptions(levels=1)
        gray = tone.load_gray(gradient_photo(tmp_path), 100)
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
        gray = tone.load_gray(gradient_photo(tmp_path), topts.working_px)
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
