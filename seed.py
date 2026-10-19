"""
Database seed script.

Creates one admin, one receptionist, three doctors and two patients with
realistic sample data. Safe to run multiple times — skips
if the admin account already exists.

Usage:
    python seed.py
"""

import os
import sys
from datetime import time

# Ensure the project root is on the path
sys.path.insert(0, os.path.dirname(__file__))

from app import create_app, db
from app.models import Department, User, Doctor, Patient


def seed():
    """Populate the database with initial sample data."""
    app = create_app(os.environ.get("FLASK_ENV", "development"))

    with app.app_context():
        # Check if already seeded
        if User.query.filter_by(email="admin@hms.com").first():
            print("Database already seeded. Skipping.")
            return

        # ── Admin ─────────────────────────────────────────────────────────
        admin = User(name="Admin User", email="admin@hms.com", role="admin")
        admin.set_password("admin123")
        db.session.add(admin)

        # ── Reception ─────────────────────────────────────────────────────
        desk = User(name="Front Desk", email="desk@hms.com", role="reception")
        desk.set_password("reception123")
        db.session.add(desk)

        # ── Doctors ───────────────────────────────────────────────────────
        doctors_data = [
            {
                "name": "Dr. Priya Sharma",
                "email": "priya@hms.com",
                "specialization": "Cardiology",
                "qualification": "MD, DM Cardiology",
                "experience_years": 12,
                "consultation_fee": 800.0,
                "work_start": time(9, 0),
                "work_end": time(17, 0),
                "slot_minutes": 30,
                "available_days": "Mon,Tue,Wed,Thu,Fri",
            },
            {
                "name": "Dr. Rajesh Patel",
                "email": "rajesh@hms.com",
                "specialization": "Orthopedics",
                "qualification": "MS Orthopedics, FICS",
                "experience_years": 15,
                "consultation_fee": 1000.0,
                "work_start": time(10, 0),
                "work_end": time(18, 0),
                "slot_minutes": 30,
                "available_days": "Mon,Tue,Wed,Thu,Fri,Sat",
            },
            {
                "name": "Dr. Anita Desai",
                "email": "anita@hms.com",
                "specialization": "Dermatology",
                "qualification": "MD Dermatology",
                "experience_years": 8,
                "consultation_fee": 600.0,
                "work_start": time(9, 0),
                "work_end": time(15, 0),
                "slot_minutes": 20,
                "available_days": "Mon,Wed,Fri",
            },
        ]

        for d in doctors_data:
            user = User(name=d["name"], email=d["email"], role="doctor")
            user.set_password("doctor123")
            db.session.add(user)
            db.session.flush()

            dept = Department.query.filter_by(name=d["specialization"]).first()
            if dept is None:
                dept = Department(name=d["specialization"])
                db.session.add(dept)
                db.session.flush()
            doctor = Doctor(
                department_id=dept.id,
                user_id=user.id,
                specialization=d["specialization"],
                qualification=d["qualification"],
                experience_years=d["experience_years"],
                consultation_fee=d["consultation_fee"],
                work_start=d["work_start"],
                work_end=d["work_end"],
                slot_minutes=d["slot_minutes"],
                available_days=d["available_days"],
            )
            db.session.add(doctor)

        # ── Patients ──────────────────────────────────────────────────────
        patients_data = [
            {
                "name": "Amit Kumar",
                "email": "amit@hms.com",
                "age": 32,
                "gender": "Male",
                "phone": "+91-9876543210",
                "blood_group": "O+",
                "address": "42 MG Road, New Delhi",
            },
            {
                "name": "Sneha Reddy",
                "email": "sneha@hms.com",
                "age": 28,
                "gender": "Female",
                "phone": "+91-9123456789",
                "blood_group": "B+",
                "address": "15 Banjara Hills, Hyderabad",
            },
        ]

        for p in patients_data:
            user = User(name=p["name"], email=p["email"], role="patient")
            user.set_password("patient123")
            db.session.add(user)
            db.session.flush()

            patient = Patient(
                user_id=user.id,
                age=p["age"],
                gender=p["gender"],
                phone=p["phone"],
                blood_group=p["blood_group"],
                address=p["address"],
            )
            db.session.add(patient)

        db.session.commit()
        print("Database seeded successfully!")
        print()
        print("  Login credentials:")
        print("  ──────────────────────────────────────────")
        print("  Admin:   admin@hms.com      / admin123")
        print("  Reception: desk@hms.com     / reception123")
        print("  Doctor:  priya@hms.com       / doctor123")
        print("  Doctor:  rajesh@hms.com      / doctor123")
        print("  Doctor:  anita@hms.com       / doctor123")
        print("  Patient: amit@hms.com        / patient123")
        print("  Patient: sneha@hms.com       / patient123")
        print("  ──────────────────────────────────────────")


if __name__ == "__main__":
    seed()
