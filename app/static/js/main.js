/* ============================================================
   SprintIQ — main.js v2.0
   Sidebar toggle, active nav, auto-dismiss alerts, form helpers
   ============================================================ */

(function () {
  'use strict';

  // ── Sidebar toggle ──────────────────────────────────────────
  const wrapper = document.getElementById('wrapper');
  const toggleBtn = document.getElementById('sidebar-toggle-btn');

  if (toggleBtn && wrapper) {
    toggleBtn.addEventListener('click', function () {
      const isMobile = window.innerWidth <= 768;
      if (isMobile) {
        wrapper.classList.toggle('sidebar-open');
      } else {
        wrapper.classList.toggle('sidebar-collapsed');
        const collapsed = wrapper.classList.contains('sidebar-collapsed');
        try {
          localStorage.setItem('siq-sidebar-collapsed', collapsed ? '1' : '0');
        } catch (e) { }
      }
    });

    if (window.innerWidth > 768) {
      try {
        const stored = localStorage.getItem('siq-sidebar-collapsed');
        if (stored === '1') wrapper.classList.add('sidebar-collapsed');
      } catch (e) { }
    }
  }

  // ── Mobile overlay close ────────────────────────────────────
  document.addEventListener('click', function (e) {
    if (window.innerWidth > 768) return;
    const sidebar = document.getElementById('sidebar-wrapper');
    if (sidebar && !sidebar.contains(e.target) && !toggleBtn.contains(e.target)) {
      wrapper.classList.remove('sidebar-open');
    }
  });

  // ── Mark active nav link ────────────────────────────────────
  const currentPath = window.location.pathname;
  let matched = false;
  // First pass: exact matches
  document.querySelectorAll('.sidebar-nav-link').forEach(function (link) {
    const href = link.getAttribute('href');
    if (href && currentPath === href) {
      link.classList.add('active');
      matched = true;
    }
  });
  // Second pass: prefix matches if not root
  if (!matched && currentPath !== '/') {
    document.querySelectorAll('.sidebar-nav-link').forEach(function (link) {
      const href = link.getAttribute('href');
      if (href && href !== '/' && currentPath.startsWith(href)) {
        link.classList.add('active');
      }
    });
  }

  // ── Auto-dismiss flash alerts ───────────────────────────────
  document.querySelectorAll('.alert-dismissible').forEach(function (alert) {
    setTimeout(function () {
      if (alert && alert.parentNode) {
        alert.classList.remove('show');
        setTimeout(function () {
          if (alert.parentNode) alert.remove();
        }, 300);
      }
    }, 6000);
  });

  // ── Prevent double submission on forms ──────────────────────
  document.querySelectorAll('form').forEach(function (form) {
    form.addEventListener('submit', function () {
      const submitBtn = form.querySelector('button[type="submit"], input[type="submit"]');
      if (submitBtn && !submitBtn.disabled) {
        submitBtn.setAttribute('data-original-text', submitBtn.innerHTML || submitBtn.value);
        setTimeout(function () {
          submitBtn.disabled = true;
        }, 50);
      }
    });
  });

})();
