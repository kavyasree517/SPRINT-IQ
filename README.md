# SprintIQ

**Intelligent Agile Sprint Planning and Workload Balancing System**

A Flask-based web application for Agile sprint planning and workload balancing. It includes SQLAlchemy domain models, session-based authentication, and project, sprint, team, and task management with a Kanban board.

## Current features
- Flask application factory and modular blueprint structure
- SQLite database configuration via Flask-SQLAlchemy with additive legacy-user schema upgrades
- Responsive UI with Bootstrap 5
- Health‑check endpoint
- User registration, login, and POST-only logout with hashed passwords and CSRF protection
- Login-protected dashboard and administrator-only placeholder route
- User, project, team member, sprint, task, and task assignment models
- Project CRUD with owner association and owner-aware project-manager permissions
- Sprint CRUD with project/date validation
- Team member CRUD with unique email addresses and optional account linking
- Task CRUD, multi-member assignment, and To Do / In Progress / Done Kanban status updates
- Additive SQLite upgrades for project owners and team member emails; existing records are retained
- Rule-based sprint allocation recommendations with skill matching, capacity limits, workload utilization, manager approval/rejection, and explicit manual overrides
- Database-backed dashboard statistics, project/sprint filters, sprint progress, task status and priority charts, and team workload visualization
- Manager-only sprint risk monitoring for deadlines, overloaded capacity, sprint completion, and task assignment suitability
- Session-authenticated JSON REST API for users, projects, project memberships, team members, sprints, tasks, allocations, workload, risk, and analytics
- Additive project-membership table and actual task-effort tracking for API reporting

## REST API

The JSON API is documented in [API_DOCUMENTATION.md](./API_DOCUMENTATION.md).
It shares the web application's session authentication and database; mutating
requests require a CSRF token from `GET /api/csrf-token` in the
`X-CSRFToken` header. Public API registration grants only the `team_member`
role, and only administrators can change roles. Risk and allocation outputs
reuse the existing deterministic, explainable rules and are not predictive
forecasts.

## Dashboard insights

The authenticated dashboard summarizes projects, sprints, tasks by status, and workspace team members using live database records. Project and sprint filters scope project, sprint, task, and workload results; the team-member total remains workspace-wide. Sprint completion is calculated from completed versus total tasks, with empty sprints safely shown as 0%. Workload uses the Phase 4 capacity calculations aggregated across the selected sprints. Chart.js renders status, priority, sprint-progress, and workload visualizations; empty result sets display explanatory states instead of fabricated sample data.

## Workload allocation

Managers can open **Allocation** or choose **Allocate** from a sprint. The recommendation score is:

```text
0.40 × skill match + 0.30 × capacity score
+ 0.20 × availability score + 0.10 × priority suitability
```

Skills are case-insensitive exact tokens separated by commas, semicolons, or newlines. A partial overlap is scored as the share of required skills that match; no required skills gives a neutral 100 score. A member with no matching required skill is not recommended.

Sprint capacity is the lower of the member's stored available sprint hours and daily capacity multiplied by weekdays in the sprint. Already assigned hours are deducted to find remaining capacity. Normal recommendations require both a skill overlap and sufficient remaining capacity. A manager can explicitly confirm a manual override; ordinary approval rechecks capacity on the server. Availability is derived only from stored capacity fields, with no assumed absences. Allocation rejection records and the added task skill column are additive; existing tasks receive an empty skills value.

Capacity score measures current remaining sprint capacity as a percentage of the member's sprint capacity. Availability score is effective sprint capacity divided by the theoretical weekday capacity. Priority suitability multiplies the skill-match ratio by a priority base score (Low 60, Medium 75, High 90, Critical 100). Workload is overloaded above `ALLOCATION_OVERLOAD_THRESHOLD` (default 100%) and underutilized below `ALLOCATION_UNDERUTILIZED_THRESHOLD` (default 50%); both thresholds can be overridden in Flask configuration.

## Sprint risk monitoring

Administrators can review workspace risks; project managers see risks only for projects they own. The read-only risk dashboard filters by project, sprint, severity, and category. Rules flag unfinished overdue tasks (LOW for 1–2 days, MEDIUM for 3–7, HIGH from 8 days), high/critical tasks due within the configurable 5-day warning window (HIGH due today/within 1 day, MEDIUM within 3 days, otherwise LOW), member allocations exceeding Phase 4 sprint capacity, and active sprints ending within 5 days whose unfinished estimated effort reaches at least 70% of available team capacity. Sprint load severity is LOW from 70%, MEDIUM from 85%, and HIGH from 100%; overload severity is LOW above capacity, MEDIUM from 110%, and HIGH from 125%. Unassigned tasks are distinguished from tasks with no eligible assignee; assignment severity follows task priority (HIGH/Critical → HIGH, Medium → MEDIUM, Low → LOW), and assigned tasks are also flagged when none of their current assignees meet Phase 4 skill and capacity rules.

Risk thresholds are configurable with `RISK_DEADLINE_WARNING_DAYS`, `RISK_DEADLINE_HIGH_DAYS`, `RISK_DEADLINE_MEDIUM_DAYS`, `RISK_OVERDUE_MEDIUM_DAYS`, `RISK_OVERDUE_HIGH_DAYS`, `RISK_OVERLOAD_MEDIUM_PERCENT`, `RISK_OVERLOAD_HIGH_PERCENT`, `RISK_SPRINT_WARNING_DAYS`, and `RISK_SPRINT_LOW_LOAD_RATIO` / `RISK_SPRINT_MEDIUM_LOAD_RATIO` / `RISK_SPRINT_HIGH_LOAD_RATIO`. Risk analysis reads existing records and requires no schema migration.

## Quick start

From the project root, create and activate a virtual environment, then install the dependencies:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Run the test suite with `python -m pytest -q`.

Start the application:

```powershell
python run.py
```

Open `http://127.0.0.1:5000/` in your browser. Register an account at `/auth/register`, then sign in at `/auth/login` to access the dashboard. Public registration assigns the `team_member` role; administrator and project-manager roles must be assigned by a trusted operator. The health-check endpoint is available at `http://127.0.0.1:5000/health`.

The app creates missing tables at startup and applies additive SQLite schema upgrades without dropping existing rows. Administrators and project managers must be assigned by a trusted operator. Administrators can manage all records. Project managers can manage projects they own, including their sprints and tasks, and can manage team members. Authenticated users can view workspace records; linked task assignees can update their task status. Back up the database before deploying schema changes to important data.

## Project structure
```
SprintIQ/
├─ app/
│  ├─ __init__.py          # Application factory
│  ├─ models.py            # SQLAlchemy model definitions
│  ├─ api/                 # Session-authenticated JSON REST API
│  ├─ main/                # Core routes (dashboard, health)
│  │   ├─ __init__.py
│  │   └─ routes.py
│  ├─ management/          # Project, sprint, team, and task workflows
│  │   ├─ forms.py
│  │   └─ routes.py
│  ├─ templates/           # Jinja2 templates
│  └─ static/              # CSS / JS assets
├─ requirements.txt
├─ API_DOCUMENTATION.md
├─ config.py
├─ run.py
└─ .gitignore
```

## License
MIT. Feel free to adapt for your capstone project.
