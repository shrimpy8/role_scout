# EXP-BRIEF: Role Scout Phase 2 — Dashboard Enhancements

| Field | Value |
|-------|-------|
| Parent | [PRD-CORE.md](./PRD-CORE.md) |
| Version | 1.0 |
| Owner | [project-owner] |
| Status | Approved |
| Updated | 2026-04-23 |

> Implementation brief for the 4 new dashboard components. **No new design system.** Inherits all CSS, typography, color, and layout from the existing Phase 1 Flask dashboard at `auto_jobsearch/jobsearch/dashboard/`.

---

## 1. Experience Principles

| Principle | Meaning | Application |
|-----------|---------|-------------|
| **Inherit, don't invent** | Reuse existing Phase 1 CSS classes, color tokens, and button styles | Every new component matches the existing table/panel/button look |
| **Optimistic UI where safe** | Apply state changes client-side immediately; revert on server error | Watchlist add/remove, threshold slider |
| **Transparency over magic** | Show cost, source health, TTL countdown — don't hide what the pipeline is doing | Banner shows cost-so-far; sidebar shows SerpAPI quota |
| **Reversible by default** | Every destructive or consequential action is either one-click-undoable or requires confirmation | Cancel banner button confirms; remove-from-watchlist restores on error |
| **Keyboard-first for power ops** | The user reviews the same table every week; keyboard shortcuts matter | `A` to approve banner, `Esc` to cancel, `T` to toggle tailor view |

---

## 2. Baseline (from Phase 1, unchanged)

The existing Flask dashboard at `auto_jobsearch/jobsearch/dashboard/` is the visual baseline. Phase 2 does not touch:

- Color palette (existing light theme)
- Typography (existing system font stack)
- Table layout (qualified_jobs, expand-row pattern)
- Button styles (primary, secondary, ghost)
- Download JD flow
- Existing Align button behavior
- CSRF token plumbing

**All Phase 2 components reuse these.** Any new styling (e.g., the banner background color) is limited to component-local CSS with existing color tokens.

---

## 3. Emotional Journey

| Moment | User Emotion | Our Goal | Design Response |
|--------|--------------|----------|-----------------|
| Banner appears (new) | Curiosity → attention | "Something needs me, but it's not urgent" | Amber banner, non-blocking, clear 3-button choice |
| Reviewing qualified count | Skepticism ("are these really good?") | Confidence via top-3 preview + watchlist-hit count | Banner shows top-3 matches inline |
| TTL ticking down | Mild anxiety (if busy) | Relief via Extend button | Countdown monotone until < 30 min → amber text; Extend button always visible |
| Dragging threshold slider | Playful exploration | "Let me see what 80% looks like" | Smooth client-side re-render, no spinner, no lag |
| Clicking Tailor (first time) | Cautious hope | "Is this going to be generic?" | Loading with "Claude is tailoring..." message (sets expectation); cached badge on repeat clicks |
| Tailor result appears | Evaluation | "Is this actually useful?" | Three clear sections (summary, bullets, keywords) with copy-to-clipboard buttons |
| Adding to watchlist | Low-stakes control | "I want to track this company" | Optimistic ★ appears immediately; silent server sync |
| Seeing cost warning ("$2.14") | Mild concern | Calibration without alarm | Yellow (not red) banner, dismissible, explains "above $2 target" |
| Source auto-skipped warning | Informational | "LinkedIn has been failing — pipeline noticed" | Sidebar shows crossed-out source with "skipped after 3 failed runs" tooltip |
| Scheduled run completed overnight | Passive satisfaction | "It just worked" | No banner, no interruption — dashboard on next open shows new qualified rows |

---

## 4. Components

Four new UI additions. Each documented with wireframe, states, interactions, error handling.

---

### 4.1 HiTL Review Banner

Appears at the top of the dashboard when `run_log.status=review_pending`. Polls `/api/pipeline/status` every 5 s.

#### Wireframe (ASCII)

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ ⏸  Pipeline paused for review        Run a1b2c3d4  ·  TTL: 3:42 remaining    │
│                                                                              │
│ Fetched 108 · New 75 · Qualified at ≥85%: 31  ·  Cost so far: $0.87         │
│                                                                              │
│ Top matches:  WorkOS (88%)  Anthropic (84%)  Stripe (81%)                    │
│ Watchlist hits: Anthropic (2)                                                │
│ Source health: LinkedIn ✓  Google ✓  TrueUp ✓                                │
│                                                                              │
│                [Approve & Export]   [Adjust Threshold]   [Cancel]  [+2h TTL] │
└──────────────────────────────────────────────────────────────────────────────┘
```

#### States

| State | Trigger | Display |
|-------|---------|---------|
| Hidden | `status != review_pending` | Banner not rendered |
| Visible | `status == review_pending` | Banner with all info and 4 buttons |
| Visible — TTL warning | TTL remaining < 30 min | Countdown text turns amber |
| Visible — extended | After +2h clicked | Button disabled with label "Extended" |
| Visible — 0 qualified | `qualified_count == 0` | Body shows "0 qualified at 85%. Top near-misses: …"; [Approve] disabled with tooltip "Nothing to export" |
| Transitioning | User clicked Approve/Cancel | Buttons disabled, spinner replaces banner body for ≤ 5 s |
| Cancelled (TTL) | `cancel_reason=ttl_expired` | Banner replaced with grey toast: "Run cancelled — no response in 4h. Re-run when ready." |
| Error | `/api/pipeline/resume` returned 5xx | Red toast "Couldn't reach pipeline. Retry?" with retry button; banner stays visible |

#### Interactions

| Action | Keyboard | Client → Server | Expected response |
|--------|----------|-----------------|-------------------|
| Approve | `A` | `POST /api/pipeline/resume` `{approved: true}` | 200, banner hides within 5 s (next poll) |
| Adjust threshold | `T` | Opens inline numeric input 75–95 | Applied as display filter, no server call |
| Cancel | `Esc` | `POST /api/pipeline/resume` `{approved: false, cancel_reason: "user_cancel"}` | 200, banner replaced by grey toast "Run cancelled" |
| Extend 2h | `E` | `POST /api/pipeline/extend` | 200, button disabled with "Extended" label; countdown updates to +2h |

#### Voice

- Title: `⏸ Pipeline paused for review` (not "Attention required" — user knows it's theirs)
- TTL: `TTL: 3:42 remaining` — compact
- Cost: `Cost so far: $0.87` — factual
- Empty state: `0 qualified at 85%. Top near-misses: …` — acknowledges without blaming the pipeline

---

### 4.2 Threshold Slider (display filter only)

Sidebar control. **Never triggers re-score.**

#### Wireframe

```
┌──────────────────────────────────┐
│  Filter                           │
│  ┌────────────────────────────┐  │
│  │ Match threshold:   82      │  │
│  │  75  ├───────●──────┤  95  │  │
│  │  Showing 22 of 31 jobs     │  │
│  └────────────────────────────┘  │
│  ℹ︎ Filter only — never re-scores │
└──────────────────────────────────┘
```

#### States

| State | Trigger | Display |
|-------|---------|---------|
| Default | Page load | Slider at `SCORE_THRESHOLD` from `.env`; table unfiltered view |
| Dragging | Mouse/touch drag | Value number updates live; table re-renders client-side on each step |
| No-match | Threshold > highest match_pct | Table shows "No jobs match this threshold. Drag left to see more." |
| Session-transient | Slider state not persisted across reload | On reload, resets to `.env` default |

#### Interactions

- Drag thumb or click a value in range — purely client-side JS filter on the existing table rows by `data-match-pct` attribute.
- **No network request on change.** Asserted by test T32.
- Counter label "Showing X of Y jobs" updates live.

#### Voice

- Help text: `Filter only — never re-scores` (disarms the implicit question)

---

### 4.3 Watchlist CRUD Panel

Sidebar panel below threshold slider.

#### Wireframe

```
┌──────────────────────────────────┐
│  Watchlist                        │
│  ┌────────────────────────────┐  │
│  │ Anthropic             ×    │  │
│  │ OpenAI                ×    │  │
│  │ Stripe                ×    │  │
│  │ WorkOS                ×    │  │
│  └────────────────────────────┘  │
│  ┌────────────────────────────┐  │
│  │ Add company…        [Add]  │  │
│  └────────────────────────────┘  │
└──────────────────────────────────┘
```

#### States

| State | Display |
|-------|---------|
| Populated | List of companies with × buttons; Add input below |
| Empty | "No watchlist companies yet. Add one to highlight their jobs with ★." |
| Adding | Input disabled with spinner; optimistic item appears with muted style |
| Add error | Optimistic item reverts; toast "Couldn't save — please retry" |
| Removing | Item fades; on error, restores |

#### Interactions

| Action | Client → Server | Behavior |
|--------|-----------------|----------|
| Type + Enter OR Add button | `POST /api/watchlist` `{company: "..."}` (CSRF header) | Optimistic: append to list; if 2xx keep, else revert |
| Click × | `DELETE /api/watchlist/<company>` (CSRF header) | Optimistic: remove; if 2xx keep, else restore |
| After any change | (no explicit action) | ★ badges in jobs table re-render on next `/api/pipeline/status` poll (≤ 5 s) |

#### Error states

- Duplicate add: server returns 200 with unchanged list (idempotent); UI accepts silently
- Invalid company (empty, > 100 chars, newline): 400 `VALIDATION_ERROR`; input shakes, toast "Company name is invalid"
- Write failure (disk): 500; toast with manual retry button

#### Voice

- Empty: `No watchlist companies yet. Add one to highlight their jobs with ★.`
- Error: `Couldn't save — please retry` (never "Server error 500")

---

### 4.4 Tailor Button (cached vs fresh)

Appears in the expanded-row panel alongside the existing Align button. Present only on qualified jobs.

#### Wireframe (expanded row with panel open)

```
┌────────────────────────────────────────────────────────────────────┐
│ WorkOS · Senior PM · San Francisco · 88%              [JD ↓]        │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│  [ Align ]   [ Tailor ]    Status: new ▾         ★ (not watched)   │
│                                                                    │
│  ┌─ Tailor result ─────────────────────── cached · 2h old · [↻] ─┐ │
│  │ Summary                                                       │ │
│  │   [3 sentences tailored to WorkOS PM role]  [Copy]            │ │
│  │                                                               │ │
│  │ Bullets                                                       │ │
│  │   • [bullet 1]                                                │ │
│  │   • [bullet 2]                                                │ │
│  │   …                                        [Copy all]         │ │
│  │                                                               │ │
│  │ Keywords incorporated                                         │ │
│  │   B2B SaaS · identity · developer-first · …                   │ │
│  └───────────────────────────────────────────────────────────────┘ │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

#### States

| State | Trigger | Display |
|-------|---------|---------|
| Idle (no prior tailor) | Expand row, no `tailored_resume` in DB | `[ Tailor ]` button only; no result panel |
| Loading | Click Tailor, cache miss | Button disabled with spinner; subtitle "Claude is tailoring — usually ~8s" |
| Success — fresh | Server returned new tailor | Result panel appears, badge "fresh · just now" |
| Success — cached | Server returned cached JSON | Result panel auto-renders on expand, badge "cached · 2h old · [↻ refresh]" |
| Refreshing | `[↻]` clicked | Panel grays out; spinner on ↻ icon; badge updates to "fresh · just now" on success |
| Error — Claude fail | 500 from Claude | Red toast "Tailoring failed — try again"; Tailor button re-enabled |
| Error — not qualified | 400 `NOT_QUALIFIED` (defensive, shouldn't happen) | Button hidden + dev-console warning |
| Stale hint (resume edited) | Server detected cache key mismatch and auto-refreshed | Badge shows "fresh · resume changed since last tailor" |

#### Interactions

| Action | Request | Response handling |
|--------|---------|-------------------|
| Click Tailor (first time) | `POST /api/tailor/<hash_id>` `{force: false}` | Show loading, then panel with `fresh` badge |
| Expand row with existing cache | No request on expand | Render from DB column; show `cached` badge |
| Click ↻ refresh | `POST /api/tailor/<hash_id>` `{force: true}` | Bypass cache; re-run Claude |
| Copy button (summary or bullets) | Clipboard API | Toast "Copied" (1s) |
| Keyboard `T` when row focused | Same as Tailor click | — |

#### Cached-freshness indicator

Badge color + label:

| Label | Color | Meaning |
|-------|-------|---------|
| `fresh · just now` | green | Generated in this session |
| `cached · Nh old` | neutral gray | From DB, cache key still valid |
| `fresh · resume changed` | green | Auto-invalidated + regenerated because `resume_summary.md` changed |

#### Voice

- Loading: `Claude is tailoring — usually ~8s`
- Success: (no banner, just render)
- Error: `Tailoring failed — try again`
- No-fabrication assurance (sidebar tooltip or footnote under the panel): `Claude reframes — never invents experience. Review bullets before sending.`

---

## 5. Cost Warning Strip (minor component)

Above the jobs table, only when last completed run `estimated_cost_usd > 2.00`.

```
┌──────────────────────────────────────────────────────────────────┐
│ ⚠ Last run cost $2.14 — above the $2 target.       [Details] [×]│
└──────────────────────────────────────────────────────────────────┘
```

Dismissible per-session. Click Details → opens `/debug/runs` page with token breakdown.

---

## 6. Voice & Tone

| Context | Tone | Example |
|---------|------|---------|
| Success | Calm, factual | `Run completed · 31 qualified · $0.94` |
| Error (network) | Neutral, actionable | `Couldn't reach pipeline. Retry?` |
| Error (validation) | Direct | `Company name is invalid` |
| Empty | Inviting | `No watchlist companies yet. Add one to highlight their jobs with ★.` |
| Loading | Set expectation | `Claude is tailoring — usually ~8s` |
| Warning | Calibrated (not alarming) | `Last run cost $2.14 — above the $2 target.` |
| TTL ticking | Informational | `TTL: 3:42 remaining` (amber under 30 min) |
| TTL expired | Acknowledging | `Run cancelled — no response in 4h. Re-run when ready.` |
| Source auto-skip | Diagnostic | `LinkedIn skipped after 3 consecutive failures.` |

---

## 7. Error Message Framework

All user-facing errors follow:

- **What happened** (one line, no jargon)
- **What to do** (clear action or button)
- **Reassurance** (only if anxiety-inducing, e.g., TTL expiry)

Technical detail (error codes, stack traces) goes to browser console, not user-facing copy.

Examples applied:

| Raw | User-facing |
|-----|-------------|
| `CLAUDE_API_ERROR: 503` | `Tailoring failed — try again` |
| `CSRF token missing` | (never shown — auto-refetched; retry silently once, then "Please reload the page") |
| `PIPELINE_BUSY` | `Another run is active. Wait for it to finish or cancel it.` |
| `StateSizeExceeded` | `Pipeline state too large. Investigate logs; report if this persists.` |

---

## 8. Accessibility

| Requirement | Standard | Implementation |
|-------------|----------|----------------|
| Keyboard nav | WCAG 2.1 AA | All banner buttons reachable via Tab; shortcuts `A`/`T`/`Esc`/`E` documented in tooltip |
| Focus ring | AA | Use existing Phase 1 focus-ring CSS; do not override |
| Color contrast | 4.5:1 | Amber banner text 4.6:1 against amber-50 bg; cost warning yellow 4.5:1 |
| Screen reader | AA | Banner uses `role="region" aria-label="Pipeline review"`; TTL countdown uses `aria-live="polite"` |
| Motion | prefers-reduced-motion | Spinner disabled when set; replace with static "Working…" text |
| Form labels | AA | Watchlist input has associated `<label>`; slider has aria-valuenow/min/max |

---

## 9. Responsive

Phase 2 target is desktop (local dashboard). Mobile is not supported; no work needed. Banner and sidebar assume ≥ 1024px viewport. Below that, layout degrades gracefully but is not a supported use case.

| Breakpoint | Width | Changes |
|------------|-------|---------|
| Desktop | ≥ 1024px | Primary target — all components in sidebar + main column |
| Tablet | 768–1023px | Sidebar collapses to top; banner full-width |
| Mobile | < 768px | Not supported; show "Best viewed on desktop" notice |

---

## 10. Design Links

| Resource | Link |
|----------|------|
| Phase 1 dashboard (baseline) | `auto_jobsearch/jobsearch/dashboard/` (existing codebase) |
| SPEC (implementation) | [./SPEC.md](./SPEC.md) |
| TECH-DESIGN (contracts) | [./TECH-DESIGN.md](./TECH-DESIGN.md) |
| PRD-CORE (strategy) | [./PRD-CORE.md](./PRD-CORE.md) |

---

## 11. What We Explicitly Did Not Design

| Not designed | Rationale |
|--------------|-----------|
| New color palette / typography | Inheriting Phase 1 |
| Onboarding / first-run wizard | Single user; owner knows the app |
| Dark mode | Phase 1 didn't ship it; out of scope |
| Illustrations / empty-state art | Functional sufficiency; time better spent elsewhere |
| Mobile-optimized layout | Desktop-only local tool |
| Notification center | No notification system in Phase 2 (push is Phase 3) |
| Settings / preferences UI | Configuration stays in `.env`; visible power-user surface |
| Multi-resume picker | Single resume supported in Phase 2 |
