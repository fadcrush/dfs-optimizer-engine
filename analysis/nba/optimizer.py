import pandas as pd
from pulp import (
    LpProblem,
    LpMaximize,
    LpVariable,
    lpSum,
    LpBinary,
    PULP_CBC_CMD
)

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


def optimize_portfolio(
    players: pd.DataFrame,
    site: str,
    n_lineups: int = 150,
    min_unique: int = 2,
    max_exposure: float = 0.60,
    leverage_weight: float = 0.25,
) -> pd.DataFrame:
    """
    Strict roster optimizer for DK and FD with diversification.

    Constraints:
      - salary cap
      - roster size
      - slot constraints
      - exposure caps
      - uniqueness vs prior lineups

    Objective:
      maximize Proj + leverage_weight * (low_own_bonus)
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

    # Contrarian bonus: lower own = more bonus
    df["LowOwnBonus"] = (1.0 - df["Own"] / 100.0).clip(lower=0)

    df["DFS_ID"] = df["DFS_ID"].astype(str)
    ids = df["DFS_ID"].tolist()

    # Eligibility lookup
    elig = {pid: _eligibility_set(df.loc[df["DFS_ID"] == pid, "Pos"].values[0]) for pid in ids}

    # Exposure control
    exposure_counts = {pid: 0 for pid in ids}
    max_count = int(max_exposure * n_lineups)

    portfolio_rows = []
    previous_lineups = []

    for k in range(n_lineups):
        prob = LpProblem(f"lineup_{k}", LpMaximize)
        x = {pid: LpVariable(f"x_{pid}", cat=LpBinary) for pid in ids}

        # Objective
        prob += lpSum(
            x[pid] * (
                float(df.loc[df["DFS_ID"] == pid, "Proj"].values[0]) +
                leverage_weight * float(df.loc[df["DFS_ID"] == pid, "LowOwnBonus"].values[0])
            )
            for pid in ids
        )

        # Salary constraint
        prob += lpSum(
            x[pid] * float(df.loc[df["DFS_ID"] == pid, "Salary"].values[0])
            for pid in ids
        ) <= salary_cap

        # Total roster size
        prob += lpSum(x[pid] for pid in ids) == roster_size

        # Exposure caps
        for pid in ids:
            if exposure_counts[pid] >= max_count:
                prob += x[pid] == 0

        # Uniqueness constraint
        for prev in previous_lineups:
            prob += lpSum(x[pid] for pid in prev) <= roster_size - min_unique

        # Slot constraints
        if site == "DK":
            # DK requires exact 1 for each slot
            for slot, req in slots.items():
                prob += lpSum(x[pid] for pid in ids if slot in elig[pid]) >= req
        else:
            # FD requires exact position counts
            for slot, req in slots.items():
                prob += lpSum(x[pid] for pid in ids if slot in elig[pid]) == req

        prob.solve(PULP_CBC_CMD(msg=False))

        chosen = [pid for pid in ids if x[pid].value() == 1]

        if len(chosen) != roster_size:
            print(f"⚠️ Optimizer stopped early at lineup {k} (no feasible solution).")
            break

        previous_lineups.append(chosen)
        for pid in chosen:
            exposure_counts[pid] += 1

        lineup_df = df[df["DFS_ID"].isin(chosen)].copy()
        lineup_df["LineupIndex"] = k
        portfolio_rows.append(lineup_df)

    if not portfolio_rows:
        return pd.DataFrame()

    return pd.concat(portfolio_rows, ignore_index=True)
