import math
from datetime import date
from functools import wraps

from email_validator import EmailNotValidError, validate_email
from flask import Blueprint, abort, current_app, jsonify, request
from flask_login import current_user, login_user, logout_user
from flask_wtf.csrf import generate_csrf
from sqlalchemy.exc import IntegrityError

from .. import db
from ..main.dashboard_data import build_dashboard_data
from ..management.allocation import (
    aggregate_member_workloads,
    candidate_score,
    member_workload,
    ranked_recommendations,
)
from ..management.risk_analysis import build_risk_alerts
from ..models import (
    AllocationRejection,
    Project,
    ProjectMember,
    Sprint,
    Task,
    TaskAssignment,
    TeamMember,
    User,
)


api_bp = Blueprint("api", __name__, url_prefix="/api")
USER_ROLES = {"administrator", "project_manager", "team_member"}
PROJECT_STATUSES = {"planning", "active", "completed", "archived"}
SPRINT_STATUSES = {"planned", "active", "completed"}
TASK_STATUSES = {"todo", "in_progress", "done"}
TASK_PRIORITIES = {"low", "medium", "high", "critical"}


def _error(code, message, status, details=None):
    error = {"code": code, "message": message}
    if details:
        error["details"] = details
    return jsonify({"error": error}), status


def _login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            return _error("authentication_required", "Sign in to access this resource.", 401)
        return view(*args, **kwargs)

    return wrapped


def _manager_required(view):
    @wraps(view)
    @_login_required
    def wrapped(*args, **kwargs):
        if current_user.role not in {"administrator", "project_manager"}:
            abort(403, description="Administrator or project-manager access is required.")
        return view(*args, **kwargs)

    return wrapped


def _admin_required(view):
    @wraps(view)
    @_login_required
    def wrapped(*args, **kwargs):
        if current_user.role != "administrator":
            abort(403, description="Administrator access is required.")
        return view(*args, **kwargs)

    return wrapped


def _body():
    if not request.is_json:
        abort(400, description="Content-Type must be application/json.")
    data = request.get_json(silent=False)
    if not isinstance(data, dict):
        abort(400, description="The JSON request body must be an object.")
    return data


def _only_fields(data, allowed):
    unknown = sorted(set(data) - set(allowed))
    if unknown:
        abort(400, description=f"Unknown field(s): {', '.join(unknown)}.")


def _text(data, key, *, required=False, maximum=None, default=None):
    value = data.get(key, default)
    if value is None:
        if required:
            abort(400, description=f"{key} is required.")
        return None
    if not isinstance(value, str):
        abort(400, description=f"{key} must be a string.")
    value = value.strip()
    if required and not value:
        abort(400, description=f"{key} is required.")
    if maximum is not None and len(value) > maximum:
        abort(400, description=f"{key} must be at most {maximum} characters.")
    return value


def _password(data, *, required=True):
    value = data.get("password")
    if not isinstance(value, str) or (required and not value):
        abort(400, description="password is required and must be a string.")
    return value


def _number(data, key, *, required=False, default=None, minimum=0):
    value = data.get(key, default)
    if value is None and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        abort(400, description=f"{key} must be a number.")
    value = float(value)
    if not math.isfinite(value) or value < minimum:
        abort(400, description=f"{key} must be finite and at least {minimum}.")
    return value


def _date(data, key, *, required=False, default=None):
    value = data.get(key, default)
    if value is None and not required:
        return None
    if not isinstance(value, str):
        abort(400, description=f"{key} must be an ISO 8601 date (YYYY-MM-DD).")
    try:
        return date.fromisoformat(value)
    except ValueError:
        abort(400, description=f"{key} must be an ISO 8601 date (YYYY-MM-DD).")


def _enum(data, key, allowed, *, required=False, default=None):
    value = data.get(key, default)
    if value is None and not required:
        return None
    if not isinstance(value, str) or value not in allowed:
        abort(400, description=f"{key} must be one of: {', '.join(sorted(allowed))}.")
    return value


def _project(project_id):
    project = db.session.get(Project, project_id)
    if project is None:
        abort(404, description="Project not found.")
    return project


def _sprint(sprint_id):
    sprint = db.session.get(Sprint, sprint_id)
    if sprint is None:
        abort(404, description="Sprint not found.")
    return sprint


def _task(task_id):
    task = db.session.get(Task, task_id)
    if task is None:
        abort(404, description="Task not found.")
    return task


def _member(member_id):
    member = db.session.get(TeamMember, member_id)
    if member is None:
        abort(404, description="Team member not found.")
    return member


def _can_manage(project):
    return current_user.role == "administrator" or (
        current_user.role == "project_manager" and project.owner_id == current_user.id
    )


def _check_project_manager(project):
    if not _can_manage(project):
        abort(403, description="You cannot manage this project.")


def _iso(value):
    return value.isoformat() if value is not None else None


def _user_json(user):
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "role": user.role,
        "created_at": _iso(user.created_at),
    }


def _member_json(member, include_email=False):
    result = {
        "id": member.id,
        "user_id": member.user_id,
        "name": member.name,
        "skills": member.skills or "",
        "available_hours": member.available_hours,
        "daily_capacity": member.daily_capacity,
    }
    if include_email:
        result["email"] = member.email
    return result


def _project_json(project, include_members=False):
    total = sum(len(sprint.tasks) for sprint in project.sprints)
    completed = sum(
        task.status == "done" for sprint in project.sprints for task in sprint.tasks
    )
    result = {
        "id": project.id,
        "owner_id": project.owner_id,
        "name": project.name,
        "description": project.description,
        "start_date": _iso(project.start_date),
        "end_date": _iso(project.end_date),
        "status": project.status,
        "sprint_count": len(project.sprints),
        "task_count": total,
        "completed_task_count": completed,
        "completion_percent": round(100 * completed / total, 2) if total else 0.0,
    }
    if include_members:
        result["members"] = [
            _member_json(link.team_member)
            for link in sorted(
                project.memberships, key=lambda link: link.team_member.name.casefold()
            )
        ]
    return result


def _sprint_json(sprint, include_tasks=False):
    tasks = sprint.tasks
    completed = sum(task.status == "done" for task in tasks)
    result = {
        "id": sprint.id,
        "project_id": sprint.project_id,
        "name": sprint.name,
        "sprint_goal": sprint.sprint_goal,
        "start_date": _iso(sprint.start_date),
        "end_date": _iso(sprint.end_date),
        "status": sprint.status,
        "task_count": len(tasks),
        "completed_task_count": completed,
        "remaining_task_count": len(tasks) - completed,
        "remaining_effort_hours": round(
            sum(task.estimated_hours for task in tasks if task.status != "done"), 2
        ),
        "completion_percent": round(100 * completed / len(tasks), 2) if tasks else 0.0,
    }
    if include_tasks:
        result["tasks"] = [_task_json(task) for task in sorted(tasks, key=lambda item: item.id)]
    return result


def _task_json(task):
    return {
        "id": task.id,
        "sprint_id": task.sprint_id,
        "title": task.title,
        "description": task.description,
        "required_skills": task.required_skills or "",
        "priority": task.priority,
        "status": task.status,
        "estimated_hours": task.estimated_hours,
        "actual_hours": task.actual_hours,
        "due_date": _iso(task.due_date),
        "assignments": [
            {
                "team_member_id": assignment.team_member_id,
                "team_member_name": assignment.team_member.name,
                "assigned_hours": assignment.assigned_hours,
            }
            for assignment in sorted(
                task.assignments, key=lambda item: item.team_member.name.casefold()
            )
        ],
    }


def _workload_json(workload):
    return {
        "team_member": _member_json(workload["member"]),
        "capacity_hours": workload["capacity"],
        "theoretical_capacity_hours": workload["theoretical_capacity"],
        "available_percent": workload["availability_score"],
        "allocated_hours": workload["allocated"],
        "remaining_hours": workload["remaining"],
        "utilization_percent": workload["utilization"],
        "classification": workload["classification"],
    }


def _api_sprints():
    query = Sprint.query.join(Project)
    if current_user.role == "project_manager":
        query = query.filter(Project.owner_id == current_user.id)
    return query.order_by(Project.name, Sprint.start_date, Sprint.id).all()


@api_bp.get("/csrf-token")
def csrf_token():
    return jsonify({"csrf_token": generate_csrf()})


@api_bp.post("/auth/register")
def register():
    data = _body()
    _only_fields(data, {"username", "email", "password"})
    username = _text(data, "username", required=True, maximum=64)
    email = _text(data, "email", required=True, maximum=120).casefold()
    password = _password(data)
    if len(username) < 2:
        abort(400, description="username must be at least 2 characters.")
    if len(password) < 6:
        abort(400, description="password must be at least 6 characters.")
    try:
        email = validate_email(email, check_deliverability=False).normalized.casefold()
    except EmailNotValidError:
        abort(400, description="email must be a valid email address.")
    user = User(username=username, email=email)
    user.set_password(password)
    db.session.add(user)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _error(
            "duplicate_user",
            "A user with that username or email already exists.",
            409,
        )
    return jsonify({"user": _user_json(user)}), 201


@api_bp.post("/auth/login")
def login():
    data = _body()
    _only_fields(data, {"email", "password"})
    email = _text(data, "email", required=True, maximum=120).casefold()
    password = _password(data)
    user = User.query.filter_by(email=email).first()
    if user is None or not user.check_password(password):
        return _error("invalid_credentials", "Email or password is incorrect.", 401)
    login_user(user)
    return jsonify({"user": _user_json(user)})


@api_bp.post("/auth/logout")
@_login_required
def logout():
    logout_user()
    return jsonify({"message": "Logged out."})


@api_bp.get("/auth/me")
@_login_required
def current_session():
    return jsonify({"user": _user_json(current_user)})


@api_bp.get("/users")
@_admin_required
def users():
    return jsonify(
        {"users": [_user_json(user) for user in User.query.order_by(User.id).all()]}
    )


@api_bp.patch("/users/<int:user_id>/role")
@_admin_required
def update_user_role(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        abort(404, description="User not found.")
    data = _body()
    _only_fields(data, {"role"})
    role = _enum(data, "role", USER_ROLES, required=True)
    user.role = role
    db.session.commit()
    return jsonify({"user": _user_json(user)})


@api_bp.get("/projects")
@_login_required
def projects():
    rows = Project.query.order_by(Project.created_at.desc(), Project.id).all()
    return jsonify({"projects": [_project_json(project) for project in rows]})


@api_bp.post("/projects")
@_manager_required
def create_project():
    data = _body()
    _only_fields(data, {"name", "description", "start_date", "end_date", "status"})
    name = _text(data, "name", required=True, maximum=120)
    start_date = _date(data, "start_date")
    end_date = _date(data, "end_date")
    status = _enum(data, "status", PROJECT_STATUSES, default="planning")
    if start_date and end_date and end_date < start_date:
        abort(400, description="end_date must not be before start_date.")
    project = Project(
        owner_id=current_user.id,
        name=name,
        description=_text(data, "description", maximum=5000),
        start_date=start_date,
        end_date=end_date,
        status=status,
    )
    db.session.add(project)
    db.session.commit()
    return jsonify({"project": _project_json(project, include_members=True)}), 201


@api_bp.get("/projects/<int:project_id>")
@_login_required
def get_project(project_id):
    return jsonify({"project": _project_json(_project(project_id), include_members=True)})


@api_bp.patch("/projects/<int:project_id>")
@_manager_required
def update_project(project_id):
    project = _project(project_id)
    _check_project_manager(project)
    data = _body()
    _only_fields(data, {"name", "description", "status", "start_date", "end_date"})
    if "name" in data:
        project.name = _text(data, "name", required=True, maximum=120)
    if "description" in data:
        project.description = _text(data, "description", maximum=5000)
    if "status" in data:
        project.status = _enum(data, "status", PROJECT_STATUSES, required=True)
    if "start_date" in data:
        project.start_date = _date(data, "start_date")
    if "end_date" in data:
        project.end_date = _date(data, "end_date")
    if project.start_date and project.end_date and project.end_date < project.start_date:
        abort(400, description="end_date must not be before start_date.")
    if project.start_date and any(
        sprint.start_date < project.start_date for sprint in project.sprints
    ):
        abort(400, description="Project start_date cannot be after an existing sprint starts.")
    if project.end_date and any(
        sprint.end_date > project.end_date for sprint in project.sprints
    ):
        abort(400, description="Project end_date cannot be before an existing sprint ends.")
    db.session.commit()
    return jsonify({"project": _project_json(project, include_members=True)})


@api_bp.delete("/projects/<int:project_id>")
@_manager_required
def delete_project(project_id):
    project = _project(project_id)
    _check_project_manager(project)
    db.session.delete(project)
    db.session.commit()
    return "", 204


@api_bp.get("/projects/<int:project_id>/members")
@_login_required
def project_members(project_id):
    project = _project(project_id)
    return jsonify(
        {
            "project_id": project.id,
            "members": [
                _member_json(link.team_member)
                for link in sorted(
                    project.memberships,
                    key=lambda link: link.team_member.name.casefold(),
                )
            ],
        }
    )


@api_bp.post("/projects/<int:project_id>/members")
@_manager_required
def add_project_member(project_id):
    project = _project(project_id)
    _check_project_manager(project)
    data = _body()
    _only_fields(data, {"team_member_id"})
    member_id = data.get("team_member_id")
    if isinstance(member_id, bool) or not isinstance(member_id, int) or member_id < 1:
        abort(400, description="team_member_id must be a positive integer.")
    member = _member(member_id)
    if ProjectMember.query.filter_by(
        project_id=project.id, team_member_id=member.id
    ).first():
        return _error("already_a_member", "Team member is already on this project.", 409)
    db.session.add(ProjectMember(project=project, team_member=member))
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _error("already_a_member", "Team member is already on this project.", 409)
    return jsonify({"project_id": project.id, "member": _member_json(member)}), 201


@api_bp.delete("/projects/<int:project_id>/members/<int:member_id>")
@_manager_required
def remove_project_member(project_id, member_id):
    project = _project(project_id)
    _check_project_manager(project)
    membership = ProjectMember.query.filter_by(
        project_id=project.id, team_member_id=member_id
    ).first()
    if membership is None:
        abort(404, description="Project membership not found.")
    db.session.delete(membership)
    db.session.commit()
    return "", 204


@api_bp.get("/team-members")
@_manager_required
def team_members():
    members = TeamMember.query.order_by(TeamMember.name, TeamMember.id).all()
    return jsonify(
        {"team_members": [_member_json(member, include_email=True) for member in members]}
    )


@api_bp.post("/team-members")
@_manager_required
def create_team_member():
    data = _body()
    _only_fields(
        data, {"name", "email", "skills", "available_hours", "daily_capacity"}
    )
    name = _text(data, "name", required=True, maximum=120)
    email = _text(data, "email", maximum=120)
    if email:
        try:
            email = validate_email(email, check_deliverability=False).normalized.casefold()
        except EmailNotValidError:
            abort(400, description="email must be a valid email address.")
    linked_user = User.query.filter_by(email=email).first() if email else None
    if linked_user and linked_user.team_member:
        return _error(
            "user_already_linked",
            "This account is already linked to a team member.",
            409,
        )
    member = TeamMember(
        name=name,
        email=email,
        skills=_text(data, "skills", maximum=2000, default="") or "",
        available_hours=_number(data, "available_hours", required=True),
        daily_capacity=_number(data, "daily_capacity", required=True),
        user=linked_user,
    )
    db.session.add(member)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _error("duplicate_email", "A team member with that email already exists.", 409)
    return jsonify({"team_member": _member_json(member, include_email=True)}), 201


@api_bp.patch("/team-members/<int:member_id>")
@_manager_required
def update_team_member(member_id):
    member = _member(member_id)
    data = _body()
    _only_fields(data, {"name", "email", "skills", "available_hours", "daily_capacity"})
    if "name" in data:
        member.name = _text(data, "name", required=True, maximum=120)
    if "email" in data:
        email = _text(data, "email", maximum=120)
        if email:
            try:
                email = validate_email(email, check_deliverability=False).normalized.casefold()
            except EmailNotValidError:
                abort(400, description="email must be a valid email address.")
        member.email = email
        linked_user = User.query.filter_by(email=email).first() if email else None
        if linked_user and linked_user.team_member and linked_user.team_member.id != member.id:
            abort(409, description="This account is already linked to another team member.")
        member.user = linked_user
    if "skills" in data:
        member.skills = _text(data, "skills", maximum=2000) or ""
    if "available_hours" in data:
        member.available_hours = _number(data, "available_hours", required=True)
    if "daily_capacity" in data:
        member.daily_capacity = _number(data, "daily_capacity", required=True)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _error("duplicate_email", "A team member with that email already exists.", 409)
    return jsonify({"team_member": _member_json(member, include_email=True)})


@api_bp.delete("/team-members/<int:member_id>")
@_manager_required
def delete_team_member(member_id):
    db.session.delete(_member(member_id))
    db.session.commit()
    return "", 204


@api_bp.get("/sprints")
@_login_required
def sprints():
    return jsonify({"sprints": [_sprint_json(sprint) for sprint in _api_sprints()]})


@api_bp.post("/projects/<int:project_id>/sprints")
@_manager_required
def create_sprint(project_id):
    project = _project(project_id)
    _check_project_manager(project)
    data = _body()
    _only_fields(data, {"name", "sprint_goal", "start_date", "end_date", "status"})
    start_date = _date(data, "start_date", required=True)
    end_date = _date(data, "end_date", required=True)
    if end_date < start_date:
        abort(400, description="end_date must not be before start_date.")
    if project.start_date and start_date < project.start_date:
        abort(400, description="Sprint must start within the project dates.")
    if project.end_date and end_date > project.end_date:
        abort(400, description="Sprint must end within the project dates.")
    sprint = Sprint(
        project=project,
        name=_text(data, "name", required=True, maximum=120),
        sprint_goal=_text(data, "sprint_goal", maximum=5000),
        start_date=start_date,
        end_date=end_date,
        status=_enum(data, "status", SPRINT_STATUSES, default="planned"),
    )
    db.session.add(sprint)
    db.session.commit()
    return jsonify({"sprint": _sprint_json(sprint, include_tasks=True)}), 201


@api_bp.get("/sprints/<int:sprint_id>")
@_login_required
def get_sprint(sprint_id):
    return jsonify({"sprint": _sprint_json(_sprint(sprint_id), include_tasks=True)})


@api_bp.patch("/sprints/<int:sprint_id>")
@_manager_required
def update_sprint(sprint_id):
    sprint = _sprint(sprint_id)
    _check_project_manager(sprint.project)
    data = _body()
    _only_fields(data, {"name", "sprint_goal", "status", "start_date", "end_date"})
    if "name" in data:
        sprint.name = _text(data, "name", required=True, maximum=120)
    if "sprint_goal" in data:
        sprint.sprint_goal = _text(data, "sprint_goal", maximum=5000)
    if "status" in data:
        sprint.status = _enum(data, "status", SPRINT_STATUSES, required=True)
    if "start_date" in data:
        sprint.start_date = _date(data, "start_date", required=True)
    if "end_date" in data:
        sprint.end_date = _date(data, "end_date", required=True)
    if sprint.end_date < sprint.start_date:
        abort(400, description="end_date must not be before start_date.")
    if sprint.project.start_date and sprint.start_date < sprint.project.start_date:
        abort(400, description="Sprint must start within the project dates.")
    if sprint.project.end_date and sprint.end_date > sprint.project.end_date:
        abort(400, description="Sprint must end within the project dates.")
    db.session.commit()
    return jsonify({"sprint": _sprint_json(sprint, include_tasks=True)})


@api_bp.delete("/sprints/<int:sprint_id>")
@_manager_required
def delete_sprint(sprint_id):
    sprint = _sprint(sprint_id)
    _check_project_manager(sprint.project)
    db.session.delete(sprint)
    db.session.commit()
    return "", 204


def _set_task_fields(task, data, creating=False):
    if creating or "title" in data:
        task.title = _text(data, "title", required=True, maximum=200)
    if creating or "description" in data:
        task.description = _text(data, "description", maximum=10000)
    if creating or "required_skills" in data:
        task.required_skills = _text(data, "required_skills", maximum=1000, default="") or ""
    if creating or "priority" in data:
        task.priority = _enum(data, "priority", TASK_PRIORITIES, default="medium", required=creating)
    if creating or "estimated_hours" in data:
        task.estimated_hours = _number(data, "estimated_hours", required=creating)
    if creating or "actual_hours" in data:
        task.actual_hours = _number(data, "actual_hours", required=creating, default=0)
    if "status" in data or creating:
        task.status = _enum(data, "status", TASK_STATUSES, default="todo", required=creating)
    if "due_date" in data or creating:
        task.due_date = _date(data, "due_date")


@api_bp.get("/tasks")
@_login_required
def tasks():
    query = Task.query.join(Sprint).join(Project)
    sprint_value = request.args.get("sprint_id")
    if sprint_value is not None:
        try:
            sprint_id = int(sprint_value)
        except ValueError:
            abort(400, description="sprint_id must be a positive integer.")
        if sprint_id < 1:
            abort(400, description="sprint_id must be a positive integer.")
        query = query.filter(Task.sprint_id == sprint_id)
    if current_user.role == "project_manager":
        query = query.filter(Project.owner_id == current_user.id)
    rows = query.order_by(Task.id).all()
    return jsonify({"tasks": [_task_json(task) for task in rows]})


@api_bp.post("/tasks")
@_manager_required
def create_task():
    data = _body()
    _only_fields(
        data,
        {
            "sprint_id",
            "title",
            "description",
            "required_skills",
            "priority",
            "estimated_hours",
            "actual_hours",
            "status",
            "due_date",
        },
    )
    sprint_id = data.get("sprint_id")
    if isinstance(sprint_id, bool) or not isinstance(sprint_id, int) or sprint_id < 1:
        abort(400, description="sprint_id must be a positive integer.")
    sprint = _sprint(sprint_id)
    _check_project_manager(sprint.project)
    task = Task(sprint=sprint)
    _set_task_fields(task, data, creating=True)
    db.session.add(task)
    db.session.commit()
    return jsonify({"task": _task_json(task)}), 201


@api_bp.get("/tasks/<int:task_id>")
@_login_required
def get_task(task_id):
    return jsonify({"task": _task_json(_task(task_id))})


def _assignment_eligibility_issues(task):
    issues = []
    for assignment in task.assignments:
        workload = member_workload(
            assignment.team_member,
            task.sprint,
            current_app.config["ALLOCATION_UNDERUTILIZED_THRESHOLD"],
            current_app.config["ALLOCATION_OVERLOAD_THRESHOLD"],
            exclude_task_id=task.id,
        )
        reasons = list(candidate_score(task, workload)["ineligibility_reasons"])
        if assignment.assigned_hours > workload["remaining"]:
            reasons.append("Insufficient remaining sprint capacity for assigned_hours.")
        if reasons:
            issues.append(
                {
                    "team_member_id": assignment.team_member_id,
                    "reasons": sorted(set(reasons)),
                }
            )
    return issues


@api_bp.patch("/tasks/<int:task_id>")
@_login_required
def update_task(task_id):
    task = _task(task_id)
    is_manager = _can_manage(task.sprint.project)
    is_assignee = any(
        assignment.team_member.user_id == current_user.id for assignment in task.assignments
    )
    if not is_manager and not is_assignee:
        abort(403, description="Only a project manager or assigned user can update this task.")
    data = _body()
    _only_fields(
        data,
        {
            "title",
            "description",
            "required_skills",
            "priority",
            "estimated_hours",
            "actual_hours",
            "status",
            "due_date",
            "sprint_id",
            "override",
        },
    )
    if not is_manager and set(data) - {"status", "actual_hours"}:
        abort(403, description="Assigned users may only update task status and actual effort.")
    if not is_manager and "override" in data:
        abort(403, description="Only a project manager can approve an assignment override.")
    override = data.pop("override", False)
    if not isinstance(override, bool):
        abort(400, description="override must be a boolean.")
    _set_task_fields(task, data)
    if "sprint_id" in data:
        if not is_manager:
            abort(403, description="Only a project manager can move a task to another sprint.")
        sprint_id = data["sprint_id"]
        if isinstance(sprint_id, bool) or not isinstance(sprint_id, int) or sprint_id < 1:
            abort(400, description="sprint_id must be a positive integer.")
        destination = _sprint(sprint_id)
        _check_project_manager(destination.project)
        task.sprint = destination
    if (
        is_manager
        and not override
        and {"estimated_hours", "required_skills", "sprint_id"} & set(data)
    ):
        issues = _assignment_eligibility_issues(task)
        if issues:
            return _error(
                "assignment_ineligible",
                "The task change would make one or more assignments exceed current eligibility.",
                409,
                {"assignments": issues},
            )
    db.session.commit()
    return jsonify({"task": _task_json(task)})


@api_bp.delete("/tasks/<int:task_id>")
@_manager_required
def delete_task(task_id):
    task = _task(task_id)
    _check_project_manager(task.sprint.project)
    db.session.delete(task)
    db.session.commit()
    return "", 204


@api_bp.post("/tasks/<int:task_id>/assignments")
@_manager_required
def assign_task(task_id):
    task = _task(task_id)
    _check_project_manager(task.sprint.project)
    if task.status == "done":
        abort(409, description="Completed tasks cannot be newly assigned.")
    data = _body()
    _only_fields(data, {"team_member_id", "assigned_hours", "override"})
    member_id = data.get("team_member_id")
    if isinstance(member_id, bool) or not isinstance(member_id, int) or member_id < 1:
        abort(400, description="team_member_id must be a positive integer.")
    member = _member(member_id)
    hours = _number(data, "assigned_hours", default=task.estimated_hours)
    assignment = TaskAssignment.query.filter_by(
        task_id=task.id, team_member_id=member.id
    ).first()
    created = assignment is None
    workload = member_workload(
        member,
        task.sprint,
        current_app.config["ALLOCATION_UNDERUTILIZED_THRESHOLD"],
        current_app.config["ALLOCATION_OVERLOAD_THRESHOLD"],
        exclude_task_id=task.id if assignment else None,
    )
    assessment = candidate_score(task, workload)
    override = data.get("override", False)
    if not isinstance(override, bool):
        abort(400, description="override must be a boolean.")
    if (not assessment["eligible"] or hours > workload["remaining"]) and not override:
        reasons = list(assessment["ineligibility_reasons"])
        if hours > workload["remaining"]:
            reasons.append("Insufficient remaining sprint capacity for assigned_hours.")
        return _error(
            "assignment_ineligible",
            "The member does not meet the recommendation criteria; set override=true for manager approval.",
            409,
            {"reasons": reasons},
        )
    if assignment is None:
        assignment = TaskAssignment(
            task=task, team_member=member, assigned_hours=hours
        )
        db.session.add(assignment)
    else:
        assignment.assigned_hours = hours
    rejection = AllocationRejection.query.filter_by(
        task_id=task.id, team_member_id=member.id
    ).first()
    if rejection:
        db.session.delete(rejection)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _error("assignment_conflict", "Assignment could not be saved.", 409)
    return (
        jsonify({"task": _task_json(task), "assessment": assessment["explanations"]}),
        201 if created else 200,
    )


@api_bp.delete("/tasks/<int:task_id>/assignments/<int:member_id>")
@_manager_required
def remove_task_assignment(task_id, member_id):
    task = _task(task_id)
    _check_project_manager(task.sprint.project)
    assignment = TaskAssignment.query.filter_by(
        task_id=task.id, team_member_id=member_id
    ).first()
    if assignment is None:
        abort(404, description="Task assignment not found.")
    db.session.delete(assignment)
    db.session.commit()
    return jsonify({"task": _task_json(task)})


@api_bp.get("/sprints/<int:sprint_id>/allocations")
@_manager_required
def sprint_allocations(sprint_id):
    sprint = _sprint(sprint_id)
    _check_project_manager(sprint.project)
    members = TeamMember.query.order_by(TeamMember.name).all()
    workloads = [
        member_workload(
            member,
            sprint,
            current_app.config["ALLOCATION_UNDERUTILIZED_THRESHOLD"],
            current_app.config["ALLOCATION_OVERLOAD_THRESHOLD"],
        )
        for member in members
    ]
    recommendations = []
    for task in sorted(sprint.tasks, key=lambda item: item.id):
        if task.status == "done" or task.assignments:
            continue
        rejected = {
            item.team_member_id for item in task.allocation_rejections
        }
        for item in ranked_recommendations(task, workloads, rejected):
            recommendations.append(
                {
                    "task_id": task.id,
                    "team_member": _member_json(item["member"]),
                    "score": item["score"],
                    "components": {
                        "skill_match": item["skill_match"],
                        "capacity_score": item["capacity_score"],
                        "availability_score": item["availability_score"],
                        "priority_suitability": item["priority_suitability"],
                    },
                    "explanations": item["explanations"],
                    "projected_utilization_percent": item["projected_utilization"],
                }
            )
    return jsonify({"sprint_id": sprint.id, "recommendations": recommendations})


@api_bp.post("/tasks/<int:task_id>/allocation-rejections")
@_manager_required
def reject_allocation(task_id):
    task = _task(task_id)
    _check_project_manager(task.sprint.project)
    data = _body()
    _only_fields(data, {"team_member_id"})
    member_id = data.get("team_member_id")
    if isinstance(member_id, bool) or not isinstance(member_id, int) or member_id < 1:
        abort(400, description="team_member_id must be a positive integer.")
    member = _member(member_id)
    if task.status == "done" or task.assignments:
        abort(409, description="Only unassigned, incomplete tasks can be rejected.")
    workload = member_workload(
        member,
        task.sprint,
        current_app.config["ALLOCATION_UNDERUTILIZED_THRESHOLD"],
        current_app.config["ALLOCATION_OVERLOAD_THRESHOLD"],
    )
    if not candidate_score(task, workload)["eligible"]:
        abort(409, description="Only an eligible recommendation can be rejected.")
    rejection = AllocationRejection.query.filter_by(
        task_id=task.id, team_member_id=member.id
    ).first()
    if rejection is None:
        db.session.add(AllocationRejection(task=task, team_member=member))
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
    return jsonify({"task_id": task.id, "rejected_team_member_id": member.id})


@api_bp.get("/sprints/<int:sprint_id>/workload")
@_manager_required
def sprint_workload(sprint_id):
    sprint = _sprint(sprint_id)
    _check_project_manager(sprint.project)
    members = TeamMember.query.order_by(TeamMember.name).all()
    workloads = {
        member.id: member_workload(
            member,
            sprint,
            current_app.config["ALLOCATION_UNDERUTILIZED_THRESHOLD"],
            current_app.config["ALLOCATION_OVERLOAD_THRESHOLD"],
        )
        for member in members
    }
    redistribution = []
    for member in members:
        workload = workloads[member.id]
        if workload["classification"] != "overloaded":
            continue
        for assignment in member.assignments:
            task = assignment.task
            if task.sprint_id != sprint.id or task.status == "done":
                continue
            alternatives = []
            for candidate in members:
                if candidate.id == member.id:
                    continue
                evaluation = candidate_score(task, workloads[candidate.id])
                if evaluation["eligible"]:
                    alternatives.append(
                        {
                            "team_member": _member_json(candidate),
                            "score": evaluation["score"],
                            "reason": (
                                f"{candidate.name} has {evaluation['remaining_capacity']:.1f}h "
                                "remaining and meets the skill and capacity rules."
                            ),
                        }
                    )
            alternatives.sort(key=lambda item: (-item["score"], item["team_member"]["name"].casefold()))
            if alternatives:
                redistribution.append(
                    {
                        "task_id": task.id,
                        "task_title": task.title,
                        "currently_assigned_to": _member_json(member),
                        "suggested_assignees": alternatives,
                        "message": "Recommendation only; no assignment was changed.",
                    }
                )
    return jsonify(
        {
            "sprint_id": sprint.id,
            "members": [_workload_json(workloads[member.id]) for member in members],
            "redistribution_recommendations": redistribution,
        }
    )


@api_bp.get("/risks")
@_manager_required
def risks():
    sprints = _api_sprints()
    if request.args.get("project_id"):
        try:
            project_id = int(request.args["project_id"])
        except ValueError:
            abort(400, description="project_id must be a positive integer.")
        project = _project(project_id)
        _check_project_manager(project)
        sprints = [sprint for sprint in sprints if sprint.project_id == project.id]
    sprint_ids = {sprint.id for sprint in sprints}
    tasks = (
        Task.query.filter(Task.sprint_id.in_(sprint_ids)).order_by(Task.id).all()
        if sprint_ids
        else []
    )
    alerts = build_risk_alerts(
        sprints,
        tasks,
        TeamMember.query.order_by(TeamMember.name).all(),
        settings=current_app.config,
    )
    return jsonify(
        {
            "summary": {
                "total": len(alerts),
                "high": sum(item["severity"] == "HIGH" for item in alerts),
                "medium": sum(item["severity"] == "MEDIUM" for item in alerts),
                "low": sum(item["severity"] == "LOW" for item in alerts),
            },
            "risks": [
                {
                    "type": item["risk_type"],
                    "severity": item["severity"],
                    "category": item["category"],
                    "project_id": item["project_id"],
                    "sprint_id": item["sprint_id"],
                    "task_id": item["task_id"],
                    "team_member_id": item["team_member_id"],
                    "description": item["description"],
                    "reason": item["reason"],
                    "recommended_action": item["action"],
                }
                for item in alerts
            ],
        }
    )


@api_bp.get("/analytics")
@_login_required
def analytics():
    projects = Project.query.order_by(Project.name).all()
    sprints = _api_sprints()
    tasks = (
        Task.query.filter(Task.sprint_id.in_([item.id for item in sprints]))
        .order_by(Task.id)
        .all()
        if sprints
        else []
    )
    members = TeamMember.query.order_by(TeamMember.name).all()
    data = build_dashboard_data(
        projects,
        sprints,
        tasks,
        members,
        underutilized_threshold=current_app.config["ALLOCATION_UNDERUTILIZED_THRESHOLD"],
        overload_threshold=current_app.config["ALLOCATION_OVERLOAD_THRESHOLD"],
    )
    sprint_workloads = {
        member.id: [
            member_workload(
                member,
                sprint,
                current_app.config["ALLOCATION_UNDERUTILIZED_THRESHOLD"],
                current_app.config["ALLOCATION_OVERLOAD_THRESHOLD"],
            )
            for sprint in sprints
        ]
        for member in members
    }
    workloads = [
        _workload_json(
            aggregate_member_workloads(
                member,
                sprint_workloads[member.id],
                current_app.config["ALLOCATION_UNDERUTILIZED_THRESHOLD"],
                current_app.config["ALLOCATION_OVERLOAD_THRESHOLD"],
            )
        )
        for member in members
    ]
    return jsonify(
        {
            "projects": {
                "total": data["total_projects"],
                "active": sum(item.status == "active" for item in projects),
                "completed": sum(item.status == "completed" for item in projects),
            },
            "sprints": {
                "total": data["total_sprints"],
                "progress": data["sprint_progress"],
            },
            "tasks": {
                "total": data["total_tasks"],
                "completed": data["completed_tasks"],
                "todo": data["pending_tasks"],
                "in_progress": data["in_progress_tasks"],
                "estimated_hours": round(sum(item.estimated_hours for item in tasks), 2),
                "actual_hours": round(sum(item.actual_hours for item in tasks), 2),
            },
            "workloads": workloads,
        }
    )


@api_bp.get("/analytics/projects/<int:project_id>")
@_login_required
def project_analytics(project_id):
    project = _project(project_id)
    tasks = [task for sprint in project.sprints for task in sprint.tasks]
    total = len(tasks)
    completed = sum(task.status == "done" for task in tasks)
    return jsonify(
        {
            "project_id": project.id,
            "sprint_count": len(project.sprints),
            "task_count": total,
            "completed_task_count": completed,
            "completion_percent": round(100 * completed / total, 2) if total else 0.0,
            "estimated_hours": round(sum(task.estimated_hours for task in tasks), 2),
            "actual_hours": round(sum(task.actual_hours for task in tasks), 2),
            "sprints": [
                _sprint_json(sprint)
                for sprint in sorted(project.sprints, key=lambda item: item.start_date)
            ],
        }
    )


@api_bp.get("/analytics/sprints/<int:sprint_id>")
@_login_required
def sprint_analytics(sprint_id):
    sprint = _sprint(sprint_id)
    tasks = sprint.tasks
    done = [task for task in tasks if task.status == "done"]
    return jsonify(
        {
            "sprint_id": sprint.id,
            "task_count": len(tasks),
            "completed_task_count": len(done),
            "completion_percent": round(100 * len(done) / len(tasks), 2) if tasks else 0.0,
            "estimated_hours": round(sum(task.estimated_hours for task in tasks), 2),
            "actual_hours": round(sum(task.actual_hours for task in tasks), 2),
            "remaining_effort_hours": round(
                sum(task.estimated_hours for task in tasks if task.status != "done"), 2
            ),
        }
    )


@api_bp.get("/analytics/developers")
@_manager_required
def developer_analytics():
    members = TeamMember.query.order_by(TeamMember.name).all()
    return jsonify(
        {
            "developers": [
                {
                    **_member_json(member),
                    "completed_tasks": sum(
                        assignment.task.status == "done" for assignment in member.assignments
                    ),
                    "assigned_tasks": len(member.assignments),
                    "estimated_hours": round(
                        sum(
                            assignment.task.estimated_hours
                            for assignment in member.assignments
                        ),
                        2,
                    ),
                    "actual_hours": round(
                        sum(
                            assignment.task.actual_hours
                            for assignment in member.assignments
                        ),
                        2,
                    ),
                    "productivity_note": (
                        "Descriptive historical totals only; not a predictive or "
                        "validated productivity score."
                    ),
                }
                for member in members
            ]
        }
    )

