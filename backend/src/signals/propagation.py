"""
Injury OUT propagation for NBA signals.

Deterministic logic to infer likely starters and rotation beneficiaries when a
player is ruled OUT. Emits derived SignalEvent objects with structured metadata
so downstream systems can apply opportunity-based adjustments.
"""

from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple, Any
import re
import logging

from signals.models import (
    SignalEvent,
    SignalType,
    InjuryStatus,
    normalize_injury_status,
)
from signals.conflict import OUT_STATUSES, CONFIRMED_STARTER_STATUSES, BENCH_STATUSES

# Optional import for injury replacement lineage (graceful degradation)
try:
    from signals.sources.nba.beat_writer_live import infer_injury_replacement_lineage
    from services.models import InjuryReplacementLineage
    HAS_LINEAGE_INFERENCE = True
except ImportError:
    HAS_LINEAGE_INFERENCE = False
    infer_injury_replacement_lineage = None
    InjuryReplacementLineage = None

logger = logging.getLogger(__name__)


PROPAGATION_SOURCE = "injury_out_propagation"

# Tiered impact settings (deterministic, conservative)
REPLACEMENT_IMPACT = 0.12
BENEFICIARY_IMPACT = 0.06
EXPECTED_STARTER_IMPACT = 0.05

REPLACEMENT_MINUTES_DELTA = 6.0
REPLACEMENT_USAGE_DELTA = 3.0
BENEFICIARY_MINUTES_DELTA = 3.0
BENEFICIARY_USAGE_DELTA = 2.0

MAX_BENEFICIARIES = 3

STATUS_PRIORITY = {
    "expected_starter": 1,
    "expected": 2,
    "projected": 3,
    "bench": 4,
    "reserve": 5,
}

# Position classifications for opportunity deltas
GUARD_POSITIONS = {"PG", "SG", "G"}
FRONTCOURT_POSITIONS = {"SF", "PF", "C", "F"}

# Role-based baseline values (for delta calculations)
ROLE_BASELINES = {
    "starter": {"minutes": 32.0, "usage": 22.0, "rebounding": 7.0, "ball_handling": 18.0},
    "sixth_man": {"minutes": 26.0, "usage": 20.0, "rebounding": 5.5, "ball_handling": 15.0},
    "rotation": {"minutes": 20.0, "usage": 16.0, "rebounding": 4.0, "ball_handling": 10.0},
    "deep_bench": {"minutes": 12.0, "usage": 14.0, "rebounding": 2.5, "ball_handling": 6.0},
    "dnp": {"minutes": 0.0, "usage": 0.0, "rebounding": 0.0, "ball_handling": 0.0},
}


def compute_opportunity_delta(
    player: Dict[str, any],
    inferred_role: str,
    baseline_role: str,
) -> Dict[str, float]:
    """
    Calculate opportunity deltas for a player based on role change.
    
    Converts news (injury OUT, lineup changes) into quantified opportunity changes.
    Returns relative deltas, not absolute projections. No fantasy scoring here.
    
    Args:
        player: Player dict with at minimum {"name": str, "position": str}
        inferred_role: New/inferred role (starter, sixth_man, rotation, deep_bench, dnp)
        baseline_role: Previous/baseline role (same values)
    
    Returns:
        Dictionary with deltas:
        - minutes_delta: Expected change in minutes per game
        - usage_delta: Expected change in usage rate percentage points
        - rebounding_delta: Expected change in rebounding opportunities (frontcourt only, else 0.0)
        - ball_handling_delta: Expected change in ball-handling opportunities (guards only, else 0.0)
    
    Rules:
        - All deltas are relative (new - baseline)
        - Injury replacements gain minutes by default (moving up roles)
        - Position-specific deltas only apply to relevant positions
        - Deterministic: same inputs always produce same outputs
        - No ML, no inference beyond role → opportunity mapping
    
    Example:
        >>> player = {"name": "Marcus Smart", "position": "PG"}
        >>> compute_opportunity_delta(player, "starter", "rotation")
        {
            "minutes_delta": 12.0,
            "usage_delta": 6.0,
            "rebounding_delta": 0.0,  # guard
            "ball_handling_delta": 8.0
        }
    """
    # Normalize role inputs
    inferred_role = inferred_role.lower().strip()
    baseline_role = baseline_role.lower().strip()
    
    # Validate roles
    if inferred_role not in ROLE_BASELINES:
        inferred_role = "rotation"  # default to rotation if unknown
    if baseline_role not in ROLE_BASELINES:
        baseline_role = "rotation"
    
    # Get baseline values for each role
    inferred_values = ROLE_BASELINES[inferred_role]
    baseline_values = ROLE_BASELINES[baseline_role]
    
    # Calculate base deltas (apply to all players)
    minutes_delta = inferred_values["minutes"] - baseline_values["minutes"]
    usage_delta = inferred_values["usage"] - baseline_values["usage"]
    
    # Position-specific deltas
    position = player.get("position", "").upper()
    
    # Rebounding delta: frontcourt only
    if position in FRONTCOURT_POSITIONS:
        rebounding_delta = inferred_values["rebounding"] - baseline_values["rebounding"]
    else:
        rebounding_delta = 0.0
    
    # Ball-handling delta: guards only
    if position in GUARD_POSITIONS:
        ball_handling_delta = inferred_values["ball_handling"] - baseline_values["ball_handling"]
    else:
        ball_handling_delta = 0.0
    
    # Injury replacement boost: if moving up roles, add a small bonus
    # (represents increased opportunity from teammate absence)
    role_priority_map = {
        "starter": 1,
        "sixth_man": 2,
        "rotation": 3,
        "deep_bench": 4,
        "dnp": 5,
    }
    
    inferred_priority = role_priority_map.get(inferred_role, 3)
    baseline_priority = role_priority_map.get(baseline_role, 3)
    
    # If moving up (lower priority number = higher role), add injury replacement boost
    if inferred_priority < baseline_priority:
        # +10% minutes delta boost for injury replacements
        minutes_delta *= 1.1
        # +5% usage delta boost
        usage_delta *= 1.05
    
    return {
        "minutes_delta": round(minutes_delta, 2),
        "usage_delta": round(usage_delta, 2),
        "rebounding_delta": round(rebounding_delta, 2),
        "ball_handling_delta": round(ball_handling_delta, 2),
    }


def propagate_injury_out(
    resolved_events: List[SignalEvent],
    context_events: Optional[List[SignalEvent]] = None,
    reference_time: Optional[datetime] = None,
    attach_lineage: bool = True,
    team_context: Optional[Dict[str, Any]] = None,
) -> List[SignalEvent]:
    """
    Propagate INJURY OUT events to likely starters and beneficiaries.
    
    NEW (Phase 21B): Optionally attaches InjuryReplacementLineage to OUT/DOUBTFUL
    events in their metadata. This provides explicit, signal-driven replacement
    candidates with confidence scores and opportunity deltas.

    Args:
        resolved_events: Conflict-resolved events (used to find OUT signals)
        context_events: Additional events for lineup context (defaults to resolved)
        reference_time: Timestamp for derived events (defaults to now)
        attach_lineage: If True, attach InjuryReplacementLineage to OUT events (default: True)
        team_context: Optional team context for lineage inference with keys:
            - depth_charts: Dict[str, TeamDepthChart] - depth charts by team
            - teammates_by_team: Dict[str, List[Dict]] - teammates by team

    Returns:
        List of derived SignalEvent objects.
        
    Lineage attachment:
        - OUT/DOUBTFUL events get 'injury_replacement_lineage' in metadata
        - Lineage is serialized dict (use InjuryReplacementLineage.from_dict to restore)
        - Non-breaking: only added if attach_lineage=True and HAS_LINEAGE_INFERENCE
        - Gracefully skips if team context unavailable
    """
    if context_events is None:
        context_events = resolved_events

    if reference_time is None:
        reference_time = datetime.now(timezone.utc)

    derived: List[SignalEvent] = []
    seen_keys = set()

    lineup_events = [e for e in context_events if e.type == SignalType.STARTING_LINEUP.value]
    lineup_by_team = _index_lineups_by_team(lineup_events)

    out_events = [e for e in resolved_events if _is_out_event(e)]
    out_players = {e.player_name for e in out_events}
    
    # =============================================================================
    # NEW (Phase 21B): Attach injury replacement lineages to OUT/DOUBTFUL events
    # =============================================================================
    if attach_lineage and HAS_LINEAGE_INFERENCE and team_context:
        _attach_lineages_to_out_events(
            out_events=out_events,
            team_context=team_context,
            context_events=context_events,
        )

    for out_event in out_events:
        team = _get_event_team(out_event)
        if not team:
            continue

        team_lineups = lineup_by_team.get(team, [])
        out_position = _infer_player_position(out_event, team_lineups)

        replacement = _find_replacement_candidate(
            team_lineups,
            out_player=out_event.player_name,
            out_position=out_position,
            out_players=out_players,
        )

        if replacement:
            expected_event = _make_expected_starter_event(
                replacement,
                out_event,
                team,
                out_position,
                reference_time,
            )
            _append_unique(derived, expected_event, seen_keys)

            replacement_bump = _make_opportunity_bump_event(
                replacement,
                out_event,
                team,
                role="replacement",
                impact=REPLACEMENT_IMPACT,
                minutes_delta=REPLACEMENT_MINUTES_DELTA,
                usage_delta=REPLACEMENT_USAGE_DELTA,
                reference_time=reference_time,
            )
            _append_unique(derived, replacement_bump, seen_keys)

        beneficiaries = _find_rotation_beneficiaries(
            team_lineups,
            out_player=out_event.player_name,
            exclude_player=replacement.player_name if replacement else None,
            out_players=out_players,
        )

        for beneficiary in beneficiaries:
            bump_event = _make_opportunity_bump_event(
                beneficiary,
                out_event,
                team,
                role="rotation_beneficiary",
                impact=BENEFICIARY_IMPACT,
                minutes_delta=BENEFICIARY_MINUTES_DELTA,
                usage_delta=BENEFICIARY_USAGE_DELTA,
                reference_time=reference_time,
            )
            _append_unique(derived, bump_event, seen_keys)

    return derived


def _index_lineups_by_team(lineup_events: List[SignalEvent]) -> Dict[str, List[SignalEvent]]:
    by_team: Dict[str, List[SignalEvent]] = {}
    for event in lineup_events:
        team = _get_event_team(event)
        if not team:
            continue
        by_team.setdefault(team, []).append(event)
    return by_team


def _is_out_event(event: SignalEvent) -> bool:
    status = _extract_status(event)
    return bool(status and status in OUT_STATUSES)


def _extract_status(event: SignalEvent) -> Optional[str]:
    metadata = event.metadata or {}
    status = metadata.get("status") or metadata.get("lineup_status")
    if status is None:
        return None
    return str(status).strip().lower()


def _get_event_team(event: SignalEvent) -> Optional[str]:
    if event.team:
        return event.team
    metadata = event.metadata or {}
    team = metadata.get("team")
    return team.upper() if isinstance(team, str) else None


def _infer_player_position(out_event: SignalEvent, lineup_events: List[SignalEvent]) -> Optional[str]:
    metadata = out_event.metadata or {}
    if "position" in metadata:
        return metadata.get("position")

    for event in lineup_events:
        if event.player_name != out_event.player_name:
            continue
        position = (event.metadata or {}).get("position")
        if position:
            return position

    return None


def _find_replacement_candidate(
    lineup_events: List[SignalEvent],
    out_player: str,
    out_position: Optional[str],
    out_players: set,
) -> Optional[SignalEvent]:
    candidates: List[Tuple[int, float, float, str, SignalEvent]] = []

    for event in lineup_events:
        if event.player_name == out_player:
            continue
        if event.player_name in out_players:
            continue

        status = _extract_status(event)
        if not status:
            continue

        if status in CONFIRMED_STARTER_STATUSES:
            continue

        if status not in STATUS_PRIORITY and status not in BENCH_STATUSES:
            continue

        position = (event.metadata or {}).get("position")
        if out_position and position and position != out_position:
            continue

        priority = STATUS_PRIORITY.get(status, STATUS_PRIORITY.get("bench", 99))
        candidates.append((priority, -event.confidence, -event.timestamp.timestamp(), event.player_name, event))

    if not candidates:
        return None

    candidates.sort()
    return candidates[0][-1]


def _find_rotation_beneficiaries(
    lineup_events: List[SignalEvent],
    out_player: str,
    exclude_player: Optional[str],
    out_players: set,
) -> List[SignalEvent]:
    beneficiaries: List[Tuple[float, float, str, SignalEvent]] = []

    for event in lineup_events:
        if event.player_name == out_player:
            continue
        if exclude_player and event.player_name == exclude_player:
            continue
        if event.player_name in out_players:
            continue

        status = _extract_status(event)
        if status not in CONFIRMED_STARTER_STATUSES:
            continue

        beneficiaries.append((
            -event.confidence,
            -event.timestamp.timestamp(),
            event.player_name,
            event,
        ))

    beneficiaries.sort()
    return [item[-1] for item in beneficiaries[:MAX_BENEFICIARIES]]


def _make_expected_starter_event(
    replacement: SignalEvent,
    out_event: SignalEvent,
    team: str,
    out_position: Optional[str],
    reference_time: datetime,
) -> SignalEvent:
    event_id = _make_event_id(out_event.id, replacement.player_name, "expected_starter")
    confidence = _derive_confidence(out_event.confidence, bump=0.0)

    metadata = {
        "lineup_status": "EXPECTED_STARTER",
        "confirmation_level": "derived",
        "team": team,
        "position": out_position,
        "propagation": "injury_out",
        "out_player": out_event.player_name,
        "out_event_id": out_event.id,
        "explanation": "Teammate ruled out; expected to move into starting role.",
    }

    return SignalEvent.create(
        player_name=replacement.player_name,
        signal_type=SignalType.STARTING_LINEUP.value,
        confidence=confidence,
        impact=EXPECTED_STARTER_IMPACT,
        source=PROPAGATION_SOURCE,
        team=team,
        metadata=metadata,
        timestamp=reference_time,
        event_id=event_id,
    )


def _make_opportunity_bump_event(
    beneficiary: SignalEvent,
    out_event: SignalEvent,
    team: str,
    role: str,
    impact: float,
    minutes_delta: float,
    usage_delta: float,
    reference_time: datetime,
) -> SignalEvent:
    event_id = _make_event_id(out_event.id, beneficiary.player_name, f"{role}_bump")
    confidence = _derive_confidence(out_event.confidence, bump=-0.1 if role != "replacement" else 0.0)

    metadata = {
        "team": team,
        "propagation": "injury_out",
        "out_player": out_event.player_name,
        "out_event_id": out_event.id,
        "role": role,
        "minutes_delta": minutes_delta,
        "usage_delta": usage_delta,
        "explanation": (
            "Minutes and usage expected to rise due to teammate OUT. "
            "Adjustment is role-based and deterministic."
        ),
    }

    return SignalEvent.create(
        player_name=beneficiary.player_name,
        signal_type=SignalType.NEWS.value,
        confidence=confidence,
        impact=impact,
        source=PROPAGATION_SOURCE,
        team=team,
        metadata=metadata,
        timestamp=reference_time,
        event_id=event_id,
    )


def _derive_confidence(base_confidence: float, bump: float = 0.0) -> float:
    raw = 0.4 + (base_confidence * 0.5) + bump
    return max(0.35, min(0.85, raw))


def _make_event_id(out_event_id: str, player_name: str, suffix: str) -> str:
    safe_name = _slugify(player_name)
    return f"propagate-{out_event_id}-{safe_name}-{suffix}"


def _slugify(name: str) -> str:
    if not name:
        return "unknown"
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", name.strip().lower())
    return cleaned.strip("_")


def _attach_lineages_to_out_events(
    out_events: List[SignalEvent],
    team_context: Dict[str, any],
    context_events: List[SignalEvent],
) -> None:
    """
    Attach InjuryReplacementLineage to OUT/DOUBTFUL events in their metadata.
    
    This mutates the out_events in place by adding 'injury_replacement_lineage'
    to their metadata dict. Non-breaking: gracefully skips if required data missing.
    
    Args:
        out_events: List of OUT/DOUBTFUL SignalEvent objects (mutated in place)
        team_context: Context dict with:
            - depth_charts: Optional[Dict[str, TeamDepthChart]] - depth charts by team abbr
            - teammates_by_team: Optional[Dict[str, List[Dict]]] - teammates by team abbr
        context_events: All context events (used to extract beat writer signals)
    
    Mutates:
        out_events: Adds 'injury_replacement_lineage' key to metadata if successful
    
    Example:
        >>> out_events = [...]
        >>> team_context = {
        ...     \"depth_charts\": {\"MIL\": depth_chart},
        ...     \"teammates_by_team\": {\"MIL\": [...]},
        ... }
        >>> _attach_lineages_to_out_events(out_events, team_context, context_events)
        >>> # out_events[0].metadata now has 'injury_replacement_lineage' key
    """
    if not HAS_LINEAGE_INFERENCE:
        logger.debug("Lineage inference not available, skipping lineage attachment")
        return
    
    # Extract team context
    depth_charts = team_context.get("depth_charts", {})
    teammates_by_team = team_context.get("teammates_by_team", {})
    
    if not teammates_by_team:
        logger.debug("No teammates_by_team in team_context, skipping lineage attachment")
        return
    
    # Index beat writer signals by team for quick lookup
    beat_writer_signals_by_team: Dict[str, List[Dict]] = {}
    for event in context_events:
        if event.type in (SignalType.STARTING_LINEUP.value, SignalType.NEWS.value):
            team = _get_event_team(event)
            if team:
                signal_dict = {
                    "player_id": event.metadata.get("player_id", event.id),
                    "player_name": event.player_name,
                    "signal_type": event.type,
                    "confidence": event.confidence,
                    "team": team,
                }
                beat_writer_signals_by_team.setdefault(team, []).append(signal_dict)
    
    # Process each OUT event
    for out_event in out_events:
        try:
            # Extract required fields
            team = _get_event_team(out_event)
            if not team:
                logger.debug(f"No team for OUT player {out_event.player_name}, skipping lineage")
                continue
            
            # Get injury status
            injury_status = _extract_status(out_event)
            normalized_status = normalize_injury_status(injury_status) if injury_status else None
            
            # Only process OUT or DOUBTFUL
            if normalized_status not in (InjuryStatus.OUT, InjuryStatus.DOUBTFUL):
                logger.debug(
                    f"Player {out_event.player_name} status {normalized_status}, "
                    f"not OUT/DOUBTFUL, skipping lineage"
                )
                continue
            
            # Get position (required)
            position = _infer_player_position(out_event, context_events)
            if not position:
                logger.debug(f"No position for OUT player {out_event.player_name}, skipping lineage")
                continue
            
            # Get player_id from metadata or use event id as fallback
            player_id = out_event.metadata.get("player_id", out_event.id)
            
            # Get teammates for this team
            teammates = teammates_by_team.get(team, [])
            if not teammates:
                logger.debug(f"No teammates for team {team}, skipping lineage")
                continue
            
            # Build out_signal for lineage inference
            out_signal = {
                "player_id": player_id,
                "player_name": out_event.player_name,
                "team": team,
                "position": position,
                "injury_status": injury_status or normalized_status.value,
                "confidence": out_event.confidence,
            }
            
            # Build team context for lineage inference
            lineage_team_context = {
                "depth_chart": depth_charts.get(team),  # Optional
                "beat_writer_signals": beat_writer_signals_by_team.get(team, []),
                "teammates": teammates,
            }
            
            # Infer replacement lineage
            lineage = infer_injury_replacement_lineage(
                out_signal=out_signal,
                team_context=lineage_team_context,
            )
            
            # Attach serialized lineage to metadata
            if lineage:
                out_event.metadata["injury_replacement_lineage"] = lineage.to_dict()
                logger.debug(
                    f"Attached lineage for {out_event.player_name}: "
                    f"{len(lineage.replacements)} replacements, "
                    f"top: {lineage.top_replacement.player_name if lineage.top_replacement else 'none'}"
                )
        
        except Exception as e:
            # Non-breaking: log error and continue
            logger.warning(
                f"Failed to attach lineage for {out_event.player_name}: {e}",
                exc_info=True
            )
            continue


def _append_unique(events: List[SignalEvent], event: SignalEvent, seen_keys: set) -> None:
    key = (event.player_name, event.type, event.metadata.get("propagation"), event.metadata.get("out_event_id"))
    if key in seen_keys:
        return
    seen_keys.add(key)
    events.append(event)


# =============================================================================
# EXPLAINABILITY
# =============================================================================

def explain_player_role(
    player_id: str,
    player_name: str,
    signals: List[Dict],
    depth_chart: Optional[Dict] = None,
    position: Optional[str] = None,
) -> Dict:
    """
    Explain injury → starter inference for a player.
    
    Critical explainability hook that shows:
    - What injury inputs drove the inference
    - Where the player sits on the depth chart
    - What role was inferred (starter, replacement, beneficiary, etc.)
    - Confidence score for the inference
    - Opportunity deltas resulting from the role change
    
    This makes injury propagation transparent and auditable.
    
    Args:
        player_id: Unique player identifier
        player_name: Player name
        signals: List of signal dicts for this player
        depth_chart: Optional depth chart dict with {position: [list of players]}
        position: Optional player position (PG, SG, SF, PF, C)
    
    Returns:
        Dictionary with complete explanation:
        {
            "player_id": str,
            "player_name": str,
            "injury_inputs": {
                "status": str,  # Raw injury status
                "normalized_status": str,  # Normalized (OUT, QUESTIONABLE, etc.)
                "out_teammates": [list of OUT teammates],
                "injury_signals": [list of injury signal dicts],
            },
            "depth_chart_position": {
                "position": str,  # Player position
                "depth_rank": int,  # 1 = starter, 2 = backup, etc.
                "total_at_position": int,
                "players_ahead": [list of players ahead on depth chart],
                "players_behind": [list of players behind],
            },
            "inferred_role": {
                "role": str,  # starter, replacement, beneficiary, rotation, etc.
                "baseline_role": str,  # Previous role
                "changed": bool,  # Whether role changed
                "reason": str,  # Why this role was inferred
            },
            "confidence_score": {
                "score": float,  # 0.0 - 1.0
                "injury_contribution": float,
                "depth_chart_contribution": float,
                "beat_writer_contribution": float,
                "caps_applied": [list of caps, e.g., "QUESTIONABLE < 0.7"],
            },
            "opportunity_deltas": {
                "minutes_delta": float,
                "usage_delta": float,
                "rebounding_delta": float,
                "ball_handling_delta": float,
                "total_boost_pct": float,  # Overall boost/penalty percentage
            },
            "metadata": {
                "explanation_timestamp": str,
                "signal_count": int,
                "propagation_source": str,
            }
        }
    
    Example:
        >>> explain_player_role(
        ...     player_id="123",
        ...     player_name="Derrick White",
        ...     signals=[
        ...         {"signal_type": "injury_out", "player_name": "Marcus Smart"},
        ...         {"signal_type": "expected_starter", "player_name": "Derrick White"}
        ...     ],
        ...     depth_chart={"PG": ["Marcus Smart", "Derrick White", "Payton Pritchard"]},
        ...     position="PG"
        ... )
    """
    from signals.models import normalize_injury_status, InjuryStatus
    
    # Import beat writer confidence scoring if available
    try:
        from signals.sources.nba.beat_writer_live import compute_starter_confidence
    except ImportError:
        compute_starter_confidence = None
    
    # =============================================================================
    # 1. EXTRACT INJURY INPUTS
    # =============================================================================
    
    injury_status = None
    normalized_status = None
    out_teammates = []
    injury_signals = []
    beat_writer_signals = []
    
    for signal in signals:
        signal_type = signal.get("signal_type", "")
        signal_player = signal.get("player_name", "")
        
        # Collect injury signals for this player
        if signal_type in (SignalType.INJURY.value, "injury", "injury_out"):
            if signal_player == player_name:
                injury_signals.append(signal)
                if not injury_status:
                    injury_status = signal.get("injury_status") or signal.get("metadata", {}).get("injury_status")
        
        # Collect OUT teammates
        metadata = signal.get("metadata") or {}
        if metadata.get("lineup_status") == "OUT" and signal_player != player_name:
            out_teammates.append(signal_player)
        
        # Collect beat writer signals
        if signal.get("source") == "beat_writer_live" and signal_player == player_name:
            beat_writer_signals.append(signal)
    
    # Normalize injury status
    if injury_status:
        normalized_status = normalize_injury_status(injury_status)
    
    injury_inputs = {
        "status": injury_status,
        "normalized_status": normalized_status.value if normalized_status else None,
        "out_teammates": list(set(out_teammates)),
        "injury_signals": injury_signals,
    }
    
    # =============================================================================
    # 2. DEPTH CHART POSITION
    # =============================================================================
    
    depth_rank = None
    total_at_position = None
    players_ahead = []
    players_behind = []
    
    if depth_chart and position:
        position_players = depth_chart.get(position, [])
        if player_name in position_players:
            depth_rank = position_players.index(player_name) + 1  # 1-based
            total_at_position = len(position_players)
            players_ahead = position_players[:depth_rank-1] if depth_rank > 1 else []
            players_behind = position_players[depth_rank:] if depth_rank < len(position_players) else []
    
    depth_chart_position = {
        "position": position,
        "depth_rank": depth_rank,
        "total_at_position": total_at_position,
        "players_ahead": players_ahead,
        "players_behind": players_behind,
    }
    
    # =============================================================================
    # 3. INFER ROLE
    # =============================================================================
    
    inferred_role_value = "rotation"  # Default
    baseline_role = "rotation"
    role_changed = False
    role_reason = "No role change detected"
    
    # Check for explicit role in signals
    for signal in signals:
        if signal.get("player_name") == player_name:
            metadata = signal.get("metadata") or {}
            
            # Replacement role
            if metadata.get("role") == "replacement":
                inferred_role_value = "starter"
                baseline_role = metadata.get("baseline_role", "rotation")
                role_changed = True
                role_reason = f"Replacement for OUT player: {metadata.get('out_player')}"
                break
            
            # Beneficiary role
            elif metadata.get("role") == "beneficiary":
                inferred_role_value = "rotation"
                baseline_role = "deep_bench"
                role_changed = True
                role_reason = f"Beneficiary of OUT player: {metadata.get('out_player')}"
                break
            
            # Expected starter
            elif metadata.get("lineup_status") == "EXPECTED_STARTER":
                inferred_role_value = "starter"
                baseline_role = metadata.get("baseline_role", "rotation")
                role_changed = True
                role_reason = metadata.get("explanation", "Expected to start")
                break
    
    # Fallback: infer from depth chart
    if not role_changed and depth_rank is not None:
        if depth_rank == 1:
            inferred_role_value = "starter"
            role_reason = "Listed as starter on depth chart"
        elif depth_rank == 2:
            inferred_role_value = "sixth_man"
            role_reason = "Listed as first backup on depth chart"
        else:
            inferred_role_value = "rotation"
            role_reason = f"Listed at depth position {depth_rank}"
    
    inferred_role = {
        "role": inferred_role_value,
        "baseline_role": baseline_role,
        "changed": role_changed,
        "reason": role_reason,
    }
    
    # =============================================================================
    # 4. CONFIDENCE SCORE
    # =============================================================================
    
    confidence_value = 0.5
    injury_contribution = 0.0
    depth_contribution = 0.0
    beat_contribution = 0.0
    caps_applied = []
    
    if compute_starter_confidence:
        # Use full confidence scoring
        confidence_value = compute_starter_confidence(
            injury_status=injury_status,
            beat_writer_signals=beat_writer_signals,
            depth_chart_position=depth_rank,
            depth_chart_total=total_at_position,
        )
        
        # Approximate contributions (simplified breakdown)
        if normalized_status == InjuryStatus.OUT:
            injury_contribution = 1.0
            caps_applied.append("OUT = 1.0 certainty")
        elif normalized_status == InjuryStatus.QUESTIONABLE:
            injury_contribution = 0.5
            caps_applied.append("QUESTIONABLE < 0.7")
        elif normalized_status == InjuryStatus.DOUBTFUL:
            injury_contribution = 0.25
            caps_applied.append("DOUBTFUL < 0.4")
        elif normalized_status == InjuryStatus.ACTIVE:
            injury_contribution = 0.6
        
        if depth_rank is not None and total_at_position:
            depth_factor = 1.0 - ((depth_rank - 1) / total_at_position)
            depth_contribution = 0.4 + (depth_factor * 0.35)
        
        if beat_writer_signals:
            max_beat_conf = max((s.get("confidence", 0.0) for s in beat_writer_signals), default=0.0)
            beat_contribution = max_beat_conf * 0.35
    else:
        # Simplified confidence without full scoring
        if normalized_status == InjuryStatus.OUT:
            confidence_value = 1.0
        elif depth_rank == 1:
            confidence_value = 0.75
        elif depth_rank == 2:
            confidence_value = 0.60
    
    confidence_score = {
        "score": round(confidence_value, 3),
        "injury_contribution": round(injury_contribution, 3),
        "depth_chart_contribution": round(depth_contribution, 3),
        "beat_writer_contribution": round(beat_contribution, 3),
        "caps_applied": caps_applied,
    }
    
    # =============================================================================
    # 5. OPPORTUNITY DELTAS
    # =============================================================================
    
    opportunity_deltas = {
        "minutes_delta": 0.0,
        "usage_delta": 0.0,
        "rebounding_delta": 0.0,
        "ball_handling_delta": 0.0,
        "total_boost_pct": 0.0,
    }
    
    # Check for explicit deltas in signals
    for signal in signals:
        if signal.get("player_name") == player_name:
            metadata = signal.get("metadata") or {}
            if "minutes_delta" in metadata or "usage_delta" in metadata:
                opportunity_deltas = {
                    "minutes_delta": metadata.get("minutes_delta", 0.0),
                    "usage_delta": metadata.get("usage_delta", 0.0),
                    "rebounding_delta": metadata.get("rebounding_delta", 0.0),
                    "ball_handling_delta": metadata.get("ball_handling_delta", 0.0),
                }
                break
    
    # Compute deltas if role changed
    if role_changed and position:
        player_dict = {"name": player_name, "position": position}
        computed_deltas = compute_opportunity_delta(
            player_dict,
            inferred_role_value,
            baseline_role,
        )
        
        # Use computed deltas if no explicit deltas found
        if opportunity_deltas["minutes_delta"] == 0.0 and opportunity_deltas["usage_delta"] == 0.0:
            opportunity_deltas = computed_deltas
    
    # Calculate total boost percentage
    minutes_factor = 1.0 + (opportunity_deltas["minutes_delta"] * 0.01)
    usage_factor = 1.0 + (opportunity_deltas["usage_delta"] * 0.005)
    total_boost = (minutes_factor * usage_factor) - 1.0
    opportunity_deltas["total_boost_pct"] = round(total_boost * 100, 2)
    
    # =============================================================================
    # 6. ASSEMBLE EXPLANATION
    # =============================================================================
    
    explanation = {
        "player_id": player_id,
        "player_name": player_name,
        "injury_inputs": injury_inputs,
        "depth_chart_position": depth_chart_position,
        "inferred_role": inferred_role,
        "confidence_score": confidence_score,
        "opportunity_deltas": opportunity_deltas,
        "metadata": {
            "explanation_timestamp": datetime.now(timezone.utc).isoformat(),
            "signal_count": len(signals),
            "propagation_source": PROPAGATION_SOURCE,
        },
    }
    
    return explanation
