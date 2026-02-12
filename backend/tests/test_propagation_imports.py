"""
Guard test: ensure propagation.py resolves InjuryReplacementLineage correctly.

This prevents a silent regression where HAS_LINEAGE_INFERENCE falls to False
because the import points at the wrong module.
"""


def test_propagation_lineage_import_resolves():
    """InjuryReplacementLineage must import from services.models, not signals.models."""
    from signals.propagation import HAS_LINEAGE_INFERENCE, InjuryReplacementLineage

    assert HAS_LINEAGE_INFERENCE is True, (
        "HAS_LINEAGE_INFERENCE is False — the InjuryReplacementLineage import is broken. "
        "It should be imported from services.models, not signals.models."
    )
    assert InjuryReplacementLineage is not None


def test_injury_replacement_lineage_is_from_services():
    """Verify the class originates from the correct module."""
    from services.models import InjuryReplacementLineage as Expected
    from signals.propagation import InjuryReplacementLineage as Actual

    assert Actual is Expected, (
        f"InjuryReplacementLineage in propagation.py points to {Actual.__module__}, "
        f"expected {Expected.__module__}"
    )
