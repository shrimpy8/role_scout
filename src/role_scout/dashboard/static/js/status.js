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
