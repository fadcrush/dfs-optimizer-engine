from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd


CorrelationMode = Literal["none", "team"]


@dataclass
class SimulationConfig:
    n_sims: int = 2000
    seed: int | None = None
    correlation: CorrelationMode = "team"
    base_corr: float = 0.05
    same_team_corr: float = 0.18
    opp_corr: float = 0.06
    variance_pct: float = 0.18


def _derive_sigma(df: pd.DataFrame, variance_pct: float) -> pd.Series:
    if "Ceiling" in df.columns and "Floor" in df.columns:
        ceiling = pd.to_numeric(df["Ceiling"], errors="coerce").fillna(0.0)
        floor = pd.to_numeric(df["Floor"], errors="coerce").fillna(0.0)
        sigma = (ceiling - floor).abs() / 4.0
        return sigma.clip(lower=1.0)

    proj = pd.to_numeric(df["Proj"], errors="coerce").fillna(0.0)
    sigma = (proj.abs() * variance_pct).clip(lower=1.0)
    return sigma


def build_team_correlation_matrix(
    df: pd.DataFrame,
    base_corr: float,
    same_team_corr: float,
    opp_corr: float,
) -> np.ndarray:
    n = len(df)
    corr = np.full((n, n), base_corr, dtype=float)
    np.fill_diagonal(corr, 1.0)

    teams = df["Team"].astype(str).fillna("").tolist()
    opps = df["Opp"].astype(str).fillna("").tolist()

    for i in range(n):
        for j in range(i + 1, n):
            if teams[i] and teams[i] == teams[j]:
                corr[i, j] = same_team_corr
                corr[j, i] = same_team_corr
            elif teams[i] and opps[j] and teams[i] == opps[j]:
                corr[i, j] = opp_corr
                corr[j, i] = opp_corr
            elif teams[j] and opps[i] and teams[j] == opps[i]:
                corr[i, j] = opp_corr
                corr[j, i] = opp_corr

    return corr


def simulate_player_outcomes(
    projections_df: pd.DataFrame,
    config: SimulationConfig,
) -> np.ndarray:
    if config.n_sims <= 0:
        raise ValueError("n_sims must be positive")

    df = projections_df.copy().reset_index(drop=True)
    mean = pd.to_numeric(df["Proj"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
    sigma = _derive_sigma(df, config.variance_pct).to_numpy(dtype=float)

    if config.correlation == "team":
        corr = build_team_correlation_matrix(
            df,
            base_corr=config.base_corr,
            same_team_corr=config.same_team_corr,
            opp_corr=config.opp_corr,
        )
    else:
        corr = np.eye(len(df), dtype=float)

    cov = np.outer(sigma, sigma) * corr
    cov += np.eye(len(df), dtype=float) * 1e-6

    rng = np.random.default_rng(config.seed)
    sims = rng.multivariate_normal(mean, cov, size=config.n_sims)
    sims = np.clip(sims, 0.0, None)
    return sims


def summarize_player_sims(
    projections_df: pd.DataFrame,
    sims: np.ndarray,
) -> pd.DataFrame:
    df = projections_df.copy().reset_index(drop=True)
    if sims.size == 0:
        return pd.DataFrame()

    summary = pd.DataFrame(
        {
            "DFS_ID": df["DFS_ID"].astype(str),
            "Sim_Mean": sims.mean(axis=0),
            "Sim_Median": np.median(sims, axis=0),
            "Sim_P90": np.percentile(sims, 90, axis=0),
            "Sim_P95": np.percentile(sims, 95, axis=0),
            "Sim_P99": np.percentile(sims, 99, axis=0),
            "Sim_StdDev": sims.std(axis=0, ddof=0),
        }
    )
    return summary


def simulate_lineup_scores(
    lineups_df: pd.DataFrame,
    projections_df: pd.DataFrame,
    config: SimulationConfig,
) -> tuple[pd.DataFrame, np.ndarray]:
    if "LineupIndex" not in lineups_df.columns:
        raise ValueError("lineups_df must include LineupIndex")
    if "DFS_ID" not in lineups_df.columns:
        raise ValueError("lineups_df must include DFS_ID")

    proj_df = projections_df.copy().reset_index(drop=True)
    if "DFS_ID" not in proj_df.columns:
        raise ValueError("projections_df must include DFS_ID")

    player_sims = simulate_player_outcomes(proj_df, config)

    id_to_index = {str(pid): idx for idx, pid in enumerate(proj_df["DFS_ID"].astype(str).tolist())}
    lineup_groups = lineups_df.groupby("LineupIndex")

    lineup_scores = []
    lineup_indices = []
    for lineup_index, group in lineup_groups:
        indices = [id_to_index.get(str(pid)) for pid in group["DFS_ID"].astype(str).tolist()]
        indices = [idx for idx in indices if idx is not None]
        if not indices:
            continue
        lineup_indices.append(int(lineup_index))
        lineup_scores.append(player_sims[:, indices].sum(axis=1))

    if not lineup_scores:
        return pd.DataFrame(), np.empty((0, config.n_sims))

    scores = np.vstack(lineup_scores)
    win_counts = np.zeros(scores.shape[0], dtype=int)
    best = np.argmax(scores, axis=0)
    for idx in best:
        win_counts[idx] += 1

    summary = pd.DataFrame(
        {
            "LineupIndex": lineup_indices,
            "Mean": scores.mean(axis=1),
            "Median": np.median(scores, axis=1),
            "P90": np.percentile(scores, 90, axis=1),
            "P95": np.percentile(scores, 95, axis=1),
            "P99": np.percentile(scores, 99, axis=1),
            "StdDev": scores.std(axis=1, ddof=0),
            "WinRate": win_counts / max(config.n_sims, 1),
        }
    )

    return summary, scores
