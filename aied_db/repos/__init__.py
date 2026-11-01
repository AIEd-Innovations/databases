"""One repository per agent: the database calls its routes need, and no more.

Each takes a guarded database and a course_id:

    db = guarded(get_db(), Role.ASSESSMENT_AGENT, actor_id="assessment_agent")
    repo = AssessmentRepo(db, course_id=course_id)

Repos return plain dicts with ISO timestamps, like the stores, so a FastAPI
route can hand one straight back.
"""

from .assessment import AssessmentRepo
from .integrity import IntegrityRepo
from .lab_companion import LabCompanionRepo

__all__ = ["AssessmentRepo", "IntegrityRepo", "LabCompanionRepo"]
