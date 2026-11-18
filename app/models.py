from datetime import datetime, timezone

from flask_login import UserMixin
from sqlalchemy import CheckConstraint, UniqueConstraint, func
from werkzeug.security import check_password_hash, generate_password_hash

from . import db


class User(UserMixin, db.Model):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role IN ('administrator', 'project_manager', 'team_member')",
            name="ck_users_role",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(
        db.String(32), nullable=False, default="team_member", server_default="team_member"
    )
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )

    team_member = db.relationship("TeamMember", back_populates="user", uselist=False)
    owned_projects = db.relationship("Project", back_populates="owner")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f"<User {self.username}>"


class Project(db.Model):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint("end_date IS NULL OR start_date IS NULL OR end_date >= start_date", name="ck_projects_dates"),
    )

    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text)
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)
    status = db.Column(db.String(32), nullable=False, default="planning", server_default="planning")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=func.now())

    owner = db.relationship("User", back_populates="owned_projects")
    sprints = db.relationship("Sprint", back_populates="project", cascade="all, delete-orphan")
    memberships = db.relationship(
        "ProjectMember", back_populates="project", cascade="all, delete-orphan"
    )


class TeamMember(db.Model):
    __tablename__ = "team_members"
    __table_args__ = (
        CheckConstraint("available_hours >= 0", name="ck_team_members_available_hours"),
        CheckConstraint("daily_capacity >= 0", name="ck_team_members_daily_capacity"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), unique=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True)
    skills = db.Column(db.Text, nullable=False, default="", server_default="")
    available_hours = db.Column(db.Float, nullable=False, default=0)
    daily_capacity = db.Column(db.Float, nullable=False, default=8)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=func.now())

    user = db.relationship("User", back_populates="team_member")
    assignments = db.relationship(
        "TaskAssignment", back_populates="team_member", cascade="all, delete-orphan"
    )
    allocation_rejections = db.relationship(
        "AllocationRejection",
        back_populates="team_member",
        cascade="all, delete-orphan",
    )
    project_memberships = db.relationship(
        "ProjectMember", back_populates="team_member", cascade="all, delete-orphan"
    )


class ProjectMember(db.Model):
    __tablename__ = "project_members"
    __table_args__ = (
        UniqueConstraint("project_id", "team_member_id", name="uq_project_member"),
    )

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(
        db.Integer, db.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    team_member_id = db.Column(
        db.Integer, db.ForeignKey("team_members.id", ondelete="CASCADE"), nullable=False
    )
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    project = db.relationship("Project", back_populates="memberships")
    team_member = db.relationship("TeamMember", back_populates="project_memberships")


class Sprint(db.Model):
    __tablename__ = "sprints"
    __table_args__ = (
        CheckConstraint("end_date >= start_date", name="ck_sprints_dates"),
    )

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(32), nullable=False, default="planned", server_default="planned")
    sprint_goal = db.Column(db.Text)

    project = db.relationship("Project", back_populates="sprints")
    tasks = db.relationship("Task", back_populates="sprint", cascade="all, delete-orphan")


class Task(db.Model):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint("estimated_hours >= 0", name="ck_tasks_estimated_hours"),
        CheckConstraint("actual_hours >= 0", name="ck_tasks_actual_hours"),
    )

    id = db.Column(db.Integer, primary_key=True)
    sprint_id = db.Column(db.Integer, db.ForeignKey("sprints.id", ondelete="CASCADE"), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    required_skills = db.Column(db.Text, nullable=False, default="", server_default="")
    priority = db.Column(db.String(32), nullable=False, default="medium", server_default="medium")
    estimated_hours = db.Column(db.Float, nullable=False, default=0)
    actual_hours = db.Column(db.Float, nullable=False, default=0, server_default="0")
    status = db.Column(db.String(32), nullable=False, default="todo", server_default="todo")
    due_date = db.Column(db.Date)

    sprint = db.relationship("Sprint", back_populates="tasks")
    assignments = db.relationship("TaskAssignment", back_populates="task", cascade="all, delete-orphan")
    allocation_rejections = db.relationship(
        "AllocationRejection", back_populates="task", cascade="all, delete-orphan"
    )


class TaskAssignment(db.Model):
    __tablename__ = "task_assignments"
    __table_args__ = (
        UniqueConstraint("task_id", "team_member_id", name="uq_assignment_task_member"),
        CheckConstraint("assigned_hours >= 0", name="ck_assignments_hours"),
    )

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    team_member_id = db.Column(
        db.Integer, db.ForeignKey("team_members.id", ondelete="CASCADE"), nullable=False
    )
    assigned_hours = db.Column(db.Float, nullable=False, default=0)
    assigned_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=func.now())

    task = db.relationship("Task", back_populates="assignments")
    team_member = db.relationship("TeamMember", back_populates="assignments")


class AllocationRejection(db.Model):
    __tablename__ = "allocation_rejections"
    __table_args__ = (
        UniqueConstraint("task_id", "team_member_id", name="uq_rejection_task_member"),
    )

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    team_member_id = db.Column(
        db.Integer, db.ForeignKey("team_members.id", ondelete="CASCADE"), nullable=False
    )
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=func.now())

    task = db.relationship("Task", back_populates="allocation_rejections")
    team_member = db.relationship("TeamMember", back_populates="allocation_rejections")
