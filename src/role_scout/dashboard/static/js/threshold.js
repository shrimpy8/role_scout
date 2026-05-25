(function () {
  'use strict';

  // Statuses where the threshold filter should not apply — the user has already
  // reviewed these jobs and the score cutoff is no longer meaningful.
  var _SKIP_THRESHOLD = { reviewed: 1, applied: 1, rejected: 1, not_a_fit: 1, not_available: 1, history: 1 };
  // Sources that bypass threshold — manually ingested jobs are user-chosen regardless of score.
  var _SKIP_THRESHOLD_SOURCE = { manual: 1 };

  function updateFilter(value) {
    var activeStatus = (window.RS_CONFIG && window.RS_CONFIG.activeStatus) || 'new';
    var activeSource = (window.RS_CONFIG && window.RS_CONFIG.activeSource) || '';
    // Skip threshold when status doesn't need it, a source is selected, or job is manual.
    var skipThreshold = !!_SKIP_THRESHOLD[activeStatus] || !!activeSource;
    var rows = document.querySelectorAll('#jobs-table tbody tr.job-row[data-match-pct]');
    var visible = 0;
    var total = 0;
    rows.forEach(function (row) {
      var pct = parseInt(row.dataset.matchPct, 10);
      total++;
      var show = skipThreshold || !!_SKIP_THRESHOLD_SOURCE[row.dataset.source] || pct >= value;
      row.hidden = !show;
      // Also hide paired expand row
      var hashId = row.dataset.hashId;
      if (hashId) {
        var expandRow = document.getElementById('expand-' + hashId);
        if (expandRow && expandRow.classList.contains('open')) {
          expandRow.hidden = !show;
        }
      }
      if (show) visible++;
    });
    var countEl = document.getElementById('threshold-count');
    if (countEl) countEl.textContent = 'Showing ' + visible + ' of ' + total;
    var noMatch = document.getElementById('no-match-msg');
    if (noMatch) noMatch.hidden = visible > 0;
  }

  function init() {
    var slider = document.getElementById('threshold-slider');
    var valEl = document.getElementById('threshold-val');
    if (!slider) return;

    var initial = parseInt(slider.value, 10);
    updateFilter(initial);

    slider.addEventListener('input', function () {
      var v = parseInt(this.value, 10);
      if (valEl) valEl.textContent = v;
      slider.setAttribute('aria-valuenow', v);
      updateFilter(v);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
