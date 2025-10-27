# database.py
import os
from sqlalchemy import create_engine, Column, String, JSON, DateTime, func
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# Connect to Render PostgreSQL (DATABASE_URL from env)
engine = create_engine(os.getenv('DATABASE_URL'), pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class UserSession(Base):
    __tablename__ = "user_sessions"

    phone = Column(String, primary_key=True)  # WhatsApp number
    step = Column(String, default="welcome")  # Current bot step
    data = Column(JSON, default=dict)         # Stores amount, account, etc.
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

# Create table if not exists
Base.metadata.create_all(bind=engine)

# === HELPER FUNCTIONS ===
def get_session(phone):
    db = SessionLocal()
    try:
        result = db.query(UserSession).filter(UserSession.phone == phone).first()
        if result:
            return {"step": result.step, "data": result.data or {}}
        return {"step": "welcome", "data": {}}
    finally:
        db.close()

def save_session(phone, step, data=None):
    db = SessionLocal()
    try:
        session = db.query(UserSession).filter(UserSession.phone == phone).first()
        if session:
            session.step = step
            if data:
                session.data = {**session.data, **data}
        else:
            new_data = data or {}
            session = UserSession(phone=phone, step=step, data=new_data)
            db.add(session)
        db.commit()
    finally:
        db.close()