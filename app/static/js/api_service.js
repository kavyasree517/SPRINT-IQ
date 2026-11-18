/**
 * SprintIQ — Centralized API Service Layer
 * Coordinates frontend modules with backend REST endpoints.
 * Includes graceful mock data fallback when backend endpoints are still under development.
 */

(function (root, factory) {
  if (typeof define === 'function' && define.amd) {
    define([], factory);
  } else if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.SprintIQ_API = factory();
  }
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  // ═════════════════════════════════════════════════════════════
  // CONFIGURATION & CSRF
  // ═════════════════════════════════════════════════════════════
  const API_BASE = '/api';
  const USE_MOCK_FALLBACK = true;

  function getCsrfToken() {
    const metaTag = document.querySelector('meta[name="csrf-token"]');
    if (metaTag) return metaTag.getAttribute('content');
    const inputTag = document.querySelector('input[name="csrf_token"]');
    if (inputTag) return inputTag.value;
    return '';
  }

  // ═════════════════════════════════════════════════════════════
  // MOCK DATA STORE (Kept completely separate from real services)
  // ═════════════════════════════════════════════════════════════
  const MockDataStore = {
    projects: [
      { id: 1, name: 'SprintIQ Core Platform', description: 'Intelligent agile management system', start_date: '2026-01-01', end_date: '2026-06-30', status: 'active', owner: 'Product Lead' },
      { id: 2, name: 'Mobile Companion App', description: 'Flutter-based task companion for developers', start_date: '2026-02-15', end_date: '2026-08-31', status: 'planning', owner: 'Mobile Lead' }
    ],
    sprints: [
      { id: 1, project_id: 1, name: 'Sprint 14: AI Engine', start_date: '2026-04-01', end_date: '2026-04-14', status: 'active', goal: 'Ship intelligent allocation algorithm' },
      { id: 2, project_id: 1, name: 'Sprint 15: Risk Analytics', start_date: '2026-04-15', end_date: '2026-04-28', status: 'planned', goal: 'Enhance automated risk monitoring' }
    ],
    teamMembers: [
      { id: 1, name: 'Sarah Chen', email: 'sarah@sprintiq.internal', skills: 'Python, ML, Flask, PostgreSQL', available_hours: 40, daily_capacity: 8, utilization: 85, status: 'balanced' },
      { id: 2, name: 'Devon Vance', email: 'devon@sprintiq.internal', skills: 'JavaScript, UI/UX, CSS, Vue, React', available_hours: 35, daily_capacity: 7, utilization: 110, status: 'overloaded' },
      { id: 3, name: 'Amara Okafor', email: 'amara@sprintiq.internal', skills: 'Docker, CI/CD, DevOps, Kubernetes', available_hours: 40, daily_capacity: 8, utilization: 45, status: 'underutilized' }
    ],
    tasks: [
      { id: 1, sprint_id: 1, title: 'Build task recommendation pipeline', estimated_hours: 8, priority: 'critical', status: 'in_progress', due_date: '2026-04-08', required_skills: 'Python, ML', assignees: ['Sarah Chen'] },
      { id: 2, sprint_id: 1, title: 'Implement dynamic burndown chart', estimated_hours: 5, priority: 'high', status: 'todo', due_date: '2026-04-10', required_skills: 'JavaScript, UI/UX', assignees: [] },
      { id: 3, sprint_id: 1, title: 'Setup automated health probe', estimated_hours: 3, priority: 'medium', status: 'done', due_date: '2026-04-04', required_skills: 'Docker, CI/CD', assignees: ['Amara Okafor'] }
    ],
    recommendations: [
      {
        member_id: 1,
        member_name: 'Sarah Chen',
        score: 92.5,
        skill_match: 100.0,
        capacity_score: 85.0,
        availability_score: 90.0,
        priority_suitability: 95.0,
        explanations: [
          'Direct match for required skill: Python',
          'Sufficient remaining capacity in this sprint (14h available)',
          'High priority alignment with historical velocity'
        ]
      }
    ]
  };

  // ═════════════════════════════════════════════════════════════
  // GENERIC HTTP REQUEST WRAPPER
  // ═════════════════════════════════════════════════════════════
  async function request(endpoint, options = {}, mockFallbackFn = null) {
    const url = `${API_BASE}${endpoint}`;
    const headers = {
      'Content-Type': 'application/json',
      'X-CSRFToken': getCsrfToken(),
      'X-Requested-With': 'XMLHttpRequest',
      ...(options.headers || {})
    };

    try {
      const response = await fetch(url, { ...options, headers });
      if (response.ok) {
        return await response.json();
      }
      // If endpoint returns 404 or 501 and fallback is allowed:
      if ((response.status === 404 || response.status === 501) && USE_MOCK_FALLBACK && mockFallbackFn) {
        console.warn(`[SprintIQ API] Endpoint ${url} returned ${response.status}. Using mock fallback.`);
        return await mockFallbackFn();
      }
      const errBody = await response.json().catch(() => ({}));
      throw new Error(errBody.message || `HTTP ${response.status}: ${response.statusText}`);
    } catch (err) {
      if (USE_MOCK_FALLBACK && mockFallbackFn) {
        console.warn(`[SprintIQ API] Network error on ${url}. Using mock fallback.`, err);
        return await mockFallbackFn();
      }
      throw err;
    }
  }

  // ═════════════════════════════════════════════════════════════
  // SERVICE MODULES
  // ═════════════════════════════════════════════════════════════

  const Projects = {
    getAll: () => request('/projects', { method: 'GET' }, () => MockDataStore.projects),
    getById: (id) => request(`/projects/${id}`, { method: 'GET' }, () => MockDataStore.projects.find(p => p.id === Number(id))),
    create: (payload) => request('/projects', { method: 'POST', body: JSON.stringify(payload) }),
    update: (id, payload) => request(`/projects/${id}`, { method: 'PUT', body: JSON.stringify(payload) }),
    delete: (id) => request(`/projects/${id}`, { method: 'DELETE' })
  };

  const Sprints = {
    getAll: (projectId) => request(projectId ? `/sprints?project_id=${projectId}` : '/sprints', { method: 'GET' }, () => {
      return projectId ? MockDataStore.sprints.filter(s => s.project_id === Number(projectId)) : MockDataStore.sprints;
    }),
    getById: (id) => request(`/sprints/${id}`, { method: 'GET' }, () => MockDataStore.sprints.find(s => s.id === Number(id))),
    create: (payload) => request('/sprints', { method: 'POST', body: JSON.stringify(payload) }),
    update: (id, payload) => request(`/sprints/${id}`, { method: 'PUT', body: JSON.stringify(payload) }),
    delete: (id) => request(`/sprints/${id}`, { method: 'DELETE' })
  };

  const Tasks = {
    getAll: (sprintId) => request(sprintId ? `/tasks?sprint_id=${sprintId}` : '/tasks', { method: 'GET' }, () => {
      return sprintId ? MockDataStore.tasks.filter(t => t.sprint_id === Number(sprintId)) : MockDataStore.tasks;
    }),
    getById: (id) => request(`/tasks/${id}`, { method: 'GET' }, () => MockDataStore.tasks.find(t => t.id === Number(id))),
    create: (payload) => request('/tasks', { method: 'POST', body: JSON.stringify(payload) }),
    update: (id, payload) => request(`/tasks/${id}`, { method: 'PUT', body: JSON.stringify(payload) }),
    updateStatus: (id, status) => request(`/tasks/${id}/status`, { method: 'PATCH', body: JSON.stringify({ status }) }),
    delete: (id) => request(`/tasks/${id}`, { method: 'DELETE' })
  };

  const Team = {
    getAll: () => request('/team', { method: 'GET' }, () => MockDataStore.teamMembers),
    getById: (id) => request(`/team/${id}`, { method: 'GET' }, () => MockDataStore.teamMembers.find(m => m.id === Number(id))),
    create: (payload) => request('/team', { method: 'POST', body: JSON.stringify(payload) }),
    update: (id, payload) => request(`/team/${id}`, { method: 'PUT', body: JSON.stringify(payload) }),
    delete: (id) => request(`/team/${id}`, { method: 'DELETE' })
  };

  const Allocation = {
    getRecommendations: (taskId, sprintId) => request(
      `/allocations/recommendations?task_id=${taskId}&sprint_id=${sprintId}`,
      { method: 'GET' },
      () => MockDataStore.recommendations
    ),
    approve: (taskId, memberId) => request(`/allocations/${taskId}/accept/${memberId}`, { method: 'POST' }),
    reject: (taskId, memberId) => request(`/allocations/${taskId}/reject/${memberId}`, { method: 'POST' }),
    override: (taskId, payload) => request(`/allocations/${taskId}/override`, { method: 'POST', body: JSON.stringify(payload) })
  };

  const Workload = {
    getOverview: (sprintId) => request(
      sprintId ? `/workload?sprint_id=${sprintId}` : '/workload',
      { method: 'GET' },
      () => MockDataStore.teamMembers.map(m => ({
        member: m,
        capacity: m.available_hours,
        allocated: m.available_hours * (m.utilization / 100),
        remaining: m.available_hours * (1 - m.utilization / 100),
        utilization: m.utilization,
        classification: m.status
      }))
    )
  };

  const Risks = {
    getAll: (params = {}) => {
      const q = new URLSearchParams(params).toString();
      return request(`/risks${q ? '?' + q : ''}`, { method: 'GET' }, () => []);
    }
  };

  const Analytics = {
    getDashboard: (params = {}) => {
      const q = new URLSearchParams(params).toString();
      return request(`/analytics${q ? '?' + q : ''}`, { method: 'GET' }, () => ({
        completion_rate: 65,
        total_hours_allocated: 42,
        avg_utilization: 80,
        total_tasks_done: 18
      }));
    }
  };

  // Toast UI notification utility
  function notify(message, type = 'info') {
    const flashContainer = document.querySelector('.flash-container') || document.querySelector('.main-content');
    if (!flashContainer) return;
    const alert = document.createElement('div');
    alert.className = `alert alert-${type === 'error' ? 'danger' : type} alert-dismissible fade show`;
    alert.setAttribute('role', 'alert');
    alert.innerHTML = `
      <i class="bi bi-${type === 'success' ? 'check-circle' : type === 'danger' || type === 'error' ? 'x-circle' : type === 'warning' ? 'exclamation-triangle' : 'info-circle'} me-2"></i>
      ${message}
      <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
    `;
    flashContainer.prepend(alert);
    setTimeout(() => {
      if (alert.parentNode) {
        alert.classList.remove('show');
        setTimeout(() => alert.remove(), 250);
      }
    }, 4500);
  }

  return {
    Projects,
    Sprints,
    Tasks,
    Team,
    Allocation,
    Workload,
    Risks,
    Analytics,
    notify
  };
});
