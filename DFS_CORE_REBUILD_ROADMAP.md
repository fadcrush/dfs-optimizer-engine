# DFS Core Rebuild Roadmap

## Status

This document is the governing rebuild plan for the DFS core. The objective is to preserve external contracts while replacing the unstable internals in a strict order. No unrelated DFS feature work should proceed until this roadmap is complete or explicitly revised.

## Execution Rules

1. Do not patch behavior directly inside `analysis/core/orchestrator.py`, backend route handlers, or optimizer call sites unless the change is part of the active roadmap phase.
2. Freeze module boundaries first. Rebuild internals behind those boundaries.
3. Do not preserve duplicate production paths for convenience.
4. Each phase must name exact files touched, tests added or expanded, success criteria, and rollback risks before implementation starts.
5. A phase does not begin until the prior phase gate passes.

## Phase Order

### Phase 0: Freeze Production Contracts

Freeze the external contracts of the current production pipeline before any logic rebuild begins.

#### Freeze Scope

- `analysis/core/orchestrator.py`
- `analysis/nba/optimizer.py`
- `backend/tasks/optimizer.py`
- `backend/routers/optimizer.py`
- `backend/routers/tasks.py`
- `backend/routers/injuries.py`
- `backend/main.py`

#### Exact Files Touched

- `backend/tests/test_canonical_pipeline.py`
- `backend/tests/test_optimizer.py`
- `backend/tests/test_injuries.py`
- `backend/tests/test_late_swap.py`
- `backend/tests/test_projections.py`
- `backend/tests/test_tasks.py`
- `backend/tests/test_pipeline.py`
- `tests/test_optimizer.py`
- `tests/test_late_swap_engine.py`
- `tests/test_injury_intelligence.py`
- `tests/test_projection_vectorized.py`

#### Work In Phase

- Add or tighten regression coverage around response shapes and public entry points.
- Snapshot route outputs and task payload shapes where coverage is currently indirect.
- Make no intended behavioral changes.

#### Test Gate Before Phase 1

- Optimizer suites pass.
- Injury suites pass.
- Late-swap suites pass.
- Projection suites pass.
- Async-task suites pass.
- Route-level suites pass.

#### Rollback Risks

- Low risk. The main failure mode is overfitting tests to unstable incidental fields instead of true contract boundaries.

### Phase 1: Rebuild Canonical Game State And Late Swap

This is the first real rebuild. Late swap is invalid if lock state is wrong.

#### Objective

Create one backend-owned canonical game-state source responsible for player start time, started flag, locked flag, and swap eligibility. Both single-lineup and batch late-swap flows must consume the same backend late-swap service.

#### Exact Files Touched

- `backend/services/late_swap_service.py`
- `analysis/core/late_swap.py`
- `backend/routers/optimizer.py`
- `backend/tasks/optimizer.py`
- `backend/routers/games.py`
- `backend/tests/test_late_swap.py`
- `backend/tests/test_games.py`
- `backend/tests/test_optimizer.py`
- `tests/test_late_swap_engine.py`

#### Planned New Files

- `backend/services/game_state_service.py`
- `backend/tests/test_game_state_service.py`

#### Work In Phase

- Centralize started and locked state in a backend service.
- Remove router and request-time authority over lock determination.
- Make batch and single-lineup swap flows call the same backend-owned service path.
- Preserve existing API shapes while changing internals.

#### Test Additions

- Auto-lock started players based on canonical game state.
- Reject swap-out of started players.
- Reject swap-in of started players.
- Preserve exact slot structure in mixed locked and unlocked lineups.
- Preserve salary and roster rules across swap flows.

#### Success Criteria

- One authoritative backend state source determines lock eligibility.
- Started players cannot be swapped out.
- Started players cannot be swapped in.
- Single-lineup and batch late swap use the same logic.
- Router code no longer owns swap business rules.

#### Test Gate Before Phase 2

- Started players auto-lock correctly.
- Started players cannot be swapped out.
- Started players cannot be swapped in.
- Mixed locked and unlocked lineups preserve exact slot structure.
- Salary and roster rules still hold across swap flows.

#### Rollback Risks

- Medium risk of changing lock behavior for slates with incomplete start-time data.
- Medium risk of breaking batch swap if slot preservation logic differs from current frontend assumptions.

### Phase 2: Rebuild Injury Impact

Make `analysis/core/injury_intelligence.py` the only production source of injury truth.

#### Objective

Add one formal injury-to-optimizer bridge that injects injury effects into the final player pool before solve. This bridge must carry beneficiary status, minutes delta, salary-freed context, ownership and chalk context, and any other optimizer-facing injury signals.

#### Exact Files Touched

- `analysis/core/injury_intelligence.py`
- `analysis/core/ownership_enrichment.py`
- `analysis/core/orchestrator.py`
- `analysis/nba/optimizer.py`
- `analysis/nba/replacement_engine.py`
- `backend/routers/injuries.py`
- `backend/tests/test_injuries.py`
- `backend/tests/test_ownership_enrichment.py`
- `backend/tests/test_optimizer.py`
- `tests/test_injury_intelligence.py`
- `tests/test_beneficiary_engine.py`
- `tests/test_injury_boost.py`

#### Planned New Files

- `analysis/core/injury_optimizer_bridge.py`
- `tests/test_injury_optimizer_bridge.py`

#### Work In Phase

- Establish `injury_intelligence.py` as the only production injury truth.
- Introduce one formal bridge from enriched injury state into optimizer-facing pool rows.
- Preserve raw `Proj` while adding optimizer-consumed injury signals through a stable contract.
- Demote `analysis/nba/replacement_engine.py` to fallback-only status.

#### Test Additions

- Remove OUT players through the canonical injury path.
- Confirm beneficiaries enter the pool only through the canonical bridge.
- Confirm ownership, chalk, minutes-delta, and salary-freed signals reach optimizer-facing rows.
- Confirm fallback mode is explicit, observable, and tested.

#### Success Criteria

- `injury_intelligence.py` is the only production injury truth.
- One pre-solve bridge injects beneficiary, chalk, minutes-delta, and salary-freed signals.
- No second production module independently applies conflicting injury boosts.
- Fallback mode is explicit and observable.

#### Test Gate Before Phase 3

- OUT players are removed correctly.
- Beneficiaries enter the pool only through the canonical bridge.
- Ownership, chalk, and salary-freed signals reach optimizer-facing rows.
- Fallback mode is explicit, observable, and tested.

#### Rollback Risks

- Medium risk of changing player-pool composition on sparse injury data days.
- Medium risk of double-applying injury effects if any old boost path remains active.

### Phase 3: Rebuild Projection Internals

Keep the `analysis/core/projection_engine.py` entry surface, but replace the internal projection path with a staged model.

#### Objective

Split projections into explicit stages such as minutes, role and usage, stat rates, scoring transform, context adjustments, and calibration.

#### Exact Files Touched

- `analysis/core/projection_engine.py`
- `analysis/core/orchestrator.py`
- `analysis/nba/pool_filter.py`
- `backend/services/projection_service.py`
- `backend/tests/test_projections.py`
- `backend/tests/test_pipeline.py`
- `tests/test_projection_vectorized.py`
- `tests/test_backtester.py`
- `tests/test_stddev_variance.py`
- `tests/test_minutes_trend.py`
- `tests/test_dvp.py`
- `tests/test_game_total.py`
- `tests/test_b2b.py`

#### Planned New Files

- `analysis/core/projection_stages.py`
- `analysis/core/projection_backtest.py`
- `tests/test_projection_stages.py`

#### Work In Phase

- Replace heuristic layering with a staged, inspectable projection pipeline.
- Keep the public projection engine surface stable.
- Add intermediate stage outputs for inspection and validation.
- Make backtesting part of the cutover gate.

#### Test Additions

- Stage-level tests for minutes, usage, rates, scoring transform, context, and calibration.
- Historical evaluation tests for MAE, RMSE, and bias.
- Segment reporting by salary tier, role tier, position group, and injury-replacement cohort.

#### Success Criteria

- `projection_engine.py` is stage-based instead of heuristic-layered.
- Intermediate outputs are inspectable.
- Historical evaluation is part of the cutover gate.
- The new path beats or stabilizes the old path on the agreed validation window.

#### Test Gate Before Phase 4

- Historical backtests report MAE, RMSE, and bias.
- Metrics are broken out by salary tier, role tier, position group, and injury-replacement cohort.
- The new path is measurably better or more stable than the old path on the agreed slate window.

#### Rollback Risks

- High risk of silent projection drift if stage outputs are not logged and compared during cutover.
- Medium risk of backend route mismatches if `projection_service.py` continues to expose an alternate production path.

### Phase 4: Rebuild Ownership And Leverage

Collapse ownership into one production model and one emergency fallback model.

#### Exact Files Touched

- `analysis/nba/ownership_v2.py`
- `analysis/nba/ownership.py`
- `analysis/nba/ownership_weighted.py`
- `analysis/nba/pool_filter.py`
- `analysis/nba/optimizer.py`
- `analysis/core/orchestrator.py`
- `backend/tests/test_ownership_enrichment.py`
- `tests/test_ownership_model.py`
- `tests/test_ownership_weighted.py`
- `tests/test_optimizer.py`

#### Planned New Files

- `tests/test_ownership_fallback_activation.py`

#### Work In Phase

- Choose one primary ownership path for production.
- Make fallback activation explicit and observable.
- Move chalk from passive tag to optimizer-consumed signal.
- Define leverage against one deliberate ownership contract.

#### Test Additions

- Verify one primary ownership path is active in production.
- Verify fallback activation is explicit and intentional.
- Bound ownership-driven ranking shifts.
- Validate contest-aware adjustments behave predictably.

#### Success Criteria

- One primary ownership estimator is active in production.
- One fallback path exists and activates only intentionally.
- Chalk is consumed by the optimizer, not just tagged in a dataframe.
- Leverage is defined against a deliberate ownership contract.

#### Test Gate Before Phase 5

- One primary ownership path is active in production.
- Fallback activation is explicit and intentional.
- Ownership-driven ranking shifts are bounded.
- Contest-aware adjustments behave predictably.

#### Rollback Risks

- Medium risk of overreacting optimizer ranking to noisy ownership values.
- Low to medium risk of fallback masking production model failures if activation rules are too permissive.

### Phase 5: Shrink Orchestrator And Remove Dead Paths

After late swap, injury, projection, and ownership are stable, reduce `analysis/core/orchestrator.py` to coordination only.

#### Exact Files Touched

- `analysis/core/orchestrator.py`
- `analysis/nba/projection_pipeline.py`
- `analysis/nba/projections.py`
- `analysis/nba/data_aggregator.py`
- `backend/routers/pipeline.py`
- `backend/services/projection_service.py`
- `backend/src/signals/propagation.py`
- `backend/tests/test_pipeline.py`
- `backend/tests/test_propagation_imports.py`
- `tests/test_pipeline_bug_fixes.py`

#### Work In Phase

- Reduce orchestrator responsibilities to stage coordination only.
- Remove bypassed branches, router-side business logic, retired modules, and duplicate production paths.
- Freeze new DFS feature work until cleanup is complete.

#### Test Additions

- Import graph cleanup checks.
- Single active production path checks.
- Router no-business-logic regression coverage where routes now delegate only.

#### Success Criteria

- `orchestrator.py` coordinates stages instead of embedding decision logic.
- The active production path is singular.
- Retired modules are removed from the import graph.
- New DFS feature work stays frozen until orchestrator shrink is complete.

#### Final Gate

- Full regression suite passes.
- API shape validation passes.
- Import graph cleanup passes.
- Only one active production path remains.

#### Rollback Risks

- Medium risk of deleting modules that still have hidden imports.
- Medium risk of route regressions if backend service delegation is incomplete.

## Rebuild First

Canonical game state and late swap must go first. This is a correctness problem, not only a quality problem. If backend-owned lock state is wrong, late swap cannot be trusted at all.

## File Map

### Freeze Now

Freeze external contracts only:

- `analysis/core/orchestrator.py`
- `analysis/nba/optimizer.py`
- `backend/tasks/optimizer.py`
- `backend/routers/optimizer.py`
- `backend/routers/tasks.py`
- `backend/routers/injuries.py`
- `backend/main.py`

### Keep

- `backend/main.py`
- `backend/services/late_swap_service.py`
- `backend/tests/test_canonical_pipeline.py`
- `tests/test_projection_vectorized.py`

### Freeze Interface, Rebuild Internals

- `analysis/core/orchestrator.py`
- `analysis/core/projection_engine.py`
- `analysis/core/late_swap.py`
- `analysis/nba/optimizer.py`

### Keep And Elevate

- `analysis/core/injury_intelligence.py`
- `analysis/core/ownership_enrichment.py`

### Refactor Or Replace As Active Production Paths

- `analysis/nba/pool_filter.py`
- `analysis/nba/ownership_v2.py`
- `analysis/nba/ownership.py`

### Fallback-Only

- `analysis/nba/replacement_engine.py`
- `analysis/nba/ownership_weighted.py`

### Retire

- `analysis/nba/projection_pipeline.py`
- `analysis/nba/projections.py`
- `analysis/nba/data_aggregator.py` if it only supports deprecated projection paths
- `backend/src/` only after confirming nothing still imports it

## Subsystem Success Criteria

### Late Swap

- One authoritative backend state source determines lock eligibility.
- Started players cannot be swapped out.
- Started players cannot be swapped in.
- Single-lineup and batch late swap use the same logic.
- Router code no longer owns swap business rules.

### Injury

- `injury_intelligence.py` is the only production injury truth.
- One pre-solve bridge injects beneficiary, chalk, minutes-delta, and salary-freed signals.
- No second production module independently applies conflicting injury boosts.
- Fallback mode is explicit and observable.

### Projection

- `projection_engine.py` becomes stage-based instead of heuristic-layered.
- Intermediate outputs are inspectable.
- Historical evaluation is part of the cutover gate.
- The new path beats or stabilizes the old path on the agreed validation window.

### Ownership And Chalk

- One primary ownership estimator is active in production.
- One fallback path exists and activates only intentionally.
- Chalk is consumed by the optimizer, not just tagged in a dataframe.
- Leverage is defined against a deliberate ownership contract.

### Orchestrator

- `orchestrator.py` coordinates stages instead of embedding decision logic.
- The active production path is singular.
- Retired modules are removed from the import graph.
- New DFS feature work stays frozen until orchestrator shrink is complete.

## Definition Of Done

The rebuild is complete only when:

1. The regression suite passes.
2. Public API and task contracts match the frozen Phase 0 baseline unless a deliberate contract revision is approved.
3. The import graph contains one active production path for late swap, injury, projection, ownership, and optimization.
4. Duplicate business logic in routers, services, and side paths has been removed or explicitly demoted to fallback-only status.