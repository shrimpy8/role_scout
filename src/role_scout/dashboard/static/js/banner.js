(function () {
  'use strict';

  function escHtml(str) {
    return String(str ?? '').replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  const POLL_MS = 5000;
  let pollTimer = null;
  let extended = false;
  let lastKnownRevision = null;
  let cancelledRunId = null;  // run_id the user already cancelled this session

  function csrfToken() {
    const el = document.querySelector('meta[name="csrf-token"]');
    return el ? el.content : '';
  }

  function formatTTL(seconds) {
    if (seconds == null) return '';
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    if (h > 0) return `${h}h ${m}m remaining`;
    if (m > 0) return `${m}:${String(s).padStart(2, '0')} remaining`;
    return `${s}s remaining`;
  }

  function ttlClass(seconds) {
    return (seconds != null && seconds < 1800) ? 'ttl-warning' : '';
  }

  function renderBanner(data) {
    const el = document.getElementById('rs-banner');
    if (!el) return;

    if (data.status !== 'review_pending' || data.run_id === cancelledRunId) {
      if (data.run_id === cancelledRunId) return; // keep the grey cancelled message visible
      el.innerHTML = '';
      el.hidden = true;
      // Show cancelled toast if applicable
      if (data.status === 'failed' && data.cancel_reason === 'ttl_expired') {
        el.innerHTML = '<div class="alert banner-cancelled mb-3">Run cancelled — no response in 4h. Re-run when ready.</div>';
        el.hidden = false;
      }
      return;
    }

    const ttlSecs = data.ttl_remaining_s;
    const ttlText = formatTTL(ttlSecs);
    const ttlCls = ttlClass(ttlSecs);
    const cost = data.estimated_cost_usd != null ? `$${data.estimated_cost_usd.toFixed(2)}` : '—';
    const qualCount = data.qualified_count ?? 0;
    const extendedLabel = (data.ttl_extended || extended) ? 'Extended' : '+2h TTL';
    const extendedDisabled = (data.ttl_extended || extended) ? 'disabled' : '';

    let top3Html = '';
    if (data.top_3_matches && data.top_3_matches.length > 0) {
      const top3Text = data.top_3_matches.map(j => `${escHtml(j.company)} (${j.match_pct}%)`).join(' · ');
      top3Html = `<div class="mt-1 small text-muted">Top: ${top3Text}</div>`;
    }

    let healthHtml = '';
    if (data.source_health && data.source_health.length > 0) {
      const healthText = data.source_health.map(s => {
        const ok = s.status === 'ok' || s.status === 'quota_low';
        return ok ? `${escHtml(s.name)} ✓` : `${escHtml(s.name)} ⚠`;
      }).join(' ');
      healthHtml = `<div class="mt-1 small text-muted">Source health: ${healthText}</div>`;
    }

    el.hidden = false;
    el.innerHTML = `
      <div class="banner-review p-3 mb-3 rounded">
        <div class="d-flex justify-content-between align-items-start">
          <div>
            <strong>⏸ Pipeline paused for review</strong>
            <span class="text-muted ms-2">Run ${escHtml((data.run_id || '').slice(0, 8))}</span>
            <span class="ms-2 ${ttlCls}" aria-live="polite">${ttlText ? '· TTL: ' + ttlText : ''}</span>
          </div>
        </div>
        <div class="mt-2 small">
          Qualified ≥${window.RS_CONFIG?.threshold ?? 85}%: <strong>${qualCount}</strong>
          · Cost so far: ${cost}
        </div>
        ${top3Html}
        ${healthHtml}
        <div class="mt-3 d-flex gap-2">
          <button id="banner-approve" class="btn btn-sm btn-success" ${qualCount === 0 ? 'disabled title="Nothing to export"' : ''}>
            Approve &amp; Export
          </button>
          <button id="banner-cancel" class="btn btn-sm btn-outline-secondary">Cancel</button>
          <button id="banner-extend" class="btn btn-sm btn-outline-warning" ${extendedDisabled}>${extendedLabel}</button>
        </div>
        <div class="mt-2 small text-muted">Keyboard: <kbd>A</kbd> Approve · <kbd>E</kbd> Extend · <kbd>Esc</kbd> Cancel</div>
      </div>`;

    document.getElementById('banner-approve')?.addEventListener('click', () => handleDecision(true));
    document.getElementById('banner-cancel')?.addEventListener('click', () => handleDecision(false));
    document.getElementById('banner-extend')?.addEventListener('click', handleExtend);
  }

  async function handleDecision(approved) {
    const el = document.getElementById('rs-banner');
    if (el) el.querySelectorAll('button').forEach(b => b.disabled = true);
    try {
      const resp = await fetch('/api/pipeline/resume', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
        body: JSON.stringify({ approved }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      if (!approved && el) {
        cancelledRunId = data.run_id || null;
        el.innerHTML = '<div class="alert banner-cancelled mb-3">Run cancelled. Re-run when ready.</div>';
      }
    } catch (e) {
      if (el) {
        el.innerHTML += `<div class="alert alert-danger mt-2">Couldn't reach pipeline. <button onclick="location.reload()">Retry</button></div>`;
      }
    }
  }

  async function handleExtend() {
    try {
      const resp = await fetch('/api/pipeline/extend', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
        body: '{}',
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      extended = true;
      const btn = document.getElementById('banner-extend');
      if (btn) { btn.disabled = true; btn.textContent = 'Extended'; }
    } catch (e) {
      console.error('Extend failed:', e);
    }
  }

  function showCostWarning(cost) {
    if (sessionStorage.getItem('costDismissed')) return;
    const el = document.getElementById('rs-cost-warning');
    if (!el) return;
    if (cost > 2.0) {
      el.hidden = false;
      el.innerHTML = `<div class="cost-warning p-2 mb-3 rounded small">
        ⚠ Last run cost $${cost.toFixed(2)} — above the $2 target.
        <a href="/debug/runs" class="btn btn-sm btn-outline-secondary ms-2">Details</a>
        <button class="btn-close btn-sm float-end" onclick="sessionStorage.setItem('costDismissed','1');this.parentElement.parentElement.hidden=true"></button>
      </div>`;
    }
  }

  async function poll() {
    try {
      const resp = await fetch('/api/pipeline/status');
      if (!resp.ok) return;
      const data = await resp.json();
      renderBanner(data);
      if (data.estimated_cost_usd > 2.0) showCostWarning(data.estimated_cost_usd);

      // Watchlist revision tracking — notify watchlist.js to re-render if changed
      if (data.watchlist_revision != null) {
        if (lastKnownRevision !== null && data.watchlist_revision !== lastKnownRevision) {
          if (typeof window.rsRefreshWatchlist === 'function') {
            window.rsRefreshWatchlist();
          }
        }
        lastKnownRevision = data.watchlist_revision;
      }
    } catch (e) {
      // silent — network failure during poll
    }
  }

  function startPolling() {
    poll();
    pollTimer = setInterval(poll, POLL_MS);
  }

  // Keyboard shortcuts
  document.addEventListener('keydown', function (e) {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    if (e.key === 'a' || e.key === 'A') {
      const btn = document.getElementById('banner-approve');
      if (btn && !btn.disabled) btn.click();
    } else if (e.key === 'Escape') {
      const btn = document.getElementById('banner-cancel');
      if (btn) btn.click();
    } else if (e.key === 'e' || e.key === 'E') {
      const btn = document.getElementById('banner-extend');
      if (btn && !btn.disabled) btn.click();
    }
  });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', startPolling);
  } else {
    startPolling();
  }
})();
