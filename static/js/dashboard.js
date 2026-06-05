// Phase D dashboard — tab switcher.
// Theme toggle, sortable tables, modal logic land in subsequent D commits.

(function () {
  'use strict';

  const tabs   = Array.from(document.querySelectorAll('.tab-nav [role="tab"]'));
  const panels = Array.from(document.querySelectorAll('[role="tabpanel"]'));

  function activate(tabId) {
    tabs.forEach(t => {
      const isActive = t.dataset.tab === tabId;
      t.classList.toggle('active', isActive);
      t.setAttribute('aria-selected', isActive ? 'true' : 'false');
    });
    panels.forEach(p => {
      p.hidden = p.id !== `tab-${tabId}`;
    });
  }

  tabs.forEach(tab => {
    tab.addEventListener('click', () => activate(tab.dataset.tab));
  });

  // Keyboard arrow nav (tablist convention)
  document.querySelector('.tab-nav')?.addEventListener('keydown', (e) => {
    const idx = tabs.indexOf(document.activeElement);
    if (idx === -1) return;
    if (e.key === 'ArrowRight') {
      e.preventDefault();
      const next = tabs[(idx + 1) % tabs.length];
      next.focus();
      activate(next.dataset.tab);
    } else if (e.key === 'ArrowLeft') {
      e.preventDefault();
      const prev = tabs[(idx - 1 + tabs.length) % tabs.length];
      prev.focus();
      activate(prev.dataset.tab);
    }
  });
})();
