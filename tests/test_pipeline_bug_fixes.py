"""
Phase 26 — Tests for critical bug fixes in projection_pipeline.py
Covers: C2 (duplicate methods), C3 (missing return), H4 (ownership never called),
        M4 (step numbering), C6 (argon2-cffi in requirements).
"""
import inspect
import importlib
import tempfile
from pathlib import Path

import pandas as pd
import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pipeline(tmp_path: Path):
    """Instantiate ProjectionPipeline with a temp output directory, bypassing
    external dependencies (aggregator / engine are not invoked in these tests)."""
    from analysis.nba.projection_pipeline import ProjectionPipeline
    pp = ProjectionPipeline(output_dir=tmp_path)
    return pp


def _sample_projections() -> pd.DataFrame:
    """Minimal DataFrame that satisfies _add_salaries / _add_ownership_projections."""
    return pd.DataFrame(
        {
            "player_id": ["p1", "p2", "p3"],
            "player_name": ["Alice", "Bob", "Carol"],
            "base_projection": [35.0, 25.0, 15.0],
            "salary": [9000, 7000, 5000],
            "value": [3.89, 3.57, 3.0],
        }
    )


# ---------------------------------------------------------------------------
# C2 — No duplicate method definitions
# ---------------------------------------------------------------------------

class TestNoDuplicateMethods:
    def test_add_fanduel_data_defined_once(self):
        """C2: _add_fanduel_data must appear exactly once (not twice) in source."""
        from analysis.nba import projection_pipeline as mod
        source = inspect.getsource(mod.ProjectionPipeline)
        count = source.count("def _add_fanduel_data(")
        assert count == 1, f"Expected 1 definition of _add_fanduel_data, got {count}"

    def test_add_salaries_defined_once(self):
        """C2: _add_salaries must appear exactly once (not twice) in source."""
        from analysis.nba import projection_pipeline as mod
        source = inspect.getsource(mod.ProjectionPipeline)
        count = source.count("def _add_salaries(")
        assert count == 1, f"Expected 1 definition of _add_salaries, got {count}"

    def test_no_ellipsis_stub(self):
        """C2: the old '# ... existing code ...' stub must not be present."""
        from analysis.nba import projection_pipeline as mod
        source = inspect.getsource(mod.ProjectionPipeline)
        assert "# ... existing code ..." not in source


# ---------------------------------------------------------------------------
# C3 — _export_projections returns a dict
# ---------------------------------------------------------------------------

class TestExportProjectionsReturns:
    def test_returns_dict(self, tmp_path):
        """C3: _export_projections must return a dict, not None."""
        pp = _make_pipeline(tmp_path)
        df = _sample_projections()
        result = pp._export_projections(df, "2026-03-19", "FD")
        assert result is not None, "_export_projections returned None (return statement missing)"
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"

    def test_returns_full_key(self, tmp_path):
        """C3: returned dict must contain the 'full' output file path."""
        pp = _make_pipeline(tmp_path)
        df = _sample_projections()
        result = pp._export_projections(df, "2026-03-19", "FD")
        assert "full" in result, f"Expected 'full' key in export result, got keys: {list(result.keys())}"

    def test_full_file_exists(self, tmp_path):
        """C3: the file referenced by result['full'] must exist on disk."""
        pp = _make_pipeline(tmp_path)
        df = _sample_projections()
        result = pp._export_projections(df, "2026-03-19", "FD")
        assert Path(result["full"]).exists(), "Full export file was not created"

    def test_upload_key_present(self, tmp_path):
        """C3: returned dict must also contain 'upload' key."""
        pp = _make_pipeline(tmp_path)
        df = _sample_projections()
        result = pp._export_projections(df, "2026-03-19", "FD")
        assert "upload" in result, f"Expected 'upload' key in export result, got: {list(result.keys())}"


# ---------------------------------------------------------------------------
# H4 — _add_ownership_projections is called and adds projected_ownership
# ---------------------------------------------------------------------------

class TestOwnershipProjectionsCalled:
    def test_method_exists(self, tmp_path):
        """H4: _add_ownership_projections must exist on ProjectionPipeline."""
        pp = _make_pipeline(tmp_path)
        assert hasattr(pp, "_add_ownership_projections"), "_add_ownership_projections method missing"

    def test_adds_projected_ownership_column(self, tmp_path):
        """H4: _add_ownership_projections must add projected_ownership column."""
        pp = _make_pipeline(tmp_path)
        df = _sample_projections()
        result = pp._add_ownership_projections(df)
        assert "projected_ownership" in result.columns

    def test_ownership_bounded(self, tmp_path):
        """H4: projected_ownership values must be clipped to [1, 50]."""
        pp = _make_pipeline(tmp_path)
        df = _sample_projections()
        result = pp._add_ownership_projections(df)
        assert result["projected_ownership"].between(1, 50).all(), (
            f"Ownership out of [1, 50] range: {result['projected_ownership'].tolist()}"
        )

    def test_run_full_pipeline_calls_ownership(self, tmp_path):
        """H4: run_full_pipeline source must call _add_ownership_projections."""
        from analysis.nba import projection_pipeline as mod
        source = inspect.getsource(mod.ProjectionPipeline.run_full_pipeline)
        assert "_add_ownership_projections" in source, (
            "run_full_pipeline does not call _add_ownership_projections"
        )


# ---------------------------------------------------------------------------
# M4 — No duplicate step labels in run_full_pipeline
# ---------------------------------------------------------------------------

class TestStepNumbering:
    def test_no_duplicate_step_labels(self):
        """M4: each [n/6] label must appear exactly once in run_full_pipeline."""
        from analysis.nba import projection_pipeline as mod
        source = inspect.getsource(mod.ProjectionPipeline.run_full_pipeline)
        for step in range(1, 7):
            label = f"[{step}/6]"
            count = source.count(label)
            assert count == 1, f"Step label '{label}' appears {count} times (expected 1)"

    def test_all_six_steps_present(self):
        """M4: all six step labels [1/6]–[6/6] must be present."""
        from analysis.nba import projection_pipeline as mod
        source = inspect.getsource(mod.ProjectionPipeline.run_full_pipeline)
        missing = [f"[{n}/6]" for n in range(1, 7) if f"[{n}/6]" not in source]
        assert not missing, f"Missing step labels: {missing}"


# ---------------------------------------------------------------------------
# C6 — argon2-cffi in requirements.txt
# ---------------------------------------------------------------------------

class TestArgon2RequirementPresent:
    def test_argon2_cffi_in_requirements(self):
        """C6: backend/requirements.txt must list argon2-cffi."""
        req_path = Path(__file__).resolve().parent.parent / "backend" / "requirements.txt"
        assert req_path.exists(), f"requirements.txt not found at {req_path}"
        content = req_path.read_text()
        assert "argon2-cffi" in content, (
            "argon2-cffi missing from backend/requirements.txt — auth.py uses "
            "passlib schemes=['argon2'] which requires this package"
        )
