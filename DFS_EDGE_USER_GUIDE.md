# DFS Edge Pro — User Guide

Complete walkthrough for all eight tabs in the application. The typical daily workflow follows the order of this guide: **Slates → Projections → Optimizer → EV Modeling → Simulation → Late Swap → Analytics → Metrics**.

---

## Table of Contents

1. [Operations — Starting & Managing the App](#1-operations--starting--managing-the-app)
2. [API Documentation & Backend Reference](#2-api-documentation--backend-reference)
3. [Slates](#3-slates)
4. [Projections](#4-projections)
5. [Optimizer](#5-optimizer)
6. [EV Modeling](#6-ev-modeling)
7. [Simulation](#7-simulation)
8. [Late Swap](#8-late-swap)
9. [Analytics](#9-analytics)
10. [Metrics](#10-metrics)

---

## 1. Operations — Starting & Managing the App

### Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed and running
- A `.env` file in the project root (`F:\Dev\N_B_A_and_N_F_L\.env`) with your API keys configured (see [ENV_CONFIGURATION_GUIDE.md](ENV_CONFIGURATION_GUIDE.md))

### Service URLs

| Service                | URL                         | Purpose                  |
| ---------------------- | --------------------------- | ------------------------ |
| **Frontend**           | http://localhost:3000       | Main application UI      |
| **Backend API**        | http://localhost:8000       | FastAPI REST server      |
| **API Docs (Swagger)** | http://localhost:8000/docs  | Interactive API explorer |
| **API Docs (ReDoc)**   | http://localhost:8000/redoc | Readable API reference   |

---

### Starting the application

**First run (builds images from source):**

```powershell
cd F:\Dev\N_B_A_and_N_F_L
docker compose up --build
```

**Normal start (images already built):**

```powershell
docker compose up -d
```

The `-d` flag runs containers in the background. The frontend waits for the backend health check to pass before starting (~20 seconds).

**Start only the backend:**

```powershell
docker compose up -d backend
```

**Start only the frontend:**

```powershell
docker compose up -d frontend
```

---

### Stopping the application

**Stop and keep containers:**

```powershell
docker compose stop
```

**Stop and remove containers (data volumes are preserved):**

```powershell
docker compose down
```

---

### Rebuilding after code changes

If you edit backend Python files or frontend TypeScript/TSX files, rebuild the affected image:

```powershell
# Rebuild and restart backend only
docker compose build backend
docker compose up -d backend

# Rebuild and restart frontend only
docker compose build frontend
docker compose up -d frontend

# Rebuild everything
docker compose build
docker compose up -d
```

> **Note:** DuckDB data files (`data/*.duckdb`), uploaded slates (`backend/uploads/`), and output CSVs (`outputs/`) are mounted as volumes and survive all rebuilds.

---

### Health checks

**Check both containers are running:**

```powershell
docker compose ps
```

**Backend health endpoint:**

```powershell
Invoke-RestMethod http://localhost:8000/health
```

Expected: `{ status: "healthy", day: "...", next: "...", scheduler: {...} }`

**Analytics DB health:**

```powershell
Invoke-RestMethod http://localhost:8000/analytics/health
```

Expected: `{ status: "ok", tables: [...] }`

**Smoke-test all pages:**

```powershell
foreach ($url in @(
  "http://localhost:3000",
  "http://localhost:3000/slates",
  "http://localhost:3000/projections",
  "http://localhost:3000/optimizer",
  "http://localhost:3000/ev-modeling",
  "http://localhost:3000/simulation",
  "http://localhost:3000/late-swap",
  "http://localhost:3000/analytics",
  "http://localhost:3000/metrics"
)) {
  $r = Invoke-WebRequest $url -UseBasicParsing
  Write-Host "$($r.StatusCode) $url"
}
```

---

### Viewing logs

```powershell
# Live backend logs
docker compose logs -f backend

# Last 50 lines from frontend
docker compose logs frontend --tail 50

# All services
docker compose logs --tail 30
```

---

### Scheduled data jobs

The backend scheduler runs the following jobs automatically:

| Job                  | Schedule             | What it does                                           |
| -------------------- | -------------------- | ------------------------------------------------------ |
| Injury ingest        | ~2 hours before lock | Fetches NBA official injury PDFs → `nba_injury_report` |
| Game log ingest      | Nightly              | Pulls last 7 days of box score results                 |
| Projection reconcile | Post-game            | Matches projections vs. actuals for accuracy tracking  |

To run any job manually:

```powershell
# Fetch today's injury report
docker exec n_b_a_and_n_f_l-backend-1 python3 /app/scripts/fetch_nba_injuries.py

# Ingest game logs (last 7 days)
docker exec n_b_a_and_n_f_l-backend-1 python3 /app/scripts/ingest_game_logs.py --days 7
```

---

### Environment variables

All configuration lives in `.env` at the project root. Key variables:

| Variable                        | Required    | Purpose                                            |
| ------------------------------- | ----------- | -------------------------------------------------- |
| `THEODDS_API_KEY`               | Recommended | Betting odds and lines feed                        |
| `SPORTSDATA_API_KEY`            | Recommended | Player stats and game data                         |
| `BALLDONTLIE_API_KEY`           | Recommended | NBA box score data                                 |
| `WEATHER_API_KEY`               | NFL only    | Weather conditions for outdoor games               |
| `OPENAI_API_KEY`                | Optional    | AI-assisted features                               |
| `DATABASE_URL`                  | Optional    | Postgres connection (omit to use DuckDB-only mode) |
| `NEXT_PUBLIC_SUPABASE_URL`      | Optional    | Supabase auth (omit to skip login)                 |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Optional    | Supabase anon key                                  |

See [ENV_CONFIGURATION_GUIDE.md](ENV_CONFIGURATION_GUIDE.md) for full details.

---

## 2. API Documentation & Backend Reference

### Interactive docs

With the backend running, open **http://localhost:8000/docs** in your browser. This is a live Swagger UI where you can:

- Browse every API endpoint grouped by tag
- See request/response schemas
- Execute calls directly from the browser (no Postman needed)

Alternatively, **http://localhost:8000/redoc** provides a cleaner read-only reference.

---

### API route map

| Tag              | Prefix             | Key Endpoints                                                                                                           |
| ---------------- | ------------------ | ----------------------------------------------------------------------------------------------------------------------- |
| **Slates**       | `/api/slates`      | `GET /` list, `POST /upload` upload CSV, `DELETE /{id}` remove                                                          |
| **Projections**  | `/api/projections` | `POST /run` generate projections from uploaded CSV                                                                      |
| **Optimizer**    | `/api/optimizer`   | `POST /run` build lineups, `POST /late-swap` find swap candidates                                                       |
| **Analytics**    | `/analytics`       | `GET /roi` ROI summary, `GET /accuracy` projection accuracy, `POST /contest` log entry, `POST /reconcile` match actuals |
| **Events (SSE)** | `/api/events`      | `GET /injuries` server-sent injury stream (real-time, 60s interval)                                                     |
| **Contests**     | `/api/contests`    | `POST /import` bulk CSV import, `GET /roi` ROI summary                                                                  |
| **Games**        | `/api/games`       | `GET /` game schedule and matchup data                                                                                  |
| **Auth**         | `/auth`            | `POST /login`, `POST /register`, `POST /refresh`                                                                        |

---

### Key endpoints used by the UI

#### `POST /api/slates/upload`

Upload a DK/FD salary CSV. Form fields: `file` (CSV), `platform` (`draftkings`|`fanduel`), `sport` (`nba`|`nfl`).

#### `POST /api/projections/run`

Generate projections. Form fields: `file` (CSV), query params: `site` (DK|FD), `sport` (NBA|NFL).
Returns `{ projections: [...], stats: { total_players, avg_projection, site } }`.

#### `POST /api/optimizer/run`

Build lineups. Form fields: `file` (CSV), query params: `site`, `sport`, `n_lineups`, optional `out_teams`, `out_players`.
Returns `{ lineups: [...], summary: { total_lineups, avg_projected, ... } }`.

#### `GET /analytics/accuracy?period_days=30&site=DK`

Returns projection accuracy metrics:

```json
{
  "total_projections": 0,
  "mae": null,
  "rmse": null,
  "bias": null,
  "period_days": 30,
  "by_day": []
}
```

#### `POST /analytics/contest`

Log a contest entry. JSON body:

```json
{
  "contest_date": "2026-02-26",
  "contest_type": "gpp",
  "site": "DK",
  "entry_fee": 25.0,
  "payout": 0,
  "final_rank": null,
  "total_entries": null,
  "notes": "Main Slate GPP"
}
```

#### `POST /analytics/reconcile`

Match projections vs. actuals after a slate completes. JSON body:

```json
{ "slate_date": "2026-02-25", "site": "DK" }
```

Run this the morning after a slate to populate accuracy metrics.

#### `GET /api/events/injuries`

Server-Sent Events stream. Connect with `EventSource` or any SSE client. Emits every 60 seconds:

```json
{
  "type": "injury_update",
  "timestamp": "2026-02-26T10:00:00Z",
  "players": [
    {
      "player_name": "Joel Embiid",
      "status": "OUT",
      "detail": "Knee",
      "team": "PHI"
    }
  ],
  "count": 1
}
```

#### `POST /api/optimizer/late-swap`

Find swap candidates. Form fields: `file` (CSV), JSON body `{ site, sport, lineup: [...], scratched: [...] }`.

---

### Data files

All persistent data is stored as DuckDB files in `data/` on the host:

| File                     | Contents                                                                |
| ------------------------ | ----------------------------------------------------------------------- |
| `data/dfs_master.duckdb` | Projections, game logs, optimizer runs                                  |
| `data/dfs_edge.duckdb`   | Analytics — contest results, accuracy log                               |
| `data/nba_news.duckdb`   | Injury reports (`nba_injury_report` table, `vw_nba_injury_status` view) |

These files are volume-mounted and survive container rebuilds. Back them up before any destructive operations:

```powershell
Copy-Item data\dfs_master.duckdb data\backups\dfs_master_$(Get-Date -Format yyyyMMdd).duckdb
```

---

## 3. Slates

**URL:** `/slates`

The Slates tab is the entry point for every contest day. Everything else in the app flows from a slate that has been uploaded here.

### What it does

Manages your library of DraftKings and FanDuel player-pool CSVs. Once a slate is uploaded it is stored on the server and made available to every other tab automatically via the **Slate Selector** component.

### How to upload a slate

1. Click **Upload Slate** in the top-right corner.
2. In the modal that appears:
   - **Platform** — choose `DraftKings` or `FanDuel`.
   - **Sport** — choose `NBA` or `NFL`.
   - **CSV File** — click the dashed box and choose your salary CSV downloaded directly from DK or FD.
3. Click **Upload**. A toast notification confirms success or shows an error.

> **Where do I get the CSV?**
>
> - DraftKings: _Contests → Create Lineup → Export Player List (.csv)_
> - FanDuel: _Lobby → Upcoming → Download Player List_

### Reading the slate table

| Column    | Meaning                                                                                |
| --------- | -------------------------------------------------------------------------------------- |
| Platform  | DraftKings or FanDuel                                                                  |
| Sport     | NBA / NFL                                                                              |
| Date      | Slate game date                                                                        |
| Lock Time | When the contest locks (UTC)                                                           |
| Type      | Slate type (Main, Turbo, etc.)                                                         |
| Players   | Number of eligible players in the pool                                                 |
| Status    | **Active** = usable, **Locked** = past lock time, **Processing** = parsing in progress |

### Deleting a slate

Click the trash icon on any row. A confirmation modal prevents accidental deletion.

### Searching

Use the search bar to filter slates by platform, sport, or date text.

---

## 4. Projections

**URL:** `/projections`

Generates fantasy-point projections for every player in the selected slate using the canonical projection engine (combination of recent game logs, pace, matchup data, and rest advantage).

### Loading a slate

The page auto-loads the most recent uploaded slate. Use the **Slate Selector** at the top to switch between saved slates. You can also upload a CSV directly via the file picker without going through the Slates tab.

### Controls

| Control        | Options                             | Default     |
| -------------- | ----------------------------------- | ----------- |
| Slate Selector | Auto-populates from uploaded slates | Most recent |
| Site           | DK / FD                             | DK          |
| Sport          | NBA / NFL                           | NBA         |

Click **Run Projections** to generate projections. The engine runs server-side and typically responds in 2–5 seconds.

### Reading the results

After projections run, a stats bar shows **Total Players**, **Avg Projection**, and **Site**. The main table contains:

| Column  | Meaning                                               |
| ------- | ----------------------------------------------------- |
| Name    | Player name                                           |
| Pos     | Eligible positions                                    |
| Team    | Team abbreviation                                     |
| Opp     | Opposing team                                         |
| Salary  | DK/FD salary                                          |
| Proj    | Projected fantasy points (primary metric)             |
| Floor   | Low-end outcome (25th percentile)                     |
| Ceiling | High-end outcome (85th percentile)                    |
| Value   | `Proj / (Salary / 1000)` — points-per-thousand salary |
| Own%    | Estimated ownership percentage                        |
| Tier    | Color-coded value tier                                |

**Value tiers:**

| Tier   | Value Score | Color        |
| ------ | ----------- | ------------ |
| Elite  | ≥ 5.0       | Green        |
| Strong | ≥ 4.0       | Yellow-green |
| Solid  | ≥ 3.0       | Yellow       |
| Weak   | ≥ 2.0       | Orange       |
| Punt   | < 2.0       | Gray         |

### Sorting and filtering

- Click any column header to sort (click again to reverse direction). Highlighted in blue when active.
- **Position filter pills** above the table — click `PG`, `SG`, `SF`, `PF`, `C`, `G`, `F`, `UTIL` to narrow to a position.
- **Search box** — filters by player name in real time.

### Downloading

Click **Download CSV** to export the current sorted/filtered view to a local CSV file named `projections_DK_NBA_YYYY-MM-DD.csv`.

---

## 5. Optimizer

**URL:** `/optimizer`

Builds optimized DFS lineups using an integer-linear programming solver. Accepts the same salary CSV as Projections and returns fully-constructed lineups ready to import or copy-paste.

### Step 1 — Load a slate

The Optimizer auto-loads the most recent uploaded slate. You can also drop a new CSV directly using the file picker. The file is validated on selection — missing required columns (Name, Position, Team, Salary, Projection) will display an error before the run.

### Step 2 — Configure parameters

**Site & Salary Cap**

| Site       | Salary Cap | Default Floor |
| ---------- | ---------- | ------------- |
| FanDuel    | $60,000    | $59,000       |
| DraftKings | $50,000    | $47,000       |

Switch site with the **FD / DK** toggle at the top. Salary floor is reset automatically when you switch.

**Lineup count** — How many unique lineups to build (1–150). More lineups = more exposure spread. Typical GPP entry: 20–150. Cash games: 1–3.

**Contest Mode presets** apply tuned constraints automatically:

| Mode         | Best For                | Key Effect                            |
| ------------ | ----------------------- | ------------------------------------- |
| GPP          | Large tournaments       | Higher ceiling players, more variance |
| Cash         | 50/50s, Double-Ups      | Safer floor players, tighter salary   |
| Single Entry | H2H or single-entry GPP | Maximum projected floor               |

**Stacking controls** (GPP-focused)

- **Enable Stacking** — forces 2+ players from the same game into each lineup (recommended for GPPs).
- **Min Game Stack** — minimum number of same-game players per lineup (default: 2).
- **Bring-Back Count** — how many players from the opposing team to pair with the main stack (default: 1).

**Lock / Fade** — Add player names to the Lock list to force them into every lineup, or the Fade list to exclude them from all lineups. Names must match the CSV spelling exactly. Faded teams set on the home dashboard are also respected automatically.

### Step 3 — Run

Click **Run Optimizer**. Building 20 lineups takes 2–8 seconds depending on pool size.

A **Run Parameters** chip bar shows the exact settings used for the completed run, useful for QA.

### Reading the lineups

Each lineup card shows:

- **Lineup number** and **total projected points**
- **Salary used** out of cap (e.g., `$59,700 / $60,000`)
- All roster slots filled with player name, position, team, salary, and projection

**Color coding:**

- Green salary bar = within cap and close to max (good salary utilization)
- Orange = leaving too much salary on the table

**Copy / Export**

- **Copy** button on each lineup — copies the lineup to clipboard in a single-line format suitable for pasting into DK/FD bulk import.
- **Download All (CSV)** — exports all lineups in the standard bulk-upload format for the selected site.
- **Download All (JSON)** — exports raw lineup data with all player fields for further processing.

### Summary panel (right column or bottom)

Shows after a run:

- **Total Lineups Built**
- **Avg Projected Points** across all lineups
- **Salary Efficiency** — average salary used vs. cap
- **Player Exposure table** — which players appear in how many lineups, sorted by exposure descending. High exposure (>50%) in a GPP is a risk worth knowing.

---

## 6. EV Modeling

**URL:** `/ev-modeling`

Expected Value modeling answers: _which players give the most projected points per dollar AND have low ownership?_ High EV + low ownership = leverage plays that can differentiate your lineups.

### Loading data

EV Modeling auto-loads the most recent slate and runs projections automatically. You can also pick a different slate or manually trigger re-runs with the **Refresh / Run** button.

### EV Score formula

```
valueBase  = projection / (salary / 1000)   ← points per $1,000
ownFactor  = max(0.35, 1 - ownership / 150) ← penalizes chalk
EV Score   = valueBase × ownFactor × 1.55
```

Higher EV score = better value at lower ownership.

### Categories

| Category     | Criteria                                 | Badge Color |
| ------------ | ---------------------------------------- | ----------- |
| **Value**    | High EV (≥7.5) or EV ≥6.5 with own < 18% | Green       |
| **Chalk**    | Ownership ≥ 25%                          | Gold        |
| **Pivot**    | Own < 10% AND valueBase ≥ 4.5            | Purple      |
| **Leverage** | Low own, moderate EV                     | Blue        |

### Bubble Chart

The SVG scatter chart in the top section plots:

- **X-axis** — Ownership %
- **Y-axis** — Projected points
- **Bubble size** — EV score (larger = higher EV)
- **Bubble color** — Category color

The chart gives an at-a-glance view of the entire player pool. Look for large bubbles in the lower-left (high projection, low ownership) — those are your leverage plays.

### Filter tabs

Above the table, filter by category: **All**, **Value**, **Leverage**, **Chalk**, **Pivot**.

### Table columns

| Column   | Meaning                                                          |
| -------- | ---------------------------------------------------------------- |
| Name     | Player (OUT badge = injured/out, GTD badge = game-time decision) |
| Pos      | Position                                                         |
| Team     | Team abbreviation                                                |
| Salary   | DK/FD salary                                                     |
| Proj     | Projected FP                                                     |
| Value    | Points per $1,000                                                |
| Own%     | Estimated ownership                                              |
| EV       | EV Score                                                         |
| Category | Badge                                                            |

Click any column header to sort.

### Injury banners

If the injury SSE stream has active data, an amber/red banner appears at the top listing OUT and GTD players. OUT players are also highlighted in red in the table with a red **OUT** badge. GTD players show an amber **GTD** badge. This updates live every 60 seconds.

> **Note:** Injury data requires `nba_injury_report` to be populated. Run `scripts/fetch_nba_injuries.py` or wait for the scheduled ingest.

---

## 7. Simulation

**URL:** `/simulation`

Runs a Monte Carlo simulation against your optimizer-built lineups to model the distribution of possible scores and ceilings.

### How it works

The simulation takes the lineups built by the Optimizer and, for each player, draws random score outcomes from a normal distribution centered on their projection with a standard deviation of ~22%. This process repeats thousands of times, producing a realistic distribution of total lineup scores.

### Controls

| Control        | Options                              | Default     |
| -------------- | ------------------------------------ | ----------- |
| Slate Selector | Saved slates                         | Most recent |
| Site           | DK / FD                              | DK          |
| Sport          | NBA / NFL                            | NBA         |
| Contest Type   | LARGE GPP, SMALL GPP, CASH, SHOWDOWN | LARGE GPP   |
| Simulations    | 1,000 / 5,000 / 10,000 / 25,000      | 5,000       |

Click **Run Simulation** to execute.

### Injury Alert

If injured players are detected (OUT/GTD/Questionable/Doubtful), an amber alert banner appears above the slate selector listing them by name. **Verify your lineup does not include any of these players before locking.**

### Score Distribution chart

After the simulation runs, the histogram shows the full distribution of simulated lineup scores:

- **X-axis** — Total fantasy points
- **Y-axis** — Frequency
- **P50 line** (blue) — Median expected score
- **P75 line** (purple) — 75th percentile score
- **P90 line** (green) — 90th percentile / ceiling

### Percentile table

Shows P10, P25, P50, P75, P90 scores across all simulated outcomes — useful for understanding the range of realistic results before entering a contest.

### Top Player Exposures

A ranked list showing each player's simulated exposure (%) — how often they naturally appear in winning lineups. High-exposure players in the simulation are "chalk" for a reason; low-exposure high-upside players are your tournament differentiators.

### Reading results vs. contest type

- **GPP:** You care about P90 ceiling more than P50. Look for outlier upside.
- **Cash:** You care about P25–P50 floor. Prefer consistent hitters.

---

## 8. Late Swap

**URL:** `/late-swap`

Full pre-lock lineup management. Two modes handle every late-swap scenario: **Swap Existing** finds targeted replacements for scratched players in lineups you've already submitted, and **Re-Optimize** builds entirely fresh lineups with all questionable players excluded from the player pool.

### Mode selector

Two tabs appear in the top-right header next to the DK / FD site toggle:

| Tab               | When to use                                                                                                                     |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| **Swap Existing** | You have submitted lineups and need to swap out a scratched player while staying under the salary cap                           |
| **Re-Optimize**   | Injury news breaks late — rebuild your full lineup set with scratches excluded from the pool and download a ready-to-upload CSV |

---

### Injury Report — Confirm Player Status

If the injury SSE stream has data, an amber **Injury Report** card automatically appears listing every OUT / GTD / Questionable / Doubtful player. Each player card has two action buttons:

| Button                     | Effect                                                                                                                     |
| -------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| **Confirm Active** (green) | Marks the player as confirmed playing. They remain eligible as a swap candidate and are **not** excluded from Re-Optimize. |
| **Scratch** (red)          | Adds the player to the Scratched list. They are excluded from swap candidates and from the Re-Optimize player pool.        |

The status persists while the page is open and updates the excluded-player summary in the Re-Optimize panel in real time.

---

## Mode: Swap Existing

### Step 1 — Load your slate

Select the active slate using the Slate Selector at the top, or drop a new salary CSV directly into the file picker.

### Step 2 — Import your lineups

**Single lineup:** Type or paste one player name per line in the lineup text area, or fill in the individual slot inputs.

**Multiple lineups:** Click **Import from Entry CSV** and select your DraftKings / FanDuel entry file (`DKEntries.csv` or equivalent). All lineups are parsed at once and loaded into the **Lineup Manager**.

> Names must match DK/FD spelling in your salary CSV.

### Lineup Manager (multi-lineup mode)

When 2 or more lineups are imported, the **Lineup Manager** grid appears above the input card:

- Each lineup is shown as a chip labeled `Lineup 1`, `Lineup 2`, etc.
- Lineups containing a scratched player display a red **!** badge and are highlighted.
- An **affected** count badge shows how many lineups need attention.
- Click any chip to make it the active editing target.

### Step 3 — Mark scratched players

Enter scratched player names in the **Scratched Players** text area (one per line), or use **Scratch** buttons in the Injury Report card. Slot inputs for scratched players turn red immediately.

### Step 4 — Find Swaps

Click **Find Best Swaps**. The engine runs server-side and returns ranked candidates for each scratched player.

**Controls:**

| Control             | Effect                                                                          |
| ------------------- | ------------------------------------------------------------------------------- |
| **Variance** slider | 0% = pure projection ranking; higher values add randomness for lineup diversity |
| **Show** field      | Number of candidates to display per scratch (5–50)                              |
| **Sort** dropdown   | Default ranking column (Score, Proj, Ceiling, Value, Own%)                      |
| **Re-roll** button  | Re-runs with a fresh randomness seed (visible when variance > 0)                |

### Reading Swap Block results

For each scratched player a **Swap Block** header shows:

| Field      | Meaning                                                    |
| ---------- | ---------------------------------------------------------- |
| Scratched  | Player being replaced (resolved name if CSV alias differs) |
| Pos        | Eligible position                                          |
| Salary     | Their salary slot                                          |
| Their Proj | Their projected FP                                         |
| Budget     | Max salary available for the replacement                   |

The candidate table columns:

| Column  | Meaning                                                 |
| ------- | ------------------------------------------------------- |
| #       | Rank                                                    |
| Player  | Candidate name                                          |
| Pos     | Position                                                |
| Team    | Team                                                    |
| Salary  | Their salary                                            |
| Proj    | Projected FP                                            |
| Ceil    | Ceiling estimate                                        |
| Value   | Proj / (Salary / 1000)                                  |
| +/-FPTS | Projection delta vs. scratched player                   |
| +/-$    | Salary delta                                            |
| New$    | New lineup total salary after swap                      |
| Own%    | Estimated ownership                                     |
| Score   | Composite swap score (proj 50%, value 30%, low-own 20%) |

**Score color:** green ≥ 80, yellow-green ≥ 60, yellow ≥ 40, orange ≥ 20, red < 20.

**ΔProj color:** green > +2, yellow-green 0–2, orange −2–0, red < −2.

**Correlation badges:**

- `STACK` — same team as another player in your lineup
- `CORR` — same game (different team)

Click column headers to re-sort within a swap block. Click **Show all N** to expand beyond the default 8.

### Applying a swap

| Button                                | Effect                                                                                                                    |
| ------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| **Use ↗**                             | Replaces the scratched player with this candidate in the active lineup only                                               |
| **All ↗↗** _(multi-lineup mode only)_ | Replaces the scratched player with this candidate in **every** lineup that contains them — one click, all lineups updated |

After a swap the scratched player is removed from the scratch list automatically and the Lineup Manager badges update.

### Exporting

| Button             | Output                                                                      |
| ------------------ | --------------------------------------------------------------------------- |
| **Export Lineup**  | Downloads the current active lineup as a `.txt` file                        |
| **Export All (N)** | Downloads all managed lineups in a single `.txt` file _(multi-lineup mode)_ |

---

## Mode: Re-Optimize

Runs the full DFS pipeline with all scratched / unconfirmed players blocked from the player pool and returns brand-new lineups ready for DK/FD upload.

### Excluded players

The panel shows a live list of everyone excluded before you run:

- All names in the **Scratched Players** text area
- Any injured player in the Injury Report who has **not** been marked Confirm Active

### Settings

| Setting              | Default          | Notes                                |
| -------------------- | ---------------- | ------------------------------------ |
| **Lineups to Build** | 20               | 1–150                                |
| **Contest Mode**     | GPP / Tournament | GPP, Cash / 50/50, Single Entry      |
| **Enable Stacking**  | On               | Forces same-game correlations        |
| **Min Game Stack**   | 2                | Minimum same-game players per lineup |
| **Bring-Back Count** | 1                | Opposing-team bring-back players     |

### Running

Click **Build N New Lineups**. The optimizer runs server-side (typically 3–10 seconds for 20 lineups).

### Results

After a run, a summary shows **Avg Projection**, **Avg Salary**, and **Projection Range**, followed by a lineup preview table (first 25 lineups). Any additional lineups are included in the downloaded file.

Click **Download DK/FD Upload CSV** to save a ready-to-upload file named `lineups_DK_late_swap_YYYY-MM-DD.csv`. This file is in the standard bulk-import format accepted by DraftKings and FanDuel.

---

## 9. Analytics

**URL:** `/analytics`

Tracks your real contest performance over time — ROI, profit/loss, and projection accuracy.

### Controls

At the top, set:

- **Site** — DK or FD (filters all data to one platform)
- **Lookback** — 7, 14, 30, 60, or 90 days

Click **Refresh** to reload.

### Logging a contest entry

Click **+ New Entry** to expand the contest logging form. Fill in:

| Field         | Required | Notes                                          |
| ------------- | -------- | ---------------------------------------------- |
| Date          | Yes      | Contest date (defaults to today)               |
| Site          | Yes      | DK or FD                                       |
| Type          | Yes      | GPP, Double Up, Cash, Winner Take All          |
| Entry Fee     | Yes      | Must be > $0                                   |
| Payout        | No       | Leave at 0 when entering; update after results |
| Final Rank    | No       | Your finishing position                        |
| Total Entries | No       | Field size of the contest                      |
| Notes         | No       | Free-text label (e.g., "Main Slate GPP")       |

Click **Log Entry** to save. The ROI panel refreshes automatically after each entry.

> **Tip:** Log the entry immediately when you submit your lineup (payout = 0). Come back after results and add the payout and rank using a second entry — or keep a note to update the database directly.

### ROI Summary panel

Shows for the selected period and site:

| Metric   | Meaning                      |
| -------- | ---------------------------- |
| Contests | Total contest entries logged |
| Invested | Total entry fees spent       |
| Won      | Total payouts received       |
| Profit   | Won − Invested               |
| ROI      | Profit / Invested × 100      |

**By Contest Type** table breaks the same stats down by GPP, Cash, Double Up, and Winner Take All.

> If the panel shows "No contest results logged yet," start logging entries to populate it.

### Projection Accuracy panel

Shows how accurate the projection engine has been against real game results.

| Metric      | Meaning                                                    |
| ----------- | ---------------------------------------------------------- |
| Projections | Number of reconciled player projections                    |
| MAE         | Mean Absolute Error — average miss in fantasy points       |
| RMSE        | Root Mean Square Error — penalizes large misses more       |
| Bias        | Positive = engine over-projects; Negative = under-projects |

The **Daily Breakdown** table shows MAE, RMSE, and Bias per slate day.

> **How accuracy data populates:** Run projections for a slate → the next morning after game results are in, call `POST /analytics/reconcile` with the slate date to match projections vs. actuals. This can also be triggered via the backend API.

---

## 10. Metrics

**URL:** `/metrics`

System health monitoring and projection engine performance dashboard. Intended for technical review, not daily contest use.

### Stat Cards (top row)

| Card        | Meaning                                                      |
| ----------- | ------------------------------------------------------------ |
| Avg MAE     | Average projection error across all reconciled slates        |
| Projections | Total reconciled projections in the analytics database       |
| RMSE        | Root Mean Square Error overall                               |
| Proj Bias   | Overall projection bias (over- or under-projecting tendency) |

### Charts

**Accuracy Trend (line chart)**

- Plots **MAE** (blue) and **RMSE** (purple) day-over-day for the selected lookback window.
- A flat or decreasing line means the engine is staying calibrated.
- Spikes indicate a bad slate day (injuries, weather, bad matchup data).

**Daily Run Bar Chart**

- Each bar represents one slate day showing how many projections were run.
- Useful for confirming that scheduled ingest jobs are running.

### Run Stats panel

Shows aggregate numbers for the selected period:

| Field                  | Meaning                             |
| ---------------------- | ----------------------------------- |
| Total Projections      | Count of all reconciled projections |
| Mean Absolute Error    | Average absolute miss in FP         |
| Root Mean Square Error | Penalized miss metric               |
| Projection Bias        | Systematic over/under tendency      |
| Period                 | Lookback window in days             |

### Lookback period

Use the **Period** selector (top-right of the page) to change the window: 7, 14, 30, 60, or 90 days.

### Interpreting MAE / RMSE

A well-calibrated engine for NBA DFS typically lands in these ranges:

| Metric | Good      | Acceptable | Needs Review |
| ------ | --------- | ---------- | ------------ |
| MAE    | < 5.0 pts | 5–8 pts    | > 8 pts      |
| RMSE   | < 7.0 pts | 7–11 pts   | > 11 pts     |
| Bias   | ±0.5 pts  | ±1.5 pts   | > ±2.0 pts   |

Positive bias (engine over-projects) is common when injured players make the final slate before scratches are confirmed. Negative bias often indicates the engine is under-pricing pace of play or matchup bonuses.

---

## Daily Workflow Summary

```
1. SLATES       → Upload today's DK/FD salary CSV
2. PROJECTIONS  → Run projections; review value tiers
3. OPTIMIZER    → Build lineups (set contest mode, stacking, locks/fades)
4. EV MODELING  → Identify leverage plays; check injury banners
5. SIMULATION   → Monte Carlo your lineup set; check P90 ceiling vs. contest type
6. LATE SWAP    → Monitor injury SSE; swap scratches before lock
7. ANALYTICS    → After results: log contest entry with payout
8. METRICS      → Weekly review of engine accuracy and system health
```

---

## Common Issues

| Symptom                                          | Likely Cause                            | Fix                                                                                          |
| ------------------------------------------------ | --------------------------------------- | -------------------------------------------------------------------------------------------- |
| Projections page shows "no slate loaded"         | No slate uploaded yet                   | Go to Slates → Upload Slate                                                                  |
| Optimizer errors "Missing required columns"      | Wrong CSV format or wrong site          | Download fresh salary CSV from DK/FD; verify Site selector matches the file                  |
| EV / Simulation / Late Swap shows no injury data | `nba_injury_report` table is empty      | Run `fetch_nba_injuries.py` or wait for scheduled ingest (PDFs publish ~2 hours before lock) |
| Analytics shows "—" for all stats                | No contest entries logged yet           | Use the Log Contest Entry form to add entries                                                |
| Metrics accuracy is blank                        | `projection_log` has no reconciled rows | Run projections for a slate, then POST `/analytics/reconcile` the next day                   |
| Backend returns 503 on health check              | Docker backend container down           | `docker compose up -d backend`                                                               |
