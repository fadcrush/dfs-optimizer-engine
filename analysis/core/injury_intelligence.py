from __future__ import annotations

import hashlib
import json
import logging
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from analysis.schemas.player import InjuryStatus
from analysis.shared.db import get_conn, write_lock
from analysis.shared.injury_utils import DB_PATH as NBA_NEWS_DB_PATH
from analysis.shared.injury_utils import slug

log = logging.getLogger(__name__)

SOURCE_PRIORITY_MAP: dict[str, int] = {
    "manual_override": 100,
    "official_lineup": 95,
    "starting_lineup": 90,
    "lineup_announcement": 88,
    "beat_report": 75,
    "warmup_report": 72,
    "official_report": 60,
    "sportsdata": 58,
    "watcher": 55,
    "market_signal": 40,
    "unknown": 25,
}

STATUS_IMPACT_MAP: dict[str, float] = {
    InjuryStatus.OUT.value: 1.00,
    InjuryStatus.DOUBTFUL.value: 0.82,
    InjuryStatus.GAME_TIME_DECISION.value: 0.70,
    InjuryStatus.QUESTIONABLE.value: 0.52,
    InjuryStatus.PROBABLE.value: 0.18,
    InjuryStatus.ACTIVE.value: 0.05,
    InjuryStatus.UNKNOWN.value: 0.10,
}

STATUS_PLAY_PROB_MAP: dict[str, float] = {
    InjuryStatus.OUT.value: 0.02,
    InjuryStatus.DOUBTFUL.value: 0.18,
    InjuryStatus.GAME_TIME_DECISION.value: 0.45,
    InjuryStatus.QUESTIONABLE.value: 0.68,
    InjuryStatus.PROBABLE.value: 0.90,
    InjuryStatus.ACTIVE.value: 0.98,
    InjuryStatus.UNKNOWN.value: 0.75,
}

LIMITED_DETAIL_KEYWORDS = (
    "limit",
    "restricted",
    "restriction",
    "minutes cap",
    "conditioning",
    "ramp",
    "monitor",
)


@dataclass(slots=True)
class InjuryEventRecord:
    event_id: str
    player_id: str
    player_name: str
    team_id: str
    game_id: str
    source: str
    source_priority: int
    raw_status: str
    normalized_status: str
    detail_text: str
    parser_confidence: float
    observed_at: datetime
    effective_at: datetime
    is_retraction: bool
    correlation_id: str
    market_impact_estimate: float
    event_priority_score: float
    news_quality_score: float
    event_classification: str
    position: str = ""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _as_utc_datetime(value: Any, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, pd.Timestamp):
        dt = value.to_pydatetime()
        return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, 12, 0, tzinfo=UTC)
    if isinstance(value, str) and value.strip():
        parsed = pd.to_datetime(value, utc=True, errors="coerce")
        if pd.notna(parsed):
            return parsed.to_pydatetime()
    return fallback


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        if math.isnan(result) or math.isinf(result):
            return default
        return result
    except Exception:
        return default


def _source_priority(source: str) -> int:
    return SOURCE_PRIORITY_MAP.get(str(source or "unknown").strip().lower(), SOURCE_PRIORITY_MAP["unknown"])


def _status_impact(status: str) -> float:
    return STATUS_IMPACT_MAP.get(status, 0.10)


def _event_classification(status: str, detail_text: str, source_priority: int, impact: float) -> str:
    detail_lower = detail_text.lower()
    if impact >= 0.65 or source_priority >= 88:
        return "true_edge_event"
    if status in {InjuryStatus.ACTIVE.value, InjuryStatus.PROBABLE.value} and not any(
        token in detail_lower for token in LIMITED_DETAIL_KEYWORDS
    ):
        return "noise"
    return "false_positive" if impact < 0.10 else "candidate"


def _detail_limited_probability(detail_text: str, normalized_status: str) -> float:
    detail_lower = detail_text.lower()
    keyword_hit = any(token in detail_lower for token in LIMITED_DETAIL_KEYWORDS)
    base = 0.0
    if normalized_status == InjuryStatus.DOUBTFUL.value:
        base = 0.55
    elif normalized_status == InjuryStatus.GAME_TIME_DECISION.value:
        base = 0.38
    elif normalized_status == InjuryStatus.QUESTIONABLE.value:
        base = 0.24
    elif normalized_status == InjuryStatus.PROBABLE.value:
        base = 0.10
    elif normalized_status == InjuryStatus.ACTIVE.value:
        base = 0.04
    if keyword_hit:
        base = max(base, 0.62)
    return min(max(base, 0.0), 0.98)


def _status_minutes_band(p_play: float, p_limited: float, normalized_status: str) -> tuple[float, float, float]:
    if normalized_status == InjuryStatus.OUT.value:
        return 0.0, 0.0, 0.0
    full_mid = 33.0 * p_play
    limited_mid = 24.0 * p_play
    mid = (limited_mid * p_limited) + (full_mid * (1.0 - p_limited))
    low = max(0.0, mid - (8.0 if p_limited > 0.35 else 5.0))
    high = max(mid, min(40.0, mid + (6.0 if normalized_status == InjuryStatus.ACTIVE.value else 4.0)))
    return round(low, 2), round(mid, 2), round(high, 2)


def _stable_id(*parts: Any) -> str:
    raw = "|".join(str(part) for part in parts)
    return hashlib.sha1(raw.encode("utf-8"), usedforsecurity=False).hexdigest()


class InjuryIntelligenceService:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path else NBA_NEWS_DB_PATH

    def _conn(self):
        return get_conn(self.db_path, db_key="nba_news")

    def build_events(
        self,
        injury_df: pd.DataFrame,
        *,
        source: str = "official_report",
        observed_at: datetime | None = None,
    ) -> list[InjuryEventRecord]:
        if injury_df is None or injury_df.empty:
            return []

        now = observed_at or _utc_now()
        source_prio = _source_priority(source)
        events: list[InjuryEventRecord] = []

        for _, row in injury_df.iterrows():
            player_name = str(row.get("player_name") or row.get("player_id") or "").strip()
            if not player_name:
                continue

            player_id = str(row.get("player_id") or slug(player_name)).strip()
            raw_status = str(row.get("status") or "").strip().upper()
            normalized_status = InjuryStatus.from_raw(raw_status).value
            detail_text = str(row.get("detail") or "").strip()
            team_id = str(row.get("team") or "").strip().upper()
            game_token = row.get("game_date") or row.get("game_id") or ""
            game_id = str(game_token).strip() or f"nba:{team_id}:unknown"
            parser_confidence = _safe_float(row.get("confidence"), 0.75)
            effective_at = _as_utc_datetime(row.get("game_date"), now)
            position = str(row.get("position") or "").strip().upper().split("/")[0].strip()
            impact = _status_impact(normalized_status)
            detail_bonus = 10.0 if any(token in detail_text.lower() for token in ("start", "warmup", "lineup")) else 0.0
            event_priority_score = round((source_prio * 0.65) + (impact * 100.0 * 0.35) + detail_bonus, 3)
            news_quality_score = round(min(1.0, (source_prio / 100.0) * 0.7 + parser_confidence * 0.3), 3)
            classification = _event_classification(normalized_status, detail_text, source_prio, impact)
            correlation_id = f"{player_id}:{effective_at.date().isoformat()}:{source}"
            event_id = _stable_id(
                player_id,
                source,
                normalized_status,
                detail_text.lower(),
                effective_at.date().isoformat(),
                bool(False),
            )
            events.append(
                InjuryEventRecord(
                    event_id=event_id,
                    player_id=player_id,
                    player_name=player_name,
                    team_id=team_id,
                    game_id=game_id,
                    source=source,
                    source_priority=source_prio,
                    raw_status=raw_status,
                    normalized_status=normalized_status,
                    detail_text=detail_text,
                    parser_confidence=parser_confidence,
                    observed_at=now,
                    effective_at=effective_at,
                    is_retraction=False,
                    correlation_id=correlation_id,
                    market_impact_estimate=round(impact, 3),
                    event_priority_score=event_priority_score,
                    news_quality_score=news_quality_score,
                    event_classification=classification,
                    position=position,
                )
            )
        return events

    def insert_events(self, events: Iterable[InjuryEventRecord]) -> int:
        rows = list(events)
        if not rows:
            return 0
        conn = self._conn()
        inserted = 0
        with write_lock("nba_news"):
            for event in rows:
                exists = conn.execute(
                    "SELECT 1 FROM injury_events WHERE event_id = ? LIMIT 1",
                    [event.event_id],
                ).fetchone()
                if exists:
                    continue
                conn.execute(
                    """
                    INSERT INTO injury_events (
                        event_id, player_id, player_name, team_id, game_id,
                        source, source_priority, raw_status, normalized_status,
                        detail_text, parser_confidence, observed_at, effective_at,
                        is_retraction, correlation_id, market_impact_estimate,
                        event_priority_score, news_quality_score, event_classification,
                        position
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        event.event_id,
                        event.player_id,
                        event.player_name,
                        event.team_id,
                        event.game_id,
                        event.source,
                        event.source_priority,
                        event.raw_status,
                        event.normalized_status,
                        event.detail_text,
                        event.parser_confidence,
                        event.observed_at,
                        event.effective_at,
                        event.is_retraction,
                        event.correlation_id,
                        event.market_impact_estimate,
                        event.event_priority_score,
                        event.news_quality_score,
                        event.event_classification,
                        event.position,
                    ],
                )
                inserted += 1
        return inserted

    def _load_events_df(self, player_ids: Iterable[str] | None = None) -> pd.DataFrame:
        conn = self._conn()
        if player_ids:
            values = [str(pid) for pid in player_ids]
            placeholders = ", ".join("?" for _ in values)
            return conn.execute(
                f"SELECT * FROM injury_events WHERE player_id IN ({placeholders}) ORDER BY observed_at DESC, event_priority_score DESC",
                values,
            ).df()
        return conn.execute(
            "SELECT * FROM injury_events ORDER BY observed_at DESC, event_priority_score DESC"
        ).df()

    def rebuild_states(self, player_ids: Iterable[str] | None = None) -> int:
        events_df = self._load_events_df(player_ids)
        if events_df.empty:
            return 0

        now = _utc_now()
        state_rows: list[list[Any]] = []
        state_player_ids: list[str] = []

        for player_id, grp in events_df.groupby("player_id", sort=False):
            grp = grp.sort_values(["observed_at", "event_priority_score"], ascending=[False, False]).head(12)
            weighted_status: dict[str, float] = {}
            total_weight = 0.0
            weighted_play = 0.0
            weighted_limited = 0.0
            weighted_news = 0.0
            last_event = grp.iloc[0]
            for _, event in grp.iterrows():
                observed_at = _as_utc_datetime(event.get("observed_at"), now)
                hours_old = max((now - observed_at).total_seconds() / 3600.0, 0.0)
                recency_weight = math.exp(-hours_old / 8.0)
                source_weight = _safe_float(event.get("source_priority"), 25.0) / 100.0
                parser_weight = max(0.05, _safe_float(event.get("parser_confidence"), 0.5))
                status = str(event.get("normalized_status") or "")
                impact_weight = 0.5 + _status_impact(status)
                weight = recency_weight * source_weight * parser_weight * impact_weight
                weighted_status[status] = weighted_status.get(status, 0.0) + weight
                total_weight += weight
                weighted_play += STATUS_PLAY_PROB_MAP.get(status, 0.75) * weight
                weighted_limited += _detail_limited_probability(
                    str(event.get("detail_text") or ""),
                    status,
                ) * weight
                weighted_news += _safe_float(event.get("news_quality_score"), 0.0) * weight

            ranked_statuses = sorted(
                weighted_status.items(),
                key=lambda item: (item[1], _status_impact(item[0])),
                reverse=True,
            )
            current_status = str(ranked_statuses[0][0])
            dominant_weight = weighted_status.get(current_status, 0.0)
            p_play = weighted_play / total_weight if total_weight else STATUS_PLAY_PROB_MAP.get(current_status, 0.75)
            p_limited = weighted_limited / total_weight if total_weight else _detail_limited_probability(
                str(last_event.get("detail_text") or ""), current_status
            )
            freshness_hours = max((now - _as_utc_datetime(last_event.get("observed_at"), now)).total_seconds() / 3600.0, 0.0)
            staleness_score = round(math.exp(-freshness_hours / 6.0), 3)
            source_agreement_score = round(dominant_weight / total_weight, 3) if total_weight else 0.0
            confidence_score = round(min(1.0, source_agreement_score * 0.7 + staleness_score * 0.3), 3)
            news_quality_score = round(weighted_news / total_weight, 3) if total_weight else 0.0
            p_late_scratch = round((1.0 - p_play) * (0.85 if current_status in {InjuryStatus.GAME_TIME_DECISION.value, InjuryStatus.QUESTIONABLE.value} else 0.45), 3)
            p_start = 0.94 if current_status == InjuryStatus.ACTIVE.value else 0.84 if current_status == InjuryStatus.PROBABLE.value else 0.62 if p_play >= 0.5 else 0.18
            low, mid, high = _status_minutes_band(p_play, p_limited, current_status)
            arbitration_context = json.dumps(
                {
                    "status_weights": {k: round(v, 5) for k, v in weighted_status.items()},
                    "event_count": int(len(grp)),
                    "dominant_source": str(last_event.get("source") or "unknown"),
                    "detail": str(last_event.get("detail_text") or ""),
                }
            )
            state_version = int(_safe_float(last_event.get("rowid"), 0.0) or 1)
            # Capture position from the most recent event with a position value
            position_val = ""
            for _, ev in grp.iterrows():
                p = str(ev.get("position") or "").strip().upper()
                if p:
                    position_val = p
                    break
            state_rows.append(
                [
                    player_id,
                    str(last_event.get("player_name") or player_id),
                    str(last_event.get("team_id") or ""),
                    str(last_event.get("game_id") or ""),
                    current_status,
                    round(p_play, 3),
                    low,
                    mid,
                    high,
                    round(p_start, 3),
                    round(p_limited, 3),
                    p_late_scratch,
                    confidence_score,
                    news_quality_score,
                    source_agreement_score,
                    staleness_score,
                    _as_utc_datetime(last_event.get("observed_at"), now),
                    state_version,
                    arbitration_context,
                    now,
                    position_val,
                ]
            )
            state_player_ids.append(player_id)

        conn = self._conn()
        with write_lock("nba_news"):
            if state_player_ids:
                placeholders = ", ".join("?" for _ in state_player_ids)
                conn.execute(
                    f"DELETE FROM player_injury_state WHERE player_id IN ({placeholders})",
                    state_player_ids,
                )
            for row in state_rows:
                conn.execute(
                    """
                    INSERT INTO player_injury_state (
                        player_id, player_name, team_id, game_id, current_status,
                        p_play, expected_minutes_low, expected_minutes_mid,
                        expected_minutes_high, p_start, p_limited, p_late_scratch,
                        confidence_score, news_quality_score, source_agreement_score,
                        staleness_score, last_event_at, state_version,
                        arbitration_context, updated_at, position
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    row,
                )

        self.refresh_scenarios(state_player_ids)
        self.rebuild_ownership_scenarios(state_player_ids)
        self.rebuild_beneficiaries(state_player_ids)
        return len(state_rows)

    def refresh_scenarios(self, player_ids: Iterable[str]) -> int:
        ids = [str(pid) for pid in player_ids if str(pid)]
        if not ids:
            return 0
        conn = self._conn()
        placeholders = ", ".join("?" for _ in ids)
        states = conn.execute(
            f"SELECT * FROM player_injury_state WHERE player_id IN ({placeholders})",
            ids,
        ).df()
        if states.empty:
            return 0

        scenario_rows: list[list[Any]] = []
        now = _utc_now()
        for _, state in states.iterrows():
            player_id = str(state["player_id"])
            p_play = _safe_float(state.get("p_play"), 0.75)
            p_limited = _safe_float(state.get("p_limited"), 0.0)
            out_prob = round(max(0.0, 1.0 - p_play), 4)
            limited_prob = round(max(0.0, p_play * p_limited), 4)
            full_prob = round(max(0.0, 1.0 - out_prob - limited_prob), 4)
            scenario_rows.extend(
                [
                    [
                        _stable_id(player_id, "OUT"),
                        player_id,
                        "OUT",
                        out_prob,
                        0.0,
                        0.0,
                        0.0,
                        0.0,
                        now,
                    ],
                    [
                        _stable_id(player_id, "IN_LIMITED"),
                        player_id,
                        "IN_LIMITED",
                        limited_prob,
                        round(_safe_float(state.get("expected_minutes_low"), 0.0) or (_safe_float(state.get("expected_minutes_mid"), 0.0) * 0.78), 2),
                        0.92,
                        1.15,
                        round(_safe_float(state.get("p_start"), 0.0) * 0.85, 3),
                        now,
                    ],
                    [
                        _stable_id(player_id, "IN_FULL"),
                        player_id,
                        "IN_FULL",
                        full_prob,
                        round(_safe_float(state.get("expected_minutes_high"), 0.0), 2),
                        1.0,
                        1.0,
                        round(_safe_float(state.get("p_start"), 0.0), 3),
                        now,
                    ],
                ]
            )

        with write_lock("nba_news"):
            conn.execute(f"DELETE FROM injury_scenarios WHERE player_id IN ({placeholders})", ids)
            for row in scenario_rows:
                conn.execute(
                    """
                    INSERT INTO injury_scenarios (
                        scenario_id, player_id, scenario_name, scenario_probability,
                        expected_minutes, usage_multiplier, volatility_multiplier,
                        p_start, generated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    row,
                )
        return len(scenario_rows)

    def rebuild_ownership_scenarios(self, player_ids: Iterable[str], site: str = "DK") -> int:
        """Compute and persist scenario-weighted ownership estimates per player.

        For each player in player_injury_state, writes three rows to
        ``ownership_scenarios`` (one per scenario: OUT, IN_LIMITED, IN_FULL).
        Ownership estimates use a simple percentile-rank heuristic based on
        expected_minutes, scaled by scenario probability.  This table is the
        source of truth for the Injury Tab's expected-ownership-delta display.

        Returns the number of rows written.
        """
        ids = [str(pid) for pid in player_ids if str(pid)]
        if not ids:
            return 0
        conn = self._conn()
        placeholders = ", ".join("?" for _ in ids)
        states = conn.execute(
            f"""
            SELECT player_id, p_play, p_limited, expected_minutes_mid,
                   expected_minutes_low, expected_minutes_high
            FROM player_injury_state
            WHERE player_id IN ({placeholders})
            """,
            ids,
        ).df()
        if states.empty:
            return 0

        now = _utc_now()
        scenario_names = ["OUT", "IN_LIMITED", "IN_FULL"]
        rows: list[list[Any]] = []

        for _, state in states.iterrows():
            player_id = str(state["player_id"])
            p_play = _safe_float(state.get("p_play"), 0.75)
            p_limited = _safe_float(state.get("p_limited"), 0.0)
            p_out = max(0.0, 1.0 - p_play)
            p_lim = max(0.0, p_play * p_limited)
            p_full = max(0.0, 1.0 - p_out - p_lim)
            # Proxy ownership estimates: minutes-band scaled ownership
            mid_min = _safe_float(state.get("expected_minutes_mid"), 0.0)
            low_min = _safe_float(state.get("expected_minutes_low"), 0.0)
            high_min = _safe_float(state.get("expected_minutes_high"), 0.0)
            # Rough ownership proxy: (minutes / 40) × baseline_ownership_rate (30%)
            # Actual model-based ownership must come from predict_ownership() at runtime.
            # This is stored as a relative signal, not an absolute prediction.
            base_rate = 0.30  # 30% baseline ownership for a "full" player
            own_out = 0.0
            own_lim = round((low_min / 40.0) * base_rate * 100.0, 2) if low_min > 0 else 0.0
            own_full = round((high_min / 40.0) * base_rate * 100.0, 2) if high_min > 0 else 0.0
            own_weighted = round(own_out * p_out + own_lim * p_lim + own_full * p_full, 2)

            for scenario, prob, own_pct in [
                ("OUT", p_out, own_out),
                ("IN_LIMITED", p_lim, own_lim),
                ("IN_FULL", p_full, own_full),
            ]:
                rows.append(
                    [
                        _stable_id(player_id, site, scenario),
                        player_id,
                        site,
                        scenario,
                        own_pct,
                        own_weighted,
                        0.0,  # reaction_lag_minutes — placeholder for future market timing model
                        now,
                    ]
                )

        if not rows:
            return 0

        with write_lock("nba_news"):
            conn.execute(
                f"DELETE FROM ownership_scenarios WHERE player_id IN ({placeholders})",
                ids,
            )
            for row in rows:
                conn.execute(
                    """
                    INSERT INTO ownership_scenarios (
                        ownership_scenario_id, player_id, site, scenario_name,
                        ownership_pct, weighted_ownership_pct, reaction_lag_minutes,
                        generated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    row,
                )
        return len(rows)

    def rebuild_beneficiaries(self, player_ids: Iterable[str]) -> int:
        """Compute and persist injury beneficiaries for the given injured player IDs.

        Role-aware, scenario-aware, position-sensitive redistribution:
        - Same-position teammates get a larger minutes share (positional overlap)
        - Rank-1 beneficiary (top minutes on team) gets reason codes indicating
          "likely starter" when p_start of injured player > 0.5 and rank == 1
        - delta_assist_rate and delta_rebound_rate estimated proportionally from
          the injured player's own usage share
        - reason_codes list captures WHY a player benefits (playable UI signal)

        Returns the total number of beneficiary rows written.
        """
        ids = [str(pid) for pid in player_ids if str(pid)]
        if not ids:
            return 0
        conn = self._conn()
        placeholders = ", ".join("?" for _ in ids)

        # Load states for the injured players being refreshed
        injured_states = conn.execute(
            f"""
            SELECT player_id, player_name, team_id, p_play, position,
                   expected_minutes_mid, p_start, p_limited, confidence_score
            FROM player_injury_state
            WHERE player_id IN ({placeholders})
            """,
            ids,
        ).df()
        if injured_states.empty:
            return 0

        # Load all active teammates (all states, for same-team lookup)
        all_states = conn.execute(
            """
            SELECT player_id, player_name, team_id, p_play,
                   expected_minutes_mid, current_status, position, p_start
            FROM player_injury_state
            """
        ).df()
        # Normalise position column — may be absent in older rows
        if "position" not in all_states.columns:
            all_states["position"] = ""
        if "position" not in injured_states.columns:
            injured_states["position"] = ""
        if "p_start" not in all_states.columns:
            all_states["p_start"] = 0.8

        now = _utc_now()
        beneficiary_rows: list[list[Any]] = []
        injured_ids_written: list[str] = []

        for _, injured in injured_states.iterrows():
            inj_id = str(injured["player_id"])
            inj_team = str(injured.get("team_id") or "")
            inj_pos = str(injured.get("position") or "").strip().upper()
            p_out = max(0.0, 1.0 - _safe_float(injured.get("p_play"), 1.0))
            if p_out < 0.10:
                continue  # negligible absence probability — skip

            inj_minutes = _safe_float(injured.get("expected_minutes_mid"), 0.0)
            inj_p_start = _safe_float(injured.get("p_start"), 0.8)
            inj_confidence = _safe_float(injured.get("confidence_score"), 0.5)

            # Teammates: same team, different player, non-trivially active
            teammates = all_states[
                (all_states["team_id"] == inj_team)
                & (all_states["player_id"] != inj_id)
                & (all_states["p_play"] >= 0.4)
            ].copy()
            if teammates.empty:
                continue

            # Position-aware weighting: same position gets 1.6× more weight
            teammates["pos_norm"] = teammates["position"].fillna("").str.strip().str.upper()
            teammates["pos_weight"] = teammates["pos_norm"].apply(
                lambda p: 1.6 if (inj_pos and p == inj_pos) else 1.0
            )
            teammates["adj_min"] = (
                teammates["expected_minutes_mid"].fillna(0.0) * teammates["pos_weight"]
            )

            # Rank by adjusted minutes (descending) — top 5
            teammates = teammates.sort_values("adj_min", ascending=False).head(5)
            total_adj_min = teammates["adj_min"].sum()
            if total_adj_min <= 0:
                continue

            injured_ids_written.append(inj_id)

            # Estimated stat rate proxies for the injured player
            # (in 36-minute units; inj_minutes > 0 guard)
            per36_assist_proxy = 0.08 if inj_pos in ("PG", "SG") else 0.04
            per36_reb_proxy    = 0.10 if inj_pos in ("PF", "C")  else 0.05

            for rank, (_, teammate) in enumerate(teammates.iterrows(), start=1):
                ben_id = str(teammate["player_id"])
                ben_name = str(teammate.get("player_name") or ben_id)
                ben_pos = str(teammate.get("pos_norm") or "").strip().upper()
                adj_min = _safe_float(teammate.get("adj_min"), 0.0)
                raw_min = _safe_float(teammate.get("expected_minutes_mid"), 0.0)

                # Share by adjusted weight
                share = adj_min / total_adj_min if total_adj_min > 0 else 0.0
                delta_minutes = round(inj_minutes * p_out * share, 2)
                delta_usage = round(delta_minutes / 36.0, 4)
                # Proportional stat rate deltas
                delta_assist_rate = round(
                    per36_assist_proxy * (delta_minutes / 36.0), 4
                ) if delta_minutes > 0 else 0.0
                delta_rebound_rate = round(
                    per36_reb_proxy * (delta_minutes / 36.0), 4
                ) if delta_minutes > 0 else 0.0

                ben_p_play = _safe_float(teammate.get("p_play"), 0.8)
                ben_p_start = _safe_float(teammate.get("p_start"), 0.7)
                p_close = round(ben_p_play * p_out * (1.0 - share * 0.4), 3)
                volatility_uplift = round(p_out * 0.15 * (1.25 if rank == 1 else 1.0), 3)
                rank_score = round(share * p_out * inj_confidence, 5)

                # ── Reason codes (UI narrative signal) ──────────────────────
                reasons: list[str] = []
                if rank == 1:
                    if inj_p_start >= 0.7:
                        reasons.append("likely_starter")
                    else:
                        reasons.append("primary_backup_minutes")
                else:
                    reasons.append("backup_minutes")

                if inj_pos and ben_pos == inj_pos:
                    reasons.append("same_position_overlap")

                if delta_minutes >= 5.0:
                    reasons.append("large_minutes_upside")
                elif delta_minutes >= 2.5:
                    reasons.append("moderate_minutes_upside")

                if inj_pos in ("PG",) and ben_pos in ("PG", "SG"):
                    reasons.append("on_ball_usage_bump")

                if inj_pos in ("PF", "C") and ben_pos in ("PF", "C"):
                    reasons.append("rebound_share_increase")

                if ben_p_start >= 0.85:
                    reasons.append("closing_lineup_boost")

                if p_out >= 0.7 and rank <= 2:
                    reasons.append("high_confidence_beneficiary")

                beneficiary_rows.append(
                    [
                        _stable_id(inj_id, ben_id),  # beneficiary_id
                        inj_id,                       # player_id (injured)
                        ben_id,                       # beneficiary_player_id
                        ben_name,                     # beneficiary_name
                        inj_team,                     # team_id
                        "OUT",                        # scenario_name
                        delta_minutes,                # delta_minutes
                        delta_usage,                  # delta_usage
                        delta_assist_rate,            # delta_assist_rate
                        delta_rebound_rate,           # delta_rebound_rate
                        ben_p_play,                   # p_start
                        p_close,                      # p_close
                        volatility_uplift,            # volatility_uplift
                        inj_confidence,               # confidence
                        rank_score,                   # rank_score
                        now,                          # generated_at
                        ben_pos,                      # position
                        json.dumps(reasons),          # reason_codes
                    ]
                )

        if not beneficiary_rows:
            return 0

        with write_lock("nba_news"):
            if injured_ids_written:
                ph = ", ".join("?" for _ in injured_ids_written)
                conn.execute(
                    f"DELETE FROM injury_beneficiaries WHERE player_id IN ({ph})",
                    injured_ids_written,
                )
            for row in beneficiary_rows:
                conn.execute(
                    """
                    INSERT INTO injury_beneficiaries (
                        beneficiary_id, player_id, beneficiary_player_id, beneficiary_name,
                        team_id, scenario_name, delta_minutes, delta_usage,
                        delta_assist_rate, delta_rebound_rate, p_start, p_close,
                        volatility_uplift, confidence, rank_score, generated_at,
                        position, reason_codes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    row,
                )

        log.info(
            "Beneficiary rows written: %d (for %d injured players)",
            len(beneficiary_rows),
            len(injured_ids_written),
        )
        return len(beneficiary_rows)

    def sync_current_injuries(
        self,
        injury_df: pd.DataFrame,
        *,
        source: str = "official_report",
        observed_at: datetime | None = None,
    ) -> dict[str, int]:
        events = self.build_events(injury_df, source=source, observed_at=observed_at)
        inserted_events = self.insert_events(events)
        rebuilt_states = self.rebuild_states({event.player_id for event in events}) if events else 0
        # rebuild_beneficiaries is called inside rebuild_states; surface count separately
        beneficiary_count = 0
        if events:
            try:
                beneficiary_count = self.rebuild_beneficiaries(
                    {event.player_id for event in events}
                )
            except Exception as exc:
                log.warning("rebuild_beneficiaries failed: %s", exc)
        return {
            "built_events": len(events),
            "inserted_events": inserted_events,
            "rebuilt_states": rebuilt_states,
            "rebuild_beneficiaries": beneficiary_count,
        }

    def load_player_states_df(self) -> pd.DataFrame:
        try:
            return self._conn().execute(
                "SELECT * FROM player_injury_state ORDER BY updated_at DESC"
            ).df()
        except Exception:
            return pd.DataFrame()


def sync_injury_intelligence(
    injury_df: pd.DataFrame,
    *,
    source: str = "official_report",
    db_path: Path | None = None,
    observed_at: datetime | None = None,
) -> dict[str, int]:
    service = InjuryIntelligenceService(db_path=db_path)
    return service.sync_current_injuries(injury_df, source=source, observed_at=observed_at)