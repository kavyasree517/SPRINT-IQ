/* ============================================================
   SprintIQ — analytics.js
   Charts for the Analytics page
   ============================================================ */

(() => {
  const dataEl = document.getElementById('analytics-data');
  if (!dataEl) return;

  let data;
  try {
    data = JSON.parse(dataEl.textContent);
  } catch (e) {
    console.error('Failed to parse analytics data', e);
    return;
  }

  if (typeof window.Chart !== 'function') return;

  const colors = {
    violet: '#7c3aed',
    cyan: '#06b6d4',
    green: '#10b981',
    amber: '#f59e0b',
    red: '#ef4444',
    blue: '#3b82f6',
    muted: '#9ca3af',
  };
  const gridColor = 'rgba(107, 114, 128, 0.1)';
  const legendOpts = { position: 'bottom', labels: { usePointStyle: true, padding: 16, font: { size: 12 } } };

  const mk = (id, cfg) => {
    const el = document.getElementById(id);
    if (el) new window.Chart(el, cfg);
  };

  // ── Sprint completion bar chart
  if (data.sprint_labels && data.sprint_labels.length) {
    mk('sprint-completion-chart', {
      type: 'bar',
      data: {
        labels: data.sprint_labels,
        datasets: [
          {
            label: 'Completion %',
            data: data.sprint_completion,
            backgroundColor: colors.green,
            borderRadius: 6,
            maxBarThickness: 40,
          },
          {
            label: 'Remaining %',
            data: data.sprint_remaining,
            backgroundColor: colors.muted,
            borderRadius: 6,
            maxBarThickness: 40,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: legendOpts },
        scales: {
          x: { stacked: true, grid: { display: false } },
          y: {
            stacked: true,
            beginAtZero: true,
            max: 100,
            ticks: { callback: v => v + '%' },
            grid: { color: gridColor },
          },
        },
      },
    });
  }

  // ── Priority doughnut
  if (data.priority_labels && data.priority_labels.length) {
    mk('priority-completion-chart', {
      type: 'doughnut',
      data: {
        labels: data.priority_labels,
        datasets: [{
          data: data.priority_values,
          backgroundColor: [colors.red, colors.amber, colors.blue, colors.muted],
          borderWidth: 0,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: '62%',
        plugins: { legend: legendOpts },
      },
    });
  }

  // ── Utilization horizontal bar
  if (data.member_labels && data.member_labels.length) {
    mk('utilization-chart', {
      type: 'bar',
      data: {
        labels: data.member_labels,
        datasets: [
          {
            label: 'Utilization %',
            data: data.member_utilization,
            backgroundColor: data.member_utilization.map(v => v > 100 ? colors.red : v < 50 ? colors.cyan : colors.green),
            borderRadius: 5,
            maxBarThickness: 28,
          },
        ],
      },
      options: {
        indexAxis: 'y',
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: ctx => ctx.raw + '% utilization' } },
        },
        scales: {
          x: {
            beginAtZero: true,
            ticks: { callback: v => v + '%' },
            grid: { color: gridColor },
          },
          y: { grid: { display: false } },
        },
      },
    });
  }

  // ── Status breakdown horizontal bar
  if (data.status_labels && data.status_labels.length) {
    mk('status-breakdown-chart', {
      type: 'bar',
      data: {
        labels: data.status_labels,
        datasets: [{
          label: 'Tasks',
          data: data.status_values,
          backgroundColor: [colors.muted, colors.blue, colors.green],
          borderRadius: 6,
          maxBarThickness: 48,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { grid: { display: false } },
          y: {
            beginAtZero: true,
            ticks: { precision: 0 },
            grid: { color: gridColor },
          },
        },
      },
    });
  }
})();
