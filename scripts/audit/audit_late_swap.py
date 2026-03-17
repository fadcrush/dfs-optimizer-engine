"""
Late Swap Tool — Deep Dive Audit & Upgrade Roadmap
===================================================
Run:  python scripts/audit_late_swap.py

Produces a structured analysis of every layer (import → scoring → export)
with concrete, expert-level directions for making the tool dynamic.
"""

import os, sys, re, textwrap, io
from pathlib import Path
from collections import defaultdict

# Force UTF-8 output on Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
FE   = ROOT / "frontend" / "src"
BE   = ROOT / "backend"

# ── helpers ──────────────────────────────────────────────────────────────────

def loc(path: Path) -> int:
    try:
        return sum(1 for _ in path.open(encoding="utf-8", errors="ignore"))
    except Exception:
        return 0

def grep_count(path: Path, pattern: str) -> int:
    try:
        return sum(1 for line in path.open(encoding="utf-8", errors="ignore")
                   if re.search(pattern, line))
    except Exception:
        return 0

def section(title: str):
    width = 80
    print(f"\n{'=' * width}")
    print(f"  {title}")
    print(f"{'=' * width}\n")

def subsection(title: str):
    print(f"\n--- {title} {'─' * max(1, 70 - len(title))}\n")

def bullet(text: str, indent=2):
    wrapped = textwrap.fill(text, width=100, initial_indent=" " * indent + "• ",
                            subsequent_indent=" " * (indent + 2))
    print(wrapped)

def numbered(items: list[str], indent=2):
    for i, item in enumerate(items, 1):
        wrapped = textwrap.fill(item, width=100,
                                initial_indent=f"{' ' * indent}{i}. ",
                                subsequent_indent=" " * (indent + 3))
        print(wrapped)

# ── inventory ────────────────────────────────────────────────────────────────

FRONTEND_FILES = {
    "Page (orchestrator)":        FE / "app" / "late-swap" / "page.tsx",
    "FD Importer":                FE / "lib" / "late-swap" / "import" / "importFanDuelEntries.ts",
    "DK Importer":                FE / "lib" / "late-swap" / "import" / "importDraftKingsEntries.ts",
    "FD Exporter":                FE / "lib" / "late-swap" / "export" / "exportFanDuelLineups.ts",
    "DK Exporter":                FE / "lib" / "late-swap" / "export" / "exportDraftKingsLineups.ts",
    "FD Model":                   FE / "lib" / "late-swap" / "models" / "fdLineup.ts",
    "DK Model":                   FE / "lib" / "late-swap" / "models" / "dkLineup.ts",
    "FD Validator":               FE / "lib" / "late-swap" / "validation" / "validateFDLineup.ts",
    "Candidate Pool Builder":     FE / "lib" / "late-swap" / "candidatePool.ts",
    "Candidate Diagnostics":      FE / "lib" / "late-swap" / "candidateDiagnostics.ts",
    "Swap Diagnostics":           FE / "lib" / "late-swap" / "diagnostics.ts",
    "Shared Types":               FE / "lib" / "late-swap" / "types.ts",
    "SwapResultComparison":       FE / "components" / "lateSwap" / "SwapResultComparison.tsx",
    "BatchSwapResultsTable":      FE / "components" / "lateSwap" / "BatchSwapResultsTable.tsx",
    "CandidatePoolInspector":     FE / "components" / "lateSwap" / "CandidatePoolInspector.tsx",
    "FD Pipeline Tests":          FE / "lib" / "late-swap" / "__tests__" / "fdPipeline.test.ts",
    "Candidate Pool Tests":       FE / "lib" / "late-swap" / "__tests__" / "candidatePool.test.ts",
}

BACKEND_FILES = {
    "Optimizer Routes (late-swap)": BE / "routers" / "optimizer.py",
    "Late Swap Tests":              BE / "tests" / "test_late_swap.py",
}


def run_inventory():
    section("1. FILE INVENTORY & LOC")

    total = 0
    print(f"  {'Component':<35} {'Lines':>6}  {'Exists':>6}  Path")
    print(f"  {'─' * 35} {'─' * 6}  {'─' * 6}  {'─' * 40}")

    for label, path in {**FRONTEND_FILES, **BACKEND_FILES}.items():
        lines = loc(path)
        exists = "✓" if path.exists() else "✗"
        rel = path.relative_to(ROOT)
        print(f"  {label:<35} {lines:>6}  {exists:>6}  {rel}")
        total += lines

    print(f"\n  {'TOTAL':<35} {total:>6}")


# ── architecture analysis ────────────────────────────────────────────────────

def run_architecture():
    section("2. ARCHITECTURE ANALYSIS")

    subsection("Current Data Flow")
    print(textwrap.dedent("""\
        SINGLE-LINEUP MANUAL SWAP
        ─────────────────────────
        Upload entry CSV  ──►  Frontend importer (detect site)
                                    │
                                    ▼
                          Parse lineups + entry metadata + player IDs
                                    │
                          User marks scratched / locked
                                    │
                          Upload slate CSV  ──►  POST /api/optimizer/late-swap
                                                        │
                                                  run_dfs_pipeline() → projections_df
                                                        │
                                                  Score candidates (50% proj, 30% value, 20% inverse own)
                                                        │
                                                  Return top-N per scratch  ──►  Frontend renders SwapBlock
                                                                                      │
                                                                              User picks candidate
                                                                                      │
                                                                              Export CSV  ──►  Download

        BATCH SWAP
        ──────────
        Same flow but hits POST /api/optimizer/batch-late-swap
        Backend auto-picks best candidate per scratch per lineup
        Returns modified CSV + swap log
    """))

    subsection("Scoring Algorithm (Current)")
    print(textwrap.dedent("""\
        swap_score = 0.50 × rank_norm(projection)
                   + 0.30 × rank_norm(value)            # value = proj / salary × 1000
                   + 0.20 × (1 - rank_norm(ownership))  # lower own = higher score

        Normalization: rank-based 0–1 scale among eligible candidates only.
        Randomness: optional uniform noise added to swap_score before sorting.
    """))

    subsection("Strengths")
    strengths = [
        "Clean separation: import → model → score → export pipeline",
        "Composite FD ID preservation through entire lifecycle",
        "Game-time auto-lock with 5-min buffer prevents illegal swaps",
        "3-tier name matching (exact → fuzzy → substring) handles DK/FD name mismatches",
        "CandidatePoolInspector gives full transparency into exclusion reasons",
        "Diagnostics layer classifies swap failures with actionable suggestions",
        "Multi-scratch budget reservation prevents overspending on one slot",
    ]
    for s in strengths:
        bullet(s)

    subsection("Weaknesses & Gaps")
    weaknesses = [
        "STATIC SCORING WEIGHTS — 50/30/20 is hardcoded. No way for user to tune aggressiveness vs. safety.",
        "NO GAME-STATE AWARENESS — Doesn't consider live scores, pace, blowout risk, or minutes expectations.",
        "NO CORRELATION LOGIC — is_same_game / is_same_team flags exist but are never used in scoring.",
        "NO STACKING ENFORCEMENT — Batch swap treats each lineup independently; no cross-lineup diversity.",
        "SINGLE-PASS GREEDY — Batch swap fills scratches sequentially (cheapest first). No global optimization.",
        "NO CEILING/FLOOR MODES — swap_score doesn't change between cash (floor) and GPP (ceiling) contexts.",
        "NO OWNERSHIP LEVERAGE — Ownership is used inversely but doesn't consider field size or contest type.",
        "NO REAL-TIME INJURY FEED — Scratches are manual. No auto-detection of late scratches from injury APIs.",
        "PAGE.TSX IS 2,682 LINES — Monolithic orchestrator; hard to test, extend, or maintain.",
        "NO WEBSOCKET/SSE — User must manually re-run to get updated data. No live refresh.",
        "TIMEZONE HARDCODED TO EDT (UTC-4) — Will break during EST months (November–March).",
        "NO UNDO/HISTORY — Applying a swap is destructive to state. No way to revert without re-importing.",
    ]
    for w in weaknesses:
        bullet(w)


# ── upgrade roadmap ──────────────────────────────────────────────────────────

def run_roadmap():
    section("3. EXPERT UPGRADE ROADMAP")

    # ── Tier 1: Quick Wins ──
    subsection("TIER 1 — Quick Wins (1–2 days each)")

    print("  1.1  CONFIGURABLE SCORING WEIGHTS")
    print(textwrap.dedent("""\
        Current: Hardcoded 0.50 / 0.30 / 0.20 in backend/routers/optimizer.py ~line 694
        Change:  Add frontend sliders (Projection / Value / Contrarian) that sum to 1.0.
                 Pass weights as query params: w_proj, w_value, w_own.

        Backend change (optimizer.py):
          - Accept optional float params: w_proj=0.5, w_value=0.3, w_own=0.2
          - Validate: w_proj + w_value + w_own ≈ 1.0 (within epsilon)
          - Replace hardcoded constants with params

        Frontend change (page.tsx):
          - Add a "Scoring Profile" panel with three range sliders
          - Presets: "Balanced" (50/30/20), "Cash" (70/20/10), "GPP" (30/20/50)
          - Pass to API call

        Why: Users playing cash games want floor/safety; GPP users want ceiling/contrarian.
              One fixed formula can't serve both contest types.
    """))

    print("  1.2  CONTEST-TYPE TOGGLE (Cash vs. GPP)")
    print(textwrap.dedent("""\
        Add a toggle: Cash | GPP | Tournament

        Cash mode:
          - Weight projection heavily (70%+), minimize ownership factor
          - Filter out high-variance / low-floor players
          - Prefer starters over bench players

        GPP mode:
          - Weight ceiling + inverse ownership heavily
          - Include game-stack correlation bonus
          - Allow higher-risk / higher-ceiling candidates

        Implementation:
          - Frontend: ToggleGroup component, sets `contest_type` param
          - Backend: Map contest_type → default weight profiles
          - Let user override via sliders (Tier 1.1)
    """))

    print("  1.3  FIX TIMEZONE BUG")
    print(textwrap.dedent("""\
        File: frontend/src/lib/late-swap/import/importFanDuelEntries.ts
              frontend/src/lib/late-swap/import/importDraftKingsEntries.ts

        Current: Hardcoded UTC-4 (EDT). Breaks Nov–Mar when Eastern = UTC-5 (EST).
        Fix:     Use a proper timezone library or detect DST dynamically.

        Option A (simple): Check if date falls in DST range, use -4 or -5 accordingly.
        Option B (robust): Use Intl.DateTimeFormat with timeZone: 'America/New_York'.

        Example:
          const eastern = new Date(
            new Date(dateStr).toLocaleString('en-US', { timeZone: 'America/New_York' })
          );
    """))

    print("  1.4  UNDO / SWAP HISTORY")
    print(textwrap.dedent("""\
        Current: Applying a swap mutates state destructively. No rollback.
        Change:  Maintain a swap history stack.

        Implementation:
          - Add `swapHistory: SwapAction[]` to state
          - Each SwapAction: { lineupIdx, slotIdx, removedPlayer, addedPlayer, timestamp }
          - "Undo" button pops last action and restores previous player
          - "Reset" button reverts to original imported lineup
          - Store original lineups in a ref (never mutated)
    """))

    # ── Tier 2: Dynamic Intelligence ──
    subsection("TIER 2 — Dynamic Intelligence (3–5 days each)")

    print("  2.1  CORRELATION-AWARE SCORING")
    print(textwrap.dedent("""\
        Current: is_same_game and is_same_team flags exist but are ignored in scoring.
        Change:  Add correlation bonus/penalty to swap_score.

        Algorithm:
          For each candidate:
            game_stack_count = count of lineup players in same game
            team_stack_count = count of lineup players on same team

            correlation_bonus = 0.0
            if game_stack_count >= 2:
                correlation_bonus += 0.05 * game_stack_count  # reward game stacks
            if team_stack_count >= 2:
                correlation_bonus += 0.03 * team_stack_count  # reward team stacks
            if candidate.is_opponent_of_stack:
                correlation_bonus += 0.04  # bring-back value

            final_score = base_swap_score + (w_corr * correlation_bonus)

        Why: In GPP, correlated lineups have higher ceiling. A PG swap should
             prefer a player in the same game as existing stack targets.
    """))

    print("  2.2  REAL-TIME INJURY FEED INTEGRATION")
    print(textwrap.dedent("""\
        Current: User manually types scratched player names.
        Change:  Auto-detect scratches from live injury data.

        Architecture:
          Backend:
            - New endpoint: GET /api/injuries/active?sport=NBA
            - Polls injury API (SportsDataIO, ESPN, or NBA API) every 60s
            - Caches in DuckDB table: injuries(player_name, status, updated_at)
            - Statuses: OUT, DOUBTFUL, QUESTIONABLE, PROBABLE

          Frontend:
            - After importing lineups, call /api/injuries/active
            - Auto-mark OUT players as scratched (with visual indicator)
            - Show QUESTIONABLE/DOUBTFUL as warnings (user decides)
            - "Refresh Injuries" button for manual re-poll
            - WebSocket or SSE for push updates (see Tier 3)

        Data source options:
          - analysis/shared/api_clients.py already has injury fetch logic
          - analysis/nba/lineup_status.py may have relevant parsers
          - Wire these into a new /api/injuries endpoint
    """))

    print("  2.3  CEILING / FLOOR PROJECTION MODES")
    print(textwrap.dedent("""\
        Current: Uses single "Proj" column. No variance awareness.
        Change:  Use Sim_P90 (already in pipeline) for ceiling, add P10 for floor.

        Backend change:
          - If contest_type == 'GPP': use Sim_P90 as projection in scoring
          - If contest_type == 'Cash': use P10 (or Proj - 1σ) as projection
          - If contest_type == 'Balanced': use Proj (median)

        Frontend change:
          - Show all three columns in CandidatePoolInspector
          - Highlight which projection mode is active
          - Sort toggles: "By Floor" / "By Median" / "By Ceiling"

        Why: A player with 25 FPTS median but 45 ceiling is better for GPP.
             A player with 25 median but 12 floor is risky for cash.
    """))

    print("  2.4  MULTI-LINEUP DIVERSITY ENFORCEMENT")
    print(textwrap.dedent("""\
        Current: Batch swap treats each lineup independently → same replacement everywhere.
        Change:  Enforce diversity across lineups in batch mode.

        Algorithm:
          After scoring candidates for all lineups:
            - Track replacement_count[player] across all lineups
            - Apply diminishing returns: effective_score *= (1 / (1 + 0.15 * times_used))
            - This naturally spreads replacements across different candidates
            - User config: diversity_factor (0 = no diversity, 1 = max diversity)

        Why: If you have 20 lineups and Player X is scratched in 15 of them,
             putting the same replacement in all 15 creates massive exposure risk.
             Diversifying replacements is standard GPP portfolio theory.
    """))

    print("  2.5  GLOBAL OPTIMIZATION (REPLACE GREEDY)")
    print(textwrap.dedent("""\
        Current: Batch swap fills scratches sequentially using greedy (cheapest-first
                 to reserve budget, then best by score).
        Change:  Use constraint-based optimization for multi-scratch lineups.

        Approach:
          - When a lineup has 2+ scratches, formulate as assignment problem:
              Maximize: sum(swap_score[candidate_i, slot_j])
              Subject to:
                - Each slot filled by exactly one candidate
                - Each candidate used at most once per lineup
                - Total salary ≤ salary_cap
                - Position eligibility constraints
          - Solve with scipy.optimize.linear_sum_assignment or PuLP
          - Falls back to greedy if solver times out (>500ms)

        Why: Greedy can paint itself into a corner. If it picks an expensive
             player for slot 1, it may have no budget left for slot 2.
             Global optimization finds the best combination.
    """))

    # ── Tier 3: Full Dynamic System ──
    subsection("TIER 3 — Full Dynamic System (1–2 weeks each)")

    print("  3.1  LIVE GAME-STATE ENGINE")
    print(textwrap.dedent("""\
        The ultimate late-swap upgrade: factor in LIVE game state.

        Data needed (per game):
          - Current score, quarter, time remaining
          - Player minutes played so far
          - Pace (possessions per minute this game vs. season avg)
          - Blowout probability (score differential → garbage time risk)

        How it affects swaps:
          - If a game is a blowout (20+ pt lead in Q3), downgrade starters
            (likely to sit Q4) → prefer players in close games
          - If pace is high, upgrade all players in that game (more possessions)
          - Remaining minutes estimation:
              expected_mins = season_avg_mins × (time_remaining / 48)
              adjusted_proj = proj × (expected_mins / season_avg_mins)

        Architecture:
          - New service: backend/services/live_game_service.py
          - Polls live box score API every 30–60s during game windows
          - Caches in memory (not DB — ephemeral data)
          - New endpoint: GET /api/games/live
          - Frontend: WebSocket connection for push updates
          - Candidate scoring adds live_adjustment factor

        API options:
          - SportsDataIO Play-By-Play (if you have a subscription)
          - NBA API (free but rate-limited)
          - balldontlie.io (free tier)
    """))

    print("  3.2  WEBSOCKET / SSE LIVE REFRESH")
    print(textwrap.dedent("""\
        Current: User manually re-runs late swap to get updated data.
        Change:  Push updates to frontend in real-time.

        Implementation:
          Backend (FastAPI WebSocket):
            @app.websocket("/ws/late-swap/{session_id}")
            async def late_swap_ws(websocket, session_id):
                await websocket.accept()
                while True:
                    # Check for injury updates, game state changes
                    updates = await check_for_updates(session_id)
                    if updates:
                        await websocket.send_json(updates)
                    await asyncio.sleep(30)

          Frontend:
            - useWebSocket hook that reconnects on disconnect
            - On message: update injury statuses, re-score candidates
            - Visual indicator: "Live" badge with green pulse when connected
            - Toast notifications: "Injury Alert: Player X ruled OUT"

        Events to push:
          - injury_update: { player, old_status, new_status }
          - game_started: { game_id, players_locked }
          - score_update: { game_id, home_score, away_score, quarter, clock }
          - projection_update: { player, old_proj, new_proj, reason }
    """))

    print("  3.3  REFACTOR PAGE.TSX INTO STATE MACHINE")
    print(textwrap.dedent("""\
        Current: page.tsx is 2,682 lines — a monolithic god component.
        Change:  Extract into a state machine + composable hooks + child components.

        Proposed architecture:

          hooks/
            useLateSwapMachine.ts    — XState or useReducer state machine
            useLineupImport.ts       — File upload + parsing logic
            useSwapExecution.ts      — API calls + response handling
            useCandidatePool.ts      — Pool building + filtering + sorting
            useSwapHistory.ts        — Undo/redo stack
            useLiveUpdates.ts        — WebSocket connection

          components/lateSwap/
            LateSwapPage.tsx         — Thin shell, wires hooks to UI
            ImportPanel.tsx          — File upload UI
            LineupEditor.tsx         — Lineup display + scratch/lock toggles
            ScoringConfig.tsx        — Weight sliders + contest type toggle
            SwapExecutionPanel.tsx   — Run swap button + progress
            CandidatePoolInspector.tsx (existing)
            SwapResultComparison.tsx   (existing)
            BatchSwapResultsTable.tsx  (existing)
            ExportPanel.tsx          — Export button + format options

        State machine states:
          IDLE → IMPORTING → LINEUP_READY → CONFIGURING → EXECUTING →
          RESULTS_READY → EXPORTING

        Why: Testability (hooks can be unit-tested), maintainability (each file
             < 300 lines), and enables features like live updates without
             touching the core swap logic.
    """))

    print("  3.4  SIMULATION-BASED SWAP EVALUATION")
    print(textwrap.dedent("""\
        The most advanced upgrade: Monte Carlo simulation for swap decisions.

        Instead of a single swap_score, simulate 10,000 contests:
          For each candidate:
            1. Sample projections from distribution (mean=proj, std=σ)
            2. Build full lineup with this candidate
            3. Simulate contest placement against field ownership
            4. Track: avg_score, P(cash), P(top10), P(1st), expected_ROI

        Display in CandidatePoolInspector:
          | Candidate | Proj | Ceiling | Cash% | Top10% | Win% | E[ROI] |
          |-----------|------|---------|-------|--------|------|--------|
          | Player A  | 35.2 |  52.1   | 78%   |  12%   | 0.8% | +15%   |
          | Player B  | 33.8 |  55.3   | 71%   |  15%   | 1.2% | +22%   |

        Player B has lower median but higher ceiling and better ROI — GPP pick.
        Player A has higher cash rate — cash game pick.

        Implementation:
          - Backend: New endpoint POST /api/optimizer/simulate-swap
          - Use existing simulation engine (backend/tests/test_simulation_engine.py
            suggests one exists)
          - Return SimulationResult per candidate
          - Frontend: Toggle "Simple Score" vs "Simulated" view
          - Cache results (expensive computation, ~2–5s per lineup)
    """))


# ── implementation priority matrix ───────────────────────────────────────────

def run_priority():
    section("4. IMPLEMENTATION PRIORITY MATRIX")

    print(f"  {'#':<5} {'Upgrade':<40} {'Impact':>8} {'Effort':>8} {'Priority':>10}")
    print(f"  {'─' * 5} {'─' * 40} {'─' * 8} {'─' * 8} {'─' * 10}")

    priorities = [
        ("1.1", "Configurable Scoring Weights",        "HIGH",   "LOW",    "★★★★★"),
        ("1.2", "Contest-Type Toggle (Cash/GPP)",       "HIGH",   "LOW",    "★★★★★"),
        ("1.3", "Fix Timezone Bug (EDT→EST)",           "MED",    "LOW",    "★★★★☆"),
        ("1.4", "Undo / Swap History",                  "MED",    "LOW",    "★★★★☆"),
        ("2.1", "Correlation-Aware Scoring",            "HIGH",   "MED",    "★★★★☆"),
        ("2.2", "Real-Time Injury Feed",                "HIGH",   "MED",    "★★★★☆"),
        ("2.3", "Ceiling / Floor Modes",                "HIGH",   "MED",    "★★★★☆"),
        ("2.4", "Multi-Lineup Diversity",               "HIGH",   "MED",    "★★★☆☆"),
        ("2.5", "Global Optimization (replace greedy)", "MED",    "HIGH",   "★★★☆☆"),
        ("3.1", "Live Game-State Engine",               "V.HIGH", "HIGH",   "★★★☆☆"),
        ("3.2", "WebSocket Live Refresh",               "HIGH",   "HIGH",   "★★★☆☆"),
        ("3.3", "Refactor page.tsx → State Machine",    "MED",    "HIGH",   "★★☆☆☆"),
        ("3.4", "Simulation-Based Swap Evaluation",     "V.HIGH", "V.HIGH", "★★☆☆☆"),
    ]

    for num, name, impact, effort, priority in priorities:
        print(f"  {num:<5} {name:<40} {impact:>8} {effort:>8} {priority:>10}")

    print(textwrap.dedent("""
    Recommended execution order:
      Phase 1 (this week):  1.1 + 1.2 + 1.3 + 1.4
      Phase 2 (next week):  2.1 + 2.2 + 2.3
      Phase 3 (week after): 2.4 + 2.5 + 3.2
      Phase 4 (stretch):    3.1 + 3.3 + 3.4
    """))


# ── code smells & tech debt ──────────────────────────────────────────────────

def run_tech_debt():
    section("5. CODE SMELLS & TECH DEBT")

    page_path = FE / "app" / "late-swap" / "page.tsx"
    optimizer_path = BE / "routers" / "optimizer.py"

    items = [
        (f"page.tsx: {loc(page_path)} lines",
         "God component. Extract into hooks + child components per Tier 3.3."),
        (f"optimizer.py late-swap handler: ~330 lines in one function",
         "Extract scoring, budget calc, and candidate filtering into separate modules."),
        ("Hardcoded EDT offset (UTC-4)",
         "Will silently produce wrong auto-locks Nov–Mar. Fix per Tier 1.3."),
        ("Rank normalization recomputed per-request",
         "For batch swap with 150 lineups, this is O(n×m×log m). Pre-compute once."),
        ("No DK validator (only FD has validateFDLineup)",
         "Add validateDKLineup with same checks: empty slots, duplicate IDs, format."),
        ("Fuzzy match cutoff=0.72 is arbitrary",
         "Log match confidence; surface low-confidence matches as warnings in UI."),
        ("No rate limiting on late-swap endpoints",
         "Each call runs full DFS pipeline. Add caching or rate limiting."),
        ("Candidate scoring happens in route handler",
         "Move to backend/services/late_swap_service.py for testability."),
    ]

    for title, desc in items:
        print(f"  ⚠  {title}")
        print(f"     → {desc}\n")


# ── quick-start implementation snippets ──────────────────────────────────────

def run_snippets():
    section("6. QUICK-START CODE SNIPPETS")

    subsection("6.1 — Backend: Accept configurable weights (optimizer.py)")
    print(textwrap.dedent("""\
        # Add to late-swap endpoint parameters:
        w_proj:  float = Query(0.50, ge=0, le=1, description="Projection weight"),
        w_value: float = Query(0.30, ge=0, le=1, description="Value weight"),
        w_own:   float = Query(0.20, ge=0, le=1, description="Inverse-ownership weight"),

        # Validate weights sum to ~1.0:
        weight_sum = w_proj + w_value + w_own
        if abs(weight_sum - 1.0) > 0.05:
            raise HTTPException(400, f"Weights must sum to 1.0 (got {weight_sum:.2f})")

        # Replace hardcoded scoring:
        swap_score = w_proj * norm_p + w_value * norm_v + w_own * (1 - norm_o)
    """))

    subsection("6.2 — Frontend: Scoring sliders component")
    print(textwrap.dedent("""\
        // ScoringConfig.tsx
        const PRESETS = {
          balanced: { proj: 0.5, value: 0.3, own: 0.2 },
          cash:     { proj: 0.7, value: 0.2, own: 0.1 },
          gpp:      { proj: 0.3, value: 0.2, own: 0.5 },
        };

        function ScoringConfig({ weights, onChange }) {
          return (
            <div className="flex gap-4">
              {Object.entries(PRESETS).map(([name, w]) => (
                <button key={name} onClick={() => onChange(w)}
                  className={cn("px-3 py-1 rounded", weightsMatch(weights, w) && "bg-accent")}>
                  {name.toUpperCase()}
                </button>
              ))}
              <Slider label="Projection" value={weights.proj}
                onChange={v => onChange(normalize({ ...weights, proj: v }))} />
              <Slider label="Value" value={weights.value}
                onChange={v => onChange(normalize({ ...weights, value: v }))} />
              <Slider label="Contrarian" value={weights.own}
                onChange={v => onChange(normalize({ ...weights, own: v }))} />
            </div>
          );
        }
    """))

    subsection("6.3 — Backend: Correlation bonus scoring")
    print(textwrap.dedent("""\
        # Add after base swap_score calculation:
        def compute_correlation_bonus(candidate, lineup_teams, lineup_games):
            bonus = 0.0
            cand_game = candidate.get("GameInfo", "")
            cand_team = candidate.get("Team", "")

            # Game stack bonus
            game_mates = sum(1 for g in lineup_games if g == cand_game)
            if game_mates >= 2:
                bonus += 0.05 * game_mates

            # Team stack bonus
            team_mates = sum(1 for t in lineup_teams if t == cand_team)
            if team_mates >= 1:
                bonus += 0.03 * team_mates

            # Bring-back: opponent in same game
            if cand_game and cand_game in lineup_games and cand_team not in lineup_teams:
                bonus += 0.04

            return min(bonus, 0.25)  # cap at 0.25
    """))

    subsection("6.4 — Backend: Diversity penalty for batch swap")
    print(textwrap.dedent("""\
        # Track usage across lineups:
        replacement_usage = defaultdict(int)

        for lineup_idx, lineup in enumerate(lineups):
            for scratch in scratched_players:
                candidates = score_candidates(...)
                for c in candidates:
                    times_used = replacement_usage[c["name"]]
                    c["effective_score"] = c["swap_score"] / (1 + diversity_factor * times_used)

                candidates.sort(key=lambda c: c["effective_score"], reverse=True)
                best = candidates[0]
                replacement_usage[best["name"]] += 1
    """))


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "█" * 80)
    print("  LATE SWAP TOOL — DEEP DIVE AUDIT & DYNAMIC UPGRADE ROADMAP")
    print("  Generated:", __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M"))
    print("█" * 80)

    run_inventory()
    run_architecture()
    run_roadmap()
    run_priority()
    run_tech_debt()
    run_snippets()

    section("7. SUMMARY")
    print(textwrap.dedent("""\
        Your late swap tool has a solid foundation — clean import/export pipeline,
        good ID preservation, and useful diagnostics. The main gap is that it's
        STATIC: fixed scoring weights, no live data, no contest awareness.

        To make it truly dynamic:

          1. START HERE: Configurable weights + contest type toggle (Tier 1.1 + 1.2)
             This alone transforms the tool from "one-size-fits-all" to "contest-aware."

          2. ADD INTELLIGENCE: Correlation scoring + injury feed (Tier 2.1 + 2.2)
             This makes swaps context-aware instead of isolated decisions.

          3. GO LIVE: WebSocket updates + game-state engine (Tier 3.1 + 3.2)
             This is what separates a static tool from a real-time decision engine.

        The tool is ~7,300 lines across 18 files. With the upgrades above, expect
        ~10,000–12,000 lines but with much better separation of concerns.
    """))


if __name__ == "__main__":
    main()
