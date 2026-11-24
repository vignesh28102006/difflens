import os
from typing import Generator, Optional
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base, Repository

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./difflens.db")

# SQLite needs check_same_thread=False for FastAPI concurrency
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine: Engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db(engine_override: Optional[Engine] = None) -> None:
    """Initializes schema and tables."""
    target_engine = engine_override or engine
    Base.metadata.create_all(bind=target_engine)


def get_db() -> Generator[Session, None, None]:
    """FastAPI DB session dependency."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_or_create_repo(
    session: Session,
    full_name: str,
    github_repo_id: Optional[int] = None,
    installation_id: Optional[int] = None,
) -> Repository:
    """Gets existing repository by full_name or creates a new record."""
    repo = session.query(Repository).filter(Repository.full_name == full_name).first()
    if not repo:
        repo = Repository(
            full_name=full_name,
            github_repo_id=github_repo_id,
            installation_id=installation_id,
        )
        session.add(repo)
        session.commit()
        session.refresh(repo)
    else:
        # Update IDs if newly provided
        updated = False
        if github_repo_id and repo.github_repo_id != github_repo_id:
            repo.github_repo_id = github_repo_id
            updated = True
        if installation_id and repo.installation_id != installation_id:
            repo.installation_id = installation_id
            updated = True
        if updated:
            session.commit()
            session.refresh(repo)
    return repo
