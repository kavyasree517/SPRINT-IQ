import sqlite3

import pytest

from app import create_app, db
from app.models import Task, User


@pytest.fixture
def app(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "phase7-test-secret",
            "WTF_CSRF_ENABLED": False,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'phase7.db'}",
        }
    )
    yield app
    with app.app_context():
        db.session.remove()
        db.engine.dispose()


@pytest.fixture
def client(app):
    return app.test_client()


def add_user(app, username, role="project_manager"):
    with app.app_context():
        user = User(
            username=username,
            email=f"{username}@example.com",
            role=role,
        )
        user.set_password("phase7-password")
        db.session.add(user)
        db.session.commit()
        return user.id


def sign_in(client, username):
    return client.post(
        "/api/auth/login",
        json={"email": f"{username}@example.com", "password": "phase7-password"},
    )


def test_api_authentication_registration_and_logout(client):
    registered = client.post(
        "/api/auth/register",
        json={
            "username": "new-user",
            "email": "new-user@example.com",
            "password": "secure-password",
        },
    )
    assert registered.status_code == 201
    assert registered.json["user"]["role"] == "team_member"
    assert client.post(
        "/api/auth/register",
        json={
            "username": "new-user",
            "email": "other@example.com",
            "password": "secure-password",
        },
    ).status_code == 409
    assert client.post(
        "/api/auth/login",
        json={"email": "new-user@example.com", "password": "wrong-password"},
    ).status_code == 401

    assert client.post(
        "/api/auth/login",
        json={"email": "new-user@example.com", "password": "secure-password"},
    ).status_code == 200
    user_id = registered.json["user"]["id"]
    assert client.get("/api/auth/me").json["user"]["username"] == "new-user"
    assert client.patch(
        f"/api/users/{user_id}/role", json={"role": "administrator"}
    ).status_code == 403
    assert client.post("/api/auth/logout").status_code == 200
    unauthorized = client.get("/api/auth/me")
    assert unauthorized.status_code == 401
    assert unauthorized.json["error"]["code"] == "authentication_required"


def test_project_crud_membership_and_owner_authorization(app, client):
    add_user(app, "owner")
    add_user(app, "other")
    assert sign_in(client, "owner").status_code == 200
    created = client.post(
        "/api/projects",
        json={
            "name": "Release",
            "start_date": "2026-01-01",
            "end_date": "2026-12-31",
            "status": "active",
        },
    )
    assert created.status_code == 201
    project_id = created.json["project"]["id"]

    member = client.post(
        "/api/team-members",
        json={
            "name": "Ada",
            "email": "ada@example.com",
            "skills": "Python",
            "available_hours": 20,
            "daily_capacity": 8,
        },
    )
    assert member.status_code == 201
    member_id = member.json["team_member"]["id"]
    assert client.post(
        f"/api/projects/{project_id}/members",
        json={"team_member_id": member_id},
    ).status_code == 201
    assert client.get(f"/api/projects/{project_id}/members").json["members"][0]["id"] == member_id
    assert client.post(
        f"/api/projects/{project_id}/members",
        json={"team_member_id": member_id},
    ).status_code == 409

    client.post("/api/auth/logout")
    sign_in(client, "other")
    forbidden = client.patch(
        f"/api/projects/{project_id}", json={"name": "Not allowed"}
    )
    assert forbidden.status_code == 403
    assert forbidden.json["error"]["code"] == "forbidden"


def test_sprint_task_crud_allocations_and_actual_effort(app, client):
    add_user(app, "manager")
    add_user(app, "developer", role="team_member")
    sign_in(client, "manager")
    project = client.post(
        "/api/projects",
        json={
            "name": "API project",
            "start_date": "2026-01-01",
            "end_date": "2026-12-31",
        },
    ).json["project"]
    member = client.post(
        "/api/team-members",
        json={
            "name": "Grace",
            "email": "developer@example.com",
            "skills": "Python, SQL",
            "available_hours": 40,
            "daily_capacity": 8,
        },
    ).json["team_member"]
    sprint_response = client.post(
        f"/api/projects/{project['id']}/sprints",
        json={
            "name": "Sprint 1",
            "start_date": "2026-11-02",
            "end_date": "2026-11-13",
            "status": "active",
        },
    )
    assert sprint_response.status_code == 201
    sprint_id = sprint_response.json["sprint"]["id"]
    task_response = client.post(
        "/api/tasks",
        json={
            "sprint_id": sprint_id,
            "title": "Build API",
            "required_skills": "python; SQL",
            "estimated_hours": 8,
            "priority": "high",
        },
    )
    assert task_response.status_code == 201
    task_id = task_response.json["task"]["id"]

    recommendation = client.get(f"/api/sprints/{sprint_id}/allocations")
    assert recommendation.status_code == 200
    item = recommendation.json["recommendations"][0]
    assert item["team_member"]["id"] == member["id"]
    assert set(item["components"]) == {
        "skill_match",
        "capacity_score",
        "availability_score",
        "priority_suitability",
    }
    assert item["explanations"]["skills"] == "2/2 required skills match."

    assigned = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"team_member_id": member["id"]},
    )
    assert assigned.status_code == 201
    assert assigned.json["task"]["assignments"][0]["assigned_hours"] == 8
    reassigned = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"team_member_id": member["id"]},
    )
    assert reassigned.status_code == 200
    assert len(reassigned.json["task"]["assignments"]) == 1
    invalid_estimate = client.patch(
        f"/api/tasks/{task_id}", json={"estimated_hours": 100}
    )
    assert invalid_estimate.status_code == 409
    assignee_client = app.test_client()
    sign_in(assignee_client, "developer")
    updated = assignee_client.patch(
        f"/api/tasks/{task_id}",
        json={"actual_hours": 6, "status": "in_progress"},
    )
    assert updated.status_code == 200
    assert updated.json["task"]["actual_hours"] == 6
    assert client.get(f"/api/analytics/sprints/{sprint_id}").json["actual_hours"] == 6
    project_stats = client.get(f"/api/analytics/projects/{project['id']}")
    assert project_stats.json["task_count"] == 1
    assert client.get("/api/analytics").json["tasks"]["actual_hours"] == 6

    ineligible = client.post(
        "/api/tasks",
        json={
            "sprint_id": sprint_id,
            "title": "Build Go service",
            "required_skills": "Go",
            "estimated_hours": 5,
        },
    ).json["task"]
    assert client.post(
        f"/api/tasks/{ineligible['id']}/assignments",
        json={"team_member_id": member["id"]},
    ).status_code == 409

    assert client.patch(
        f"/api/sprints/{sprint_id}", json={"status": "completed"}
    ).status_code == 200


def test_workload_overload_risks_and_manager_only_analytics(app, client):
    add_user(app, "manager")
    sign_in(client, "manager")
    project = client.post(
        "/api/projects",
        json={
            "name": "Risk project",
            "start_date": "2026-01-01",
            "end_date": "2026-12-31",
        },
    ).json["project"]
    member = client.post(
        "/api/team-members",
        json={
            "name": "Lin",
            "skills": "Python",
            "available_hours": 8,
            "daily_capacity": 8,
        },
    ).json["team_member"]
    sprint = client.post(
        f"/api/projects/{project['id']}/sprints",
        json={
            "name": "Risk sprint",
            "start_date": "2026-11-02",
            "end_date": "2026-11-13",
        },
    ).json["sprint"]
    task = client.post(
        "/api/tasks",
        json={
            "sprint_id": sprint["id"],
            "title": "Urgent task",
            "required_skills": "Python",
            "estimated_hours": 10,
            "priority": "high",
            "due_date": "2026-11-01",
        },
    ).json["task"]
    assert client.post(
        f"/api/tasks/{task['id']}/assignments",
        json={
            "team_member_id": member["id"],
            "assigned_hours": 10,
            "override": True,
        },
    ).status_code == 201
    workload = client.get(f"/api/sprints/{sprint['id']}/workload")
    assert workload.status_code == 200
    assert workload.json["members"][0]["classification"] == "overloaded"

    response = client.get(f"/api/risks?project_id={project['id']}")
    assert response.status_code == 200
    assert response.json["summary"]["high"] >= 1
    assert any(risk["reason"] for risk in response.json["risks"])
    assert client.get("/api/analytics/developers").status_code == 200

    client.post("/api/auth/logout")
    client.post(
        "/api/auth/register",
        json={
            "username": "member",
            "email": "member@example.com",
            "password": "secure-password",
        },
    )
    client.post(
        "/api/auth/login",
        json={"email": "member@example.com", "password": "secure-password"},
    )
    assert client.get("/api/analytics/developers").status_code == 403
    assert client.get(f"/api/sprints/{sprint['id']}/workload").status_code == 403


def test_api_write_requires_csrf_token(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "phase7-csrf-test-secret",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'csrf.db'}",
        }
    )
    client = app.test_client()
    rejected = client.post(
        "/api/auth/register",
        json={
            "username": "csrf-user",
            "email": "csrf@example.com",
            "password": "secure-password",
        },
    )
    assert rejected.status_code == 400
    assert rejected.json["error"]["code"] == "csrf_error"

    token = client.get("/api/csrf-token").json["csrf_token"]
    accepted = client.post(
        "/api/auth/register",
        json={
            "username": "csrf-user",
            "email": "csrf@example.com",
            "password": "secure-password",
        },
        headers={"X-CSRFToken": token},
    )
    assert accepted.status_code == 201
    with app.app_context():
        db.session.remove()
        db.engine.dispose()


def test_actual_effort_migration_preserves_existing_tasks(tmp_path):
    database_path = tmp_path / "existing.db"
    connection = sqlite3.connect(database_path)
    connection.execute(
        """
        CREATE TABLE tasks (
            id INTEGER PRIMARY KEY,
            sprint_id INTEGER NOT NULL,
            title VARCHAR(200) NOT NULL,
            description TEXT,
            priority VARCHAR(32) NOT NULL DEFAULT 'medium',
            estimated_hours FLOAT NOT NULL DEFAULT 0,
            status VARCHAR(32) NOT NULL DEFAULT 'todo',
            due_date DATE
        )
        """
    )
    connection.execute(
        "INSERT INTO tasks (id, sprint_id, title, estimated_hours) VALUES (9, 1, 'Existing', 5)"
    )
    connection.commit()
    connection.close()

    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "phase7-migration-secret",
            "WTF_CSRF_ENABLED": False,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database_path}",
        }
    )
    with app.app_context():
        existing = db.session.get(Task, 9)
        assert existing.title == "Existing"
        assert existing.estimated_hours == 5
        assert existing.actual_hours == 0
        db.session.remove()
        db.engine.dispose()

