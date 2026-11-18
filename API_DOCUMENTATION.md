# SprintIQ REST API

The JSON API is served by the same Flask application and SQLAlchemy database as
the web application. Dates use `YYYY-MM-DD`; effort and capacity are hours.
Responses use JSON unless the successful response is `204 No Content`.

## Authentication and request security

Authentication uses the existing Flask session cookie; this API does not issue
bearer tokens. Public registration always creates a `team_member`. Only an
administrator can change user roles.

Before sending a state-changing request, including login and registration,
fetch `GET /api/csrf-token` and send its `csrf_token` as the `X-CSRFToken`
header. Keep the session cookie from the same client. JSON write requests must
use `Content-Type: application/json`.

## Error format

Errors have a consistent shape:

```json
{
  "error": {
    "code": "validation_error",
    "message": "A human-readable explanation.",
    "details": {}
  }
}
```

`details` is included when useful. Common statuses are `400` invalid input,
`401` unauthenticated or invalid credentials, `403` forbidden, `404` missing
resource, `409` duplicate/conflicting change, and `500` unexpected server
failure. `details` may be absent.

## Endpoints

| Method | Endpoint | Authentication | Purpose |
|---|---|---|---|
| GET | `/api/csrf-token` | No | Obtain a CSRF token. |
| POST | `/api/auth/register` | No | Register with `username`, `email`, `password`. |
| POST | `/api/auth/login` | No | Sign in with `email`, `password`; establishes session. |
| POST | `/api/auth/logout` | Session | End the current session. |
| GET | `/api/auth/me` | Session | Get the current user. |
| GET | `/api/users` | Administrator | List users. |
| PATCH | `/api/users/{user_id}/role` | Administrator | Set `role` to `administrator`, `project_manager`, or `team_member`. |
| GET | `/api/projects` | Session | List projects. |
| POST | `/api/projects` | Manager | Create a project. |
| GET | `/api/projects/{project_id}` | Session | Get project progress and members. |
| PATCH | `/api/projects/{project_id}` | Owning manager | Update project fields. |
| DELETE | `/api/projects/{project_id}` | Owning manager | Delete project and dependent records. |
| GET | `/api/projects/{project_id}/members` | Session | List project team members. |
| POST | `/api/projects/{project_id}/members` | Owning manager | Add member using `team_member_id`. |
| DELETE | `/api/projects/{project_id}/members/{member_id}` | Owning manager | Remove project membership. |
| GET | `/api/team-members` | Manager | List team members. |
| POST | `/api/team-members` | Manager | Create a team member. |
| PATCH | `/api/team-members/{member_id}` | Manager | Update member details/capacity. |
| DELETE | `/api/team-members/{member_id}` | Manager | Delete member and assignments. |
| GET | `/api/sprints` | Session | List sprints (project managers see their own). |
| POST | `/api/projects/{project_id}/sprints` | Owning manager | Create sprint. |
| GET | `/api/sprints/{sprint_id}` | Session | Get sprint progress and tasks. |
| PATCH | `/api/sprints/{sprint_id}` | Owning manager | Update sprint. |
| DELETE | `/api/sprints/{sprint_id}` | Owning manager | Delete sprint and dependent tasks. |
| GET | `/api/tasks?sprint_id={id}` | Session | List tasks; project managers see their projects. |
| POST | `/api/tasks` | Owning manager | Create task. |
| GET | `/api/tasks/{task_id}` | Session | Get task and assignments. |
| PATCH | `/api/tasks/{task_id}` | Owning manager or assignee | Update task; assignees may update `status` and `actual_hours`. |
| DELETE | `/api/tasks/{task_id}` | Owning manager | Delete task. |
| POST | `/api/tasks/{task_id}/assignments` | Owning manager | Add/update assignment; optional explicit `override`. |
| DELETE | `/api/tasks/{task_id}/assignments/{member_id}` | Owning manager | Remove assignment. |
| GET | `/api/sprints/{sprint_id}/allocations` | Owning manager | Get eligible, ranked recommendations and explanations. |
| POST | `/api/tasks/{task_id}/allocation-rejections` | Owning manager | Reject eligible candidate using `team_member_id`. |
| GET | `/api/sprints/{sprint_id}/workload` | Owning manager | Capacity, utilization, overload, and redistribution suggestions. |
| GET | `/api/risks?project_id={id}` | Manager | Risk summary and explainable alerts; managers are scoped to owned projects. |
| GET | `/api/analytics` | Session | Project/sprint/task statistics and workload totals. |
| GET | `/api/analytics/projects/{project_id}` | Session | Project statistics. |
| GET | `/api/analytics/sprints/{sprint_id}` | Session | Sprint performance. |
| GET | `/api/analytics/developers` | Manager | Descriptive developer effort/task totals, not a productivity score. |

### Request JSON

Project create/update accepts `name`, `description`, `start_date`, `end_date`,
and `status` (`planning`, `active`, `completed`, `archived`). Sprint creation
also requires `name`, `start_date`, and `end_date`; optional fields are
`sprint_goal` and `status` (`planned`, `active`, `completed`).

Team members accept `name`, optional `email` and `skills`, and non-negative
`available_hours` and `daily_capacity`.

Task create requires `sprint_id`, `title`, and `estimated_hours`. Optional
fields are `description`, `required_skills`, `priority` (`low`, `medium`,
`high`, `critical`), `status` (`todo`, `in_progress`, `done`), `due_date`, and
`actual_hours`. Task update accepts a subset of those fields; `sprint_id` can
also be changed by an owning manager. `actual_hours` defaults to `0`. Updating an assigned task's estimate, skills, or
sprint rechecks each assignment; a manager may explicitly include
`"override": true` to approve changes that make existing assignments ineligible.

Assignment request:

```json
{
  "team_member_id": 12,
  "assigned_hours": 6,
  "override": false
}
```

`assigned_hours` defaults to the task estimate. Assignments failing skill or
capacity eligibility return `409` unless an authorized manager explicitly
sets `override: true`. Recommendations are not applied automatically.

## Response examples

Registration and login return `{"user": {"id": 1, "username": "sam",
"email": "sam@example.com", "role": "team_member", "created_at": "..."}}`.

Project, sprint and task endpoints wrap a resource in its matching key, e.g.
`{"project": {...}}`, `{"sprint": {...}}`, or `{"task": {...}}`. List endpoints
return an array under `projects`, `sprints`, `tasks`, or `team_members`.
Task assignment objects include `team_member_id`, `team_member_name`, and
`assigned_hours`.

`GET /api/sprints/{id}/allocations` returns recommendation `score`,
`components` (`skill_match`, `capacity_score`, `availability_score`,
`priority_suitability`), projected utilization, and explanations.
`GET /api/sprints/{id}/workload` returns member capacity/utilization and
recommendations that are informational only.

`GET /api/risks` returns `summary` counts and `risks` with severity,
description, reason, and recommended action. Risk classifications use the
existing configurable deterministic rules; they are not validated forecasts
and do not claim predictive accuracy.

## Frontend integration

Use the same browser session for `GET /api/csrf-token` and subsequent JSON
requests; send the token in `X-CSRFToken` for every unsafe HTTP method. Public
registration grants no manager privileges. A trusted administrator must
promote accounts through the role endpoint.
