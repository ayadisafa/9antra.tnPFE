from .activity import Activity
from .bias import RegistrationBias  # <-- Added bias model
from .candidate import Candidate
from .classgroup import ClassGroup, ClassStatus
from .notification import Notification
from .payment import Payment
from .program import Program
from .quiz import Quiz
from .quiz_agent_message import QuizAgentMessage
from .registration import Registration
from .programprogress import ProgramProgress  # <-- Added program progress model
from .professor import Professor
from .classsession import ClassSession
from .skill import Skill
from .scheduling import ProfessorAvailability, CourseBatch, BatchSession
from .professor_absence import ProfessorAbsence
from .candidate_reminder import CandidateReminder
from .candidate_email import CandidateEmail
from .candidate_absence import CandidateAbsence
from .feedback import CandidateFeedback
from .admin_config import SystemSettings
from .email_thread import EmailThread, MailboxState
from .knowledge import KnowledgeEntry
from .email_template import EmailTemplate
from .email_file import EmailFile
from .program_proposal import ProgramProposal
from .course_material import AdviceSnooze, ClassMaterial, ClassWeek, WeekQuestion
from .user import Attendance, User, UserSession
from .website import WebsiteCheck, WebsiteItem
from .session_join import SessionJoin
from .market_offer import MarketOffer
from .admin_reminder import AdminReminder
from .ui_translation import UiTranslation
from .edit_log import EditLog

__all__ = [
    "MarketOffer",
    "AdminReminder",
    "UiTranslation",
    "EditLog",
    "SessionJoin",
    "WebsiteItem",
    "WebsiteCheck",
    "User",
    "UserSession",
    "Attendance",
    "ProgramProposal",
    "ClassMaterial",
    "ClassWeek",
    "WeekQuestion",
    "AdviceSnooze",
    "SystemSettings",
    "EmailFile",
    "EmailThread",
    "MailboxState",
    "KnowledgeEntry",
    "EmailTemplate",
    "Activity",
    "RegistrationBias",
    "Candidate",
    "ClassGroup",
    "ClassStatus",
    "Notification",
    "Payment",
    "Program",
    "Quiz",
    "QuizAgentMessage",
    "Registration",
    "ProgramProgress",
    "Professor",
    "Skill",
    "ProfessorAvailability",
    "CourseBatch",
    "BatchSession",
    "ProfessorAbsence",
    "CandidateReminder",
    "CandidateEmail",
    "CandidateAbsence",
    "CandidateFeedback",
]