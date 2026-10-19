import shutil
from datetime import date, time, timedelta

import pytest

from app import create_app, db
from app.models import Doctor, Patient, User


@pytest.fixture()
def app():
    app = create_app("testing")
    with app.app_context():
        db.create_all()
        _seed()
        yield app
        db.session.remove()
        db.drop_all()
    shutil.rmtree(app.config["UPLOAD_FOLDER"], ignore_errors=True)


@pytest.fixture()
def client(app):
    return app.test_client()


def _user(name, email, role, password="password123"):
    u = User(name=name, email=email, role=role)
    u.set_password(password)
    db.session.add(u)
    db.session.flush()
    return u


def _seed():
    _user("Admin", "admin@t.com", "admin")
    _user("Desk", "desk@t.com", "reception")
    d = _user("Dr. Who", "doc@t.com", "doctor")
    db.session.add(Doctor(
        user_id=d.id, specialization="General", qualification="MBBS",
        work_start=time(9, 0), work_end=time(11, 0), slot_minutes=30,
        available_days="Mon,Tue,Wed,Thu,Fri,Sat,Sun",
    ))
    for n in ("pat", "pat2"):
        p = _user(n.title(), f"{n}@t.com", "patient")
        db.session.add(Patient(user_id=p.id, age=30, gender="Male", phone="1234567"))
    db.session.commit()


def login(client, email, password="password123"):
    return client.post("/login", data={"email": email, "password": password}, follow_redirects=False)


@pytest.fixture()
def tomorrow():
    return date.today() + timedelta(days=1)
