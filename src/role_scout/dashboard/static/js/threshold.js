(function () {
  'use strict';

  function updateFilter(value) {
    const rows = document.querySelectorAll('#jobs-table tbody tr[data-match-pct]');
    let visible = 0;
    let total = 0;
    rows.forEach(function (row) {
      const pct = parseInt(row.dataset.matchPct, 10);
      total++;
      const show = pct >= value;
      row.hidden = !show;
      // Also hide paired tailor panel row
      const hashId = row.dataset.hashId;
      if (hashId) {
        const panelRow = document.querySelector(`tr.tailor-panel-row[data-for="${hashId}"]`);
        if (panelRow && !panelRow.classList.contains('d-none')) {
          panelRow.hidden = !show;
        }
      }
      if (show) visible++;
    });
    const countEl = document.getElementById('threshold-count');
    if (countEl) countEl.textContent = `Showing ${visible} of ${total} jobs`;
    const noMatch = document.getElementById('no-match-msg');
    if (noMatch) noMatch.hidden = visible > 0;
  }

  function init() {
    const slider = document.getElementById('threshold-slider');
    const valEl = document.getElementById('threshold-val');
    if (!slider) return;

    const initial = parseInt(slider.value, 10);
    updateFilter(initial);

    slider.addEventListener('input', function () {
      const v = parseInt(this.value, 10);
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
