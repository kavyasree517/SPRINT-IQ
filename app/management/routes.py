from functools import wraps

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from .. import db
from ..models import (
    AllocationRejection,
    Project,
    Sprint,
    Task,
    TaskAssignment,
    TeamMember,
    User,
)
from .allocation import (
    candidate_score,
    member_workload,
    ranked_recommendations,
    skill_match_score,
)
from .forms import (
    AllocationOverrideForm,
    ProjectForm,
    SprintForm,
    TaskForm,
    TeamMemberForm,
)
from .risk_analysis import (
    RISK_CATEGORIES,
    RISK_SEVERITIES,
    build_risk_alerts,
)


management_bp = Blueprint("management", __name__)
TASK_STATUSES = (("todo", "To Do"), ("in_progress", "In Progress"), ("done", "Done"))
PRIORITY_ORDER = ["critical", "high", "medium", "low"]


def management_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if current_user.role not in {"administrator", "project_manager"}:
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def can_manage_project(project):
    return current_user.role == "administrator" or (
        current_user.role == "project_manager" and project.owner_id == current_user.id
    )


def get_project_or_404(project_id):
    project = db.session.get(Project, project_id)
    if project is None:
        abort(404)
    return project


def get_sprint_or_404(sprint_id):
    sprint = db.session.get(Sprint, sprint_id)
    if sprint is None:
        abort(404)
    return sprint


def get_task_or_404(task_id):
    task = db.session.get(Task, task_id)
    if task is None:
        abort(404)
    return task


def check_project_permission(project):
    if not can_manage_project(project):
        abort(403)


def populate_task_choices(form, projects, members):
    form.sprint_id.choices = {
        str(sprint.id): f"{sprint.project.name} / {sprint.name}"
        for project in projects
        for sprint in project.sprints
    }
    form.team_member_ids.choices = {
        member.id: f"{member.name} ({member.email})" if member.email else member.name
        for member in members
    }


def reconcile_assignments(task, team_member_ids, assigned_hours):
    assignments = {assignment.team_member_id: assignment for assignment in task.assignments}
    selected_ids = set(team_member_ids)
    for member_id, assignment in assignments.items():
        if member_id not in selected_ids:
            task.assignments.remove(assignment)
            db.session.delete(assignment)

    for member_id in selected_ids:
        assignment = assignments.get(member_id)
        if assignment is None:
            assignment = TaskAssignment(
                team_member_id=member_id,
                assigned_hours=assigned_hours or 0,
            )
            task.assignments.append(assignment)
        else:
            assignment.assigned_hours = assigned_hours or 0


def populate_task(task, form, sprint):
    task.sprint = sprint
    task.title = form.title.data.strip()
    task.description = form.description.data
    task.required_skills = form.required_skills.data or ""
    task.priority = form.priority.data
    task.estimated_hours = form.estimated_hours.data
    task.status = form.status.data
    task.due_date = form.due_date.data


def validate_task_assignments(form, sprint, task_id=None):
    assigned_hours = form.assigned_hours.data or 0
    has_override = form.confirm_allocation_override.data
    for member_id in form.team_member_ids.data or []:
        member = db.session.get(TeamMember, member_id)
        if member is None:
            abort(400)
        workload = member_workload(
            member,
            sprint,
            current_app.config["ALLOCATION_UNDERUTILIZED_THRESHOLD"],
            current_app.config["ALLOCATION_OVERLOAD_THRESHOLD"],
            exclude_task_id=task_id,
        )
        has_skill_match = skill_match_score(
            form.required_skills.data, member.skills
        ) > 0
        has_capacity = (
            workload["capacity"] > 0
            and workload["remaining"] >= assigned_hours
        )
        if (not has_skill_match or not has_capacity) and not has_override:
            reasons = []
            if not has_skill_match:
                reasons.append("no matching required skills")
            if not has_capacity:
                reasons.append("insufficient remaining sprint capacity")
            form.team_member_ids.errors.append(
                f"{member.name}: {', '.join(reasons)}. Confirm the override to continue."
            )
            return False
    return True


def can_change_task_status(task):
    return can_manage_project(task.sprint.project) or any(
        assignment.team_member.user_id == current_user.id
        for assignment in task.assignments
    )


def allocation_workloads(sprint, members):
    underutilized_threshold = current_app.config["ALLOCATION_UNDERUTILIZED_THRESHOLD"]
    overload_threshold = current_app.config["ALLOCATION_OVERLOAD_THRESHOLD"]
    return [
        member_workload(
            member,
            sprint,
            underutilized_threshold,
            overload_threshold,
        )
        for member in members
    ]


def allocation_redirect(task):
    return redirect(
        url_for("management.allocations", sprint_id=task.sprint_id)
    )


@management_bp.route("/projects")
@login_required
def projects():
    project_list = Project.query.order_by(Project.created_at.desc(), Project.name).all()
    return render_template(
        "management/projects.html",
        projects=project_list,
        can_create=current_user.role in {"administrator", "project_manager"},
        can_manage=can_manage_project,
    )


@management_bp.route("/sprints")
@login_required
def sprints():
    sprint_list = Sprint.query.join(Project).order_by(
        Project.name, Sprint.start_date
    ).all()
    return render_template("management/sprints.html", sprints=sprint_list)


@management_bp.route("/projects/new", methods=["GET", "POST"])
@management_required
def create_project():
    form = ProjectForm()
    if form.validate_on_submit():
        if form.start_date.data and form.end_date.data and form.end_date.data < form.start_date.data:
            form.end_date.errors.append("End date must be on or after the start date.")
        else:
            project = Project(
                owner_id=current_user.id,
                name=form.name.data.strip(),
                description=form.description.data,
                start_date=form.start_date.data,
                end_date=form.end_date.data,
                status=form.status.data,
            )
            db.session.add(project)
            db.session.commit()
            flash("Project created.", "success")
            return redirect(url_for("management.project_detail", project_id=project.id))
    return render_template(
        "management/form.html",
        title="Create project",
        form=form,
        back_url=url_for("management.projects"),
    )


@management_bp.route("/projects/<int:project_id>")
@login_required
def project_detail(project_id):
    project = get_project_or_404(project_id)
    return render_template(
        "management/project_detail.html",
        project=project,
        can_manage=can_manage_project(project),
    )


@management_bp.route("/projects/<int:project_id>/edit", methods=["GET", "POST"])
@management_required
def edit_project(project_id):
    project = get_project_or_404(project_id)
    check_project_permission(project)
    form = ProjectForm(obj=project)
    if form.validate_on_submit():
        if form.start_date.data and form.end_date.data and form.end_date.data < form.start_date.data:
            form.end_date.errors.append("End date must be on or after the start date.")
        elif form.start_date.data and any(
            sprint.start_date < form.start_date.data for sprint in project.sprints
        ):
            form.start_date.errors.append(
                "Project start date cannot be after an existing sprint starts."
            )
        elif form.end_date.data and any(
            sprint.end_date > form.end_date.data for sprint in project.sprints
        ):
            form.end_date.errors.append(
                "Project end date cannot be before an existing sprint ends."
            )
        else:
            project.name = form.name.data.strip()
            project.description = form.description.data
            project.start_date = form.start_date.data
            project.end_date = form.end_date.data
            project.status = form.status.data
            db.session.commit()
            flash("Project updated.", "success")
            return redirect(url_for("management.project_detail", project_id=project.id))
    return render_template(
        "management/form.html",
        title="Edit project",
        form=form,
        back_url=url_for("management.project_detail", project_id=project.id),
    )


@management_bp.route("/projects/<int:project_id>/delete", methods=["POST"])
@management_required
def delete_project(project_id):
    project = get_project_or_404(project_id)
    check_project_permission(project)
    db.session.delete(project)
    db.session.commit()
    flash("Project and its sprints and tasks were deleted.", "success")
    return redirect(url_for("management.projects"))


@management_bp.route("/projects/<int:project_id>/sprints/new", methods=["GET", "POST"])
@management_required
def create_sprint(project_id):
    project = get_project_or_404(project_id)
    check_project_permission(project)
    form = SprintForm()
    if form.validate_on_submit():
        if form.end_date.data < form.start_date.data:
            form.end_date.errors.append("End date must be on or after the start date.")
        elif project.start_date and form.start_date.data < project.start_date:
            form.start_date.errors.append("Sprint must start within the project dates.")
        elif project.end_date and form.end_date.data > project.end_date:
            form.end_date.errors.append("Sprint must end within the project dates.")
        else:
            sprint = Sprint(
                project=project,
                name=form.name.data.strip(),
                sprint_goal=form.sprint_goal.data,
                start_date=form.start_date.data,
                end_date=form.end_date.data,
                status=form.status.data,
            )
            db.session.add(sprint)
            db.session.commit()
            flash("Sprint created.", "success")
            return redirect(
                url_for("management.project_detail", project_id=project.id)
            )
    return render_template(
        "management/form.html",
        title=f"Create sprint — {project.name}",
        form=form,
        back_url=url_for("management.project_detail", project_id=project.id),
    )


@management_bp.route("/sprints/<int:sprint_id>/edit", methods=["GET", "POST"])
@management_required
def edit_sprint(sprint_id):
    sprint = get_sprint_or_404(sprint_id)
    check_project_permission(sprint.project)
    form = SprintForm(obj=sprint)
    if form.validate_on_submit():
        if form.end_date.data < form.start_date.data:
            form.end_date.errors.append("End date must be on or after the start date.")
        elif sprint.project.start_date and form.start_date.data < sprint.project.start_date:
            form.start_date.errors.append("Sprint must start within the project dates.")
        elif sprint.project.end_date and form.end_date.data > sprint.project.end_date:
            form.end_date.errors.append("Sprint must end within the project dates.")
        else:
            sprint.name = form.name.data.strip()
            sprint.sprint_goal = form.sprint_goal.data
            sprint.start_date = form.start_date.data
            sprint.end_date = form.end_date.data
            sprint.status = form.status.data
            db.session.commit()
            flash("Sprint updated.", "success")
            return redirect(
                url_for("management.project_detail", project_id=sprint.project_id)
            )
    return render_template(
        "management/form.html",
        title=f"Edit sprint — {sprint.project.name}",
        form=form,
        back_url=url_for(
            "management.project_detail", project_id=sprint.project_id
        ),
    )


@management_bp.route("/sprints/<int:sprint_id>/delete", methods=["POST"])
@management_required
def delete_sprint(sprint_id):
    sprint = get_sprint_or_404(sprint_id)
    check_project_permission(sprint.project)
    project_id = sprint.project_id
    db.session.delete(sprint)
    db.session.commit()
    flash("Sprint and its tasks were deleted.", "success")
    return redirect(url_for("management.project_detail", project_id=project_id))


@management_bp.route("/team")
@management_required
def team_members():
    members = TeamMember.query.order_by(TeamMember.name).all()
    return render_template("management/team.html", members=members)


@management_bp.route("/team/new", methods=["GET", "POST"])
@management_required
def create_team_member():
    form = TeamMemberForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        linked_user = User.query.filter_by(email=email).first()
        if linked_user and linked_user.team_member:
            form.email.errors.append("This account is already linked to a team member.")
        else:
            member = TeamMember(
                name=form.name.data.strip(),
                email=email,
                skills=form.skills.data or "",
                available_hours=form.available_hours.data,
                daily_capacity=form.daily_capacity.data,
                user=linked_user,
            )
            db.session.add(member)
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                form.email.errors.append("A team member with this email already exists.")
            else:
                flash("Team member added.", "success")
                return redirect(url_for("management.team_members"))
    return render_template(
        "management/form.html",
        title="Add team member",
        form=form,
        back_url=url_for("management.team_members"),
    )


@management_bp.route("/team/<int:member_id>/edit", methods=["GET", "POST"])
@management_required
def edit_team_member(member_id):
    member = db.get_or_404(TeamMember, member_id)
    form = TeamMemberForm(obj=member)
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        linked_user = User.query.filter_by(email=email).first()
        if linked_user and linked_user.team_member and linked_user.team_member.id != member.id:
            form.email.errors.append("This account is already linked to another team member.")
        else:
            member.name = form.name.data.strip()
            member.email = email
            member.skills = form.skills.data or ""
            member.available_hours = form.available_hours.data
            member.daily_capacity = form.daily_capacity.data
            member.user = linked_user
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                form.email.errors.append("A team member with this email already exists.")
            else:
                flash("Team member updated.", "success")
                return redirect(url_for("management.team_members"))
    return render_template(
        "management/form.html",
        title="Edit team member",
        form=form,
        back_url=url_for("management.team_members"),
    )


@management_bp.route("/team/<int:member_id>/delete", methods=["POST"])
@management_required
def delete_team_member(member_id):
    member = db.get_or_404(TeamMember, member_id)
    db.session.delete(member)
    db.session.commit()
    flash("Team member and their task assignments were removed.", "success")
    return redirect(url_for("management.team_members"))


@management_bp.route("/tasks")
@login_required
def tasks():
    task_list = Task.query.join(Sprint).join(Project).order_by(
        Project.name, Sprint.start_date, Task.id
    ).all()
    return render_template(
        "management/kanban.html",
        tasks=task_list,
        statuses=TASK_STATUSES,
        can_manage_task=can_manage_project,
        can_change_status=can_change_task_status,
    )


@management_bp.route("/allocations")
@management_required
def allocations():
    sprints = [
        sprint
        for sprint in Sprint.query.join(Project).order_by(
            Project.name, Sprint.start_date
        )
        if can_manage_project(sprint.project)
    ]
    sprint_id = request.args.get("sprint_id", type=int)
    selected_sprint = None
    if sprint_id is not None:
        selected_sprint = db.session.get(Sprint, sprint_id)
        if selected_sprint is None:
            abort(404)
        check_project_permission(selected_sprint.project)
    elif sprints:
        selected_sprint = sprints[0]

    members = TeamMember.query.order_by(TeamMember.name).all()
    workloads = allocation_workloads(selected_sprint, members) if selected_sprint else []
    allocation_tasks = []
    task_choices = []
    recommendations_by_task = {}
    override_forms = {}
    selected_task_id = request.args.get("task_id", type=int)
    if selected_sprint:
        allocation_tasks = (
            Task.query.filter_by(sprint_id=selected_sprint.id)
            .filter(Task.status != "done")
            .order_by(Task.id)
            .all()
        )
        task_choices = [task for task in allocation_tasks if not task.assignments]
        allocation_tasks = task_choices
        if selected_task_id is not None:
            if not any(task.id == selected_task_id for task in allocation_tasks):
                abort(404)
            allocation_tasks = [
                task for task in allocation_tasks if task.id == selected_task_id
            ]
        for task in allocation_tasks:
            rejected_member_ids = {
                rejection.team_member_id
                for rejection in task.allocation_rejections
            }
            recommendations_by_task[task.id] = ranked_recommendations(
                task, workloads, rejected_member_ids
            )
            form = AllocationOverrideForm()
            form.team_member_id.choices = [
                (member.id, member.name) for member in members
            ]
            override_forms[task.id] = form

    total_capacity = round(sum(item["capacity"] for item in workloads), 2)
    total_allocated = round(sum(item["allocated"] for item in workloads), 2)
    return render_template(
        "management/allocations.html",
        sprints=sprints,
        selected_sprint=selected_sprint,
        workloads=workloads,
        allocation_tasks=allocation_tasks,
        task_choices=task_choices,
        selected_task_id=selected_task_id,
        recommendations_by_task=recommendations_by_task,
        override_forms=override_forms,
        total_capacity=total_capacity,
        total_allocated=total_allocated,
        total_remaining=round(total_capacity - total_allocated, 2),
        overload_threshold=current_app.config["ALLOCATION_OVERLOAD_THRESHOLD"],
        underutilized_threshold=current_app.config[
            "ALLOCATION_UNDERUTILIZED_THRESHOLD"
        ],
    )


@management_bp.route(
    "/allocations/<int:task_id>/accept/<int:member_id>", methods=["POST"]
)
@management_required
def accept_allocation(task_id, member_id):
    task = get_task_or_404(task_id)
    check_project_permission(task.sprint.project)
    if task.status == "done" or task.assignments:
        flash("This task is no longer available for allocation.", "warning")
        return allocation_redirect(task)

    members = TeamMember.query.order_by(TeamMember.name).all()
    workloads = allocation_workloads(task.sprint, members)
    rejected_ids = {
        rejection.team_member_id for rejection in task.allocation_rejections
    }
    recommendation = next(
        (
            item
            for item in ranked_recommendations(task, workloads, rejected_ids)
            if item["member_id"] == member_id
        ),
        None,
    )
    if recommendation is None:
        flash(
            "That recommendation is no longer eligible. Review the current allocation.",
            "warning",
        )
        return allocation_redirect(task)

    db.session.add(
        TaskAssignment(
            task=task,
            team_member_id=member_id,
            assigned_hours=task.estimated_hours,
        )
    )
    rejection = AllocationRejection.query.filter_by(
        task_id=task.id, team_member_id=member_id
    ).first()
    if rejection:
        db.session.delete(rejection)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("The assignment changed before it could be saved. Refresh and retry.", "warning")
    else:
        flash("Recommended assignment approved.", "success")
    return allocation_redirect(task)


@management_bp.route(
    "/allocations/<int:task_id>/reject/<int:member_id>", methods=["POST"]
)
@management_required
def reject_allocation(task_id, member_id):
    task = get_task_or_404(task_id)
    check_project_permission(task.sprint.project)
    if task.status == "done" or task.assignments:
        flash("This task is no longer available for recommendation review.", "warning")
        return allocation_redirect(task)

    members = TeamMember.query.order_by(TeamMember.name).all()
    workloads = allocation_workloads(task.sprint, members)
    rejected_ids = {
        rejection.team_member_id for rejection in task.allocation_rejections
    }
    recommendation = next(
        (
            item
            for item in ranked_recommendations(task, workloads, rejected_ids)
            if item["member_id"] == member_id
        ),
        None,
    )
    if recommendation is None:
        flash("Only a current eligible recommendation can be rejected.", "warning")
        return allocation_redirect(task)

    rejection = AllocationRejection.query.filter_by(
        task_id=task.id, team_member_id=member_id
    ).first()
    if rejection is None:
        db.session.add(
            AllocationRejection(task_id=task.id, team_member_id=member_id)
        )
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("The rejection changed before it could be saved. Refresh and retry.", "warning")
            return allocation_redirect(task)
    flash("Recommendation rejected.", "info")
    return allocation_redirect(task)


@management_bp.route(
    "/allocations/<int:task_id>/override", methods=["POST"]
)
@management_required
def override_allocation(task_id):
    task = get_task_or_404(task_id)
    check_project_permission(task.sprint.project)
    if task.status == "done" or task.assignments:
        flash("Only unassigned, incomplete tasks can be manually allocated.", "warning")
        return allocation_redirect(task)

    form = AllocationOverrideForm()
    members = TeamMember.query.order_by(TeamMember.name).all()
    form.team_member_id.choices = [(member.id, member.name) for member in members]
    if not form.validate_on_submit():
        flash("Select a valid team member for the manual assignment.", "danger")
        return allocation_redirect(task)

    member = db.session.get(TeamMember, form.team_member_id.data)
    if member is None:
        abort(404)
    workload = member_workload(
        member,
        task.sprint,
        current_app.config["ALLOCATION_UNDERUTILIZED_THRESHOLD"],
        current_app.config["ALLOCATION_OVERLOAD_THRESHOLD"],
    )
    assessment = candidate_score(task, workload)
    if not assessment["eligible"] and not form.confirm_override.data:
        flash(
            "This member does not meet the recommendation criteria. Confirm the "
            "skill/capacity override to continue.",
            "danger",
        )
        return allocation_redirect(task)

    db.session.add(
        TaskAssignment(
            task=task,
            team_member=member,
            assigned_hours=task.estimated_hours,
        )
    )
    for rejection in list(task.allocation_rejections):
        db.session.delete(rejection)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("The assignment changed before it could be saved. Refresh and retry.", "warning")
    else:
        flash("Manual assignment saved.", "success")
    return allocation_redirect(task)


@management_bp.route("/tasks/new", methods=["GET", "POST"])
@management_required
def create_task():
    projects = [
        project
        for project in Project.query.order_by(Project.name).all()
        if can_manage_project(project)
    ]
    members = TeamMember.query.order_by(TeamMember.name).all()
    form = TaskForm()
    populate_task_choices(form, projects, members)
    if form.validate_on_submit():
        sprint = db.session.get(Sprint, int(form.sprint_id.data))
        if sprint is None or not can_manage_project(sprint.project):
            abort(403)
        if form.due_date.data and not (
            sprint.start_date <= form.due_date.data <= sprint.end_date
        ):
            form.due_date.errors.append("Deadline must fall within the selected sprint.")
        elif not validate_task_assignments(form, sprint):
            pass
        else:
            task = Task()
            populate_task(task, form, sprint)
            db.session.add(task)
            db.session.flush()
            reconcile_assignments(
                task, form.team_member_ids.data, form.assigned_hours.data
            )
            db.session.commit()
            flash("Task created.", "success")
            return redirect(url_for("management.tasks"))
    return render_template(
        "management/form.html",
        title="Create task",
        form=form,
        back_url=url_for("management.tasks"),
    )


@management_bp.route("/tasks/<int:task_id>/edit", methods=["GET", "POST"])
@management_required
def edit_task(task_id):
    task = get_task_or_404(task_id)
    check_project_permission(task.sprint.project)
    projects = [
        project
        for project in Project.query.order_by(Project.name).all()
        if can_manage_project(project)
    ]
    members = TeamMember.query.order_by(TeamMember.name).all()
    form = TaskForm(obj=task)
    populate_task_choices(form, projects, members)
    if request.method == "GET":
        form.team_member_ids.data = [
            assignment.team_member_id for assignment in task.assignments
        ]
        if task.assignments:
            form.assigned_hours.data = task.assignments[0].assigned_hours
    if form.validate_on_submit():
        sprint = db.session.get(Sprint, int(form.sprint_id.data))
        if sprint is None or not can_manage_project(sprint.project):
            abort(403)
        if form.due_date.data and not (
            sprint.start_date <= form.due_date.data <= sprint.end_date
        ):
            form.due_date.errors.append("Deadline must fall within the selected sprint.")
        elif not validate_task_assignments(form, sprint, task.id):
            pass
        else:
            populate_task(task, form, sprint)
            reconcile_assignments(
                task, form.team_member_ids.data, form.assigned_hours.data
            )
            for rejection in list(task.allocation_rejections):
                db.session.delete(rejection)
            db.session.commit()
            flash("Task updated.", "success")
            return redirect(url_for("management.tasks"))
    return render_template(
        "management/form.html",
        title="Edit task",
        form=form,
        back_url=url_for("management.tasks"),
    )


@management_bp.route("/tasks/<int:task_id>/delete", methods=["POST"])
@management_required
def delete_task(task_id):
    task = get_task_or_404(task_id)
    check_project_permission(task.sprint.project)
    db.session.delete(task)
    db.session.commit()
    flash("Task deleted.", "success")
    return redirect(url_for("management.tasks"))


@management_bp.route("/tasks/<int:task_id>/status", methods=["POST"])
@login_required
def update_task_status(task_id):
    task = get_task_or_404(task_id)
    status = request.form.get("status", "")
    if status not in {value for value, _label in TASK_STATUSES}:
        flash("Choose a valid task status.", "danger")
        return redirect(url_for("management.tasks"))

    if not can_change_task_status(task):
        abort(403)

    task.status = status
    db.session.commit()
    flash("Task status updated.", "success")
    return redirect(url_for("management.tasks"))


def _risk_filter_id(name):
    value = request.args.get(name)
    if value in (None, ""):
        return None
    try:
        filter_id = int(value)
    except ValueError:
        abort(400, description=f"Invalid {name} filter.")
    if filter_id <= 0:
        abort(400, description=f"Invalid {name} filter.")
    return filter_id


@management_bp.route("/risks")
@management_required
def risk_dashboard():
    project_id = _risk_filter_id("project_id")
    sprint_id = _risk_filter_id("sprint_id")
    severity_filter = request.args.get("severity", "").upper()
    category_filter = request.args.get("category", "")
    if severity_filter and severity_filter not in RISK_SEVERITIES:
        abort(400, description="Invalid risk severity filter.")
    if category_filter and category_filter not in RISK_CATEGORIES:
        abort(400, description="Invalid risk category filter.")

    project_query = Project.query.order_by(Project.name)
    if current_user.role != "administrator":
        project_query = project_query.filter_by(owner_id=current_user.id)
    projects = project_query.all()
    project_ids = {project.id for project in projects}

    selected_project = None
    if project_id is not None:
        selected_project = next(
            (project for project in projects if project.id == project_id), None
        )
        if selected_project is None:
            abort(404)

    selected_sprint = None
    if sprint_id is not None:
        selected_sprint = db.session.get(Sprint, sprint_id)
        if selected_sprint is None or selected_sprint.project_id not in project_ids:
            abort(404)
        if selected_project and selected_sprint.project_id != selected_project.id:
            abort(400, description="The selected sprint does not belong to the selected project.")
        if selected_project is None:
            selected_project = next(
                project
                for project in projects
                if project.id == selected_sprint.project_id
            )

    sprint_query = Sprint.query.order_by(Sprint.start_date, Sprint.name)
    if not project_ids:
        sprints = []
    else:
        sprint_query = sprint_query.filter(Sprint.project_id.in_(project_ids))
        if selected_project:
            sprint_query = sprint_query.filter_by(project_id=selected_project.id)
        if selected_sprint:
            sprint_query = sprint_query.filter_by(id=selected_sprint.id)
        sprints = sprint_query.all()

    tasks = (
        Task.query.filter(Task.sprint_id.in_([sprint.id for sprint in sprints]))
        .order_by(Task.id)
        .all()
        if sprints
        else []
    )
    members = TeamMember.query.order_by(TeamMember.name).all()
    alerts = build_risk_alerts(
        sprints,
        tasks,
        members,
        settings=current_app.config,
    )
    if severity_filter:
        alerts = [alert for alert in alerts if alert["severity"] == severity_filter]
    if category_filter:
        alerts = [alert for alert in alerts if alert["category"] == category_filter]

    severity_counts = {
        severity: sum(alert["severity"] == severity for alert in alerts)
        for severity in RISK_SEVERITIES
    }
    category_counts = {
        category: sum(alert["category"] == category for alert in alerts)
        for category in RISK_CATEGORIES
    }
    filter_sprints = (
        Sprint.query.filter_by(project_id=selected_project.id)
        .order_by(Sprint.start_date, Sprint.name)
        .all()
        if selected_project
        else sprints
    )
    return render_template(
        "management/risks.html",
        projects=projects,
        sprints=filter_sprints,
        alerts=alerts,
        total_risks=len(alerts),
        severity_counts=severity_counts,
        category_counts=category_counts,
        category_labels=RISK_CATEGORIES,
        severity_options=RISK_SEVERITIES,
        selected_project_id=selected_project.id if selected_project else None,
        selected_sprint_id=selected_sprint.id if selected_sprint else None,
        selected_severity=severity_filter,
        selected_category=category_filter,
    )


# ── Analytics route ──────────────────────────────────────────
@management_bp.route("/analytics")
@management_required
def analytics():
    """Analytics page with sprint burndown, utilization, and task stats."""
    project_id = request.args.get("project_id", type=int)
    sprint_id = request.args.get("sprint_id", type=int)

    if current_user.role == "administrator":
        projects = Project.query.order_by(Project.name).all()
    else:
        projects = Project.query.filter_by(owner_id=current_user.id).order_by(Project.name).all()

    project_ids = {p.id for p in projects}

    selected_project = None
    if project_id:
        selected_project = next((p for p in projects if p.id == project_id), None)

    sprint_q = Sprint.query.filter(Sprint.project_id.in_(project_ids)).order_by(Sprint.start_date)
    if selected_project:
        sprint_q = sprint_q.filter_by(project_id=selected_project.id)
    if sprint_id:
        sprint_q = sprint_q.filter_by(id=sprint_id)
    sprints = sprint_q.all()

    filter_sprints_q = Sprint.query.filter(Sprint.project_id.in_(project_ids)).order_by(Sprint.start_date)
    filter_sprints = filter_sprints_q.all()

    sprint_ids_set = {s.id for s in sprints}
    tasks = Task.query.filter(Task.sprint_id.in_(sprint_ids_set)).all() if sprint_ids_set else []
    members = TeamMember.query.order_by(TeamMember.name).all()

    # Build sprint rows and chart data
    sprint_rows = []
    sprint_labels = []
    sprint_completion = []
    sprint_remaining = []
    total_hours_allocated = 0.0
    total_tasks_done = 0

    for s in sprints:
        s_tasks = [t for t in tasks if t.sprint_id == s.id]
        s_total = len(s_tasks)
        s_done = sum(1 for t in s_tasks if t.status == "done")
        s_pct = round(s_done / s_total * 100, 1) if s_total > 0 else 0.0
        s_effort = sum(
            a.assigned_hours
            for t in s_tasks
            if t.status != "done"
            for a in t.assignments
        )
        sprint_rows.append({
            "name": s.name,
            "project_name": s.project.name,
            "start_date": str(s.start_date),
            "end_date": str(s.end_date),
            "total_tasks": s_total,
            "done_tasks": s_done,
            "remaining_tasks": s_total - s_done,
            "completion": s_pct,
            "remaining_effort": s_effort,
        })
        label = f"{s.project.name[:8]}/{s.name[:10]}"
        sprint_labels.append(label)
        sprint_completion.append(s_pct)
        sprint_remaining.append(round(100.0 - s_pct, 1))
        total_tasks_done += s_done
        total_hours_allocated += sum(a.assigned_hours for t in s_tasks for a in t.assignments)

    avg_completion = sum(sprint_completion) / len(sprint_completion) if sprint_completion else 0.0

    # Priority breakdown
    priority_map = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for t in tasks:
        pr = (t.priority or "medium").lower()
        if pr in priority_map:
            priority_map[pr] += 1
    priority_labels = [k.title() for k, v in priority_map.items() if v > 0]
    priority_values = [v for v in priority_map.values() if v > 0]

    # Status breakdown
    status_map = {"todo": 0, "in_progress": 0, "done": 0}
    for t in tasks:
        st = (t.status or "todo").lower()
        if st in status_map:
            status_map[st] += 1
    status_labels = ["To Do", "In Progress", "Done"]
    status_values = [status_map["todo"], status_map["in_progress"], status_map["done"]]

    # Member utilization
    member_labels = []
    member_utilization = []
    total_util = 0.0
    util_count = 0
    for m in members:
        allocated = sum(
            a.assigned_hours
            for a in m.assignments
            if a.task.sprint_id in sprint_ids_set
        )
        if m.available_hours > 0 and sprint_ids_set:
            util = round(allocated / m.available_hours * 100, 1)
        else:
            util = 0.0
        member_labels.append(m.name)
        member_utilization.append(util)
        if util > 0:
            total_util += util
            util_count += 1

    avg_utilization = round(total_util / util_count, 1) if util_count else 0.0

    analytics_data = {
        "completion_rate": round(avg_completion, 1),
        "total_hours_allocated": round(total_hours_allocated, 1),
        "avg_utilization": avg_utilization,
        "total_tasks_done": total_tasks_done,
        "sprint_labels": sprint_labels,
        "sprint_completion": sprint_completion,
        "sprint_remaining": sprint_remaining,
        "priority_labels": priority_labels,
        "priority_values": priority_values,
        "status_labels": status_labels,
        "status_values": status_values,
        "member_labels": member_labels,
        "member_utilization": member_utilization,
        "sprint_rows": sprint_rows,
    }

    return render_template(
        "management/analytics.html",
        projects=projects,
        filter_sprints=filter_sprints,
        selected_project_id=selected_project.id if selected_project else None,
        selected_sprint_id=sprint_id,
        analytics=analytics_data,
    )
