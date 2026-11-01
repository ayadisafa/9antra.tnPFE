"""Social media (services/social_*.py): the centre's posts and their engagement, the answers to feedback (“you said, we
did”), and the pictures and videos the application makes for them."""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

from app.database import Base


class SocialPost(Base):
    """One post of the centre on a social network, with the numbers it got (typed, or read from the network)."""
    __tablename__ = "social_posts"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    platform = Column(String, nullable=False, default="facebook")     # facebook | instagram | linkedin | tiktok | other
    kind = Column(String, nullable=False, default="post")             # post | story | video
    topic = Column(String, nullable=True)                             # success | testimonial | listen | program | trend | join | other
    text = Column(Text, nullable=True)
    link = Column(String, nullable=True)
    published_at = Column(DateTime, nullable=True, index=True)
    reactions = Column(Integer, nullable=True)
    comments = Column(Integer, nullable=True)
    shares = Column(Integer, nullable=True)
    saves = Column(Integer, nullable=True)
    clicks = Column(Integer, nullable=True)
    views = Column(Integer, nullable=True)                            # reach or views, as the network says
    followers = Column(Integer, nullable=True)                        # the page's followers when the numbers were read
    source = Column(String, nullable=False, default="typed")          # typed | generated | facebook
    external_id = Column(String, nullable=True, index=True)
    asset_id = Column(String, nullable=True)                          # the picture or video made here, when it was one
    numbers_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class FeedbackAnswer(Base):
    """What the centre did about what people said: “You said …, we did …” (shown in posts and videos)."""
    __tablename__ = "feedback_answers"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    theme = Column(String, nullable=True)                             # a key of feedback.THEMES, when it came from one
    you_said = Column(Text, nullable=False)
    we_did = Column(Text, nullable=True)
    active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class SocialAsset(Base):
    """A picture or a video made for social media, kept as a file in SOCIAL_DIR."""
    __tablename__ = "social_assets"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    kind = Column(String, nullable=False)                             # image | video
    title = Column(String, nullable=True)
    file = Column(String, nullable=True)                              # the file's name in the folder
    size = Column(Integer, nullable=True)
    status = Column(String, nullable=False, default="ready")          # working | ready | failed
    spec = Column(Text, nullable=True)                                # what it was made from, as JSON
    error = Column(Text, nullable=True)
    seconds = Column(Integer, nullable=True)                          # a video's length
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
