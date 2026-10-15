from app.database import Base
from app.database import engine

from app.models.candidate import Candidate
from app.models.notification import Notification
from app.models.activity import Activity
from app.models.quiz import Quiz
from app.models.candidate_course import CandidateCourse

Base.metadata.create_all(
    bind=engine
)

print(
    "Tables created successfully"
)