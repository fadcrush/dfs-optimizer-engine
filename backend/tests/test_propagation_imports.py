"""Guard tests for the lineage import target used by backend/src/signals/propagation.py."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PROPAGATION_FILE = ROOT / "backend" / "src" / "signals" / "propagation.py"


def _propagation_source() -> str:
    return PROPAGATION_FILE.read_text(encoding="utf-8")


def test_propagation_lineage_import_uses_services_models():
    """The lineage import must point at services.models, not signals.models."""
    source = _propagation_source()

    assert "from services.models import InjuryReplacementLineage" in source, (
        "propagation.py no longer imports InjuryReplacementLineage from services.models."
    )


def test_propagation_lineage_import_does_not_point_at_signals_models():
    """Regression guard: the lineage import must not point at signals.models."""
    source = _propagation_source()

    assert "from signals.models import InjuryReplacementLineage" not in source, (
        "propagation.py incorrectly imports InjuryReplacementLineage from signals.models."
    )
