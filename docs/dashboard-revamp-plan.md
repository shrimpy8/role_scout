# Dashboard Revamp Plan — Role Scout v2

## Goal
Bring the full feature set of `auto_jobsearch` dashboard into `role_scout`, wiring all
available DB fields and adding every user-facing capability from the reference design.
The current basic dashboard remains reachable at `/debug/basic` as a fallback until
the new one is verified.

---

## 1. Feature Gap Analysis

| Feature | auto_jobsearch | role_scout (current) | Action |
|---------|---------------|---------------------|--------|
| Run history strip | ✅ | ❌ | Add |
| Status filter sidebar (New/Reviewed/Applied/Rejected/All/History) | ✅ | ❌ | Add |
| Source filter sidebar (LinkedIn/Google/TrueUp) | ✅ | ❌ | Add |
| Sortable columns | ✅ | ❌ | Add |
| Work Model pill column | ✅ | ❌ | Add |
| Company Stage column | ✅ | ❌ | Add |
| Compensation column | ✅ | ❌ | Add |
| JD Download column | ✅ | ❌ | Add |
| External link column (↗) | ✅ | ❌ | Add |
| Clickable title → job URL | ✅ | ❌ | Add |
| Expandable detail row | ✅ | ❌ | Add |
| Score breakdown ring + bars | ✅ | ❌ | Add |
| Reasoning text in expand | ✅ | ❌ | Add |
| JD Alignment (Claude, on-demand) | ✅ | ❌ | Add |
| Key Requirements tags | ✅ | ❌ | Add |
| Red Flags pills | ✅ | ❌ | Add |
| Inline status dropdown | ✅ | ❌ | Add |
| Watchlist star highlight | ✅ | ✅ | Keep |
| Watchlist CRUD sidebar | ✅ | ✅ | Keep |
| Dark/light theme toggle | ✅ | ❌ | Add |
| HiTL approve/cancel banner | N/A | ✅ | Keep |
| Match threshold slider | N/A | ✅ | Keep |
| Tailor resume (Claude) | N/A | ✅ | Keep (modal) |
| Footer status counts | ✅ | ❌ | Add |
| Topbar with total count | ✅ | ❌ | Add |

---

## 2. Data Available in DB

All fields below exist in `qualified_jobs` and are available to the dashboard.

### Already displayed
- `hash_id`, `title`, `company`, `location`, `match_pct`, `status`

### Will be added
- `url` — clickable title link + external link column
- `apply_url` — Apply button in expanded row
- `city`, `country` — richer location display
- `work_model` — remote/hybrid/onsite pill
- `company_stage` — stage pill
- `comp_range`, `salary_visible` — comp column
- `is_watchlist` — star highlight
- `source` — source filter, shown in expanded
- `posted_date` — in expanded row
- `seniority_score`, `domain_score`, `location_score`, `stage_score`, `comp_score` — score bars
- `reasoning` — expanded reasoning section
- `key_requirements` — tags
- `red_flags` — red pills
- `jd_alignment` — on-demand Claude alignment (display + trigger re-run)
- `description` — needed to know if alignment is runnable
- `jd_filename` — JD download button
- `domain_tags` — can display in expanded

---

## 3. New API Endpoints Needed

### Already exists
- `GET /` — index (needs query param expansion)
- `POST /api/tailor/<hash_id>` — tailor (working)
- `GET /api/pipeline/status` — banner poll (working)
- `POST /api/pipeline/resume` — approve/cancel (working)
- `POST /api/pipeline/extend` — TTL extend (working)
- `GET /api/runs` — run history JSON
- `POST /api/watchlist` — add watchlist
- `DELETE /api/watchlist/<company>` — remove watchlist

### New endpoints to add
1. `POST /api/status/<hash_id>` — inline status update
   - Body: `{"status": "reviewed"|"applied"|"rejected"|"new"}`
   - Response: `{"data": {"hash_id": "...", "status": "reviewed", "updated": true}}`
   - Errors: 400 invalid status, 403 CSRF, 404 not found

2. `POST /api/alignment/<hash_id>` — on-demand JD alignment via Claude
   - Body: `{"force": false}` (force=true re-runs even if cached)
   - Response: `{"data": {"hash_id": "...", "jd_alignment": "...", "cached": true}}`
   - Errors: 404 not found, 422 no description/no resume, 500 Claude error, 403 CSRF

3. `GET /jds/<filename>` — download JD text file
   - Path traversal protection: reject `..` and `/` prefixes
   - Response: file as attachment (text/plain)
   - Errors: 400 bad filename, 404 not found

### Modified endpoints
- `GET /` — add query params: `status`, `source`, `sort`, `dir`; pass `total_counts`, `source_counts`, `run_history`, `active_filter`, `active_source`, `active_sort`, `active_dir` to template

---

## 4. Backend Implementation Plan

### 4a. routes.py additions

```python
# POST /api/status/<hash_id>
@bp.route("/api/status/<hash_id>", methods=["POST"])
def status_update(hash_id):
    # Validate CSRF (Flask-WTF auto-validates on POST)
    # Validate status in {new, reviewed, applied, rejected}
    # Call update_job_status(conn, hash_id, status)
    # Return 200 on success, 404 if not found, 400 if bad status

# POST /api/alignment/<hash_id>
@bp.route("/api/alignment/<hash_id>", methods=["POST"])
def alignment_run(hash_id):
    # Get job from DB — 404 if not found
    # If no description: 422 VALIDATION_ERROR
    # If force=False and jd_alignment already set: return cached
    # Otherwise: call Claude with job description + resume summary
    # Store result in DB via update_jd_alignment()
    # Return alignment JSON

# GET /jds/<path:filename>
@bp.route("/jds/<path:filename>", methods=["GET"])
def jd_download(filename):
    # Reject ".." and absolute paths
    # Look up file in configured JD output directory
    # Send as attachment
```

### 4b. Updated index() route

```python
@bp.route("/", methods=["GET"])
def index():
    # Read query params: status (default 'new'), source, sort (default 'match_pct'), dir (default 'desc')
    # Validate sort col and dir
    # Fetch jobs with get_qualified_jobs(conn, status, source, sort, dir, limit=200)
    # Fetch total_counts via get_job_count_by_status()
    # Fetch source_counts via get_job_count_by_source()
    # Fetch run_history (last 5 runs from run_log)
    # Parse key_requirements/red_flags JSON strings to lists
    # Pass watchlist to template (for star highlights server-side)
    # Render template with all context
```

---

## 5. Frontend Implementation Plan

### 5a. base.html overhaul
- Add CSS variables for light/dark mode (toggle via `data-theme` on `<html>`)
- Sticky topbar with job count display and theme toggle button
- Load new JS files: `main.js`, `status.js`, `alignment.js`
- Keep existing: `banner.js`, `threshold.js`, `watchlist.js`, `tailor.js`
- Store theme preference in localStorage

### 5b. index.html complete rewrite
Structure:
```
┌─ Topbar (sticky) ─────────────────────────────────────────────────────────┐
│  ⬡ Role Scout          N jobs tracked                    ☀/☽             │
├─ Run History Strip ───────────────────────────────────────────────────────┤
│  Last Run: Apr 27 · Fetched: 36 · New: 14 · Qualified: 14 · LI:10 G:3 T:1│
├─ Sidebar ──┬─ Main Content ────────────────────────────────────────────────┤
│            │  Threshold slider + watchlist (keep)                         │
│ Status     │  Toolbar: "N jobs · new"                                     │
│ ○ New 14   │  ┌────────────────────────────────────────────────────────── │
│ ○ Reviewed │  │ Match│Company  │Title     │Loc│Model │Stage│Comp│Status│↓JD│↗│
│ ○ Applied  │  ├──────┤─────────┤──────────┤───┤──────┤─────┤────┤──────┤───┤─┤
│ ○ Rejected │  │ 92%  │Anthropic│Sr PM AI  │SF │Remote│B/C  │$200│[New▾]│ — │↗│
│ ○ All      │  │ ▼ expanded row (click to toggle)                         │
│            │  │   ○ Score ring + bars                                     │
│ Source     │  │   ○ Reasoning                                             │
│ ○ All      │  │   ○ JD Alignment [▶ Run]                                  │
│ ○ LinkedIn │  │   ○ Key Requirements tags                                 │
│ ○ Google   │  │   ○ Red Flags tags                                        │
│ ○ TrueUp   │  │   ○ Tailor button (opens modal)                           │
│            │  └──────────────────────────────────────────────────────────┘
│ Watchlist  │                                                               │
│ + Add      │                                                               │
└────────────┴───────────────────────────────────────────────────────────────┘
```

### 5c. New JavaScript files

**`main.js`** — Core UI controller
- Theme toggle (localStorage persisted, syncs icon)
- Row expand/collapse (toggleExpand)
- Score ring SVG animation (requestAnimationFrame)
- Score bar animation
- Sort link building (preserves active filters)
- Init on DOMContentLoaded

**`status.js`** — Inline status updates
- Listen for change on `.status-select` selects
- POST `/api/status/<hash_id>`
- Visual feedback: green border (800ms) on success, red border + revert on error
- Stores `data-previous` for rollback

**`alignment.js`** — JD alignment
- Listen for click on `.alignment-run-btn`
- POST `/api/alignment/<hash_id>` with `{force: isRerun}`
- Show "…" during load, restore button on complete
- Parse and render strong_matches / reframing_opportunities / genuine_gaps / overall_take
- Display in `#alignment-result-<hash_id>` div

### 5d. CSS Design Tokens (inline in base.html `<style>`)

```css
:root {
  --bg-base: #f4f4f8;
  --bg-surface: #ffffff;
  --bg-elevated: #ededf2;
  --border: #e2e2e8;
  --text-primary: #18181b;
  --text-secondary: #3f3f46;
  --text-muted: #71717a;
  --text-faint: #a1a1aa;
  --accent: #6c5ce7;
  --green: #16a34a;
  --amber: #d97706;
  --red: #dc2626;
}
[data-theme="dark"] {
  --bg-base: #0f0f11;
  --bg-surface: #18181b;
  --bg-elevated: #1c1c1f;
  --border: #27272a;
  --text-primary: #e2e2e5;
  --text-secondary: #a1a1aa;
  --text-muted: #71717a;
  --text-faint: #52525b;
  --accent: #7c6af7;
  --green: #22c55e;
  --amber: #f59e0b;
  --red: #ef4444;
}
```

---

## 6. Fallback Strategy

1. The current basic dashboard remains accessible at **`/debug/basic`** — this is a new route that renders the old `index.html` (renamed to `basic.html`).
2. New dashboard lives at **`/`** (replaces current index.html).
3. If the new dashboard has a critical bug, user can use `/debug/basic` immediately.
4. Implementation steps keep the old template intact until new one is verified.

---

## 7. JD Alignment — Claude Prompt Design

The alignment endpoint compares the job description against the candidate's resume summary:

**Input**: job description (up to 2000 chars) + resume_summary.md content

**Output JSON**:
```json
{
  "strong_matches": ["PM with AI product experience", "Large-scale infra background"],
  "reframing_opportunities": ["Enterprise experience reframes as 'platform at scale'"],
  "genuine_gaps": ["No iOS consumer product experience"],
  "overall_take": "Strong match — reframe enterprise experience as platform expertise"
}
```

**Prompt location**: `src/role_scout/prompts/alignment_system.md` (new file)

**Cache behavior**: Result stored in `jd_alignment` column. Re-runs only when `force=True`.

**Resume path**: `config/resume_summary.md` (already gitignored, user places there)

---

## 8. Test Plan (Playwright MCP)

Test sequence using `DB_PATH=output/test.db` with data from most recent pipeline run:

1. **Page load** — Dashboard renders with job list, no JS errors
2. **Status filter** — Click "Reviewed" → URL changes to `?status=reviewed`, table updates
3. **Source filter** — Click "LinkedIn" → `?source=linkedin`, correct jobs shown
4. **Sort** — Click "Match" header → `?sort=match_pct&dir=asc`, order reverses
5. **Row expand** — Click a job row → detail panel opens, score ring animates
6. **Score bars** — Verify seniority/domain/location/stage/comp bars fill correctly
7. **Status dropdown** — Change status from "new" to "reviewed" → green flash, no page reload
8. **External link** — Click ↗ → opens job URL in new tab (verify target="_blank")
9. **Tailor button** — Opens modal, shows loading spinner, then result (or cached)
10. **Copy summary** — Copies summary text to clipboard
11. **Alignment** — Click ▶ Run → shows loading, renders alignment sections
12. **Alignment re-run** — Click ↻ Re-run → force=true, fresh Claude call
13. **Theme toggle** — Click ☀/☽ → CSS variables switch, preference persists on reload
14. **Watchlist star** — Add company to watchlist → ★ appears on matching rows instantly
15. **No JS errors** — Check console throughout

---

## 9. Implementation Order

1. ✅ Write this plan
2. Add `/debug/basic` fallback route + `basic.html` (copy current index.html)
3. Add new backend routes: `POST /api/status/<hash_id>`, `POST /api/alignment/<hash_id>`, `GET /jds/<filename>`
4. Update `GET /` route with full data fetching
5. Rewrite `base.html` with CSS tokens and theme system
6. Rewrite `index.html` with full table structure and expandable rows
7. Write `main.js`, `status.js`, `alignment.js`
8. Write `prompts/alignment_system.md`
9. Run Playwright tests against test DB
10. Fix any issues found during testing

---

## 10. Files to Create/Modify

| File | Action | Notes |
|------|--------|-------|
| `src/role_scout/dashboard/routes.py` | Modify | Add 3 new endpoints, expand index() |
| `src/role_scout/dashboard/templates/base.html` | Rewrite | CSS tokens, topbar, theme toggle, new JS |
| `src/role_scout/dashboard/templates/index.html` | Rewrite | Full new layout |
| `src/role_scout/dashboard/templates/basic.html` | Create | Copy of current index.html (fallback) |
| `src/role_scout/dashboard/static/js/main.js` | Create | Theme, expand, ring animation |
| `src/role_scout/dashboard/static/js/status.js` | Create | Inline status updates |
| `src/role_scout/dashboard/static/js/alignment.js` | Create | JD alignment |
| `src/role_scout/dashboard/static/js/tailor.js` | Keep | Already working |
| `src/role_scout/dashboard/static/js/banner.js` | Keep | Already working |
| `src/role_scout/dashboard/static/js/threshold.js` | Keep | Already working |
| `src/role_scout/dashboard/static/js/watchlist.js` | Keep | Already working |
| `src/role_scout/prompts/alignment_system.md` | Create | Claude alignment prompt |
