(function () {
  'use strict';
  var el = document.getElementById('rs-page-config');
  if (!el) return;
  window.RS_CONFIG = {
    threshold: parseInt(el.getAttribute('data-threshold'), 10) || 70,
    totalJobs: parseInt(el.getAttribute('data-total-jobs'), 10) || 0,
    watchlist: JSON.parse(el.getAttribute('data-watchlist') || '[]'),
    donotapply: JSON.parse(el.getAttribute('data-donotapply') || '[]'),
    donotapplyLocked: JSON.parse(el.getAttribute('data-donotapply-locked') || '[]'),
    csrfToken: (document.querySelector('meta[name="csrf-token"]') || {}).content || '',
  };
})();
