from datetime import date
import json
import pytest

from app import create_app, db
from app.models import Project, Sprint, Task, TaskAssignment, TeamMember, User


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
        "/auth/login",
        data={
            "email": f"{username}@example.com",
            "password": "phase7-password",
        },
        follow_redirects=True,
    )


def test_analytics_requires_login(client):
    res = client.get("/analytics")
    assert res.status_code == 302


def test_analytics_manager_access_and_render(app, client):
    manager_id = add_user(app, "analytics-manager", role="project_manager")
    with app.app_context():
        manager = db.session.get(User, manager_id)
        project = Project(
            owner=manager,
            name="Analytics Alpha",
            start_date=date(2026, 4, 1),
            end_date=date(2026, 4, 30),
            status="active",
        )
        sprint = Sprint(
            project=project,
            name="Sprint One",
            start_date=date(2026, 4, 6),
            end_date=date(2026, 4, 17),
            status="active",
        )
        member = TeamMember(
            name="Jane Developer",
            email="jane@example.com",
            skills="Python, SQL",
            available_hours=40,
            daily_capacity=8,
        )
        t1 = Task(
            sprint=sprint,
            title="Analytics task 1",
            priority="high",
            estimated_hours=5,
            status="done",
        )
        t2 = Task(
            sprint=sprint,
            title="Analytics task 2",
            priority="medium",
            estimated_hours=10,
            status="in_progress",
        )
        db.session.add_all([project, sprint, member, t1, t2])
        db.session.flush()
        db.session.add(TaskAssignment(task=t1, team_member=member, assigned_hours=5))
        db.session.commit()
        p_id = project.id
        s_id = sprint.id

    sign_in(client, "analytics-manager")
    res = client.get("/analytics")
    assert res.status_code == 200
    assert b"Analytics" in res.data
    assert b"Sprint Performance Report" in res.data
    assert b"Analytics Alpha" in res.data
    assert b"Sprint One" in res.data

    # Filtered by project
    res_filtered = client.get(f"/analytics?project_id={p_id}")
    assert res_filtered.status_code == 200

    # Filtered by sprint
    res_sprint = client.get(f"/analytics?sprint_id={s_id}")
    assert res_sprint.status_code == 200


def test_analytics_team_member_forbidden(app, client):
    add_user(app, "regular-worker", role="team_member")
    sign_in(client, "regular-worker")
    res = client.get("/analytics")
    assert res.status_code == 403
