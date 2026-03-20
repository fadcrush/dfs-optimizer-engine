"""
User Model - The foundation of our 100K+ user base!
"""

from sqlalchemy import Column, String, Boolean, DateTime, Integer
from sqlalchemy.orm import declarative_base  # sqlalchemy.ext.declarative is deprecated
from datetime import datetime, timezone
import uuid

Base = declarative_base()

class User(Base):
    """User model - Each one represents $29-79/month! 💰"""
    
    __tablename__ = "users"
    
    # Primary key
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    
    # Authentication
    email = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    email_verified = Column(Boolean, default=False)
    
    # Profile
    full_name = Column(String)
    
    # Subscription (Free/Pro/Elite/Enterprise)
    tier = Column(String, default="free")  # free, pro, elite, enterprise
    subscription_status = Column(String, default="active")  # active, cancelled, expired
    subscription_expires_at = Column(DateTime, nullable=True)

    # Stripe billing
    stripe_customer_id = Column(String, nullable=True, unique=True)
    stripe_subscription_id = Column(String, nullable=True)

    # Password reset (HMAC token stored as SHA-256 hash)
    password_reset_token_hash = Column(String, nullable=True)
    password_reset_expires_at = Column(DateTime, nullable=True)
    
    # Tracking
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    last_login_at = Column(DateTime, nullable=True)
    
    # Usage stats (for analytics)
    lineups_generated = Column(Integer, default=0)
    slates_processed = Column(Integer, default=0)

    # Admin flag
    is_admin = Column(Boolean, default=False)

    def __repr__(self):
        return f"<User {self.email} - {self.tier}>"
    
    def to_dict(self):
        """Convert to dictionary for API responses"""
        return {
            "id": self.id,
            "email": self.email,
            "full_name": self.full_name,
            "tier": self.tier,
            "subscription_status": self.subscription_status,
            "email_verified": self.email_verified,
            "stripe_customer_id": self.stripe_customer_id,
            "stripe_subscription_id": self.stripe_subscription_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_login_at": self.last_login_at.isoformat() if self.last_login_at else None,
            "lineups_generated": self.lineups_generated,
            "slates_processed": self.slates_processed,
            "is_admin": self.is_admin,
        }