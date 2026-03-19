from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import pandas as pd
from pulp import (
    LpProblem,
    LpMaximize,
    LpVariable,
    lpSum,
    LpBinary,
    PULP_CBC_CMD,
    PulpSolverError,
)

log = logging.getLogger(__name__)


@dataclass
class StackRule:
    """Stacking constraints for GPP lineup construction."""
    min_from_same_game: int = 2     # min players from any one game
    bring_back_count: int = 1       # min from opposing team when stacking
    min_from_same_team: int = 0     # min from one specific team (0 = off)
    max_from_same_team: int = 8     # cap players from one team
    max_from_same_game: int = 6     # cap players from one game
# ---------- Roster rules ----------
FD_SLOTS = {
    "PG": 2,
    "SG": 2,
    "SF": 2,
    "PF": 2,
    "C": 1,
}

DK_SLOTS = {
    "PG": 1,
    "SG": 1,
    "SF": 1,
    "PF": 1,
    "C": 1,
    "G": 1,
    "F": 1,
    "UTIL": 1,
}

SITE_RULES = {
    "DK": {"salary_cap": 50000, "roster_size": 8, "slots": DK_SLOTS},
    "FD": {"salary_cap": 60000, "roster_size": 9, "slots": FD_SLOTS},
}


def _eligibility_set(pos_str: str) -> set[str]:
    """Convert 'PG/G/UTIL' -> {'PG','G','UTIL'}"""
    if not isinstance(pos_str, str):
        return set()
    return set(p.strip().upper() for p in pos_str.split("/") if p.strip())


def _slot_is_eligible(site: str, slot: str, player_eligibility: set[str]) -> bool:
    if site == "FD":
        return slot in player_eligibility
    if slot == "UTIL":
        return len(player_eligibility.intersection({"PG", "SG", "SF", "PF", "C", "G", "F"})) > 0
    if slot == "G":
        return len(player_eligibility.intersection({"PG", "SG", "G"})) > 0
    if slot == "F":
        return len(player_eligibility.intersection({"SF", "PF", "F"})) > 0
    return slot in player_eligibility


def _build_slot_list(site: str) -> list[tuple[str, str]]:
    if site == "FD":
        return [
            ("PG", "PG"), ("PG_2", "PG"),
            ("SG", "SG"), ("SG_2", "SG"),
            ("SF", "SF"), ("SF_2", "SF"),
            ("PF", "PF"), ("PF_2", "PF"),
            ("C", "C"),
        ]
    return [
        ("PG", "PG"),
        ("SG", "SG"),
        ("SF", "SF"),
        ("PF", "PF"),
        ("C", "C"),
        ("G", "G"),
        ("F", "F"),
        ("UTIL", "UTIL"),
    ]


def assign_lineup_slots(players_df: pd.DataFrame, site: str) -> dict[str, str] | None:
    site = site.upper().strip()
    if site not in SITE_RULES:
        return None

    slot_defs = _build_slot_list(site)
    eligibilities = [_eligibility_set(v) for v in players_df["Pos"].astype(str).tolist()]
    raw_names = players_df["Name"].astype(str).tolist()

    # Preview always uses plain player names; ID formatting is handled at export time.
    names = raw_names

    slot_candidates: list[tuple[str, str, list[int]]] = []
    for slot_key, slot_type in slot_defs:
        candidates = [idx for idx, elig in enumerate(eligibilities) if _slot_is_eligible(site, slot_type, elig)]
        slot_candidates.append((slot_key, slot_type, candidates))

    slot_candidates.sort(key=lambda item: len(item[2]))

    used = set()
    assignment: dict[str, str] = {}

    def backtrack(i: int) -> bool:
        if i >= len(slot_candidates):
            return True
        slot_key, slot_type, candidates = slot_candidates[i]
        for idx in candidates:
            if idx in used:
                continue
            if not _slot_is_eligible(site, slot_type, eligibilities[idx]):
                continue
            used.add(idx)
            assignment[slot_key] = names[idx]
            if backtrack(i + 1):
                return True
            used.remove(idx)
            assignment.pop(slot_key, None)
        return False

    if not backtrack(0):
        return None
    return assignment


def validate_lineup(players_df: pd.DataFrame, site: str) -> bool:
    site = site.upper().strip()
    if site not in SITE_RULES:
        return False

    rules = SITE_RULES[site]
    roster_size = rules["roster_size"]
    slots = rules["slots"]

    if len(players_df) != roster_size:
        return False

    salary = pd.to_numeric(players_df["Salary"], errors="coerce").fillna(0).sum()
    if salary > rules["salary_cap"]:
        return False

    return assign_lineup_slots(players_df, site) is not None


def optimize_portfolio(
    players: pd.DataFrame,
    site: str,
    n_lineups: int = 150,
    num_unique: int = 2,
    max_exposure: float = 0.60,
    leverage_weight: float = 0.25,
    per_player_caps: dict[str, int] | None = None,
    per_player_floors: dict[str, int] | None = None,
    locks: list[str] | None = None,
    fades: list[str] | None = None,
    stack_rules: StackRule | None = None,
) -> pd.DataFrame:
    """
    Strict roster optimizer for DK and FD with diversification.

    Constraints:
      - salary cap
      - roster size
      - slot constraints
      - global + per-player exposure caps / floors
      - uniqueness vs prior lineups

    Objective:
      maximize Proj + leverage_weight * (Leverage if present, else LowOwnBonus)

    Parameters
    ----------
    per_player_caps
        Dict of {DFS_ID: max_lineup_count}.  Overrides the global
        ``max_exposure`` count for individual players.
    per_player_floors
        Dict of {DFS_ID: min_lineup_count}.  Forces a player to appear
        in at least this many lineups (hard LP constraint added once the
        floor is reached up from below).
    """

    site = site.upper().strip()
    if site not in SITE_RULES:
        raise ValueError(f"Unknown site: {site}. Expected FD or DK.")

    rules = SITE_RULES[site]
    salary_cap = rules["salary_cap"]
    roster_size = rules["roster_size"]
    slots = rules["slots"]

    df = players.copy()

    # Required columns
    for col in ["DFS_ID", "Salary", "Proj", "Pos"]:
        if col not in df.columns:
            raise ValueError(f"Players dataframe missing required column: {col}")

    # Numeric cleanup
    df["Salary"] = pd.to_numeric(df["Salary"], errors="coerce").fillna(0)
    df["Proj"] = pd.to_numeric(df["Proj"], errors="coerce").fillna(0)
    df["Own"] = pd.to_numeric(df.get("Own", 0), errors="coerce").fillna(0)

    # Apply fades — remove excluded players before building LP
    _fades_lower = {f.lower().strip() for f in (fades or [])}
    _locks_lower = {l.lower().strip() for l in (locks or [])}
    if _fades_lower:
        name_col = next((c for c in ["Name", "player_name"] if c in df.columns), None)
        if name_col:
            df = df[~df[name_col].str.lower().str.strip().isin(_fades_lower)].copy()
            log.info("Faded %d players from pool", len(players) - len(df))

    # Build game_id from Team + Opp for stacking (frozenset so A vs B == B vs A)
    team_col = next((c for c in ["Team", "team"] if c in df.columns), None)
    opp_col = next((c for c in ["Opp", "opp", "Opponent"] if c in df.columns), None)
    if team_col and opp_col:
        df["game_id"] = df.apply(
            lambda r: str(frozenset({str(r[team_col]).strip(), str(r[opp_col]).strip()})),
            axis=1,
        )
    else:
        df["game_id"] = "unknown"

    # Contrarian bonus
    df["LowOwnBonus"] = (1.0 - df["Own"] / 100.0).clip(lower=0)
    obj_bonus_col = "Leverage" if "Leverage" in df.columns else "LowOwnBonus"

    df["DFS_ID"] = df["DFS_ID"].astype(str)
    ids = df["DFS_ID"].tolist()

    # Lock/fade ID sets (by DFS_ID for LP; also match by name)
    name_to_id: dict[str, str] = {}
    if "Name" in df.columns:
        name_to_id = dict(zip(df["Name"].str.lower().str.strip(), df["DFS_ID"].astype(str)))
    locked_ids: set[str] = set()
    for lock in (locks or []):
        low = lock.lower().strip()
        if low in name_to_id:
            locked_ids.add(name_to_id[low])

    # Eligibility lookup — O(n) vs O(n²) df.loc scan
    elig = {
        pid: _eligibility_set(pos)
        for pid, pos in zip(df["DFS_ID"].tolist(), df["Pos"].astype(str).tolist())
    }
    slot_names: list[str] = []
    for slot_name, req in slots.items():
        for n in range(req):
            slot_names.append(f"{slot_name}_{n}")

    # Exposure control
    exposure_counts = {pid: 0 for pid in ids}
    global_max_count = max(1, math.ceil(max_exposure * n_lineups))
    _caps = per_player_caps or {}
    _floors = per_player_floors or {}
    max_count_for = {pid: _caps.get(pid, global_max_count) for pid in ids}
    floor_remaining = {pid: cnt for pid, cnt in _floors.items() if pid in ids}

    # Game/team sets for stacking — pre-built O(1) dicts avoid O(n²) per-player df scans
    _pid_to_game_id = dict(zip(df["DFS_ID"].tolist(), df["game_id"].astype(str).tolist()))
    _pid_to_team = (
        dict(zip(df["DFS_ID"].tolist(), df[team_col].astype(str).str.strip().tolist()))
        if team_col else {}
    )
    games: dict[str, list[str]] = {}
    teams: dict[str, list[str]] = {}
    for pid in ids:
        gid = _pid_to_game_id.get(pid, "unknown")
        team = _pid_to_team.get(pid, "UNK") if team_col else "UNK"
        games.setdefault(gid, []).append(pid)
        teams.setdefault(team, []).append(pid)

    # Build team→opponent mapping for bring-back
    team_to_opp: dict[str, str] = {}
    if team_col and opp_col:
        for _, row in df.drop_duplicates(subset=[team_col]).iterrows():
            team_to_opp[str(row[team_col]).strip()] = str(row[opp_col]).strip()

    # Pre-compute game → {team: [player_ids]} for bring-back constraints
    # (avoids expensive df lookups inside the per-lineup loop)
    _player_to_team: dict[str, str] = {}
    if team_col:
        _player_to_team = dict(zip(df["DFS_ID"].tolist(), df[team_col].astype(str).str.strip().tolist()))
    game_team_players: dict[str, dict[str, list[str]]] = {}
    for _gid, _pids in games.items():
        _tmap: dict[str, list[str]] = {}
        for _p in _pids:
            _t = _player_to_team.get(_p, "UNK")
            _tmap.setdefault(_t, []).append(_p)
        game_team_players[_gid] = _tmap

    # Pre-build O(1) value lookup dicts — eliminates O(n²) df.loc scans inside the
    # per-lineup loop (150 lineups × N players × 3 columns = significant speedup).
    proj_lookup: dict[str, float] = dict(zip(df["DFS_ID"].tolist(), df["Proj"].astype(float).tolist()))
    salary_lookup: dict[str, float] = dict(zip(df["DFS_ID"].tolist(), df["Salary"].astype(float).tolist()))
    bonus_lookup: dict[str, float] = dict(zip(df["DFS_ID"].tolist(), df[obj_bonus_col].astype(float).tolist()))

    portfolio_rows = []
    previous_lineups = []

    for k in range(n_lineups):
        prob = LpProblem(f"lineup_{k}", LpMaximize)
        x = {pid: LpVariable(f"x_{pid}", cat=LpBinary) for pid in ids}
        y = {
            (pid, slot): LpVariable(f"y_{pid}_{slot}", cat=LpBinary)
            for pid in ids
            for slot in slot_names
            if _slot_is_eligible(site, slot.split("_")[0], elig[pid])
        }

        # Objective
        prob += lpSum(
            x[pid] * (proj_lookup[pid] + leverage_weight * bonus_lookup[pid])
            for pid in ids
        )

        # Salary cap
        prob += lpSum(x[pid] * salary_lookup[pid] for pid in ids) <= salary_cap

        # Roster size
        prob += lpSum(x[pid] for pid in ids) == roster_size

        # Lock constraints — forced into lineup
        for pid in locked_ids:
            if pid in x:
                prob += x[pid] == 1

        # Exposure caps
        for pid in ids:
            if exposure_counts[pid] >= max_count_for[pid]:
                prob += x[pid] == 0

        # Floor constraints
        lineups_remaining = n_lineups - k
        for pid, remaining_needed in list(floor_remaining.items()):
            if remaining_needed >= lineups_remaining and pid in x:
                prob += x[pid] == 1

        # Uniqueness
        for prev in previous_lineups:
            prob += lpSum(x[pid] for pid in prev) <= roster_size - num_unique

        # Slot assignment
        for pid in ids:
            assignable = [y[(pid, slot)] for slot in slot_names if (pid, slot) in y]
            if assignable:
                prob += lpSum(assignable) == x[pid]
            else:
                prob += x[pid] == 0

        for slot in slot_names:
            slot_assignments = [y[(pid, slot)] for pid in ids if (pid, slot) in y]
            prob += lpSum(slot_assignments) == 1

        # ── Stacking constraints ─────────────────────────────────────────
        if stack_rules is not None:
            sr = stack_rules

            # Max from one team
            for team, team_ids in teams.items():
                valid_ids = [p for p in team_ids if p in x]
                if valid_ids:
                    prob += lpSum(x[p] for p in valid_ids) <= sr.max_from_same_team

            # Max from one game
            for gid, game_ids in games.items():
                valid_ids = [p for p in game_ids if p in x]
                if valid_ids:
                    prob += lpSum(x[p] for p in valid_ids) <= sr.max_from_same_game

            # Min game stack + bring-back
            if sr.min_from_same_game >= 2:
                # Binary indicator: is this game "stacked"?
                game_stacked = {}
                for _gidx, (gid, game_ids) in enumerate(games.items()):
                    valid_ids = [p for p in game_ids if p in x]
                    if len(valid_ids) < sr.min_from_same_game:
                        continue
                    # Use enumerate index + lineup index to guarantee unique var name
                    g_var = LpVariable(f"gstack_{_gidx}_{k}", cat=LpBinary)
                    game_stacked[gid] = g_var
                    # If stacked, must have >= min_from_same_game from this game
                    prob += lpSum(x[p] for p in valid_ids) >= sr.min_from_same_game * g_var

                # Require at least 1 stacked game in lineup
                if game_stacked:
                    prob += lpSum(game_stacked.values()) >= 1

                # Bring-back: when a game is stacked (g_var=1), require
                # >= bring_back_count from EACH team in that game so both
                # sides of the matchup are represented.  This replaces the
                # previous per-team big-M approach which caused cascading
                # infeasibility on smaller player pools.
                if sr.bring_back_count >= 1 and game_stacked:
                    for _stk_gid, _g_var in game_stacked.items():
                        for _t, _tpids in game_team_players.get(_stk_gid, {}).items():
                            _valid_t = [p for p in _tpids if p in x]
                            if _valid_t:
                                prob += (
                                    lpSum(x[p] for p in _valid_t)
                                    >= sr.bring_back_count * _g_var
                                )

        try:
            prob.solve(PULP_CBC_CMD(msg=False, timeLimit=30))
        except PulpSolverError as exc:
            log.warning("CBC solver error at lineup %d — stopping early: %s", k, exc)
            break
        except Exception as exc:
            log.warning("Unexpected solver error at lineup %d — stopping early: %s", k, exc)
            break

        chosen = [pid for pid in ids if (x[pid].value() or 0) > 0.5]

        if len(chosen) != roster_size:
            print(f"⚠️ Optimizer stopped early at lineup {k} (no feasible solution).")
            break

        lineup_df = df[df["DFS_ID"].isin(chosen)].copy()
        if not validate_lineup(lineup_df, site):
            print(f"⚠️ Optimizer returned an invalid {site} lineup at index {k}; stopping generation.")
            break

        previous_lineups.append(chosen)
        for pid in chosen:
            exposure_counts[pid] += 1
            # Update floor tracker
            if pid in floor_remaining:
                floor_remaining[pid] = max(0, floor_remaining[pid] - 1)

        lineup_df["LineupIndex"] = k
        portfolio_rows.append(lineup_df)

    if not portfolio_rows:
        return pd.DataFrame()

    return pd.concat(portfolio_rows, ignore_index=True)
