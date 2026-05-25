(function () {
  'use strict';

  function csrfToken() {
    var el = document.querySelector('meta[name="csrf-token"]');
    return el ? el.content : '';
  }

  function flashSelect(select, cssClass, durationMs) {
    select.classList.add(cssClass);
    setTimeout(function () { select.classList.remove(cssClass); }, durationMs);
  }

  // Statuses counted under the "all active" and "history" aggregates
  var _ALL_ACTIVE   = { new: 1, reviewed: 1 };
  var _HISTORY      = { applied: 1, rejected: 1, not_a_fit: 1, not_available: 1 };

  function adjustSidebarCount(status, delta) {
    var link = document.querySelector('.sb-filter[data-status="' + status + '"]');
    if (!link) return;
    var span = link.querySelector('.sb-count');
    if (!span) return;
    span.textContent = Math.max(0, (parseInt(span.textContent, 10) || 0) + delta);
  }

  function adjustAggregates(oldStatus, newStatus) {
    // "all active" = new + reviewed
    var allDelta = (_ALL_ACTIVE[newStatus] ? 1 : 0) - (_ALL_ACTIVE[oldStatus] ? 1 : 0);
    if (allDelta) adjustSidebarCount('all', allDelta);
    // "history" = applied + rejected + not_a_fit + not_available
    var histDelta = (_HISTORY[newStatus] ? 1 : 0) - (_HISTORY[oldStatus] ? 1 : 0);
    if (histDelta) adjustSidebarCount('history', histDelta);
  }

  function adjustFooterCount(status, delta) {
    var span = document.querySelector('[data-footer-status="' + status + '"]');
    if (!span) return;
    var m = span.textContent.match(/^(\d+)\s+(.+)$/);
    if (!m) return;
    span.textContent = Math.max(0, (parseInt(m[1], 10) || 0) + delta) + ' ' + m[2];
  }

  async function updateStatus(select) {
    var hashId = select.getAttribute('data-hash-id');
    var newStatus = select.value;
    var previous = select.getAttribute('data-previous');

    try {
      var resp = await fetch('/api/status/' + hashId, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrfToken(),
        },
        body: JSON.stringify({ status: newStatus }),
      });
      if (!resp.ok) {
        throw new Error('HTTP ' + resp.status);
      }
      select.setAttribute('data-previous', newStatus);
      flashSelect(select, 'success', 800);
      // Adjust sidebar counts without a page reload
      if (previous !== newStatus) {
        adjustSidebarCount(previous, -1);
        adjustSidebarCount(newStatus, +1);
        adjustAggregates(previous, newStatus);
        adjustFooterCount(previous, -1);
        adjustFooterCount(newStatus, +1);
      }
    } catch (_) {
      select.value = previous;
      flashSelect(select, 'error', 1200);
    }
  }

  function init() {
    document.addEventListener('change', function (e) {
      if (e.target.classList.contains('status-select')) {
        updateStatus(e.target);
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
