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
# C3 — ProjectionPipeline is deprecated (Phase 3 gate)
# The old tests for _export_projections / _add_ownership_projections are
# superseded by the Phase 3 gate contract: these methods are unreachable
# because __init__ raises RuntimeError before any of them can be called.
# ---------------------------------------------------------------------------

class TestExportProjectionsReturns:
    """
    Phase 3 update: ProjectionPipeline is deprecated.  Tests now verify the
    deprecation guard rather than the old C3 behaviour (return value of
    _export_projections).  Canonical export path: run_dfs_pipeline().
    """

    def test_pipeline_init_raises_on_construction(self, tmp_path):
        """Phase 3 gate: ProjectionPipeline() must raise RuntimeError."""
        from analysis.nba.projection_pipeline import ProjectionPipeline
        with pytest.raises(RuntimeError, match="DEPRECATED"):
            ProjectionPipeline(output_dir=tmp_path)

    def test_pipeline_raises_without_output_dir(self):
        """Phase 3 gate: ProjectionPipeline() raises even without args."""
        from analysis.nba.projection_pipeline import ProjectionPipeline
        with pytest.raises(RuntimeError, match="DEPRECATED"):
            ProjectionPipeline()

    def test_error_message_references_canonical_path(self, tmp_path):
        """Phase 3 gate: RuntimeError message points to run_dfs_pipeline."""
        from analysis.nba.projection_pipeline import ProjectionPipeline
        with pytest.raises(RuntimeError) as exc_info:
            ProjectionPipeline()
        assert "run_dfs_pipeline" in str(exc_info.value)

    def test_deprecated_header_in_source(self):
        """Phase 3 gate: module-level DEPRECATED comment is present."""
        from analysis.nba import projection_pipeline as mod
        source = inspect.getsource(mod)
        assert "DEPRECATED" in source


# ---------------------------------------------------------------------------
# H4 — _add_ownership_projections source-level checks (no instantiation)
# ---------------------------------------------------------------------------

class TestOwnershipProjectionsCalled:
    """
    Phase 3 update: instance tests removed (ProjectionPipeline is deprecated).
    Source-inspection tests are retained to verify the source code still
    references _add_ownership_projections (legacy contract documented).
    """

    def test_run_full_pipeline_calls_ownership(self):
        """H4 (source check): run_full_pipeline source references _add_ownership_projections."""
        from analysis.nba import projection_pipeline as mod
        source = inspect.getsource(mod.ProjectionPipeline.run_full_pipeline)
        assert "_add_ownership_projections" in source, (
            "run_full_pipeline does not reference _add_ownership_projections"
        )

    def test_ownership_projections_method_defined_in_source(self):
        """H4 (source check): _add_ownership_projections method definition is present."""
        from analysis.nba import projection_pipeline as mod
        source = inspect.getsource(mod.ProjectionPipeline)
        assert "def _add_ownership_projections" in source


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
