import os
import sys

# FIXED: Use gevent instead of eventlet for better stability
try:
    from gevent import monkey
    monkey.patch_all()
    print("✓ Gevent monkey patching applied")
except ImportError:
    print("⚠ Gevent not available, using default threading")

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, TextAreaField, SelectField, FileField, BooleanField, IntegerField
from wtforms.validators import DataRequired, Email, Length, Optional
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta
import boto3
from botocore.exceptions import NoCredentialsError, ClientError
import stripe
from config import get_config
import re
import json
import uuid
import pytz
from flask_socketio import SocketIO, emit, join_room, leave_room
import time
import jwt
from datetime import datetime, timedelta
import logging
import signal
import threading
import requests
import base64
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
import textwrap

from flask_mail import Mail, Message
from itsdangerous import URLSafeTimedSerializer


# Later in the file (around line 60-90), your LiveKit section should look like:

# LiveKit - Manual token generation (no SDK needed for server-side)
LIVEKIT_AVAILABLE = False  # SDK not needed
TOKEN_AVAILABLE = True     # We generate tokens manually
AccessToken = None
VideoGrants = None
api = None

print("✓ Using manual LiveKit token generation")


# Initialize Flask app
app = Flask(__name__)

APP_UPDATE_DISCORD_WEBHOOK_URL = app.config.get('APP_UPDATE_DISCORD_WEBHOOK_URL')

app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your-secret-key-here')
serializer = URLSafeTimedSerializer(app.config['SECRET_KEY'])

app.config['MAIL_SERVER'] = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = int(os.environ.get('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.environ.get('MAIL_USE_TLS', 'True').lower() == 'true'
app.config['MAIL_USE_SSL'] = os.environ.get('MAIL_USE_SSL', 'False').lower() == 'true'
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_USERNAME')
# FIXED: Load configuration with better error handling
try:
    config_class = get_config()
    app.config.from_object(config_class)
    print("✓ Configuration loaded successfully")
except Exception as e:
    print(f"❌ Configuration error: {e}")
    sys.exit(1)

# FIXED: Initialize Stripe with error handling
try:
    stripe.api_key = app.config.get('STRIPE_SECRET_KEY')
    if stripe.api_key:
        print("✓ Stripe API key configured")
    else:
        print("⚠ Stripe API key not found")
except Exception as e:
    print(f"⚠ Stripe initialization error: {e}")

# FIXED: Initialize SocketIO with gevent and better configuration
try:
    socketio = SocketIO(
        app, 
        cors_allowed_origins="*", 
        async_mode='gevent',  # Changed from threading to gevent
        logger=False,
        engineio_logger=False,
        ping_timeout=60,
        ping_interval=25,
        # FIXED: Add additional stability options
        allow_upgrades=True,
        transports=['websocket', 'polling']
    )
    print("✓ SocketIO initialized with gevent")
except Exception as e:
    print(f"❌ SocketIO initialization error: {e}")
    # Fallback without SocketIO
    socketio = None

# FIXED: Initialize database with better error handling and retry logic
db = None
max_retries = 3
retry_count = 0

while retry_count < max_retries:
    try:
        db = SQLAlchemy(app)
        # Test the database connection
        with app.app_context():
            db.engine.connect()
        print("✓ Database connection established successfully")
        break
    except Exception as e:
        retry_count += 1
        print(f"❌ Database connection attempt {retry_count} failed: {e}")
        if retry_count >= max_retries:
            print("❌ Maximum database connection retries exceeded")
            if 'sqlite' not in app.config.get('SQLALCHEMY_DATABASE_URI', ''):
                print("⚠ Falling back to SQLite for development")
                app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///fallback.db'
                try:
                    db = SQLAlchemy(app)
                    with app.app_context():
                        db.engine.connect()
                    print("✓ Fallback SQLite database connected")
                    break
                except Exception as fallback_error:
                    print(f"❌ Even SQLite fallback failed: {fallback_error}")
                    sys.exit(1)
        else:
            time.sleep(2)  # Wait before retry

if db is None:
    print("❌ Database initialization completely failed")
    sys.exit(1)

# FIXED: Configure Login Manager with better error handling
try:
    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = 'login'
    print("✓ Login manager configured")
except Exception as e:
    print(f"❌ Login manager error: {e}")
    sys.exit(1)

# Initialize Flask-Mail
try:
    mail = Mail(app)
    print("✓ Flask-Mail initialized successfully")
except Exception as e:
    print(f"❌ Flask-Mail initialization error: {e}")
    mail = None

# Association table for many-to-many relationship between videos and tags
video_tags = db.Table('video_tags',
    db.Column('video_id', db.Integer, db.ForeignKey('videos.id'), primary_key=True),
    db.Column('tag_id', db.Integer, db.ForeignKey('tags.id'), primary_key=True)
)


# Models - MySQL Optimized

class TradingSignalForm(FlaskForm):
    #date = db.Column(db.Date, default=datetime.utcnow().date)
    trader_name = SelectField('Trader', choices=[
        ('Ray', 'Ray'),
        ('Jordan', 'Jordan')
    ], validators=[DataRequired()])
    pair_name = SelectField('Trading Pair', choices=[
        ('EURUSD', 'EUR/USD'),
        ('XAUUSD', 'Gold (XAU/USD)'),
        ('NQ', 'NASDAQ Futures (NQ)'),
        ('GBPUSD', 'GBP/USD'),
        ('USDJPY', 'USD/JPY'),
        ('EURJPY', 'EUR/JPY'),
        ('GBPJPY', 'GBP/JPY'),
        ('AUDUSD', 'AUD/USD'),
        ('NZDUSD', 'NZD/USD'),
        ('ES', 'S&P 500 Futures (ES)'),
        ('YM', 'Dow Jones Futures (YM)')
    ], validators=[DataRequired()])
    trade_type = SelectField('Trade Type', choices=[
        ('Buy', 'Buy'),
        ('Sell', 'Sell')
    ], validators=[DataRequired()])
    entry_price = StringField('Entry Price', validators=[DataRequired()], 
                             render_kw={"placeholder": "1.0500", "step": "0.00001"})
    stop_loss_price = StringField('Stop Loss Price', validators=[DataRequired()], 
                                 render_kw={"placeholder": "1.0450", "step": "0.00001"})
    target_price = StringField('Target Price', validators=[DataRequired()], 
                              render_kw={"placeholder": "1.0650", "step": "0.00001"})
    risk_reward_ratio = StringField('Risk/Reward Ratio', validators=[DataRequired()], 
                                   render_kw={"placeholder": "3.0", "step": "0.1"})
    outcome = SelectField('Outcome', choices=[
        ('Win', 'Win'),
        ('Loss', 'Loss'),
        ('Breakeven', 'Breakeven')
    ], validators=[DataRequired()])
    
    # UPDATED: Renamed and clarified field descriptions
    actual_rr = StringField('Actual R Outcome', validators=[DataRequired()], 
                           render_kw={"placeholder": "3.0 for win, -1.0 for loss", "step": "0.1"})
    
    # NEW: Maximum favorable excursion field
    achieved_rr = StringField('Achieved R (Max Favorable)', validators=[Optional()], 
                             render_kw={"placeholder": "1.5 (how far trade went in your favor)", "step": "0.1"})
    
    notes = TextAreaField('Notes', validators=[Optional()], 
                         render_kw={"placeholder": "Optional trading notes..."})
    linked_video_id = SelectField('Link to Trading Video', choices=[], coerce=int, validators=[Optional()])
    
class User(UserMixin, db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    
    # Enhanced subscription fields
    has_subscription = db.Column(db.Boolean, default=False, nullable=False)
    subscription_expires = db.Column(db.DateTime, nullable=True)
    
    # NEW: Stripe integration fields
    stripe_customer_id = db.Column(db.String(100), nullable=True, index=True)
    stripe_subscription_id = db.Column(db.String(100), nullable=True, index=True)
    subscription_status = db.Column(db.String(50), nullable=True)  # active, canceled, past_due, etc.
    subscription_plan = db.Column(db.String(50), nullable=True)  # monthly, annual
    subscription_price_id = db.Column(db.String(100), nullable=True)
    subscription_current_period_start = db.Column(db.DateTime, nullable=True)
    subscription_current_period_end = db.Column(db.DateTime, nullable=True)
    subscription_cancel_at_period_end = db.Column(db.Boolean, default=False, nullable=False)
    
    # Payment tracking
    total_revenue = db.Column(db.Numeric(10, 2), default=0.00, nullable=False)
    last_payment_date = db.Column(db.DateTime, nullable=True)
    last_payment_amount = db.Column(db.Numeric(10, 2), nullable=True)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    
    # Stream-related fields (existing)
    display_name = db.Column(db.String(100), nullable=True)
    can_stream = db.Column(db.Boolean, default=False, nullable=False)
    stream_color = db.Column(db.String(7), default='#10B981', nullable=False)
    timezone = db.Column(db.String(50), default='America/Chicago', nullable=False)
    
    # Relationships (existing)
    progress = db.relationship('UserProgress', backref='user', lazy=True, cascade='all, delete-orphan')
    favorites = db.relationship('UserFavorite', backref='user', lazy=True, cascade='all, delete-orphan')
    created_streams = db.relationship('Stream', backref='creator', lazy=True, cascade='all, delete-orphan')
    activities = db.relationship('UserActivity', backref='user', lazy=True, cascade='all, delete-orphan')
    notifications = db.relationship('Notification', backref='user', lazy=True, cascade='all, delete-orphan')

    def has_active_subscription(self):
        """Check if user has an active subscription - updated for lifetime"""
        if not self.has_subscription:
            return False
        
        # Lifetime subscription never expires
        if hasattr(self, 'subscription_plan') and self.subscription_plan == 'lifetime':
            return True
        
        if hasattr(self, 'subscription_status') and self.subscription_status in ['active', 'trialing']:
            return True
            
        if hasattr(self, 'subscription_expires') and self.subscription_expires and self.subscription_expires > datetime.utcnow():
            return True
            
        return False

    def migrate_stream_recording_id():
    """Add recording_id field to Stream model"""
    try:
        with app.app_context():
            # Add recording_id column if it doesn't exist
            try:
                db.session.execute('ALTER TABLE streams ADD COLUMN recording_id VARCHAR(100)')
                db.session.commit()
                print("✅ Added recording_id column to streams table")
            except Exception as e:
                if "already exists" in str(e).lower() or "duplicate column" in str(e).lower():
                    print("ℹ️ recording_id column already exists")
                else:
                    print(f"⚠️ Error adding recording_id column: {e}")
                db.session.rollback()
        
        return True
    except Exception as e:
        print(f"❌ Migration failed: {e}")
        db.session.rollback()
        return False
        
    def get_subscription_status_display(self):
        """Get human-readable subscription status - updated for lifetime"""
        if not self.has_subscription:
            return "Free"
        
        if hasattr(self, 'subscription_plan') and self.subscription_plan == 'lifetime':
            return "Lifetime"
        
        if not hasattr(self, 'subscription_status'):
            return "Free"
            
        status_map = {
            'active': 'Active',
            'trialing': 'Trial',
            'past_due': 'Past Due',
            'canceled': 'Canceled',
            'unpaid': 'Unpaid',
            'incomplete': 'Incomplete'
        }
        
        return status_map.get(self.subscription_status, 'Unknown')

    def get_subscription_plan_display(self):
        """Get human-readable subscription plan - updated for lifetime"""
        if not hasattr(self, 'subscription_plan') or not self.subscription_plan:
            return 'Free Plan'
            
        plan_map = {
            'monthly': 'Monthly ($29/month)',
            'annual': 'Annual ($299/year)',
            'lifetime': 'Lifetime Access ($499 one-time)'
        }
        
        return plan_map.get(self.subscription_plan, 'Unknown Plan')

    def is_lifetime_subscriber(self):
        """Check if user has lifetime subscription"""
        return (hasattr(self, 'subscription_plan') and 
                self.subscription_plan == 'lifetime' and 
                self.has_subscription)


class SubscriptionEvent(db.Model):
    __tablename__ = 'subscription_events'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    stripe_customer_id = db.Column(db.String(100), nullable=True, index=True)
    stripe_subscription_id = db.Column(db.String(100), nullable=True, index=True)
    event_type = db.Column(db.String(50), nullable=False, index=True)  # created, updated, deleted, payment_succeeded, etc.
    event_data = db.Column(db.Text, nullable=True)  # JSON data from Stripe
    amount = db.Column(db.Numeric(10, 2), nullable=True)
    currency = db.Column(db.String(3), default='usd', nullable=False)
    processed = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationship
    user = db.relationship('User', backref='subscription_events')

# NEW: Model to track revenue analytics
class RevenueAnalytics(db.Model):
    __tablename__ = 'revenue_analytics'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    date = db.Column(db.Date, nullable=False, index=True)
    daily_revenue = db.Column(db.Numeric(10, 2), default=0.00, nullable=False)
    monthly_revenue = db.Column(db.Numeric(10, 2), default=0.00, nullable=False)
    new_subscriptions = db.Column(db.Integer, default=0, nullable=False)
    canceled_subscriptions = db.Column(db.Integer, default=0, nullable=False)
    active_subscriptions = db.Column(db.Integer, default=0, nullable=False)
    churn_rate = db.Column(db.Numeric(5, 2), default=0.00, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    
    __table_args__ = (db.UniqueConstraint('date', name='unique_daily_analytics'),)
    

class Category(db.Model):
    __tablename__ = 'categories'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(100), nullable=False, index=True)
    description = db.Column(db.Text, nullable=True)
    image_url = db.Column(db.String(500), nullable=True)  # Keep this for category icon
    background_image_url = db.Column(db.String(500), nullable=True)  # NEW: For video thumbnails
    order_index = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationships
    videos = db.relationship('Video', backref='category', lazy=True, cascade='all, delete-orphan')

class Tag(db.Model):
    __tablename__ = 'tags'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(50), unique=True, nullable=False, index=True)
    slug = db.Column(db.String(50), unique=True, nullable=False, index=True)
    description = db.Column(db.Text, nullable=True)
    color = db.Column(db.String(7), default='#10B981', nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationships
    videos = db.relationship('Video', secondary=video_tags, lazy='subquery',
                           backref=db.backref('tags', lazy=True))

class Video(db.Model):
    __tablename__ = 'videos'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    title = db.Column(db.String(200), nullable=False, index=True)
    description = db.Column(db.Text, nullable=True)
    s3_url = db.Column(db.String(500), nullable=False)
    thumbnail_url = db.Column(db.String(500), nullable=True)
    duration = db.Column(db.Integer, nullable=True)
    is_free = db.Column(db.Boolean, default=True, nullable=False)
    order_index = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    
    # Foreign Keys
    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'), nullable=False, index=True)
    
    # Relationships
    files = db.relationship('VideoFile', backref='video', lazy=True, cascade='all, delete-orphan')
    progress = db.relationship('UserProgress', backref='video', lazy=True, cascade='all, delete-orphan')
    favorites = db.relationship('UserFavorite', backref='video', lazy=True, cascade='all, delete-orphan')

class VideoFile(db.Model):
    __tablename__ = 'video_files'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    filename = db.Column(db.String(200), nullable=False)
    file_type = db.Column(db.String(50), nullable=True)
    s3_url = db.Column(db.String(500), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    
    # Foreign Keys
    video_id = db.Column(db.Integer, db.ForeignKey('videos.id'), nullable=False, index=True)

class UserProgress(db.Model):
    __tablename__ = 'user_progress'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    watched_duration = db.Column(db.Integer, default=0, nullable=False)
    completed = db.Column(db.Boolean, default=False, nullable=False)
    last_watched = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    
    # Foreign Keys
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    video_id = db.Column(db.Integer, db.ForeignKey('videos.id'), nullable=False, index=True)
    
    # Composite unique constraint
    __table_args__ = (db.UniqueConstraint('user_id', 'video_id', name='unique_user_video_progress'),)

class UserFavorite(db.Model):
    __tablename__ = 'user_favorites'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    
    # Foreign Keys
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    video_id = db.Column(db.Integer, db.ForeignKey('videos.id'), nullable=False, index=True)
    
    # Composite unique constraint
    __table_args__ = (db.UniqueConstraint('user_id', 'video_id', name='unique_user_video_favorite'),)

# NEW MODELS FOR ENHANCED FEATURES
class UserActivity(db.Model):
    __tablename__ = 'user_activities'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    activity_type = db.Column(db.String(50), nullable=False)  # 'video_completed', 'video_favorited', 'course_started'
    description = db.Column(db.String(500), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    
    # Foreign Keys
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)

class Notification(db.Model):
    __tablename__ = 'notifications'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    title = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text, nullable=False)
    notification_type = db.Column(db.String(50), nullable=False)  # 'new_video', 'live_stream', 'system'
    is_read = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    
    # Foreign Keys
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)

class Recommendation(db.Model):
    __tablename__ = 'recommendations'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    title = db.Column(db.String(200), nullable=False, index=True)
    description = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(50), nullable=False, index=True)  # 'broker', 'software', 'education', 'tools', 'other'
    affiliate_url = db.Column(db.String(500), nullable=False)
    image_url = db.Column(db.String(500), nullable=True)
    demo_url = db.Column(db.String(500), nullable=True)
    price_info = db.Column(db.String(100), nullable=True)
    coupon_code = db.Column(db.String(50), nullable=True)
    discount_percentage = db.Column(db.Integer, nullable=True)
    features = db.Column(db.Text, nullable=True)  # Comma-separated features
    is_featured = db.Column(db.Boolean, default=False, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    order_index = db.Column(db.Integer, default=0, nullable=False)
    click_count = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

class RecommendationClick(db.Model):
    __tablename__ = 'recommendation_clicks'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    ip_address = db.Column(db.String(45), nullable=True)
    user_agent = db.Column(db.String(500), nullable=True)
    clicked_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    
    # Foreign Keys
    recommendation_id = db.Column(db.Integer, db.ForeignKey('recommendations.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)

class TradingSignal(db.Model):
    __tablename__ = 'trading_signals'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    date = db.Column(db.Date, nullable=False, default=datetime.utcnow().date)
    day_of_week = db.Column(db.String(10), nullable=False)  # Monday, Tuesday, etc.
    trader_name = db.Column(db.String(50), nullable=False)  # Ray, Jordan
    pair_name = db.Column(db.String(10), nullable=False)  # EURUSD, NQ, XAUUSD
    trade_type = db.Column(db.String(4), nullable=False)  # Buy, Sell
    entry_price = db.Column(db.Numeric(10, 5), nullable=False)
    stop_loss_price = db.Column(db.Numeric(10, 5), nullable=False)
    target_price = db.Column(db.Numeric(10, 5), nullable=False)
    risk_reward_ratio = db.Column(db.Numeric(4, 2), nullable=False)  # 1.0, 3.0, etc.
    outcome = db.Column(db.String(10), nullable=False)  # Win, Breakeven, Loss
    
    # UPDATED: Renamed achieved_reward to actual_rr for final trade outcome
    actual_rr = db.Column(db.Numeric(4, 2), nullable=False, default=0.0)  # Final R achieved (negative for losses)
    
    # NEW: Maximum favorable excursion before reversal
    achieved_rr = db.Column(db.Numeric(4, 2), nullable=True, default=0.0)  # How far price went in trade direction
    
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    
    # Optional link to live trading video
    linked_video_id = db.Column(db.Integer, db.ForeignKey('videos.id'), nullable=True)
    
    # Relationships
    creator = db.relationship('User', backref='trading_signals')
    linked_video = db.relationship('Video', backref='trading_signals')
    
    def __repr__(self):
        return f'<TradingSignal {self.trader_name} {self.pair_name} {self.date}>'
    
    def calculate_pips_risked(self):
        """Calculate pips risked based on pair and prices"""
        if self.pair_name in ['EURUSD', 'GBPUSD', 'AUDUSD', 'NZDUSD']:
            pip_value = 0.0001
        elif self.pair_name in ['USDJPY', 'EURJPY', 'GBPJPY']:
            pip_value = 0.01
        elif self.pair_name == 'XAUUSD':
            pip_value = 0.1
        elif self.pair_name in ['NQ', 'ES', 'YM']:
            pip_value = 1.0
        else:
            pip_value = 0.0001
        
        if self.trade_type.upper() == 'BUY':
            pips_risked = (float(self.entry_price) - float(self.stop_loss_price)) / pip_value
        else:  # SELL
            pips_risked = (float(self.stop_loss_price) - float(self.entry_price)) / pip_value
        
        return abs(pips_risked)
    
    def calculate_pips_target(self):
        """Calculate pips to target"""
        if self.pair_name in ['EURUSD', 'GBPUSD', 'AUDUSD', 'NZDUSD']:
            pip_value = 0.0001
        elif self.pair_name in ['USDJPY', 'EURJPY', 'GBPJPY']:
            pip_value = 0.01
        elif self.pair_name == 'XAUUSD':
            pip_value = 0.1
        elif self.pair_name in ['NQ', 'ES', 'YM']:
            pip_value = 1.0
        else:
            pip_value = 0.0001
        
        if self.trade_type.upper() == 'BUY':
            pips_target = (float(self.target_price) - float(self.entry_price)) / pip_value
        else:  # SELL
            pips_target = (float(self.entry_price) - float(self.target_price)) / pip_value
        
        return abs(pips_target)
    
    def to_dict(self):
        """Convert to dictionary for JSON serialization"""
        return {
            'id': self.id,
            'date': self.date.isoformat(),
            'day_of_week': self.day_of_week,
            'trader_name': self.trader_name,
            'pair_name': self.pair_name,
            'trade_type': self.trade_type,
            'entry_price': float(self.entry_price),
            'stop_loss_price': float(self.stop_loss_price),
            'target_price': float(self.target_price),
            'risk_reward_ratio': float(self.risk_reward_ratio),
            'outcome': self.outcome,
            'actual_rr': float(self.actual_rr),  # UPDATED: renamed from achieved_reward
            'achieved_rr': float(self.achieved_rr or 0),  # NEW: maximum favorable excursion
            'notes': self.notes,
            'linked_video_id': self.linked_video_id,
            'linked_video_title': self.linked_video.title if self.linked_video else None,
            'pips_risked': self.calculate_pips_risked(),
            'pips_target': self.calculate_pips_target(),
            'created_at': self.created_at.isoformat()
        }

class WhopPriceMapping(db.Model):
    """Maps Whop price IDs to your app's price IDs"""
    __tablename__ = 'whop_price_mappings'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    whop_price_id = db.Column(db.String(100), unique=True, nullable=False, index=True)
    app_price_id = db.Column(db.String(100), nullable=False, index=True)
    product_name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    def __repr__(self):
        return f'<WhopPriceMapping {self.product_name}: {self.whop_price_id} -> {self.app_price_id}>'

class WhopTransaction(db.Model):
    """Store Whop transactions for verification"""
    __tablename__ = 'whop_transactions'
    
    STATUS_CHOICES = [
        ('pending', 'Pending Verification'),
        ('verified', 'Verified'),
        ('failed', 'Failed Verification'),
        ('access_granted', 'Access Granted'),
    ]
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    email = db.Column(db.String(120), nullable=False, index=True)
    stripe_customer_id = db.Column(db.String(100), nullable=True, index=True)
    stripe_subscription_id = db.Column(db.String(100), nullable=True, index=True)
    whop_price_id = db.Column(db.String(100), nullable=False, index=True)
    app_price_id = db.Column(db.String(100), nullable=True, index=True)
    transaction_id = db.Column(db.String(100), unique=True, nullable=False, index=True)
    amount = db.Column(db.Numeric(10, 2), nullable=True)
    currency = db.Column(db.String(3), default='USD', nullable=False)
    status = db.Column(db.String(20), default='pending', nullable=False, index=True)
    verified_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Additional metadata
    stripe_webhook_data = db.Column(db.JSON, default=dict, nullable=True)
    whop_metadata = db.Column(db.JSON, default=dict, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    
    # Relationships
    user = db.relationship('User', backref='whop_transactions')
    
    def __repr__(self):
        return f'<WhopTransaction {self.email} - {self.status}>'

# Enhanced Stripe webhook handler with Whop detection
def is_whop_transaction(subscription_data, customer_data):
    """
    Determine if a Stripe transaction came from Whop.com
    Multiple detection methods for reliability
    """
    try:
        # Method 1: Check price ID against known Whop mappings
        if subscription_data.get('items', {}).get('data'):
            price_id = subscription_data['items']['data'][0]['price']['id']
            if WhopPriceMapping.query.filter_by(whop_price_id=price_id, is_active=True).first():
                return True, price_id
        
        # Method 2: Check customer metadata
        customer_metadata = customer_data.get('metadata', {})
        if any(key.lower().startswith('whop') for key in customer_metadata.keys()):
            return True, None
        
        # Method 3: Check subscription metadata
        subscription_metadata = subscription_data.get('metadata', {})
        if subscription_metadata.get('platform') == 'whop' or subscription_metadata.get('source') == 'whop':
            return True, None
        
        # Method 4: Check customer description or name patterns
        customer_name = customer_data.get('name', '').lower()
        customer_description = customer_data.get('description', '').lower()
        if 'whop' in customer_name or 'whop' in customer_description:
            return True, None
        
        return False, None
        
    except Exception as e:
        print(f"Error detecting Whop transaction: {e}")
        return False, None

def handle_whop_subscription_created(subscription_data, customer_data):
    """Handle new Whop subscription"""
    try:
        print(f"🛍️ Processing Whop subscription: {subscription_data['id']}")
        
        # Get price information
        price_id = subscription_data['items']['data'][0]['price']['id'] if subscription_data.get('items', {}).get('data') else None
        
        # Get or create price mapping
        price_mapping = None
        app_price_id = price_id  # Default fallback
        
        if price_id:
            price_mapping = WhopPriceMapping.query.filter_by(
                whop_price_id=price_id,
                is_active=True
            ).first()
            
            if price_mapping:
                app_price_id = price_mapping.app_price_id
                print(f"✅ Found price mapping: {price_id} -> {app_price_id}")
            else:
                print(f"⚠️ No price mapping found for Whop price: {price_id}")
        
        # Create transaction record
        transaction_id = f"whop_{subscription_data['id']}_{int(time.time())}"
        
        whop_transaction = WhopTransaction(
            email=customer_data['email'],
            stripe_customer_id=customer_data['id'],
            stripe_subscription_id=subscription_data['id'],
            whop_price_id=price_id or 'unknown',
            app_price_id=app_price_id,
            transaction_id=transaction_id,
            amount=subscription_data.get('items', {}).get('data', [{}])[0].get('price', {}).get('unit_amount', 0) / 100 if subscription_data.get('items', {}).get('data') else 0,
            currency=subscription_data.get('currency', 'usd').upper(),
            status='verified',
            verified_at=datetime.utcnow(),
            stripe_webhook_data=subscription_data,
            whop_metadata=customer_data.get('metadata', {})
        )
        
        db.session.add(whop_transaction)
        db.session.flush()
        
        # Try to grant access immediately
        grant_whop_user_access(whop_transaction)
        
        db.session.commit()
        print(f"✅ Whop subscription processed successfully")
        
    except Exception as e:
        print(f"❌ Error handling Whop subscription: {e}")
        db.session.rollback()
        raise

def grant_whop_user_access(whop_transaction):
    """Grant access to user based on Whop transaction"""
    try:
        # Find existing user or create account
        user = User.query.filter_by(email=whop_transaction.email).first()
        
        if not user:
            # Create new user account
            username = whop_transaction.email.split('@')[0]
            counter = 1
            original_username = username
            
            # Ensure unique username
            while User.query.filter_by(username=username).first():
                username = f"{original_username}{counter}"
                counter += 1
            
            user = User(
                username=username,
                email=whop_transaction.email,
                password_hash=generate_password_hash(f"whop_{uuid.uuid4().hex[:12]}"),  # Random secure password
                timezone='America/Chicago'
            )
            db.session.add(user)
            db.session.flush()
            print(f"🆕 Created new user account: {user.username}")
        
        # Update user subscription status
        user.has_subscription = True
        user.subscription_status = 'active'
        user.stripe_customer_id = whop_transaction.stripe_customer_id
        user.stripe_subscription_id = whop_transaction.stripe_subscription_id
        
        # Determine plan type from price mapping
        if whop_transaction.app_price_id:
            price_ids = initialize_stripe_price_ids()
            for plan_name, plan_price_id in price_ids.items():
                if whop_transaction.app_price_id == plan_price_id:
                    user.subscription_plan = plan_name
                    break
            
            if not user.subscription_plan:
                # Default plan assignment based on amount
                if whop_transaction.amount >= 499:
                    user.subscription_plan = 'lifetime'
                    user.subscription_expires = datetime.utcnow() + timedelta(days=36500)  # 100 years
                elif whop_transaction.amount >= 299:
                    user.subscription_plan = 'annual'
                    user.subscription_expires = datetime.utcnow() + timedelta(days=365)
                else:
                    user.subscription_plan = 'monthly'
                    user.subscription_expires = datetime.utcnow() + timedelta(days=30)
        
        # Update transaction
        whop_transaction.user_id = user.id
        whop_transaction.status = 'access_granted'
        
        # Create welcome notification
        create_notification(
            user.id,
            'Welcome from Whop! 🛍️',
            f'Your Whop.com purchase has been verified and access has been granted! Welcome to TGFX Trade Lab.',
            'subscription'
        )
        
        # Create activity log
        create_user_activity(
            user.id,
            'whop_access_granted',
            f'Access granted via Whop.com purchase ({user.subscription_plan})'
        )
        
        print(f"✅ Granted access to user: {user.email} ({user.subscription_plan})")
        
        return user
        
    except Exception as e:
        print(f"❌ Error granting Whop access: {e}")
        raise

# Enhanced webhook handler
@app.route('/webhook/stripe/whop', methods=['POST'])
def stripe_webhook_whop():
    """Enhanced Stripe webhook handler with Whop detection"""
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get('Stripe-Signature')
    
    # Use separate webhook secret for Whop
    endpoint_secret = app.config.get('STRIPE_WHOP_WEBHOOK_SECRET') or app.config.get('STRIPE_WEBHOOK_SECRET')
    
    if not endpoint_secret:
        print("⚠️ Stripe webhook secret not configured")
        return jsonify({'error': 'Webhook secret not configured'}), 400
    
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)
    except Exception as e:
        print(f"❌ Webhook signature verification failed: {e}")
        return jsonify({'error': 'Invalid signature'}), 400
    
    try:
        event_type = event['type']
        event_data = event['data']['object']
        
        print(f"📡 Received webhook: {event_type}")
        
        if event_type == 'customer.subscription.created':
            # Get customer data
            customer = stripe.Customer.retrieve(event_data['customer'])
            
            # Check if this is a Whop transaction
            is_whop, whop_price_id = is_whop_transaction(event_data, customer)
            
            if is_whop:
                print(f"🛍️ Detected Whop transaction!")
                handle_whop_subscription_created(event_data, customer)
            else:
                print(f"💳 Processing direct subscription")
                handle_subscription_created(event_data)
        
        elif event_type == 'invoice.payment_succeeded':
            # Check if this could be a Whop payment
            customer = stripe.Customer.retrieve(event_data['customer'])
            
            # Look for existing Whop transaction
            whop_transaction = WhopTransaction.query.filter_by(
                stripe_customer_id=customer['id']
            ).first()
            
            if whop_transaction:
                print(f"🛍️ Processing Whop payment for existing transaction")
                handle_whop_payment_succeeded(event_data, customer, whop_transaction)
            else:
                print(f"💳 Processing direct payment")
                handle_payment_succeeded(event_data)
        
        else:
            # Handle other webhook events normally
            if event_type == 'customer.subscription.updated':
                handle_subscription_updated(event['data']['object'])
            elif event_type == 'customer.subscription.deleted':
                handle_subscription_deleted(event['data']['object'])
        
        return jsonify({'success': True})
        
    except Exception as e:
        print(f"❌ Error processing webhook: {e}")
        return jsonify({'error': str(e)}), 500

def create_serializer():
    return URLSafeTimedSerializer(app.config['SECRET_KEY'])

def generate_reset_token(email):
    """Generate a secure token for password reset"""
    serializer = create_serializer()
    return serializer.dumps(email, salt='password-reset-salt')

def verify_reset_token(token, expiration=3600):
    """Verify the reset token and return email if valid"""
    serializer = create_serializer()
    try:
        email = serializer.loads(token, salt='password-reset-salt', max_age=expiration)
        return email
    except:
        return None

def send_reset_email(user, token):
    """Send password reset email to user"""
    try:
        reset_url = url_for('reset_password', token=token, _external=True)
        
        # Create HTML email content
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Password Reset - TGFX Trade Lab</title>
            <style>
                body {{
                    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
                    background-color: #0a0a0a;
                    color: #ffffff;
                    margin: 0;
                    padding: 20px;
                }}
                .container {{
                    max-width: 600px;
                    margin: 0 auto;
                    background: linear-gradient(135deg, #141414 0%, #1a1a1a 100%);
                    border-radius: 20px;
                    padding: 40px;
                    border: 1px solid rgba(16, 185, 129, 0.2);
                }}
                .logo {{
                    text-align: center;
                    font-size: 2rem;
                    font-weight: 800;
                    background: linear-gradient(135deg, #10B981 0%, #059669 100%);
                    -webkit-background-clip: text;
                    -webkit-text-fill-color: transparent;
                    margin-bottom: 30px;
                }}
                .content {{
                    line-height: 1.6;
                    color: #e5e7eb;
                }}
                .button {{
                    display: inline-block;
                    background: linear-gradient(135deg, #10B981 0%, #059669 100%);
                    color: white;
                    padding: 12px 32px;
                    text-decoration: none;
                    border-radius: 12px;
                    font-weight: 600;
                    margin: 20px 0;
                    text-align: center;
                }}
                .button:hover {{
                    transform: translateY(-2px);
                    box-shadow: 0 8px 25px rgba(16, 185, 129, 0.3);
                }}
                .footer {{
                    margin-top: 30px;
                    padding-top: 20px;
                    border-top: 1px solid rgba(255, 255, 255, 0.08);
                    font-size: 0.875rem;
                    color: #9ca3af;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="logo">TGFX Trade Lab</div>
                
                <div class="content">
                    <h2 style="color: #ffffff; margin-bottom: 20px;">Reset Your Password</h2>
                    
                    <p>Hello {user.username},</p>
                    
                    <p>You requested a password reset for your TGFX Trade Lab account. Click the button below to reset your password:</p>
                    
                    <div style="text-align: center;">
                        <a href="{reset_url}" class="button">Reset My Password</a>
                    </div>
                    
                    <p><strong>This link will expire in 1 hour.</strong></p>
                    
                    <p>If you didn't request this password reset, you can safely ignore this email. Your password will remain unchanged.</p>
                    
                    <p>If the button doesn't work, you can copy and paste this link into your browser:</p>
                    <p style="word-break: break-all; color: #10B981;">{reset_url}</p>
                </div>
                
                <div class="footer">
                    <p>This email was sent by TGFX Trade Lab. If you have any questions, please contact our support team.</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        # Create plain text version
        text_content = f"""
        TGFX Trade Lab - Password Reset
        
        Hello {user.username},
        
        You requested a password reset for your TGFX Trade Lab account.
        
        Click this link to reset your password: {reset_url}
        
        This link will expire in 1 hour.
        
        If you didn't request this password reset, you can safely ignore this email.
        
        Best regards,
        TGFX Trade Lab Team
        """
        
        # Send using Flask-Mail if configured, otherwise use basic SMTP
        try:
            msg = Message(
                subject='Reset Your Password - TGFX Trade Lab',
                recipients=[user.email],
                html=html_content,
                body=text_content
            )
            mail.send(msg)
            print(f"✅ Password reset email sent to {user.email}")
            return True
        except Exception as e:
            print(f"❌ Flask-Mail failed: {e}")
            # Fallback to basic SMTP
            return send_email_smtp(user.email, 'Reset Your Password - TGFX Trade Lab', html_content, text_content)
            
    except Exception as e:
        print(f"❌ Error sending reset email: {e}")
        return False

def send_email_smtp(to_email, subject, html_content, text_content):
    """Fallback SMTP email sending"""
    try:
        from_email = app.config.get('MAIL_USERNAME')
        password = app.config.get('MAIL_PASSWORD')
        smtp_server = app.config.get('MAIL_SERVER', 'smtp.gmail.com')
        smtp_port = app.config.get('MAIL_PORT', 587)
        
        if not all([from_email, password]):
            print("❌ Email credentials not configured")
            return False
        
        msg = MimeMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = from_email
        msg['To'] = to_email
        
        # Add both plain text and HTML versions
        text_part = MimeText(text_content, 'plain')
        html_part = MimeText(html_content, 'html')
        
        msg.attach(text_part)
        msg.attach(html_part)
        
        # Send email
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(from_email, password)
        server.send_message(msg)
        server.quit()
        
        print(f"✅ SMTP email sent successfully to {to_email}")
        return True
        
    except Exception as e:
        print(f"❌ SMTP email failed: {e}")
        return False

def handle_whop_payment_succeeded(invoice_data, customer_data, whop_transaction):
    """Handle successful Whop payment"""
    try:
        payment_amount = invoice_data['amount_paid'] / 100
        
        # Update transaction
        whop_transaction.amount = payment_amount
        whop_transaction.verified_at = datetime.utcnow()
        whop_transaction.status = 'verified'
        
        # Update user if exists
        if whop_transaction.user:
            user = whop_transaction.user
            user.last_payment_date = datetime.fromtimestamp(invoice_data['created'])
            user.last_payment_amount = payment_amount
            user.total_revenue = (user.total_revenue or 0) + payment_amount
        
        db.session.commit()
        print(f"✅ Whop payment processed: ${payment_amount}")
        
    except Exception as e:
        print(f"❌ Error handling Whop payment: {e}")
        db.session.rollback()

@app.route('/api/admin/whop-price-mapping/<int:mapping_id>', methods=['GET'])
@login_required
def api_get_whop_price_mapping(mapping_id):
    """Get individual Whop price mapping"""
    if not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    try:
        mapping = WhopPriceMapping.query.get_or_404(mapping_id)
        
        return jsonify({
            'success': True,
            'mapping': {
                'id': mapping.id,
                'product_name': mapping.product_name,
                'whop_price_id': mapping.whop_price_id,
                'app_price_id': mapping.app_price_id,
                'description': mapping.description,
                'is_active': mapping.is_active
            }
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Add these routes to your app.py

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    """Forgot password page"""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    form = ForgotPasswordForm()
    
    if form.validate_on_submit():
        try:
            user = User.query.filter_by(email=form.email.data.lower().strip()).first()
            
            if user:
                # Generate reset token
                token = generate_reset_token(user.email)
                
                # Send reset email
                email_sent = send_reset_email(user, token)
                
                if email_sent:
                    flash('Password reset instructions have been sent to your email address. Please check your inbox.', 'info')
                    
                    # Create user activity log
                    create_user_activity(
                        user.id,
                        'password_reset_requested',
                        f'Password reset requested for email: {user.email}'
                    )
                else:
                    flash('There was an error sending the reset email. Please try again later.', 'error')
            else:
                # Don't reveal if email exists or not for security
                flash('If an account with that email address exists, password reset instructions have been sent.', 'info')
            
            # Always redirect to prevent form resubmission
            return redirect(url_for('forgot_password'))
            
        except Exception as e:
            print(f"Error in forgot password: {e}")
            flash('An error occurred. Please try again.', 'error')
    
    return render_template('auth/forgot_password.html', form=form)

@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    """Reset password with token"""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    # Verify the token
    email = verify_reset_token(token)
    if not email:
        flash('The password reset link is invalid or has expired.', 'error')
        return redirect(url_for('forgot_password'))
    
    # Find user by email
    user = User.query.filter_by(email=email).first()
    if not user:
        flash('Invalid reset link.', 'error')
        return redirect(url_for('forgot_password'))
    
    form = ResetPasswordForm()
    
    if form.validate_on_submit():
        try:
            # Update user password
            user.password_hash = generate_password_hash(form.password.data)
            db.session.commit()
            
            # Create user activity log
            create_user_activity(
                user.id,
                'password_reset_completed',
                f'Password successfully reset for user: {user.username}'
            )
            
            # Create notification
            create_notification(
                user.id,
                'Password Updated',
                'Your password has been successfully updated. If you did not make this change, please contact support immediately.',
                'security'
            )
            
            flash('Your password has been successfully updated! You can now log in with your new password.', 'success')
            return redirect(url_for('login'))
            
        except Exception as e:
            db.session.rollback()
            print(f"Error resetting password: {e}")
            flash('An error occurred while updating your password. Please try again.', 'error')
    
    return render_template('auth/reset_password.html', form=form, token=token)

@app.route('/api/test-email', methods=['POST'])
@login_required
def api_test_email():
    """Test email configuration (admin only)"""
    if not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    try:
        # Test email by sending to admin
        test_content = """
        <h2>Email Test</h2>
        <p>This is a test email from TGFX Trade Lab.</p>
        <p>If you receive this, your email configuration is working correctly!</p>
        """
        
        success = send_email_smtp(
            current_user.email,
            'TGFX Trade Lab - Email Test',
            test_content,
            'This is a test email from TGFX Trade Lab.'
        )
        
        if success:
            return jsonify({
                'success': True,
                'message': f'Test email sent successfully to {current_user.email}'
            })
        else:
            return jsonify({
                'success': False,
                'message': 'Failed to send test email. Check your email configuration.'
            })
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/whop-price-mapping/<int:mapping_id>', methods=['DELETE'])
@login_required
def api_delete_whop_price_mapping(mapping_id):
    """Delete Whop price mapping"""
    if not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    try:
        mapping = WhopPriceMapping.query.get_or_404(mapping_id)
        db.session.delete(mapping)
        db.session.commit()
        
        return jsonify({'success': True})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/favicon.ico')
def favicon():
    return redirect(url_for('static', filename='favicon.png'))

# User-facing verification endpoint
@app.route('/api/verify-whop-purchase', methods=['POST'])
@login_required
def api_verify_whop_purchase():
    """User-facing endpoint to verify Whop purchase"""
    try:
        data = request.get_json()
        email = data.get('email', current_user.email)
        transaction_id = data.get('transaction_id')  # Optional
        
        if not email:
            return jsonify({'error': 'Email is required'}), 400
        
        # Look for pending/verified Whop transactions
        query = WhopTransaction.query.filter_by(email=email)
        
        if transaction_id:
            query = query.filter_by(transaction_id=transaction_id)
        
        whop_transaction = query.filter(
            WhopTransaction.status.in_(['pending', 'verified'])
        ).order_by(WhopTransaction.created_at.desc()).first()
        
        if whop_transaction:
            # Link to current user and grant access
            if not whop_transaction.user_id:
                whop_transaction.user_id = current_user.id
            
            if whop_transaction.status == 'verified':
                grant_whop_user_access(whop_transaction)
                db.session.commit()
                
                return jsonify({
                    'success': True,
                    'message': 'Whop purchase verified! Access granted.',
                    'transaction_id': whop_transaction.transaction_id,
                    'plan': current_user.subscription_plan,
                    'amount': float(whop_transaction.amount or 0)
                })
            else:
                return jsonify({
                    'success': False,
                    'message': 'Whop purchase found but not yet verified. Please wait a few minutes and try again.'
                })
        else:
            return jsonify({
                'success': False,
                'message': 'No Whop purchase found for your email. Please ensure you used the same email for both Whop and your account.'
            })
            
    except Exception as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': f'Error verifying purchase: {str(e)}'
        })

# Admin routes for managing Whop transactions
@app.route('/admin/whop-transactions')
@login_required
def admin_whop_transactions():
    """Admin page for managing Whop transactions"""
    if not current_user.is_admin:
        flash('Access denied', 'error')
        return redirect(url_for('dashboard'))
    
    transactions = WhopTransaction.query.order_by(WhopTransaction.created_at.desc()).all()
    
    # Calculate stats
    total_transactions = len(transactions)
    verified_transactions = len([t for t in transactions if t.status == 'verified'])
    access_granted = len([t for t in transactions if t.status == 'access_granted'])
    total_revenue = sum([float(t.amount or 0) for t in transactions if t.status in ['verified', 'access_granted']])
    
    return render_template('admin/whop_transactions.html',
                         transactions=transactions,
                         total_transactions=total_transactions,
                         verified_transactions=verified_transactions,
                         access_granted=access_granted,
                         total_revenue=total_revenue)

@app.route('/admin/whop-price-mappings')
@login_required
def admin_whop_price_mappings():
    """Admin page for managing Whop price mappings"""
    if not current_user.is_admin:
        flash('Access denied', 'error')
        return redirect(url_for('dashboard'))
    
    mappings = WhopPriceMapping.query.order_by(WhopPriceMapping.created_at.desc()).all()
    
    return render_template('admin/whop_price_mappings.html', mappings=mappings)

# API endpoints for admin management
@app.route('/api/admin/whop-price-mapping', methods=['POST'])
@login_required
def api_create_whop_price_mapping():
    """Create new Whop price mapping"""
    if not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    try:
        data = request.get_json()
        
        # Check if mapping already exists
        existing = WhopPriceMapping.query.filter_by(
            whop_price_id=data['whop_price_id']
        ).first()
        
        if existing:
            return jsonify({'error': 'Mapping for this Whop price ID already exists'}), 400
        
        mapping = WhopPriceMapping(
            whop_price_id=data['whop_price_id'],
            app_price_id=data['app_price_id'],
            product_name=data['product_name'],
            description=data.get('description', ''),
            is_active=data.get('is_active', True)
        )
        
        db.session.add(mapping)
        db.session.commit()
        
        return jsonify({'success': True, 'id': mapping.id})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/whop-price-mapping/<int:mapping_id>', methods=['PUT'])
@login_required
def api_update_whop_price_mapping(mapping_id):
    """Update Whop price mapping"""
    if not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    try:
        mapping = WhopPriceMapping.query.get_or_404(mapping_id)
        data = request.get_json()
        
        mapping.whop_price_id = data.get('whop_price_id', mapping.whop_price_id)
        mapping.app_price_id = data.get('app_price_id', mapping.app_price_id)
        mapping.product_name = data.get('product_name', mapping.product_name)
        mapping.description = data.get('description', mapping.description)
        mapping.is_active = data.get('is_active', mapping.is_active)
        mapping.updated_at = datetime.utcnow()
        
        db.session.commit()
        
        return jsonify({'success': True})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/whop-transaction/<int:transaction_id>/grant-access', methods=['POST'])
@login_required
def api_grant_whop_access(transaction_id):
    """Manually grant access for a Whop transaction"""
    if not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    try:
        transaction = WhopTransaction.query.get_or_404(transaction_id)
        
        if transaction.status == 'access_granted':
            return jsonify({'error': 'Access already granted'}), 400
        
        grant_whop_user_access(transaction)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'Access granted to {transaction.email}'
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

# Database migration function
def migrate_whop_tables():
    """Run this once to add Whop tables to existing database"""
    try:
        with app.app_context():
            db.create_all()
            print("✅ Whop tables created successfully")
            
            # Create some default price mappings if needed
            if not WhopPriceMapping.query.first():
                print("Creating default Whop price mappings...")
                
                # You'll need to update these with your actual price IDs
                default_mappings = [
                    {
                        'whop_price_id': 'price_whop_monthly_example',
                        'app_price_id': 'price_1R4RMQCir8vKAFowSpnyfvnI',  # Your monthly price
                        'product_name': 'Monthly Subscription (Whop)',
                        'description': 'Monthly subscription purchased through Whop.com'
                    },
                    {
                        'whop_price_id': 'price_whop_annual_example',
                        'app_price_id': 'price_1Rx5qACir8vKAFowfoovlQmt',  # Your annual price
                        'product_name': 'Annual Subscription (Whop)',
                        'description': 'Annual subscription purchased through Whop.com'
                    }
                ]
                
                for mapping_data in default_mappings:
                    mapping = WhopPriceMapping(**mapping_data)
                    db.session.add(mapping)
                
                db.session.commit()
                print("✅ Default price mappings created")
            
            return True
            
    except Exception as e:
        print(f"❌ Whop migration error: {e}")
        db.session.rollback()
        return False

# Add this to your app initialization
def initialize_whop_integration():
    """Initialize Whop integration during app startup"""
    try:
        # Run migration
        migrate_whop_tables()
        
        print("🛍️ Whop integration initialized successfully!")
        print("📋 Next steps:")
        print("   1. Add your Whop price IDs to the price mapping table")
        print("   2. Configure webhook URL in Stripe: /webhook/stripe/whop")
        print("   3. Test with a Whop transaction")
        
        return True
        
    except Exception as e:
        print(f"❌ Whop integration initialization failed: {e}")
        return False

# Database migration function
def migrate_trading_signals_fields():
    """Migrate trading signals to add achieved_rr field and rename achieved_reward to actual_rr"""
    try:
        # Add the new achieved_rr column
        try:
            db.session.execute('ALTER TABLE trading_signals ADD COLUMN achieved_rr DECIMAL(4,2) DEFAULT 0.0')
            print("✅ Added achieved_rr column")
        except Exception as e:
            print(f"ℹ️ achieved_rr column may already exist: {e}")
            db.session.rollback()
        
        # Check if we need to rename achieved_reward to actual_rr
        try:
            # First, try to add actual_rr column
            db.session.execute('ALTER TABLE trading_signals ADD COLUMN actual_rr DECIMAL(4,2) DEFAULT 0.0')
            print("✅ Added actual_rr column")
            
            # Copy data from achieved_reward to actual_rr
            db.session.execute('UPDATE trading_signals SET actual_rr = achieved_reward WHERE achieved_reward IS NOT NULL')
            print("✅ Copied data from achieved_reward to actual_rr")
            
            # Drop the old column (careful with this in production!)
            # db.session.execute('ALTER TABLE trading_signals DROP COLUMN achieved_reward')
            # print("✅ Dropped old achieved_reward column")
            
        except Exception as e:
            print(f"ℹ️ actual_rr column migration: {e}")
            db.session.rollback()
        
        db.session.commit()
        print("✅ Trading signals field migration completed")
        
    except Exception as e:
        print(f"❌ Error migrating trading signals fields: {e}")
        db.session.rollback()

# Update TradingStats model aggregation function
def update_trading_stats(signal):
    """Update aggregated trading stats when a signal is added/modified - UPDATED for new fields"""
    try:
        stats = TradingStats.query.filter_by(
            trader_name=signal.trader_name,
            date=signal.date
        ).first()
        
        if not stats:
            stats = TradingStats(
                trader_name=signal.trader_name,
                date=signal.date
            )
            db.session.add(stats)
        
        # Recalculate stats for this trader on this date
        daily_signals = TradingSignal.query.filter_by(
            trader_name=signal.trader_name,
            date=signal.date
        ).all()
        
        stats.total_trades = len(daily_signals)
        stats.wins = len([s for s in daily_signals if s.outcome == 'Win'])
        stats.losses = len([s for s in daily_signals if s.outcome == 'Loss'])
        stats.breakevens = len([s for s in daily_signals if s.outcome == 'Breakeven'])
        
        # UPDATED: Use actual_rr instead of achieved_reward
        stats.total_r_reward = sum([float(s.actual_rr) for s in daily_signals])
        stats.total_pips = sum([s.calculate_pips_risked() * float(s.actual_rr) for s in daily_signals])
        
        db.session.commit()
        
    except Exception as e:
        print(f"Error updating trading stats: {e}")
        db.session.rollback()

# UPDATED Stream Model for LiveKit
class Stream(db.Model):
    __tablename__ = 'streams'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    
    # LiveKit specific fields
    room_name = db.Column(db.String(100), unique=True, nullable=True)
    room_sid = db.Column(db.String(100), unique=True, nullable=True)
    recording_id = db.Column(db.String(100), nullable=True)
    
    # Stream status
    is_active = db.Column(db.Boolean, default=False, nullable=False)
    is_recording = db.Column(db.Boolean, default=False, nullable=False)
    recording_url = db.Column(db.String(500), nullable=True)
    viewer_count = db.Column(db.Integer, default=0, nullable=False)
    started_at = db.Column(db.DateTime, nullable=True)
    ended_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    
    # Stream metadata
    streamer_name = db.Column(db.String(100), nullable=True)
    stream_type = db.Column(db.String(50), default='general', nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    
    # Relationships
    viewers = db.relationship('StreamViewer', backref='stream', lazy=True, cascade='all, delete-orphan')

    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'description': self.description,
            'room_name': self.room_name,
            'room_sid': self.room_sid,
            'is_active': self.is_active,
            'is_recording': self.is_recording,
            'recording_url': self.recording_url,
            'viewer_count': self.viewer_count,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'ended_at': self.ended_at.isoformat() if self.ended_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'streamer_name': self.streamer_name,
            'stream_type': self.stream_type,
            'created_by': self.created_by
        }

class StreamViewer(db.Model):
    __tablename__ = 'stream_viewers'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    participant_identity = db.Column(db.String(100), nullable=False)  # LiveKit participant identity
    participant_sid = db.Column(db.String(100), nullable=True)  # LiveKit participant SID
    joined_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    left_at = db.Column(db.DateTime, nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    
    # Foreign Keys
    stream_id = db.Column(db.Integer, db.ForeignKey('streams.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    
    # Composite unique constraint
    __table_args__ = (db.UniqueConstraint('stream_id', 'user_id', name='unique_stream_viewer'),)

# Forms
class LoginForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired()])
    password = PasswordField('Password', validators=[DataRequired()])

class SignupForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=3, max=20)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired(), Length(min=6)])

class ForgotPasswordForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email()])

class ResetPasswordForm(FlaskForm):
    password = PasswordField('New Password', validators=[DataRequired(), Length(min=6)])
    confirm_password = PasswordField('Confirm Password', validators=[DataRequired()])
    
    def validate_confirm_password(self, confirm_password):
        if self.password.data != confirm_password.data:
            raise ValidationError('Passwords do not match.')

class VideoForm(FlaskForm):
    title = StringField('Title', validators=[DataRequired()])
    description = TextAreaField('Description')
    s3_url = StringField('S3 URL', validators=[DataRequired()])
    thumbnail_url = StringField('Thumbnail URL', validators=[Optional()])
    category_id = SelectField('Category', coerce=int, validators=[DataRequired()])
    is_free = BooleanField('Free Video')
    order_index = IntegerField('Order', default=0)

class VideoFormWithTags(VideoForm):
    tags = StringField('Tags', validators=[Optional()], 
                      render_kw={"placeholder": "Enter tags separated by commas (e.g., market structure, support resistance)"})

class CategoryForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired()])
    description = TextAreaField('Description')
    image_url = StringField('Category Icon URL', validators=[Optional()])
    background_image_url = StringField('Background Image URL (for video thumbnails)', validators=[Optional()])
    order_index = IntegerField('Order', default=0)

class TagForm(FlaskForm):
    name = StringField('Tag Name', validators=[DataRequired(), Length(min=2, max=50)])
    description = TextAreaField('Description', validators=[Optional()])
    color = StringField('Color', validators=[Optional()], default='#10B981')

class StreamForm(FlaskForm):
    title = StringField('Stream Title', validators=[DataRequired(), Length(min=3, max=200)])
    description = TextAreaField('Description', validators=[Optional()])

class DualStreamForm(FlaskForm):
    title = StringField('Stream Title', validators=[DataRequired(), Length(min=3, max=200)])
    description = TextAreaField('Description', validators=[Optional()])
    stream_type = SelectField('Stream Type', choices=[
        ('general', 'General Discussion'),
        ('trading', 'Live Trading'),
        ('education', 'Educational Content'),
        ('webinar', 'Webinar')
    ], default='trading')

class RecommendationForm(FlaskForm):
    title = StringField('Title', validators=[DataRequired()])
    description = TextAreaField('Description', validators=[DataRequired()])
    category = SelectField('Category', choices=[
        ('broker', 'Broker'),
        ('software', 'Software'),
        ('education', 'Education'),
        ('tools', 'Tools'),
        ('other', 'Other')
    ], validators=[DataRequired()])
    affiliate_url = StringField('Affiliate URL', validators=[DataRequired()])
    image_url = StringField('Image URL', validators=[Optional()])
    demo_url = StringField('Demo URL', validators=[Optional()])
    price_info = StringField('Price Information', validators=[Optional()])
    coupon_code = StringField('Coupon Code', validators=[Optional()])
    discount_percentage = IntegerField('Discount Percentage', validators=[Optional()])
    features = TextAreaField('Features', validators=[Optional()])
    is_featured = BooleanField('Featured')
    is_active = BooleanField('Active', default=True)
    order_index = IntegerField('Order', default=0)

# NEW FORM FOR USER SETTINGS
class UserSettingsForm(FlaskForm):
    timezone = SelectField('Timezone', choices=[
        ('America/New_York', 'Eastern Time (ET) - New York'),
        ('America/Chicago', 'Central Time (CT) - Chicago'),
        ('America/Denver', 'Mountain Time (MT) - Denver'),
        ('America/Los_Angeles', 'Pacific Time (PT) - Los Angeles'),
        ('America/Toronto', 'Eastern Time (ET) - Toronto'),
        ('America/Vancouver', 'Pacific Time (PT) - Vancouver'),
        ('Europe/London', 'Greenwich Mean Time (GMT) - London'),
        ('Europe/Berlin', 'Central European Time (CET) - Berlin'),
        ('Europe/Zurich', 'Central European Time (CET) - Zurich'),
        ('Asia/Tokyo', 'Japan Standard Time (JST) - Tokyo'),
        ('Asia/Shanghai', 'China Standard Time (CST) - Shanghai'),
        ('Asia/Hong_Kong', 'Hong Kong Time (HKT) - Hong Kong'),
        ('Asia/Singapore', 'Singapore Standard Time (SGT) - Singapore'),
        ('Asia/Kolkata', 'India Standard Time (IST) - Mumbai'),
        ('Australia/Sydney', 'Australian Eastern Time (AET) - Sydney'),
        ('Australia/Melbourne', 'Australian Eastern Time (AET) - Melbourne'),
        ('Pacific/Auckland', 'New Zealand Standard Time (NZST) - Auckland'),
        ('UTC', 'Coordinated Universal Time (UTC)'),
    ], validators=[DataRequired()])

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

# Helper Functions

# Initialize the app with migration
def initialize_enhanced_app():
    """Enhanced initialization with new features"""
    try:
        with app.app_context():
            # Run existing initialization
            db.create_all()
            print("✅ Database tables created successfully")
            
            # Run new migrations
            migrate_category_background_images()
            migrate_user_timezones()  # Existing function
            
            # Create admin user if needed (existing code)
            admin_user = User.query.filter_by(username='admin').first()
            if not admin_user:
                admin_user = User(
                    username='admin',
                    email='ray@tgfx-academy.com',
                    password_hash=generate_password_hash('admin123!345gdfb3f35'),
                    is_admin=True,
                    display_name='Ray',
                    can_stream=True,
                    stream_color='#10B981',
                    timezone='America/Chicago'
                )
                db.session.add(admin_user)
                db.session.commit()
                print("✅ Admin user created")
            
            # Initialize streamers
            initialize_streamers()
            
            print("✅ Enhanced app initialization complete!")
            
    except Exception as e:
        print(f"❌ Enhanced app initialization error: {e}")
        return False
    
    return True
    

# Database migration function to add background_image_url column
def migrate_category_background_images():
    """Run this once to add background_image_url column to categories table"""
    try:
        # Try to add the column if it doesn't exist
        try:
            db.session.execute('ALTER TABLE categories ADD COLUMN background_image_url VARCHAR(500)')
            db.session.commit()
            print("✅ Added background_image_url column to categories table")
        except Exception as e:
            # Column probably already exists
            db.session.rollback()
            print("ℹ️ background_image_url column already exists or error adding:", str(e))
        
        return True
        
    except Exception as e:
        db.session.rollback()
        print(f"❌ Error migrating category background images: {e}")
        return False

def send_discord_webhook(title, description, color=5814783, fields=None, thumbnail_url=None, footer_text=None):
    """
    Send Discord webhook with simplified, public-friendly messaging
    """
    try:
        webhook_url = current_app.config.get('APP_UPDATE_DISCORD_WEBHOOK_URL')
        if not webhook_url:
            print("⚠️ Discord webhook URL not configured")
            return False
        
        # Create embed
        embed = {
            "title": title,
            "description": description,
            "color": color,
            "timestamp": datetime.utcnow().isoformat(),
            "footer": {
                "text": footer_text or "TGFX Trade Lab",
                "icon_url": "https://tgfx-tradelab.s3.amazonaws.com/logo.png"
            }
        }
        
        # Add optional fields
        if fields:
            embed["fields"] = fields
            
        if thumbnail_url:
            embed["thumbnail"] = {"url": thumbnail_url}
        
        # Create webhook payload
        webhook_data = {
            "embeds": [embed],
            "username": "TGFX Trade Lab",
            "avatar_url": "https://tgfx-tradelab.s3.amazonaws.com/logo.png"
        }
        
        # Send webhook
        response = requests.post(
            webhook_url,
            json=webhook_data,
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        
        if response.status_code == 204:
            print(f"✅ Discord webhook sent: {title}")
            return True
        else:
            print(f"❌ Discord webhook failed: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"❌ Discord webhook error: {e}")
        return False

def migrate_trading_signals_to_dual_rr():
    """
    Migrate trading signals database to support both actual_rr and achieved_rr fields
    
    This migration:
    1. Adds the new 'achieved_rr' column for maximum favorable excursion
    2. Renames 'achieved_reward' to 'actual_rr' for clarity
    3. Provides backward compatibility
    """
    try:
        print("🔄 Starting trading signals database migration...")
        
        # Step 1: Add the new achieved_rr column
        try:
            db.session.execute('''
                ALTER TABLE trading_signals 
                ADD COLUMN achieved_rr DECIMAL(4,2) DEFAULT NULL
            ''')
            print("✅ Added 'achieved_rr' column for maximum favorable excursion tracking")
        except Exception as e:
            if "already exists" in str(e).lower() or "duplicate column" in str(e).lower():
                print("ℹ️ 'achieved_rr' column already exists")
            else:
                print(f"⚠️ Error adding achieved_rr column: {e}")
            db.session.rollback()
        
        # Step 2: Add the new actual_rr column  
        try:
            db.session.execute('''
                ALTER TABLE trading_signals 
                ADD COLUMN actual_rr DECIMAL(4,2) DEFAULT 0.0
            ''')
            print("✅ Added 'actual_rr' column for final trade outcomes")
        except Exception as e:
            if "already exists" in str(e).lower() or "duplicate column" in str(e).lower():
                print("ℹ️ 'actual_rr' column already exists")
            else:
                print(f"⚠️ Error adding actual_rr column: {e}")
            db.session.rollback()
        
        # Step 3: Copy data from achieved_reward to actual_rr (if needed)
        try:
            # Check if achieved_reward column exists
            result = db.session.execute('''
                SELECT COUNT(*) as count 
                FROM information_schema.columns 
                WHERE table_name = 'trading_signals' 
                AND column_name = 'achieved_reward'
            ''').fetchone()
            
            if result and result[0] > 0:
                # Copy data from old column to new column
                db.session.execute('''
                    UPDATE trading_signals 
                    SET actual_rr = achieved_reward 
                    WHERE achieved_reward IS NOT NULL AND actual_rr = 0.0
                ''')
                
                rows_updated = db.session.execute('SELECT ROW_COUNT()').fetchone()[0]
                print(f"✅ Copied {rows_updated} records from 'achieved_reward' to 'actual_rr'")
                
                # Note: We don't drop the old column immediately for safety
                print("ℹ️ Old 'achieved_reward' column preserved for safety (can be dropped manually later)")
            else:
                print("ℹ️ No 'achieved_reward' column found to migrate")
                
        except Exception as e:
            print(f"⚠️ Error during data migration: {e}")
            db.session.rollback()
            
        # Step 4: Update any null actual_rr values based on outcome
        try:
            db.session.execute('''
                UPDATE trading_signals 
                SET actual_rr = CASE 
                    WHEN outcome = 'Win' AND risk_reward_ratio IS NOT NULL THEN risk_reward_ratio
                    WHEN outcome = 'Loss' THEN -1.0
                    WHEN outcome = 'Breakeven' THEN 0.0
                    ELSE 0.0
                END
                WHERE actual_rr IS NULL OR actual_rr = 0.0
            ''')
            print("✅ Updated null actual_rr values based on trade outcomes")
        except Exception as e:
            print(f"⚠️ Error updating null values: {e}")
            db.session.rollback()
        
        # Step 5: Add helpful indexes for performance
        try:
            db.session.execute('''
                CREATE INDEX IF NOT EXISTS idx_trading_signals_actual_rr 
                ON trading_signals(actual_rr)
            ''')
            db.session.execute('''
                CREATE INDEX IF NOT EXISTS idx_trading_signals_achieved_rr 
                ON trading_signals(achieved_rr)
            ''')
            print("✅ Added database indexes for better query performance")
        except Exception as e:
            print(f"⚠️ Error adding indexes: {e}")
            db.session.rollback()
        
        # Commit all changes
        db.session.commit()
        
        # Step 6: Validate migration
        validation_result = validate_migration()
        if validation_result['success']:
            print("✅ Migration validation passed!")
            print(f"   📊 Total signals: {validation_result['total_signals']}")
            print(f"   📈 Signals with actual_rr: {validation_result['signals_with_actual_rr']}")
            print(f"   📉 Signals with achieved_rr: {validation_result['signals_with_achieved_rr']}")
        else:
            print("⚠️ Migration validation warnings:")
            for warning in validation_result['warnings']:
                print(f"   - {warning}")
        
        print("🎉 Trading signals migration completed successfully!")
        print()
        print("📋 What's New:")
        print("   • 'Actual R' - Final trade outcome (positive for wins, negative for losses)")
        print("   • 'Achieved R' - Maximum favorable excursion before reversal")
        print("   • Enhanced hypothetical analysis using achieved R data")
        print("   • Better insights for exit strategy optimization")
        print()
        print("📝 Next Steps:")
        print("   1. Start tracking 'Achieved R' for new signals")
        print("   2. Update historical signals with achieved R data when possible")
        print("   3. Use hypothetical analysis to optimize exit strategies")
        
        return True
        
    except Exception as e:
        print(f"❌ Migration failed: {e}")
        db.session.rollback()
        return False

def validate_migration():
    """Validate that the migration completed successfully"""
    try:
        # Count total signals
        total_signals = db.session.execute(
            'SELECT COUNT(*) FROM trading_signals'
        ).scalar()
        
        # Count signals with actual_rr data
        signals_with_actual_rr = db.session.execute(
            'SELECT COUNT(*) FROM trading_signals WHERE actual_rr IS NOT NULL'
        ).scalar()
        
        # Count signals with achieved_rr data
        signals_with_achieved_rr = db.session.execute(
            'SELECT COUNT(*) FROM trading_signals WHERE achieved_rr IS NOT NULL'
        ).scalar()
        
        warnings = []
        
        # Check for potential issues
        if signals_with_actual_rr < total_signals:
            warnings.append(f"{total_signals - signals_with_actual_rr} signals missing actual_rr data")
        
        if signals_with_achieved_rr == 0:
            warnings.append("No signals have achieved_rr data yet (this is normal for historical data)")
        
        # Check for impossible values
        impossible_values = db.session.execute('''
            SELECT COUNT(*) FROM trading_signals 
            WHERE outcome = 'Win' AND actual_rr < 0
        ''').scalar()
        
        if impossible_values > 0:
            warnings.append(f"{impossible_values} winning trades have negative actual_rr values")
        
        return {
            'success': len(warnings) == 0 or all('normal for historical' in w for w in warnings),
            'total_signals': total_signals,
            'signals_with_actual_rr': signals_with_actual_rr,
            'signals_with_achieved_rr': signals_with_achieved_rr,
            'warnings': warnings
        }
        
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'warnings': [f"Validation failed: {e}"]
        }

def rollback_migration():
    """Rollback the migration if needed (use with caution!)"""
    try:
        print("⚠️ Rolling back trading signals migration...")
        
        # Restore data to achieved_reward if it exists
        try:
            db.session.execute('''
                UPDATE trading_signals 
                SET achieved_reward = actual_rr 
                WHERE actual_rr IS NOT NULL
            ''')
            print("✅ Restored data to achieved_reward column")
        except Exception as e:
            print(f"⚠️ Could not restore to achieved_reward: {e}")
        
        # Drop new columns (commented out for safety)
        # db.session.execute('ALTER TABLE trading_signals DROP COLUMN actual_rr')
        # db.session.execute('ALTER TABLE trading_signals DROP COLUMN achieved_rr')
        # print("✅ Dropped new columns")
        
        db.session.commit()
        print("✅ Migration rollback completed")
        
    except Exception as e:
        print(f"❌ Rollback failed: {e}")
        db.session.rollback()

def get_trading_analytics(trader_name=None, start_date=None, end_date=None):
    """Calculate comprehensive trading analytics"""
    try:
        # Build query
        query = TradingSignal.query
        
        if trader_name:
            query = query.filter_by(trader_name=trader_name)
        
        if start_date:
            query = query.filter(TradingSignal.date >= start_date)
        
        if end_date:
            query = query.filter(TradingSignal.date <= end_date)
        
        signals = query.all()
        
        if not signals:
            return {
                'total_trades': 0,
                'wins': 0,
                'losses': 0,
                'breakevens': 0,
                'win_rate': 0,
                'total_r_reward': 0,
                'average_r_per_trade': 0,
                'best_trade': 0,
                'worst_trade': 0,
                'day_of_week_stats': {}
            }
        
        # Calculate basic stats
        total_trades = len(signals)
        wins = len([s for s in signals if s.outcome == 'Win'])
        losses = len([s for s in signals if s.outcome == 'Loss'])
        breakevens = len([s for s in signals if s.outcome == 'Breakeven'])
        
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        
        # Calculate R rewards using actual_rr field
        total_r_reward = sum([float(getattr(s, 'actual_rr', getattr(s, 'achieved_reward', 0))) for s in signals])
        average_r_per_trade = total_r_reward / total_trades if total_trades > 0 else 0
        
        # Best and worst trades
        r_values = [float(getattr(s, 'actual_rr', getattr(s, 'achieved_reward', 0))) for s in signals]
        best_trade = max(r_values) if r_values else 0
        worst_trade = min(r_values) if r_values else 0
        
        # Day of week statistics
        day_stats = {}
        for signal in signals:
            day = signal.day_of_week
            if day not in day_stats:
                day_stats[day] = {'trades': 0, 'wins': 0, 'total_r': 0}
            
            day_stats[day]['trades'] += 1
            if signal.outcome == 'Win':
                day_stats[day]['wins'] += 1
            day_stats[day]['total_r'] += float(getattr(signal, 'actual_rr', getattr(signal, 'achieved_reward', 0)))
        
        return {
            'total_trades': total_trades,
            'wins': wins,
            'losses': losses,
            'breakevens': breakevens,
            'win_rate': round(win_rate, 2),
            'total_r_reward': round(total_r_reward, 2),
            'average_r_per_trade': round(average_r_per_trade, 2),
            'best_trade': round(best_trade, 2),
            'worst_trade': round(worst_trade, 2),
            'day_of_week_stats': day_stats
        }
        
    except Exception as e:
        print(f"Error calculating trading analytics: {e}")
        return {
            'total_trades': 0,
            'wins': 0,
            'losses': 0,
            'breakevens': 0,
            'win_rate': 0,
            'total_r_reward': 0,
            'average_r_per_trade': 0,
            'best_trade': 0,
            'worst_trade': 0,
            'day_of_week_stats': {}
        }

def calculate_day_of_week(date):
    """Calculate day of week from date"""
    days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    return days[date.weekday()]

def get_trader_defaults(user):
    """Get default trader settings based on user"""
    trader_defaults = {
        'jordan': ('Jordan', 'XAUUSD', 2.0),
        'jwill24': ('Jordan', 'XAUUSD', 2.0),
        'admin': ('Ray', 'EURUSD', 2.0),
        'ray': ('Ray', 'EURUSD', 2.0)
    }
    
    username = user.username.lower() if user.username else 'unknown'
    return trader_defaults.get(username, (user.display_name or user.username, 'EURUSD', 2.0))

def update_trading_stats(signal):
    """Update aggregated trading stats when a signal is added/modified"""
    try:
        # This is a placeholder - you can implement TradingStats model aggregation here if needed
        print(f"Updated stats for {signal.trader_name} signal on {signal.date}")
        return True
        
    except Exception as e:
        print(f"Error updating trading stats: {e}")
        return False

class TradingStats(db.Model):
    """Model for aggregated trading statistics (optional)"""
    __tablename__ = 'trading_stats'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    trader_name = db.Column(db.String(50), nullable=False)
    date = db.Column(db.Date, nullable=False)
    total_trades = db.Column(db.Integer, default=0, nullable=False)
    wins = db.Column(db.Integer, default=0, nullable=False)
    losses = db.Column(db.Integer, default=0, nullable=False)
    breakevens = db.Column(db.Integer, default=0, nullable=False)
    total_r_reward = db.Column(db.Numeric(10, 2), default=0.0, nullable=False)
    total_pips = db.Column(db.Numeric(10, 2), default=0.0, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    
    # Composite unique constraint
    __table_args__ = (db.UniqueConstraint('trader_name', 'date', name='unique_trader_date_stats'),)

# API endpoint for manual migration trigger
@app.route('/api/admin/migrate-trading-signals', methods=['POST'])
@login_required
def api_migrate_trading_signals():
    """API endpoint to trigger trading signals migration"""
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403
    
    try:
        success = migrate_trading_signals_to_dual_rr()
        if success:
            return jsonify({
                'success': True,
                'message': 'Trading signals migration completed successfully'
            })
        else:
            return jsonify({
                'success': False,
                'message': 'Migration failed, check server logs'
            })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Add this to your app initialization in the main block
def enhanced_initialize_app():
    try:
        with app.app_context():
            # Your existing initialization code...
            db.create_all()
            
            # NEW: Initialize Whop integration
            initialize_whop_integration()
            
            print("✅ Enhanced app initialization complete!")
            
    except Exception as e:
        print(f"❌ Enhanced app initialization error: {e}")
        return False
    
    return True

def send_new_video_webhook(video, category):
    """Send Discord notification for new video"""
    try:
        # Create fields with public-friendly information
        fields = [
            {
                "name": "📁 Category",
                "value": category.name,
                "inline": True
            },
            {
                "name": "🎯 Access",
                "value": "🆓 Free" if video.is_free else "💎 Premium",
                "inline": True
            }
        ]
        
        # Add duration if available
        if video.duration:
            duration_minutes = video.duration // 60
            duration_seconds = video.duration % 60
            fields.append({
                "name": "⏱️ Duration",
                "value": f"{duration_minutes}:{duration_seconds:02d}",
                "inline": True
            })
        
        # Add top tags if available (limit to 2 for cleaner look)
        if hasattr(video, 'tags') and video.tags:
            tag_names = [tag.name for tag in video.tags[:2]]
            if tag_names:
                fields.append({
                    "name": "🏷️ Topics",
                    "value": ", ".join(tag_names),
                    "inline": False
                })
        
        # Create clean description
        description = f"🎥 **New educational content is now available!**\n\n"
        
        if video.description and len(video.description) > 10:
            # Show first sentence or 100 characters, whichever is shorter
            desc_preview = video.description.split('.')[0]
            if len(desc_preview) > 100:
                desc_preview = video.description[:100] + "..."
            description += f"*{desc_preview}*\n\n"
        
        description += f"{'🆓 **Free for everyone**' if video.is_free else '💎 **Premium members only**'}"
        
        return send_discord_webhook(
            title=f"📹 {video.title}",
            description=description,
            color=3447003,  # Blue
            fields=fields,
            thumbnail_url=video.thumbnail_url,
            footer_text=f"New Video • {category.name}"
        )
        
    except Exception as e:
        print(f"❌ Error sending new video webhook: {e}")
        return False

def send_live_stream_webhook(stream, action="started"):
    """Send Discord notification for live stream events"""
    try:
        if action == "started":
            emoji = "🔴"
            color = 15158332  # Red
            title = f"{emoji} {stream.streamer_name} is LIVE!"
            
            description = f"**{stream.title}**\n\n"
            
            if stream.description and len(stream.description) > 10:
                desc_preview = stream.description[:120] + "..." if len(stream.description) > 120 else stream.description
                description += f"*{desc_preview}*\n\n"
            
            description += "🎮 **Join the live session now!**"
            
            fields = [
                {
                    "name": "👤 Trader",
                    "value": stream.streamer_name,
                    "inline": True
                },
                {
                    "name": "📺 Type",
                    "value": stream.stream_type.replace('_', ' ').title(),
                    "inline": True
                },
                {
                    "name": "⏰ Started",
                    "value": f"<t:{int(stream.started_at.timestamp())}:R>" if stream.started_at else "Now",
                    "inline": True
                }
            ]
            
            if stream.is_recording:
                description += "\n📹 *Session will be recorded and added to the course library*"
                
            footer_text = f"Live Stream • {stream.stream_type.title()}"
        
        elif action == "ended":
            emoji = "⏹️"
            color = 9807270  # Gray
            title = f"{emoji} Stream Ended"
            
            description = f"**{stream.streamer_name}'s session has ended**\n\n"
            description += f"*{stream.title}*\n\n"
            
            # Calculate duration
            if stream.started_at and stream.ended_at:
                duration = stream.ended_at - stream.started_at
                duration_minutes = int(duration.total_seconds() / 60)
                description += f"⏱️ **Session Length:** {duration_minutes} minutes\n"
            
            if stream.recording_url:
                description += "📼 **Recording will be available in the course library soon**"
            
            fields = [
                {
                    "name": "👤 Trader",
                    "value": stream.streamer_name,
                    "inline": True
                },
                {
                    "name": "👥 Viewers",
                    "value": str(stream.viewer_count or 0),
                    "inline": True
                }
            ]
            
            footer_text = f"Stream Ended • {duration_minutes}min" if 'duration_minutes' in locals() else "Stream Ended"
        
        return send_discord_webhook(
            title=title,
            description=description,
            color=color,
            fields=fields,
            footer_text=footer_text
        )
        
    except Exception as e:
        print(f"❌ Error sending live stream webhook: {e}")
        return False

def send_trading_signal_webhook(signal):
    """Send Discord notification for new trading signals - NO TRADE DETAILS"""
    try:
        # Determine emoji based on trade type but don't reveal details
        emoji = "📊"
        color = 5763719  # Gold
        
        # Very minimal information - just announce that a signal was posted
        description = f"📊 **New trade analysis from {signal.trader_name}**\n\n"
        description += f"💱 **Market:** {signal.pair_name}\n"
        description += f"📅 **Date:** {signal.date.strftime('%B %d, %Y')}\n\n"
        description += "💎 **Premium members can view full details in the trading signals section**"
        
        # Only show basic, non-sensitive information
        fields = [
            {
                "name": "👤 Trader",
                "value": signal.trader_name,
                "inline": True
            },
            {
                "name": "💱 Pair",
                "value": signal.pair_name,
                "inline": True
            },
            {
                "name": "📅 Date",
                "value": signal.date.strftime('%m/%d/%Y'),
                "inline": True
            }
        ]
        
        # If there's a linked video, mention it but don't give details
        if signal.linked_video_id:
            fields.append({
                "name": "🎥 Live Session",
                "value": "✅ Linked to live trading video",
                "inline": False
            })
        
        return send_discord_webhook(
            title=f"{emoji} New Trade Analysis - {signal.pair_name}",
            description=description,
            color=color,
            fields=fields,
            footer_text=f"Trading Signal • {signal.trader_name}"
        )
        
    except Exception as e:
        print(f"❌ Error sending trading signal webhook: {e}")
        return False

def send_course_completion_webhook(user, category, completion_stats):
    """Send Discord notification for course completions"""
    try:
        description = f"🎓 **Course completed!**\n\n"
        description += f"**{user.username}** just finished the **{category.name}** course!\n\n"
        description += f"🎯 Completed **{completion_stats['completed']}/{completion_stats['total']} videos**\n\n"
        description += "👏 *Congratulations on advancing your trading education!*"
        
        fields = [
            {
                "name": "🎓 Course",
                "value": category.name,
                "inline": True
            },
            {
                "name": "📊 Progress",
                "value": f"{completion_stats['completed']}/{completion_stats['total']} videos",
                "inline": True
            },
            {
                "name": "🏆 Achievement",
                "value": "Course Completed",
                "inline": True
            }
        ]
        
        return send_discord_webhook(
            title=f"🏆 Course Completion: {category.name}",
            description=description,
            color=10181046,  # Purple
            fields=fields,
            footer_text=f"Course Completed • {user.username}"
        )
        
    except Exception as e:
        print(f"❌ Error sending course completion webhook: {e}")
        return False

def test_discord_webhook():
    """Test Discord webhook with a sample message"""
    try:
        test_fields = [
            {
                "name": "🧪 Test Status",
                "value": "✅ Integration Active",
                "inline": True
            },
            {
                "name": "📢 Notifications",
                "value": "Videos, Streams, Signals, Completions",
                "inline": True
            }
        ]
        
        description = """**🎉 Discord notifications are now active!**

Your community will receive updates for:
• 📹 **New educational videos**
• 🔴 **Live trading sessions** 
• 📊 **New trade analyses** *(premium details in app)*
• 🏆 **Course completions**

Everything is working perfectly! 🚀"""
        
        return send_discord_webhook(
            title="🎉 TGFX Trade Lab Notifications Active",
            description=description,
            color=5763719,  # Gold
            fields=test_fields,
            footer_text="Integration Test • All Systems Go"
        )
        
    except Exception as e:
        print(f"❌ Error in test webhook: {e}")
        return False

def send_new_course_webhook(category):
    """Send Discord notification for new course/category"""
    description = f"🎓 **New course category added**\n\n"
    
    if category.description:
        desc_preview = category.description[:200] + "..." if len(category.description) > 200 else category.description
        description += f"{desc_preview}\n\n"
    
    description += "📚 **Start learning with our latest content!**"
    
    fields = [
        {
            "name": "📁 Category",
            "value": category.name,
            "inline": True
        },
        {
            "name": "📊 Order",
            "value": str(category.order_index),
            "inline": True
        }
    ]
    
    send_discord_webhook(
        title=f"📚 New Course: {category.name}",
        description=description,
        color=10181046,  # Purple color for courses
        fields=fields,
        thumbnail_url=category.image_url
    )

# Test webhook function
def test_discord_webhook():
    """Test Discord webhook with a sample message"""
    test_fields = [
        {
            "name": "🧪 Test Field",
            "value": "This is a test notification",
            "inline": True
        },
        {
            "name": "⚡ Status",
            "value": "Integration Working",
            "inline": True
        }
    ]
    
    return send_discord_webhook(
        title="🎉 Discord Integration Test",
        description="**Discord webhooks are now active!**\n\nYou'll receive notifications for:\n• 📹 New videos\n• 🔴 Live streams\n• 📚 New courses",
        color=5763719,  # Gold color
        fields=test_fields
    )

# Add this function to your app.py

def migrate_stripe_integration():
    """Run this once to add Stripe integration fields to existing database"""
    try:
        # Add new columns to users table
        stripe_columns = [
            'ALTER TABLE users ADD COLUMN stripe_customer_id VARCHAR(100)',
            'ALTER TABLE users ADD COLUMN stripe_subscription_id VARCHAR(100)',
            'ALTER TABLE users ADD COLUMN subscription_status VARCHAR(50)',
            'ALTER TABLE users ADD COLUMN subscription_plan VARCHAR(50)',
            'ALTER TABLE users ADD COLUMN subscription_price_id VARCHAR(100)',
            'ALTER TABLE users ADD COLUMN subscription_current_period_start DATETIME',
            'ALTER TABLE users ADD COLUMN subscription_current_period_end DATETIME',
            'ALTER TABLE users ADD COLUMN subscription_cancel_at_period_end BOOLEAN DEFAULT FALSE',
            'ALTER TABLE users ADD COLUMN total_revenue DECIMAL(10,2) DEFAULT 0.00',
            'ALTER TABLE users ADD COLUMN last_payment_date DATETIME',
            'ALTER TABLE users ADD COLUMN last_payment_amount DECIMAL(10,2)',
            'CREATE INDEX idx_users_stripe_customer_id ON users(stripe_customer_id)',
            'CREATE INDEX idx_users_stripe_subscription_id ON users(stripe_subscription_id)'
        ]
        
        for sql in stripe_columns:
            try:
                db.session.execute(sql)
                print(f"✅ Executed: {sql[:50]}...")
            except Exception as e:
                if 'already exists' in str(e) or 'duplicate column' in str(e).lower():
                    print(f"ℹ️ Column already exists: {sql[:50]}...")
                else:
                    print(f"⚠️ Error executing {sql[:50]}...: {e}")
        
        # Create new tables
        db.create_all()
        db.session.commit()
        print("✅ Stripe integration migration completed successfully")
        
        return True
        
    except Exception as e:
        db.session.rollback()
        print(f"❌ Migration failed: {e}")
        return False

def initialize_stripe_price_ids():
    """Set up your Stripe price IDs - update these with your actual Stripe price IDs"""
    return {
        'monthly': 'price_1R4RMQCir8vKAFowSpnyfvnI',  # Replace with your actual monthly price ID
        'annual': 'price_1Rx5qACir8vKAFowfoovlQmt',
        'lifetime': 'price_1Rx5rfCir8vKAFowQ7UZ6syL'
    }

def sync_user_with_stripe(user_id, stripe_customer_id=None):
    """Sync a user's subscription data with Stripe"""
    try:
        user = User.query.get(user_id)
        if not user:
            return False
        
        # If no customer ID provided, try to find or create Stripe customer
        if not stripe_customer_id:
            if user.stripe_customer_id:
                stripe_customer_id = user.stripe_customer_id
            else:
                # Create new Stripe customer
                customer = stripe.Customer.create(
                    email=user.email,
                    name=user.username,
                    metadata={'user_id': user.id}
                )
                user.stripe_customer_id = customer.id
                stripe_customer_id = customer.id
        
        # Get customer from Stripe
        customer = stripe.Customer.retrieve(stripe_customer_id)
        
        # Get active subscriptions
        subscriptions = stripe.Subscription.list(
            customer=stripe_customer_id,
            status='all',
            limit=1
        )
        
        if subscriptions.data:
            subscription = subscriptions.data[0]
            
            # Update user subscription data
            user.stripe_subscription_id = subscription.id
            user.subscription_status = subscription.status
            user.subscription_current_period_start = datetime.fromtimestamp(subscription.current_period_start)
            user.subscription_current_period_end = datetime.fromtimestamp(subscription.current_period_end)
            user.subscription_cancel_at_period_end = subscription.cancel_at_period_end
            
            # Determine plan type
            if subscription.items.data:
                price_id = subscription.items.data[0].price.id
                price_ids = initialize_stripe_price_ids()
                
                for plan_name, plan_price_id in price_ids.items():
                    if price_id == plan_price_id:
                        user.subscription_plan = plan_name
                        break
                
                user.subscription_price_id = price_id
            
            # Update subscription status
            user.has_subscription = subscription.status in ['active', 'trialing']
            user.subscription_expires = user.subscription_current_period_end
        else:
            # No active subscription
            user.has_subscription = False
            user.subscription_status = None
            user.stripe_subscription_id = None
        
        db.session.commit()
        print(f"✅ Synced user {user.username} with Stripe")
        return True
        
    except Exception as e:
        db.session.rollback()
        print(f"❌ Error syncing user with Stripe: {e}")
        return False
        

# Manual LiveKit token generation (works perfectly without SDK)
def generate_livekit_token(room_name, participant_identity, participant_name, is_publisher=False):
    """Generate LiveKit JWT access token manually (100% compatible)"""
    try:
        livekit_api_key = app.config.get('LIVEKIT_API_KEY')
        livekit_api_secret = app.config.get('LIVEKIT_API_SECRET')
        
        if not all([livekit_api_key, livekit_api_secret]):
            print("⚠ LiveKit credentials missing - using development token")
            return "development-token-" + uuid.uuid4().hex[:16]
        
        # Create video grants exactly as LiveKit expects
        video_grants = {
            "roomJoin": True,
            "room": room_name,
            "canPublish": is_publisher,
            "canPublishData": is_publisher,
            "canSubscribe": True,
            "canUpdateOwnMetadata": False,
            "hidden": False,
            "recorder": False
        }
        
        # Create JWT payload
        now = int(time.time())
        exp = now + (4 * 60 * 60)  # 4 hours
        
        payload = {
            "exp": exp,
            "iss": livekit_api_key,
            "sub": participant_identity,
            "nbf": now,
            "iat": now,
            "name": participant_name,
            "video": video_grants,
            "metadata": ""
        }
        
        # Generate JWT token
        token = jwt.encode(
            payload,
            livekit_api_secret,
            algorithm='HS256'
        )
        
        # Handle both string and bytes return
        if isinstance(token, bytes):
            token = token.decode('utf-8')
        
        return token
        
    except Exception as e:
        print(f"Error generating LiveKit token: {e}")
        return "fallback-token-" + uuid.uuid4().hex[:16]
        
def init_s3_client():
    try:
        s3_client = boto3.client(
            's3',
            aws_access_key_id=app.config['AWS_ACCESS_KEY_ID'],
            aws_secret_access_key=app.config['AWS_SECRET_ACCESS_KEY'],
            region_name=app.config['AWS_REGION']
        )
        return s3_client
    except (NoCredentialsError, KeyError):
        return None

def generate_thumbnail(category_background_url, category_name, video_title, output_width=1280, output_height=720):
    """
    Generate a thumbnail by overlaying text on category background
    """
    try:
        # Download background image
        if category_background_url:
            response = requests.get(category_background_url)
            background = Image.open(BytesIO(response.content))
        else:
            # Create default background if none provided
            background = Image.new('RGB', (output_width, output_height), color='#1a1a1a')
        
        # Resize background to fit dimensions
        background = background.resize((output_width, output_height), Image.Resampling.LANCZOS)
        
        # Create drawing context
        draw = ImageDraw.Draw(background)
        
        # Try to load Poppins Bold font, fallback to default
        try:
            # You'll need to upload Poppins-Bold.ttf to your server
            font_large = ImageFont.truetype('/app/static/fonts/Poppins-Bold.ttf', 72)
            font_medium = ImageFont.truetype('/app/static/fonts/Poppins-Bold.ttf', 48)
        except:
            # Fallback to default font
            font_large = ImageFont.load_default()
            font_medium = ImageFont.load_default()
        
        # Add semi-transparent overlay for better text readability
        overlay = Image.new('RGBA', (output_width, output_height), (0, 0, 0, 128))
        background = Image.alpha_composite(background.convert('RGBA'), overlay)
        draw = ImageDraw.Draw(background)
        
        # Wrap and draw category name (top)
        category_wrapped = textwrap.fill(category_name.upper(), width=20)
        category_bbox = draw.multiline_textbbox((0, 0), category_wrapped, font=font_medium)
        category_width = category_bbox[2] - category_bbox[0]
        category_height = category_bbox[3] - category_bbox[1]
        category_x = (output_width - category_width) // 2
        category_y = output_height // 4
        
        # Draw category name with outline for better visibility
        for dx in [-2, -1, 0, 1, 2]:
            for dy in [-2, -1, 0, 1, 2]:
                draw.multiline_text((category_x + dx, category_y + dy), category_wrapped, 
                                  fill='black', font=font_medium, align='center')
        draw.multiline_text((category_x, category_y), category_wrapped, 
                          fill='white', font=font_medium, align='center')
        
        # Wrap and draw video title (center)
        video_wrapped = textwrap.fill(video_title, width=30)
        video_bbox = draw.multiline_textbbox((0, 0), video_wrapped, font=font_large)
        video_width = video_bbox[2] - video_bbox[0]
        video_height = video_bbox[3] - video_bbox[1]
        video_x = (output_width - video_width) // 2
        video_y = (output_height - video_height) // 2
        
        # Draw video title with outline
        for dx in [-3, -2, -1, 0, 1, 2, 3]:
            for dy in [-3, -2, -1, 0, 1, 2, 3]:
                draw.multiline_text((video_x + dx, video_y + dy), video_wrapped, 
                                  fill='black', font=font_large, align='center')
        draw.multiline_text((video_x, video_y), video_wrapped, 
                          fill='white', font=font_large, align='center')
        
        # Convert back to RGB for JPEG saving
        if background.mode != 'RGB':
            background = background.convert('RGB')
        
        return background
        
    except Exception as e:
        print(f"Error generating thumbnail: {e}")
        # Return a default thumbnail
        default = Image.new('RGB', (output_width, output_height), color='#10B981')
        draw = ImageDraw.Draw(default)
        draw.text((output_width//2, output_height//2), f"{category_name}\n{video_title}", 
                 fill='white', anchor='mm')
        return default

def upload_thumbnail_to_s3(image, video_id, video_title):
    """
    Upload generated thumbnail to S3
    """
    try:
        s3_client = init_s3_client()
        if not s3_client:
            return None
        
        # Create filename
        safe_title = re.sub(r'[^a-zA-Z0-9\s-]', '', video_title)
        safe_title = re.sub(r'\s+', '-', safe_title.strip())
        filename = f"thumbnails/video-{video_id}-{safe_title[:30]}.jpg"
        
        # Save image to bytes
        img_buffer = BytesIO()
        image.save(img_buffer, format='JPEG', quality=90, optimize=True)
        img_buffer.seek(0)
        
        # Upload to S3
        bucket = app.config.get('AWS_S3_BUCKET', 'tgfx-tradelab')
        s3_client.upload_fileobj(
            img_buffer,
            bucket,
            filename,
            ExtraArgs={
                'ContentType': 'image/jpeg',
                'ServerSideEncryption': 'AES256'
            }
        )
        
        # Return S3 URL
        return f"https://{bucket}.s3.amazonaws.com/{filename}"
        
    except Exception as e:
        print(f"Error uploading thumbnail to S3: {e}")
        return None


def auto_fill_live_session_title(user):
    """
    Generate auto-filled title for live trading sessions
    """
    trader_name, trading_pair = get_trader_defaults(user)
    current_date = datetime.now().strftime('%m-%d-%y')
    return f"{trader_name} - {current_date} {trading_pair}"

def user_can_access_video(video):
    if video.is_free:
        return True
    if current_user.is_authenticated and current_user.has_subscription:
        return True
    return False

def get_or_create_tag(tag_name):
    slug = re.sub(r'[^a-zA-Z0-9\s-]', '', tag_name.lower())
    slug = re.sub(r'\s+', '-', slug.strip())
    
    tag = Tag.query.filter_by(slug=slug).first()
    if not tag:
        tag = Tag(
            name=tag_name.strip().title(),
            slug=slug,
            color='#10B981'
        )
        db.session.add(tag)
        db.session.flush()
    
    return tag

def process_video_tags(video, tags_string):
    if not tags_string:
        video.tags.clear()
        return
    
    video.tags.clear()
    tag_names = [name.strip() for name in tags_string.split(',') if name.strip()]
    
    for tag_name in tag_names:
        if tag_name:
            tag = get_or_create_tag(tag_name)
            video.tags.append(tag)

def create_user_activity(user_id, activity_type, description):
    """Create a new user activity record"""
    try:
        activity = UserActivity(
            user_id=user_id,
            activity_type=activity_type,
            description=description,
            timestamp=datetime.utcnow()
        )
        db.session.add(activity)
        db.session.commit()
    except Exception as e:
        print(f"Error creating user activity: {e}")
        db.session.rollback()

def convert_empty_strings_to_none(data, integer_fields):
    """Convert empty strings to None for integer fields"""
    for field in integer_fields:
        if field in data and data[field] == '':
            data[field] = None
        elif field in data and data[field] is not None:
            try:
                # Try to convert to int if it's a string with a value
                data[field] = int(data[field]) if str(data[field]).strip() else None
            except (ValueError, TypeError):
                data[field] = None
    return data

def create_notification(user_id, title, message, notification_type):
    """Create a new notification for a user"""
    notification = Notification(
        user_id=user_id,
        title=title,
        message=message,
        notification_type=notification_type
    )
    db.session.add(notification)
    db.session.commit()

def broadcast_notification(title, message, notification_type, target_users='all'):
    """Send notification to multiple users"""
    if target_users == 'all':
        users = User.query.all()
    elif target_users == 'premium':
        users = User.query.filter_by(has_subscription=True).all()
    elif target_users == 'free':
        users = User.query.filter_by(has_subscription=False).all()
    else:
        users = []
    
    for user in users:
        create_notification(user.id, title, message, notification_type)

def get_category_progress(category_id, user_progress):
    category = Category.query.get(category_id)
    if not category:
        return {'completed': 0, 'total': 0}
    
    videos = category.videos
    total_videos = len(videos)
    completed_videos = 0
    
    for video in videos:
        progress = user_progress.get(video.id)
        if progress and progress.completed:
            completed_videos += 1
    
    return {
        'completed': completed_videos,
        'total': total_videos
    }

def get_course_tags(videos):
    tags = set()
    for video in videos:
        for tag in video.tags:
            tags.add(tag)
    return list(tags)

def get_total_duration(videos):
    total = 0
    for video in videos:
        if video.duration:
            total += video.duration
    return total

# LiveKit Helper Functions - NEW
def init_livekit_api():
    """Initialize LiveKit API configuration - no SDK needed"""
    try:
        livekit_api_key = app.config.get('LIVEKIT_API_KEY')
        livekit_api_secret = app.config.get('LIVEKIT_API_SECRET')
        livekit_url = app.config.get('LIVEKIT_URL')
        
        if not all([livekit_api_key, livekit_api_secret, livekit_url]):
            print("LiveKit configuration incomplete")
            return None
        
        # Return configuration for reference
        return {
            'url': livekit_url,
            'api_key': livekit_api_key,
            'api_secret': livekit_api_secret,
            'configured': True
        }
    except Exception as e:
        print(f"Error initializing LiveKit config: {e}")
        return None

def create_livekit_room(room_name, streamer_name):
    """Create a LiveKit room reference - actual room created client-side"""
    try:
        # Check configuration is available
        lk_config = init_livekit_api()
        if not lk_config:
            print("LiveKit configuration not available")
        
        # Room creation happens on LiveKit server when first participant joins
        # We just create a reference for our database
        class Room:
            def __init__(self, name):
                self.name = name
                self.sid = f"RM_{uuid.uuid4().hex[:12]}"
        
        room = Room(room_name)
        print(f"✓ Room reference created: {room_name} for {streamer_name}")
        return room
            
    except Exception as e:
        print(f"Error creating room reference: {e}")
        # Return a room object anyway so the app continues
        class MockRoom:
            def __init__(self, name):
                self.name = name
                self.sid = f"RM_{uuid.uuid4().hex[:12]}"
        return MockRoom(room_name)

def delete_livekit_room(room_name):
    """Mark room as deleted - actual deletion handled by LiveKit server"""
    try:
        print(f"✓ Room marked for cleanup: {room_name}")
        return True
    except Exception as e:
        print(f"Error in room cleanup: {e}")
        return True

def generate_livekit_token(room_name, participant_identity, participant_name, is_publisher=False):
    """Generate LiveKit JWT access token manually (100% compatible)"""
    try:
        livekit_api_key = app.config.get('LIVEKIT_API_KEY')
        livekit_api_secret = app.config.get('LIVEKIT_API_SECRET')
        
        if not all([livekit_api_key, livekit_api_secret]):
            print("⚠ LiveKit credentials missing - using development token")
            return "development-token-" + uuid.uuid4().hex[:16]
        
        # Create video grants exactly as LiveKit expects
        video_grants = {
            "roomJoin": True,
            "room": room_name,
            "canPublish": is_publisher,
            "canPublishData": is_publisher,
            "canSubscribe": True,
            "canUpdateOwnMetadata": False,
            "hidden": False,
            "recorder": False
        }
        
        # Create JWT payload following LiveKit specification
        now = int(time.time())
        exp = now + (4 * 60 * 60)  # 4 hours
        
        payload = {
            "exp": exp,
            "iss": livekit_api_key,
            "sub": participant_identity,
            "nbf": now,
            "iat": now,
            "name": participant_name,
            "video": video_grants,
            "metadata": ""
        }
        
        # Generate JWT token
        token = jwt.encode(
            payload,
            livekit_api_secret,
            algorithm='HS256'
        )
        
        # Handle both string and bytes return
        if isinstance(token, bytes):
            token = token.decode('utf-8')
        
        print(f"✓ Generated LiveKit token for {participant_name}")
        return token
        
    except Exception as e:
        print(f"Error generating LiveKit token: {e}")
        return "fallback-token-" + uuid.uuid4().hex[:16]

def start_livekit_recording(room_name):
    """Recording handled by LiveKit Cloud"""
    print(f"Recording for {room_name} - handled by LiveKit Cloud")
    # Return a mock recording info
    return {"id": f"REC_{uuid.uuid4().hex[:12]}"}

def stop_livekit_recording(recording_id):
    """Stop recording via LiveKit Cloud"""
    print(f"Stop recording {recording_id} - handled by LiveKit Cloud")
    return True
    
def get_recording_s3_key(stream_id, streamer_name, timestamp=None):
    """Generate S3 key for stream recording with streamer name"""
    if not timestamp:
        timestamp = datetime.utcnow()
    
    date_str = timestamp.strftime('%Y/%m/%d')
    filename = f"{streamer_name}-stream-{stream_id}-{timestamp.strftime('%Y%m%d-%H%M%S')}.mp4"
    
    prefix = app.config.get('STREAM_RECORDINGS_PREFIX', 'livestream-recordings/')
    return f"{prefix}livekit/{date_str}/{filename}"

def upload_recording_to_s3(local_file_path, stream_id, streamer_name):
    """Upload recording file to S3 with streamer name"""
    s3_client = init_s3_client()
    if not s3_client:
        return None
    
    try:
        bucket = app.config['STREAM_RECORDINGS_BUCKET']
        s3_key = get_recording_s3_key(stream_id, streamer_name)
        
        s3_client.upload_file(
            local_file_path,
            bucket,
            s3_key,
            ExtraArgs={
                'ContentType': 'video/mp4',
                'ServerSideEncryption': 'AES256',
                'Metadata': {
                    'streamer': streamer_name,
                    'stream_id': str(stream_id)
                }
            }
        )
        
        s3_url = f"https://{bucket}.s3.amazonaws.com/{s3_key}"
        return s3_url
        
    except ClientError as e:
        print(f"Error uploading recording to S3: {e}")
        return None

def handle_subscription_created(subscription):
    """Handle new subscription creation"""
    try:
        print(f"🆕 New subscription created: {subscription['id']}")
        
        # Find user by customer ID
        user = User.query.filter_by(stripe_customer_id=subscription['customer']).first()
        if not user:
            print(f"⚠️ User not found for customer {subscription['customer']}")
            return
        
        # Update user subscription info
        user.stripe_subscription_id = subscription['id']
        user.subscription_status = subscription['status']
        user.has_subscription = subscription['status'] in ['active', 'trialing']
        user.subscription_current_period_start = datetime.fromtimestamp(subscription['current_period_start'])
        user.subscription_current_period_end = datetime.fromtimestamp(subscription['current_period_end'])
        user.subscription_expires = user.subscription_current_period_end
        user.subscription_cancel_at_period_end = subscription.get('cancel_at_period_end', False)
        
        # Determine plan type
        if subscription['items']['data']:
            price_id = subscription['items']['data'][0]['price']['id']
            price_ids = initialize_stripe_price_ids()
            
            for plan_name, plan_price_id in price_ids.items():
                if price_id == plan_price_id:
                    user.subscription_plan = plan_name
                    break
            
            user.subscription_price_id = price_id
        
        db.session.commit()
        
        # Create welcome notification
        create_notification(
            user.id,
            'Welcome to Premium!',
            f'Your {user.subscription_plan} subscription is now active. Enjoy exclusive access to all premium content!',
            'subscription'
        )
        
        # Create activity log
        create_user_activity(
            user.id,
            'subscription_activated',
            f'Premium subscription activated ({user.subscription_plan})'
        )
        
        print(f"✅ User {user.username} subscription activated: {user.subscription_plan}")
        
    except Exception as e:
        print(f"❌ Error handling subscription created: {e}")
        db.session.rollback()

def handle_subscription_updated(subscription):
    """Handle subscription updates"""
    try:
        print(f"🔄 Subscription updated: {subscription['id']}")
        
        # Find user by subscription ID
        user = User.query.filter_by(stripe_subscription_id=subscription['id']).first()
        if not user:
            print(f"⚠️ User not found for subscription {subscription['id']}")
            return
        
        old_status = user.subscription_status
        
        # Update subscription info
        user.subscription_status = subscription['status']
        user.has_subscription = subscription['status'] in ['active', 'trialing']
        user.subscription_current_period_start = datetime.fromtimestamp(subscription['current_period_start'])
        user.subscription_current_period_end = datetime.fromtimestamp(subscription['current_period_end'])
        user.subscription_expires = user.subscription_current_period_end
        user.subscription_cancel_at_period_end = subscription.get('cancel_at_period_end', False)
        
        # Check if plan changed
        if subscription['items']['data']:
            price_id = subscription['items']['data'][0]['price']['id']
            price_ids = initialize_stripe_price_ids()
            
            old_plan = user.subscription_plan
            for plan_name, plan_price_id in price_ids.items():
                if price_id == plan_price_id:
                    user.subscription_plan = plan_name
                    break
            
            user.subscription_price_id = price_id
            
            # Notify if plan changed
            if old_plan != user.subscription_plan:
                create_notification(
                    user.id,
                    'Plan Updated',
                    f'Your subscription plan has been updated from {old_plan} to {user.subscription_plan}.',
                    'subscription'
                )
        
        # Notify on status changes
        if old_status != user.subscription_status:
            if user.subscription_status == 'past_due':
                create_notification(
                    user.id,
                    'Payment Past Due',
                    'Your subscription payment is past due. Please update your payment method to continue service.',
                    'payment_issue'
                )
            elif user.subscription_status == 'canceled':
                create_notification(
                    user.id,
                    'Subscription Canceled',
                    'Your subscription has been canceled. You will lose access to premium content at the end of your billing period.',
                    'subscription'
                )
        
        db.session.commit()
        print(f"✅ User {user.username} subscription updated: {user.subscription_status}")
        
    except Exception as e:
        print(f"❌ Error handling subscription updated: {e}")
        db.session.rollback()

def handle_subscription_deleted(subscription):
    """Handle subscription cancellation"""
    try:
        print(f"❌ Subscription deleted: {subscription['id']}")
        
        # Find user by subscription ID
        user = User.query.filter_by(stripe_subscription_id=subscription['id']).first()
        if not user:
            print(f"⚠️ User not found for subscription {subscription['id']}")
            return
        
        # Update user subscription info
        user.has_subscription = False
        user.subscription_status = 'canceled'
        user.subscription_cancel_at_period_end = False
        
        # Keep expiration date so they have access until end of period
        if not user.subscription_expires:
            user.subscription_expires = datetime.fromtimestamp(subscription['current_period_end'])
        
        db.session.commit()
        
        # Create notification
        create_notification(
            user.id,
            'Subscription Ended',
            'Your premium subscription has ended. You can resubscribe anytime to regain access to premium content.',
            'subscription'
        )
        
        # Create activity log
        create_user_activity(
            user.id,
            'subscription_canceled',
            'Premium subscription canceled'
        )
        
        print(f"✅ User {user.username} subscription canceled")
        
    except Exception as e:
        print(f"❌ Error handling subscription deleted: {e}")
        db.session.rollback()

def handle_payment_succeeded(invoice):
    """Handle successful payments"""
    try:
        print(f"💰 Payment succeeded: {invoice['id']}")
        
        # Find user by customer ID
        user = User.query.filter_by(stripe_customer_id=invoice['customer']).first()
        if not user:
            print(f"⚠️ User not found for customer {invoice['customer']}")
            return
        
        payment_amount = invoice['amount_paid'] / 100  # Convert cents to dollars
        
        # Update user payment info
        user.last_payment_date = datetime.fromtimestamp(invoice['created'])
        user.last_payment_amount = payment_amount
        user.total_revenue = (user.total_revenue or 0) + payment_amount
        
        db.session.commit()
        
        # Create notification for significant payments (subscriptions)
        if payment_amount >= 20:  # Only for subscription payments, not small fees
            create_notification(
                user.id,
                'Payment Received',
                f'Thank you! Your payment of ${payment_amount:.2f} has been processed successfully.',
                'payment'
            )
        
        print(f"✅ Payment processed for {user.username}: ${payment_amount:.2f}")
        
    except Exception as e:
        print(f"❌ Error handling payment succeeded: {e}")
        db.session.rollback()

def handle_payment_failed(invoice):
    """Handle failed payments"""
    try:
        print(f"⚠️ Payment failed: {invoice['id']}")
        
        # Find user by customer ID
        user = User.query.filter_by(stripe_customer_id=invoice['customer']).first()
        if not user:
            print(f"⚠️ User not found for customer {invoice['customer']}")
            return
        
        # Create urgent notification
        create_notification(
            user.id,
            'Payment Failed',
            'Your subscription payment failed. Please update your payment method to avoid service interruption.',
            'payment_issue'
        )
        
        # If multiple failed payments, send email reminder (you can implement this)
        failed_attempts = invoice.get('attempt_count', 1)
        if failed_attempts >= 2:
            create_notification(
                user.id,
                'Urgent: Multiple Payment Failures',
                f'We\'ve attempted to charge your card {failed_attempts} times. Please update your payment method immediately to keep your subscription active.',
                'payment_issue'
            )
        
        print(f"⚠️ Payment failed for {user.username} (attempt {failed_attempts})")
        
    except Exception as e:
        print(f"❌ Error handling payment failed: {e}")

def handle_trial_will_end(subscription):
    """Handle trial ending soon"""
    try:
        print(f"⏰ Trial ending soon: {subscription['id']}")
        
        # Find user by subscription ID
        user = User.query.filter_by(stripe_subscription_id=subscription['id']).first()
        if not user:
            return
        
        trial_end = datetime.fromtimestamp(subscription['trial_end'])
        
        create_notification(
            user.id,
            'Trial Ending Soon',
            f'Your free trial ends on {trial_end.strftime("%B %d, %Y")}. Add a payment method to continue enjoying premium access.',
            'trial'
        )
        
        print(f"✅ Trial reminder sent to {user.username}")
        
    except Exception as e:
        print(f"❌ Error handling trial will end: {e}")

def handle_checkout_completed(session):
    """Handle completed checkout sessions"""
    try:
        print(f"🛒 Checkout completed: {session['id']}")
        
        # Get user from metadata
        user_id = session['metadata'].get('user_id')
        if user_id:
            user = User.query.get(user_id)
            if user:
                # Sync user data with Stripe
                sync_user_with_stripe(user.id)
                
                create_notification(
                    user.id,
                    'Welcome to Premium!',
                    'Your subscription has been activated. Start exploring premium content now!',
                    'subscription'
                )
        
    except Exception as e:
        print(f"❌ Error handling checkout completed: {e}")

def log_stripe_event(event):
    """Log Stripe events for analytics"""
    try:
        # Determine user if possible
        user_id = None
        stripe_customer_id = None
        stripe_subscription_id = None
        amount = None
        
        event_object = event['data']['object']
        
        # Extract customer info
        if 'customer' in event_object:
            stripe_customer_id = event_object['customer']
            user = User.query.filter_by(stripe_customer_id=stripe_customer_id).first()
            if user:
                user_id = user.id
        
        # Extract subscription info
        if 'subscription' in event_object:
            stripe_subscription_id = event_object['subscription']
        elif event_object.get('object') == 'subscription':
            stripe_subscription_id = event_object['id']
        
        # Extract amount for payment events
        if 'amount_paid' in event_object:
            amount = event_object['amount_paid'] / 100
        elif 'amount' in event_object:
            amount = event_object['amount'] / 100
        
        # Create event record
        subscription_event = SubscriptionEvent(
            user_id=user_id,
            stripe_customer_id=stripe_customer_id,
            stripe_subscription_id=stripe_subscription_id,
            event_type=event['type'],
            event_data=json.dumps(event_object),
            amount=amount,
            processed=True
        )
        
        db.session.add(subscription_event)
        db.session.commit()
        
        print(f"📊 Logged event: {event['type']}")
        
    except Exception as e:
        print(f"❌ Error logging Stripe event: {e}")
        db.session.rollback()

# Helper function to update revenue analytics daily
@app.route('/api/admin/update-revenue-analytics', methods=['POST'])
@login_required
def api_update_revenue_analytics():
    """Manually trigger revenue analytics update"""
    if not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    try:
        update_revenue_analytics()
        return jsonify({'success': True, 'message': 'Revenue analytics updated'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Function to sync all subscription statuses with Stripe
def sync_all_subscriptions_with_stripe():
    """Sync all user subscriptions with Stripe - run this periodically"""
    try:
        print("🔄 Starting bulk subscription sync with Stripe...")
        
        # Get all users with Stripe customer IDs
        users_with_stripe = User.query.filter(User.stripe_customer_id.isnot(None)).all()
        
        success_count = 0
        error_count = 0
        
        for user in users_with_stripe:
            try:
                if sync_user_with_stripe(user.id):
                    success_count += 1
                else:
                    error_count += 1
            except Exception as e:
                print(f"Error syncing user {user.id}: {e}")
                error_count += 1
        
        print(f"✅ Bulk sync completed: {success_count} successful, {error_count} errors")
        return success_count, error_count
        
    except Exception as e:
        print(f"❌ Error in bulk sync: {e}")
        return 0, 0



def initialize_streamers():
    """Initialize Ray and Jordan as streamers - run this once after deployment"""
    try:
        ray = User.query.filter_by(username='admin').first()
        if ray:
            ray.display_name = 'Ray'
            ray.can_stream = True
            ray.stream_color = '#10B981'
            ray.timezone = ray.timezone or 'America/Chicago'  # Set default timezone
        
        jordan = User.query.filter_by(username='jordan').first()
        if not jordan:
            jordan = User(
                username='jwill24',
                email='williamsjordan947@gmail.com',
                password_hash=generate_password_hash('jordan123!secure'),
                is_admin=True,
                display_name='Jordan',
                can_stream=True,
                stream_color='#3B82F6',
                timezone='America/Chicago'  # Default timezone
            )
            db.session.add(jordan)
        else:
            jordan.display_name = 'Jordan'
            jordan.can_stream = True
            jordan.stream_color = '#3B82F6'
            jordan.is_admin = True
            jordan.timezone = jordan.timezone or 'America/Chicago'  # Set default timezone
        
        db.session.commit()
        print("✅ Streamers initialized: Ray (Green) and Jordan (Blue)")
        
    except Exception as e:
        print(f"❌ Error initializing streamers: {e}")
        db.session.rollback()

def migrate_user_timezones():
    """Run this once to add timezone to existing users"""
    try:
        # First, try to add the column if it doesn't exist
        try:
            db.session.execute('ALTER TABLE users ADD COLUMN timezone VARCHAR(50) DEFAULT "America/Chicago"')
            db.session.commit()
            print("✓ Added timezone column to users table")
        except Exception as e:
            # Column probably already exists
            db.session.rollback()
            print("✓ Timezone column already exists or error adding:", str(e))
        
        # Update users without timezone
        users_without_timezone = User.query.filter(
            db.or_(User.timezone.is_(None), User.timezone == '')
        ).all()
        
        for user in users_without_timezone:
            user.timezone = 'America/Chicago'  # Default to CST
        
        db.session.commit()
        print(f"✓ Updated {len(users_without_timezone)} users with default timezone")
        
    except Exception as e:
        db.session.rollback()
        print(f"❌ Error migrating user timezones: {e}")

def has_active_subscription(self):
    """Check if user has an active subscription - updated for lifetime"""
    if not self.has_subscription:
        return False
    
    # Lifetime subscription never expires
    if self.subscription_plan == 'lifetime':
        return True
    
    if self.subscription_status in ['active', 'trialing']:
        return True
        
    if self.subscription_expires and self.subscription_expires > datetime.utcnow():
        return True
        
    return False

def get_subscription_status_display(self):
    """Get human-readable subscription status - updated for lifetime"""
    if not self.has_subscription:
        return "Free"
    
    if self.subscription_plan == 'lifetime':
        return "Lifetime"
    
    status_map = {
        'active': 'Active',
        'trialing': 'Trial',
        'past_due': 'Past Due',
        'canceled': 'Canceled',
        'unpaid': 'Unpaid',
        'incomplete': 'Incomplete'
    }
    
    return status_map.get(self.subscription_status, 'Unknown')

def get_subscription_plan_display(self):
    """Get human-readable subscription plan - updated for lifetime"""
    plan_map = {
        'monthly': 'Monthly ($80/month)',
        'annual': 'Annual ($777/year)',
        'lifetime': 'Lifetime Access ($1497 one-time)'
    }
    
    return plan_map.get(self.subscription_plan, 'Unknown Plan')

def is_lifetime_subscriber(self):
    """Check if user has lifetime subscription"""
    return self.subscription_plan == 'lifetime' and self.has_subscription
        
# Custom Jinja2 filters

@app.template_filter('linkify')
def linkify_filter(text):
    """Convert URLs in text to clickable links with enhanced detection"""
    if text is None:
        return ''
    
    # Enhanced URL regex pattern that captures more URL formats
    url_pattern = re.compile(
        r'(?i)\b(?:'
        r'(?:https?://|www\.)'  # http://, https://, or www.
        r'(?:[^\s<>"]+)'        # non-whitespace, non-HTML chars
        r'|'
        r'(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}'  # domain.com format
        r'(?:/[^\s<>"]*)?'      # optional path
        r')\b'
    )
    
    def replace_url(match):
        url = match.group(0)
        # Add http:// if the URL doesn't start with a protocol
        if not url.startswith(('http://', 'https://')):
            href_url = 'https://' + url
        else:
            href_url = url
        
        # Truncate display text if URL is very long
        display_url = url if len(url) <= 50 else url[:47] + '...'
        
        return f'<a href="{href_url}" target="_blank" rel="noopener noreferrer" class="notes-link" title="{url}">{display_url}</a>'
    
    # First convert line breaks to <br> tags
    text_with_breaks = text.replace('\n', '<br>\n')
    
    # Then linkify URLs
    linkified_text = url_pattern.sub(replace_url, text_with_breaks)
    
    return linkified_text
    
@app.template_filter('nl2br')
def nl2br_filter(text):
    if text is None:
        return ''
    return text.replace('\n', '<br>\n')

@app.template_filter('extract')
def extract_filter(dictionary, key):
    try:
        if isinstance(dictionary, dict):
            return dictionary.get(key)
        elif hasattr(dictionary, '__getitem__'):
            return dictionary[key]
        else:
            return None
    except (KeyError, IndexError, TypeError, AttributeError):
        return None


if socketio:
    # Store active connections and stream rooms
    active_connections = {}
    stream_rooms = {}

    @socketio.on('connect')
    def handle_connect():
        """Handle client connection with enhanced error handling and debugging"""
        try:
            client_id = request.sid
            user_id = None
            
            print(f"🔌 WebSocket connect attempt - Client ID: {client_id}")
            
            # Get user ID if authenticated with more robust checking
            if hasattr(current_user, 'is_authenticated') and current_user.is_authenticated:
                user_id = current_user.id
                is_admin = getattr(current_user, 'is_admin', False)
                can_stream = getattr(current_user, 'can_stream', False)
                
                active_connections[client_id] = {
                    'user_id': user_id,
                    'username': current_user.username,
                    'is_admin': is_admin,
                    'can_stream': can_stream,
                    'connected_at': time.time()
                }
                
                print(f"✅ Authenticated user connected: {current_user.username} (Admin: {is_admin}, Can Stream: {can_stream})")
            else:
                active_connections[client_id] = {
                    'user_id': None,
                    'username': 'Anonymous',
                    'is_admin': False,
                    'can_stream': False,
                    'connected_at': time.time()
                }
                print(f"👤 Anonymous user connected: {client_id}")
            
            emit('connection_status', {
                'status': 'connected', 
                'client_id': client_id,
                'user_info': active_connections[client_id]
            })
            
        except Exception as e:
            print(f"❌ Error in connect handler: {e}")
            emit('error', {'message': 'Connection error'})

@socketio.on('join_stream')
def handle_join_stream(data):
    """Handle user joining a stream with automatic admin recording"""
    try:
        client_id = request.sid
        stream_id = data.get('stream_id')
        
        print(f"🎬 Join stream request - Client: {client_id}, Stream: {stream_id}")
        
        if not stream_id:
            emit('error', {'message': 'Stream ID required'})
            return

        # Get user info from active connections
        user_info = active_connections.get(client_id, {})
        print(f"📊 User info for {client_id}: {user_info}")
        
        # Verify stream exists and is active
        stream = Stream.query.filter_by(id=stream_id, is_active=True).first()
        if not stream:
            emit('error', {'message': 'Stream not found or inactive'})
            return

        room_id = f"stream_{stream_id}"
        
        # Initialize room if it doesn't exist
        if room_id not in stream_rooms:
            stream_rooms[room_id] = {
                'stream_id': stream_id,
                'admin_client': None,
                'viewers': [],
                'created_at': time.time(),
                'media_published': False  # Track if media is being published
            }

        # Join the room
        join_room(room_id)
        
        # Check if user is admin/stream owner
        is_authenticated = (
            hasattr(current_user, 'is_authenticated') and 
            current_user.is_authenticated
        )
        
        is_stream_owner = (
            is_authenticated and 
            stream.created_by == current_user.id
        )
        
        is_admin_user = user_info.get('is_admin', False)
        can_stream_user = user_info.get('can_stream', False)
        
        # User is stream admin if they own the stream OR are admin with streaming rights
        is_stream_admin = is_stream_owner or (is_admin_user and can_stream_user)
        
        print(f"🔍 Admin check for {client_id}:")
        print(f"  - Final Admin Status: {is_stream_admin}")
        
        if is_stream_admin:
            stream_rooms[room_id]['admin_client'] = client_id
            emit('admin_joined', {'stream_id': stream_id}, room=room_id)
            print(f"🎬 Admin joined stream room: {room_id}")
            
            # Generate LiveKit token for publisher (streamer)
            participant_identity = f"streamer-{current_user.id}"
            participant_name = stream.streamer_name or current_user.username
            
            livekit_token = generate_livekit_token(
                stream.room_name, 
                participant_identity, 
                participant_name,
                is_publisher=True
            )
            
            # DON'T START RECORDING IMMEDIATELY - Wait for media_published event
            emit('admin_status', {
                'is_admin': True,
                'can_broadcast': True,
                'stream_id': stream_id,
                'livekit_token': livekit_token,
                'livekit_url': app.config.get('LIVEKIT_URL'),
                'room_name': stream.room_name,
                'participant_identity': participant_identity,
                'wait_for_media': True  # Tell client to notify when media is ready
            })
        else:
            # Handle viewer join...
            pass
            
    except Exception as e:
        print(f"❌ Error in join_stream handler: {e}")
        emit('error', {'message': 'Failed to join stream'})

# Add new event handler for media ready
@socketio.on('media_published')
def handle_media_published(data):
    """Handle notification that media is being published"""
    try:
        client_id = request.sid
        stream_id = data.get('stream_id')
        
        print(f"📹 Media published notification for stream {stream_id}")
        
        stream = Stream.query.get(stream_id)
        if not stream or not stream.is_active:
            return
        
        room_id = f"stream_{stream_id}"
        if room_id in stream_rooms:
            stream_rooms[room_id]['media_published'] = True
        
        # NOW start recording since media is being published
        if not stream.is_recording:
            print(f"🔴 Starting recording now that media is published...")
            
            recording_result = start_livekit_egress_recording(
                room_name=stream.room_name,
                stream_id=stream_id,
                streamer_name=stream.streamer_name
            )
            
            if recording_result and recording_result.get('success'):
                stream.is_recording = True
                stream.recording_id = recording_result.get('egress_id')
                db.session.commit()
                
                print(f"✅ Recording started successfully!")
                print(f"🔑 Egress ID: {recording_result.get('egress_id')}")
                
                socketio.emit('recording_started', {
                    'stream_id': stream_id,
                    'message': 'Recording has started',
                    'egress_id': recording_result.get('egress_id')
                }, room=room_id)
            else:
                print(f"⚠️ Failed to start recording: {recording_result.get('error')}")
                
    except Exception as e:
        print(f"❌ Error handling media published: {e}")
    
    @socketio.on('screen_frame')
    def handle_screen_frame(data):
        """Handle screen sharing frames - LiveKit handles this natively"""
        try:
            client_id = request.sid
            stream_id = data.get('stream_id')
            
            # With LiveKit, screen sharing is handled directly by the SDK
            print(f"🖥️ Screen frame received for stream {stream_id} from {client_id}")
            
            # Emit for any custom overlays or notifications
            room_id = f"stream_{stream_id}"
            if room_id in stream_rooms:
                emit('screen_share_activity', {
                    'stream_id': stream_id,
                    'is_sharing': True,
                    'timestamp': time.time()
                }, room=room_id, include_self=False)
                
        except Exception as e:
            print(f"❌ Error in screen_frame handler: {e}")

    @socketio.on('stream_control')
    def handle_stream_control(data):
        """Handle stream control commands (audio/screen start/stop)"""
        try:
            client_id = request.sid
            stream_id = data.get('stream_id')
            control_type = data.get('type')
            control_data = data.get('data', {})
            
            print(f"🎛️ Stream control: {control_type} from client {client_id} for stream {stream_id}")
            
            # Verify admin status same as audio/screen handlers
            user_info = active_connections.get(client_id, {})
            
            is_authenticated = (
                hasattr(current_user, 'is_authenticated') and 
                current_user.is_authenticated
            )
            
            is_admin_user = user_info.get('is_admin', False)
            can_stream_user = user_info.get('can_stream', False)
            
            stream = Stream.query.filter_by(id=stream_id, is_active=True).first()
            is_stream_owner = (
                stream and 
                is_authenticated and 
                stream.created_by == current_user.id
            )
            
            can_control = is_stream_owner or (is_admin_user and can_stream_user)
            
            if not can_control:
                emit('error', {'message': 'Not authorized to control stream'})
                return

            room_id = f"stream_{stream_id}"
            
            if room_id in stream_rooms:
                # Broadcast control update to all viewers
                emit('stream_update', {
                    'type': control_type,
                    'data': control_data,
                    'timestamp': time.time(),
                    'stream_id': stream_id
                }, room=room_id, include_self=False)
                
                print(f"✅ Stream control broadcasted: {control_type}")
            else:
                emit('error', {'message': 'Stream room not found'})
                
        except Exception as e:
            print(f"❌ Error in stream_control handler: {e}")
            emit('error', {'message': 'Stream control error'})

    @socketio.on('status_update')
    def handle_status_update(data):
        """Handle status updates from admin with enhanced verification"""
        try:
            client_id = request.sid
            stream_id = data.get('stream_id')
            status = data.get('status', {})
            
            # Same verification pattern
            user_info = active_connections.get(client_id, {})
            
            is_authenticated = (
                hasattr(current_user, 'is_authenticated') and 
                current_user.is_authenticated
            )
            
            is_admin_user = user_info.get('is_admin', False)
            can_stream_user = user_info.get('can_stream', False)
            
            stream = Stream.query.filter_by(id=stream_id, is_active=True).first()
            is_stream_owner = (
                stream and 
                is_authenticated and 
                stream.created_by == current_user.id
            )
            
            can_update_status = is_stream_owner or (is_admin_user and can_stream_user)
            
            if not can_update_status:
                emit('error', {'message': 'Not authorized to update status'})
                return

            room_id = f"stream_{stream_id}"
            
            if (room_id in stream_rooms and 
                stream_rooms[room_id]['admin_client'] == client_id):
                
                # Broadcast status to all viewers
                emit('status_update', {
                    'status': status,
                    'timestamp': time.time(),
                    'stream_id': stream_id
                }, room=room_id, include_self=False, broadcast=True)
                
                print(f"📊 Status update for stream {stream_id}: {status}")
            else:
                emit('error', {'message': 'Not authorized for this stream'})
                
        except Exception as e:
            print(f"❌ Error in status_update handler: {e}")
            emit('error', {'message': 'Status update error'})

    @socketio.on('disconnect')
    def handle_disconnect():
        """Handle client disconnection with cleanup"""
        try:
            client_id = request.sid
            
            if client_id in active_connections:
                user_info = active_connections[client_id]
                print(f"🔌 Client disconnected: {client_id} (User: {user_info.get('username')})")
                del active_connections[client_id]
            
            # Clean up stream rooms
            for room_id in list(stream_rooms.keys()):
                if client_id in stream_rooms[room_id]['viewers']:
                    stream_rooms[room_id]['viewers'].remove(client_id)
                    emit('viewer_left', {
                        'client_id': client_id,
                        'viewer_count': len(stream_rooms[room_id]['viewers'])
                    }, room=room_id)
                
                if stream_rooms[room_id]['admin_client'] == client_id:
                    stream_rooms[room_id]['admin_client'] = None
                    emit('admin_left', {'stream_id': stream_rooms[room_id]['stream_id']}, room=room_id)
                    print(f"🎬 Admin left stream room: {room_id}")
                    
        except Exception as e:
            print(f"❌ Error in disconnect handler: {e}")

def generate_livekit_api_token():
    """Generate a JWT token for LiveKit API access"""
    import jwt
    import time
    
    livekit_api_key = app.config.get('LIVEKIT_API_KEY')
    livekit_api_secret = app.config.get('LIVEKIT_API_SECRET')
    
    if not livekit_api_key or not livekit_api_secret:
        return None
    
    # Create JWT payload for API access
    now = int(time.time())
    exp = now + 600  # 10 minutes expiry
    
    payload = {
        "iss": livekit_api_key,
        "exp": exp,
        "nbf": now,
        "iat": now,
        "video": {
            "roomCreate": True,
            "roomList": True,
            "roomRecord": True,
            "roomAdmin": True,
            "ingressAdmin": True,
            "egressAdmin": True
        }
    }
    
    # Generate JWT token
    token = jwt.encode(
        payload,
        livekit_api_secret,
        algorithm='HS256'
    )
    
    # Handle both string and bytes return
    if isinstance(token, bytes):
        token = token.decode('utf-8')
    
    return token

def handle_subscription_created(subscription):
    """Handle new subscription creation - updated for lifetime"""
    try:
        print(f"🆕 New subscription created: {subscription['id']}")
        
        # Find user by customer ID
        user = User.query.filter_by(stripe_customer_id=subscription['customer']).first()
        if not user:
            print(f"⚠️ User not found for customer {subscription['customer']}")
            return
        
        # Update user subscription info
        user.stripe_subscription_id = subscription['id']
        user.subscription_status = subscription['status']
        user.has_subscription = subscription['status'] in ['active', 'trialing']
        user.subscription_current_period_start = datetime.fromtimestamp(subscription['current_period_start'])
        user.subscription_current_period_end = datetime.fromtimestamp(subscription['current_period_end'])
        user.subscription_cancel_at_period_end = subscription.get('cancel_at_period_end', False)
        
        # Determine plan type
        if subscription['items']['data']:
            price_id = subscription['items']['data'][0]['price']['id']
            price_ids = initialize_stripe_price_ids()
            
            for plan_name, plan_price_id in price_ids.items():
                if price_id == plan_price_id:
                    user.subscription_plan = plan_name
                    break
            
            user.subscription_price_id = price_id
            
            # For lifetime subscriptions, set special expiration
            if user.subscription_plan == 'lifetime':
                # Set expiration to 100 years from now (effectively never expires)
                user.subscription_expires = datetime.utcnow() + timedelta(days=36500)
                print(f"🏆 Lifetime subscription activated for {user.username}")
            else:
                user.subscription_expires = user.subscription_current_period_end
        
        db.session.commit()
        
        # Create welcome notification with plan-specific message
        if user.subscription_plan == 'lifetime':
            create_notification(
                user.id,
                'Welcome to Lifetime Access! 🏆',
                'Congratulations! You now have lifetime access to all TGFX Trade Lab content, including all future releases. Welcome to the VIP community!',
                'subscription'
            )
        else:
            create_notification(
                user.id,
                'Welcome to Premium!',
                f'Your {user.subscription_plan} subscription is now active. Enjoy exclusive access to all premium content!',
                'subscription'
            )
        
        # Create activity log
        create_user_activity(
            user.id,
            'subscription_activated',
            f'Premium subscription activated ({user.subscription_plan})'
        )
        
        print(f"✅ User {user.username} subscription activated: {user.subscription_plan}")
        
    except Exception as e:
        print(f"❌ Error handling subscription created: {e}")
        db.session.rollback()

def handle_payment_succeeded(invoice):
    """Handle successful payments - updated for lifetime"""
    try:
        print(f"💰 Payment succeeded: {invoice['id']}")
        
        # Find user by customer ID
        user = User.query.filter_by(stripe_customer_id=invoice['customer']).first()
        if not user:
            print(f"⚠️ User not found for customer {invoice['customer']}")
            return
        
        payment_amount = invoice['amount_paid'] / 100  # Convert cents to dollars
        
        # Update user payment info
        user.last_payment_date = datetime.fromtimestamp(invoice['created'])
        user.last_payment_amount = payment_amount
        user.total_revenue = (user.total_revenue or 0) + payment_amount
        
        db.session.commit()
        
        # Create notification for significant payments with plan-specific messaging
        if payment_amount >= 20:  # Only for subscription payments, not small fees
            if payment_amount >= 400:  # Likely lifetime payment
                create_notification(
                    user.id,
                    'Lifetime Access Confirmed! 🎉',
                    f'Your lifetime payment of ${payment_amount:.2f} has been processed. You now have permanent access to all TGFX Trade Lab content!',
                    'payment'
                )
            else:
                create_notification(
                    user.id,
                    'Payment Received',
                    f'Thank you! Your payment of ${payment_amount:.2f} has been processed successfully.',
                    'payment'
                )
        
        print(f"✅ Payment processed for {user.username}: ${payment_amount:.2f}")
        
    except Exception as e:
        print(f"❌ Error handling payment succeeded: {e}")
        db.session.rollback()

@app.route('/payment-methods')
@login_required
def payment_methods():
    """Payment methods management page"""
    return render_template('payment_methods.html')

@app.route('/admin/subscriptions')
@login_required
def admin_subscriptions():
    """Admin subscriptions management"""
    if not current_user.is_admin:
        flash('Access denied', 'error')
        return redirect(url_for('dashboard'))
    
    # Get all users with subscriptions
    users_with_subs = User.query.filter(User.has_subscription == True).all()
    
    # Get subscription stats
    total_subscribers = len(users_with_subs)
    active_subs = User.query.filter(User.subscription_status == 'active').count()
    past_due_subs = User.query.filter(User.subscription_status == 'past_due').count()
    canceled_subs = User.query.filter(User.subscription_status == 'canceled').count()
    
    return render_template('admin/subscriptions.html',
                         users_with_subs=users_with_subs,
                         total_subscribers=total_subscribers,
                         active_subs=active_subs,
                         past_due_subs=past_due_subs,
                         canceled_subs=canceled_subs)

@app.route('/admin/payment-issues')
@login_required
def admin_payment_issues():
    """Admin payment issues management"""
    if not current_user.is_admin:
        flash('Access denied', 'error')
        return redirect(url_for('dashboard'))
    
    # Get users with payment issues
    users_with_issues = User.query.filter(
        User.subscription_status.in_(['past_due', 'unpaid', 'incomplete'])
    ).all()
    
    return render_template('admin/payment_issues.html',
                         users_with_issues=users_with_issues)

@app.route('/api/user/upgrade-to-lifetime', methods=['POST'])
@login_required
def api_upgrade_to_lifetime():
    """Upgrade user to lifetime plan"""
    try:
        data = request.get_json()
        coupon_code = data.get('coupon_code')  # Optional coupon
        
        if current_user.subscription_plan == 'lifetime':
            return jsonify({'error': 'Already on lifetime plan'}), 400
        
        price_ids = initialize_stripe_price_ids()
        lifetime_price_id = price_ids.get('lifetime')
        
        if not lifetime_price_id:
            return jsonify({'error': 'Lifetime plan not configured'}), 500
        
        # Create or get Stripe customer
        if current_user.stripe_customer_id:
            customer_id = current_user.stripe_customer_id
        else:
            customer = stripe.Customer.create(
                email=current_user.email,
                name=current_user.username,
                metadata={'user_id': current_user.id}
            )
            current_user.stripe_customer_id = customer.id
            db.session.commit()
            customer_id = customer.id
        
        existing_subscription_id = current_user.stripe_subscription_id
        
        # Prepare checkout parameters
        checkout_params = {
            'customer': customer_id,
            'payment_method_types': ['card'],
            'line_items': [{
                'price': lifetime_price_id,
                'quantity': 1,
            }],
            'mode': 'payment',  # One-time payment
            'success_url': f"{request.host_url}subscription-success?session_id={{CHECKOUT_SESSION_ID}}&plan=lifetime",
            'cancel_url': f"{request.host_url}manage-subscription?upgrade_canceled=true",
            'metadata': {
                'user_id': current_user.id,
                'plan_type': 'lifetime',
                'cancel_subscription_id': existing_subscription_id if existing_subscription_id else ''
            },
            'allow_promotion_codes': True,  # Enable promotion codes
        }
        
        # Apply specific coupon if provided
        if coupon_code:
            try:
                coupon = stripe.Coupon.retrieve(coupon_code)
                if coupon.valid:
                    checkout_params['discounts'] = [{
                        'coupon': coupon_code
                    }]
                else:
                    return jsonify({'error': 'Invalid or expired coupon code'}), 400
            except stripe.error.InvalidRequestError:
                return jsonify({'error': 'Invalid coupon code'}), 400
        
        # Create checkout session
        session = stripe.checkout.Session.create(**checkout_params)
        
        return jsonify({
            'success': True,
            'checkout_url': session.url,
            'message': 'Redirecting to lifetime upgrade checkout'
        })
        
    except stripe.error.StripeError as e:
        return jsonify({'error': f'Stripe error: {str(e)}'}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/manage-subscription')
@login_required
def manage_subscription():
    """User subscription management page"""
    return render_template('manage_subscription.html')

@app.route('/api/user/billing-history')
@login_required
def api_get_user_billing_history():
    """Get current user's billing history"""
    try:
        invoices = []
        
        if current_user.stripe_customer_id:
            # Get invoices from Stripe
            stripe_invoices = stripe.Invoice.list(
                customer=current_user.stripe_customer_id,
                limit=20
            )
            
            for invoice in stripe_invoices.data:
                invoices.append({
                    'id': invoice.id,
                    'date': datetime.fromtimestamp(invoice.created).strftime('%m/%d/%Y'),
                    'amount': f"{invoice.amount_paid / 100:.2f}",
                    'status': invoice.status,
                    'description': invoice.description or f"Subscription - {invoice.lines.data[0].price.nickname if invoice.lines.data else 'Premium'}",
                    'pdf_url': invoice.invoice_pdf if invoice.status == 'paid' else None
                })
        
        return jsonify({
            'success': True,
            'invoices': invoices
        })
        
    except stripe.error.StripeError as e:
        return jsonify({'error': f'Stripe error: {str(e)}'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Trading Stats Routes

@app.route('/trading-stats')
@login_required
def trading_stats():
    """Main trading stats viewer page"""
    return render_template('trading_stats/index.html')

@app.route('/trading-stats/signals')
@login_required
def trading_signals_list():
    """List all trading signals with filters"""
    trader_filter = request.args.get('trader')
    pair_filter = request.args.get('pair')
    outcome_filter = request.args.get('outcome')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    query = TradingSignal.query
    
    if trader_filter:
        query = query.filter_by(trader_name=trader_filter)
    
    if pair_filter:
        query = query.filter_by(pair_name=pair_filter)
    
    if outcome_filter:
        query = query.filter_by(outcome=outcome_filter)
    
    if start_date:
        query = query.filter(TradingSignal.date >= datetime.strptime(start_date, '%Y-%m-%d').date())
    
    if end_date:
        query = query.filter(TradingSignal.date <= datetime.strptime(end_date, '%Y-%m-%d').date())
    
    signals = query.order_by(TradingSignal.date.desc(), TradingSignal.created_at.desc()).all()
    
    return render_template('trading_stats/signals_list.html', 
                         signals=signals,
                         trader_filter=trader_filter,
                         pair_filter=pair_filter,
                         outcome_filter=outcome_filter,
                         start_date=start_date,
                         end_date=end_date)

# Admin Routes
@app.route('/admin/trading-signals')
@login_required
def admin_trading_signals():
    """Admin page for managing trading signals"""
    if not current_user.is_admin:
        flash('Access denied', 'error')
        return redirect(url_for('dashboard'))
    
    signals = TradingSignal.query.order_by(TradingSignal.date.desc(), TradingSignal.created_at.desc()).limit(50).all()
    
    return render_template('admin/trading_signals.html', signals=signals)

# COMPLETE DISCORD WEBHOOK INTEGRATION POINTS
# Copy and paste these exact code sections into your existing app.py

# ==============================================================================
# 1. REPLACE YOUR EXISTING admin_add_video ROUTE WITH THIS:
# ==============================================================================

@app.route('/admin/video/add', methods=['GET', 'POST'])
@login_required
def admin_add_video():
    if not current_user.is_admin:
        flash('Access denied', 'error')
        return redirect(url_for('dashboard'))
    
    try:
        form = VideoFormWithTags()
    except NameError:
        form = VideoForm()
    
    form.category_id.choices = [(c.id, c.name) for c in Category.query.all()]
    
    if form.validate_on_submit():
        tags_data = None
        if hasattr(form, 'tags'):
            tags_data = form.tags.data
        
        # Handle integer fields properly
        order_index = form.order_index.data
        if order_index == '' or order_index is None:
            order_index = 0
        else:
            order_index = int(order_index)
        
        # Get thumbnail URL from form (manual override)
        thumbnail_url = form.thumbnail_url.data if form.thumbnail_url.data else None
        
        video = Video(
            title=form.title.data,
            description=form.description.data,
            s3_url=form.s3_url.data,
            thumbnail_url=thumbnail_url,  # Will be updated if auto-generated
            category_id=form.category_id.data,
            is_free=form.is_free.data,
            order_index=order_index
        )
        db.session.add(video)
        db.session.flush()  # Get the video ID
        
        # Auto-generate thumbnail if no manual thumbnail provided
        if not thumbnail_url:
            category = Category.query.get(form.category_id.data)
            if category and category.background_image_url:
                try:
                    # Generate thumbnail
                    thumbnail_image = generate_thumbnail(
                        category.background_image_url,
                        category.name,
                        video.title
                    )
                    
                    # Upload to S3
                    auto_thumbnail_url = upload_thumbnail_to_s3(thumbnail_image, video.id, video.title)
                    
                    if auto_thumbnail_url:
                        video.thumbnail_url = auto_thumbnail_url
                        print(f"✅ Auto-generated thumbnail for video: {video.title}")
                    else:
                        print(f"⚠️ Failed to upload auto-generated thumbnail for video: {video.title}")
                        
                except Exception as e:
                    print(f"❌ Error auto-generating thumbnail: {e}")
        
        # Process tags
        if tags_data is not None:
            try:
                process_video_tags(video, tags_data)
            except:
                pass
        
        db.session.commit()
        
        # 🆕 DISCORD WEBHOOK: Send Discord notification for new video
        try:
            category = Category.query.get(form.category_id.data)
            webhook_success = send_new_video_webhook(video, category)
            if webhook_success:
                print(f"✅ Discord notified about new video: {video.title}")
            else:
                print(f"⚠️ Failed to notify Discord about new video: {video.title}")
        except Exception as e:
            print(f"❌ Discord webhook error for new video: {e}")
            # Don't let webhook failures break the main functionality
        
        # Broadcast notification about new video
        broadcast_notification(
            'New Video Available!',
            f'Check out our latest video: "{video.title}"',
            'new_video'
        )
        
        flash('Video added successfully!', 'success')
        return redirect(url_for('admin_videos'))
    
    return render_template('admin/video_form.html', form=form, title='Add Video')

# ==============================================================================
# 2. REPLACE YOUR EXISTING api_start_stream ROUTE WITH THIS:
# ==============================================================================

@app.route('/api/stream/start', methods=['POST'])
@login_required
def api_start_stream():
    if not current_user.is_admin or not current_user.can_stream:
        return jsonify({'error': 'Access denied - not authorized to stream'}), 403
    
    user_active_stream = Stream.query.filter_by(
        created_by=current_user.id,
        is_active=True
    ).first()
    if user_active_stream:
        return jsonify({'error': 'You already have an active stream'}), 400
    
    active_stream_count = Stream.query.filter_by(is_active=True).count()
    if active_stream_count >= 2:
        return jsonify({'error': 'Maximum concurrent streams reached (2)'}), 400
    
    data = request.get_json()
    title = data.get('title', 'Live Stream')
    description = data.get('description', '')
    stream_type = data.get('stream_type', 'general')
    
    streamer_name = current_user.display_name or current_user.username
    
    if streamer_name not in title:
        title = f"{streamer_name}'s {title}"
    
    # Create LiveKit room name
    room_name = f"stream-{streamer_name.lower()}-{uuid.uuid4().hex[:12]}"
    
    # Create LiveKit room
    room_info = create_livekit_room(room_name, streamer_name)
    if not room_info:
        return jsonify({'error': 'Failed to create LiveKit room'}), 500
    
    # Generate publisher token for the streamer
    participant_identity = f"streamer-{current_user.id}"
    streamer_token = generate_livekit_token(
        room_name,
        participant_identity,
        streamer_name,
        is_publisher=True
    )
    
    if not streamer_token:
        delete_livekit_room(room_name)
        return jsonify({'error': 'Failed to create streamer token'}), 500
    
    # Create database record
    stream = Stream(
        title=title,
        description=description,
        room_name=room_name,
        room_sid=getattr(room_info, 'sid', f"RM_{uuid.uuid4().hex[:12]}"),
        is_active=True,
        is_recording=False,  # Will start when first participant joins
        started_at=datetime.utcnow(),
        created_by=current_user.id,
        streamer_name=streamer_name,
        stream_type=stream_type,
        recording_id=None  # Will be set when recording starts
    )
    
    db.session.add(stream)
    db.session.commit()
    
    # Send Discord notification for live stream start
    try:
        webhook_success = send_live_stream_webhook(stream, action="started")
        if webhook_success:
            print(f"✅ Discord notified about live stream: {stream.title}")
    except Exception as e:
        print(f"❌ Discord webhook error for live stream: {e}")
    
    # Broadcast notification about new stream via WebSocket
    if socketio:
        socketio.emit('new_stream_started', {
            'stream_id': stream.id,
            'title': stream.title,
            'streamer_name': stream.streamer_name,
            'message': f'{streamer_name} is now live!',
            'is_recording': False
        })
    
    # Send traditional notification
    broadcast_notification(
        'Live Stream Started!',
        f'{streamer_name} is now live: "{title}"',
        'live_stream'
    )
    
    return jsonify({
        'success': True,
        'stream': {
            'id': stream.id,
            'title': stream.title,
            'streamer_name': stream.streamer_name,
            'room_name': stream.room_name,
            'room_sid': stream.room_sid,
            'livekit_token': streamer_token,
            'livekit_url': app.config.get('LIVEKIT_URL'),
            'participant_identity': participant_identity,
            'is_recording': False
        }
    })

# ==============================================================================
# 3. UPDATE YOUR EXISTING api_stop_stream ROUTE - ADD DISCORD WEBHOOK:
# ==============================================================================

@app.route('/api/stream/stop', methods=['POST'])
@login_required
def api_stop_stream():
    if not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    data = request.get_json()
    stream_id = data.get('stream_id')
    
    if stream_id:
        stream = Stream.query.filter_by(
            id=stream_id,
            created_by=current_user.id,
            is_active=True
        ).first()
    else:
        stream = Stream.query.filter_by(
            created_by=current_user.id,
            is_active=True
        ).first()
    
    if not stream:
        return jsonify({'error': 'No active stream found or access denied'}), 400
    
    recording_url = None
    recording_saved = False
    video_created = False
    
    # Stop recording if active
    if stream.is_recording and stream.recording_id:
        print(f"🔴 Stopping recording for egress {stream.recording_id}...")
        
        stop_result = stop_livekit_egress_recording(stream.recording_id)
        
        if stop_result.get('success'):
            recording_url = stop_result.get('recording_url')
            print(f"✅ Recording stopped successfully: {recording_url}")
            
            # Verify the recording file exists in S3
            if recording_url:
                print("⏳ Verifying recording file exists in S3...")
                if verify_s3_recording_exists(recording_url, max_wait_time=60):
                    stream.recording_url = recording_url
                    recording_saved = True
                    print("✅ Recording file verified and URL saved")
                else:
                    print("⚠️ Recording file not found in S3, but saving URL anyway")
                    stream.recording_url = recording_url
                    recording_saved = True
        else:
            print(f"❌ Failed to stop recording: {stop_result.get('error')}")
    
    # Calculate stream duration
    duration_minutes = 0
    if stream.started_at:
        stream.ended_at = datetime.utcnow()
        duration = stream.ended_at - stream.started_at
        duration_minutes = int(duration.total_seconds() / 60)
    
    # Send Discord notification BEFORE stopping stream
    try:
        webhook_success = send_live_stream_webhook(stream, action="ended")
        if webhook_success:
            print(f"✅ Discord notified about stream ending: {stream.title}")
    except Exception as e:
        print(f"❌ Discord webhook error for stream ending: {e}")
    
    # AUTO-SYNC TO COURSE LIBRARY with enhanced title format
    if recording_saved and recording_url:
        try:
            # Find or create the Live Trading Sessions category
            live_sessions_category = Category.query.filter_by(
                name='Live Trading Sessions'
            ).first()
            
            if not live_sessions_category:
                live_sessions_category = Category(
                    name='Live Trading Sessions',
                    description='Recorded live trading sessions from our professional traders',
                    order_index=1
                )
                db.session.add(live_sessions_category)
                db.session.flush()
                print("📁 Created Live Trading Sessions category")
            
            # Create enhanced video title: "{Streamer Name} - {MM-DD-YY} {Stream Title}"
            stream_date = stream.started_at.strftime('%m-%d-%y') if stream.started_at else datetime.utcnow().strftime('%m-%d-%y')
            
            # Extract the core title (remove "streamer's" prefix if it exists)
            core_title = stream.title
            if f"{stream.streamer_name}'s " in core_title:
                core_title = core_title.replace(f"{stream.streamer_name}'s ", "")
            
            video_title = f"{stream.streamer_name} - {stream_date} {core_title}"
            
            # Create comprehensive description
            video_description = f"Live trading session with {stream.streamer_name}\n"
            video_description += f"Original stream: {stream.title}\n"
            video_description += f"Duration: {duration_minutes} minutes\n"
            video_description += f"Stream Type: {stream.stream_type.replace('_', ' ').title()}\n"
            video_description += f"Recorded: {stream.started_at.strftime('%B %d, %Y at %I:%M %p') if stream.started_at else 'Unknown'}\n"
            
            if stream.description:
                video_description += f"\nDescription: {stream.description}"
            
            # Get the next order index for this category
            max_order = db.session.query(db.func.max(Video.order_index)).filter_by(
                category_id=live_sessions_category.id
            ).scalar() or 0
            
            # Create the video entry
            new_video = Video(
                title=video_title,
                description=video_description,
                s3_url=recording_url,
                thumbnail_url=None,  # Could auto-generate later
                duration=duration_minutes * 60,  # Store in seconds
                is_free=False,  # Premium content
                order_index=max_order + 1,
                category_id=live_sessions_category.id,
                created_at=stream.started_at or datetime.utcnow()
            )
            db.session.add(new_video)
            db.session.flush()
            
            # Add comprehensive tags
            tags_to_add = [
                stream.streamer_name,
                'Live Session',
                'Live Trading',
                stream.started_at.strftime('%B %Y') if stream.started_at else datetime.utcnow().strftime('%B %Y'),
                stream.stream_type.replace('_', ' ').title()
            ]
            
            for tag_name in tags_to_add:
                tag = get_or_create_tag(tag_name)
                if tag and tag not in new_video.tags:
                    new_video.tags.append(tag)
            
            video_created = True
            print(f"🎥 Created video entry: {video_title}")
            print(f"🆔 Video ID: {new_video.id}")
            
            # Send notification about new recording
            broadcast_notification(
                'New Live Session Recording Available!',
                f"{stream.streamer_name}'s live trading session is now available in the course library.",
                'new_video',
                target_users='all'
            )
            
        except Exception as e:
            print(f"❌ Error creating video entry: {e}")
            db.session.rollback()
            video_created = False
    
    # Notify viewers via WebSocket
    room_id = f"stream_{stream.id}"
    if socketio and room_id in stream_rooms:
        end_message = {
            'stream_id': stream.id,
            'message': f'{stream.streamer_name} has ended the stream',
            'redirect': True
        }
        
        if recording_url:
            end_message['recording_message'] = 'Recording has been saved and will be available in the course library shortly'
        
        socketio.emit('stream_ending', {
            'stream_id': stream.id,
            'message': 'Stream is ending in 3 seconds...'
        }, room=room_id)
        
        time.sleep(1)
        socketio.emit('stream_ended', end_message, room=room_id)
        
        if room_id in stream_rooms:
            del stream_rooms[room_id]
    
    # Clean up LiveKit room
    if stream.room_name:
        delete_livekit_room(stream.room_name)
    
    # Update database
    stream.is_active = False
    stream.is_recording = False
    
    # Update viewer records
    StreamViewer.query.filter_by(stream_id=stream.id, is_active=True).update({
        'is_active': False,
        'left_at': datetime.utcnow()
    })
    
    # Commit all changes
    db.session.commit()
    print(f"✅ Stream {stream.id} ended and synced to course library")
    
    response_data = {
        'success': True,
        'message': f'{stream.streamer_name}\'s stream ended',
        'duration_minutes': duration_minutes,
        'recording': {
            'saved': recording_saved,
            'url': recording_url if recording_saved else None,
            'video_created': video_created,
            'message': f'Recording saved and added to course library' if video_created else 'Recording saved but not added to courses'
        }
    }
    
    return jsonify(response_data)
    
# ==============================================================================
# 4. UPDATE YOUR EXISTING admin_add_trading_signal ROUTE - ADD DISCORD WEBHOOK:
# ==============================================================================

@app.route('/admin/trading-signal/add', methods=['GET', 'POST'])
@login_required
def admin_add_trading_signal():
    """Add new trading signal"""
    if not current_user.is_admin:
        flash('Access denied', 'error')
        return redirect(url_for('dashboard'))
    
    form = TradingSignalForm()
    
    # Populate video choices with live trading sessions
    live_sessions_category = Category.query.filter_by(name='Live Trading Sessions').first()
    if live_sessions_category:
        video_choices = [(0, 'No video linked')] + [
            (v.id, f"{v.title} ({v.created_at.strftime('%m/%d/%Y')})")
            for v in live_sessions_category.videos
        ]
    else:
        video_choices = [(0, 'No videos available')]
    
    form.linked_video_id.choices = video_choices
    
    if form.validate_on_submit():
        try:
            # Get trader defaults
            trader_name, default_pair, default_rr = get_trader_defaults(current_user)
            
            # Override with form data
            trader_name = form.trader_name.data
            
            # Create trading signal
            signal = TradingSignal(
                date=datetime.utcnow().date(),
                day_of_week=calculate_day_of_week(datetime.utcnow().date()),
                trader_name=trader_name,
                pair_name=form.pair_name.data,
                trade_type=form.trade_type.data,
                entry_price=float(form.entry_price.data),
                stop_loss_price=float(form.stop_loss_price.data),
                target_price=float(form.target_price.data),
                risk_reward_ratio=float(form.risk_reward_ratio.data),
                outcome=form.outcome.data,
                actual_rr=float(form.actual_rr.data),
                achieved_rr=float(form.achieved_rr.data) if form.achieved_rr.data else None,
                notes=form.notes.data,
                created_by=current_user.id,
                linked_video_id=form.linked_video_id.data if form.linked_video_id.data != 0 else None
            )
            
            db.session.add(signal)
            db.session.commit()
            
            # Update aggregated stats
            update_trading_stats(signal)
            
            # 🆕 DISCORD WEBHOOK: Send Discord notification for trading signal (NO DETAILS)
            try:
                webhook_success = send_trading_signal_webhook(signal)
                if webhook_success:
                    print(f"✅ Discord notified about new trading signal: {signal.pair_name}")
                else:
                    print(f"⚠️ Failed to notify Discord about trading signal: {signal.pair_name}")
            except Exception as e:
                print(f"❌ Discord webhook error for trading signal: {e}")
            
            flash('Trading signal added successfully!', 'success')
            return redirect(url_for('admin_trading_signals'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding trading signal: {str(e)}', 'error')
    
    return render_template('admin/trading_signal_form.html', form=form, title='Add Trading Signal')

@app.route('/admin/trading-signal/edit/<int:signal_id>', methods=['GET', 'POST'])
@login_required
def admin_edit_trading_signal(signal_id):
    """Edit trading signal"""
    if not current_user.is_admin:
        flash('Access denied', 'error')
        return redirect(url_for('dashboard'))
    
    signal = TradingSignal.query.get_or_404(signal_id)
    form = TradingSignalForm(obj=signal)
    
    # Populate video choices
    live_sessions_category = Category.query.filter_by(name='Live Trading Sessions').first()
    if live_sessions_category:
        video_choices = [(0, 'No video linked')] + [
            (v.id, f"{v.title} ({v.created_at.strftime('%m/%d/%Y')})")
            for v in live_sessions_category.videos
        ]
    else:
        video_choices = [(0, 'No videos available')]
    
    form.linked_video_id.choices = video_choices
    
    if request.method == 'GET':
        # Pre-populate form with existing data
        form.trader_name.data = signal.trader_name
        form.pair_name.data = signal.pair_name
        form.trade_type.data = signal.trade_type
        form.entry_price.data = str(signal.entry_price)
        form.stop_loss_price.data = str(signal.stop_loss_price)
        form.target_price.data = str(signal.target_price)
        form.risk_reward_ratio.data = str(signal.risk_reward_ratio)
        form.outcome.data = signal.outcome
        form.actual_rr.data = str(signal.actual_rr)
        form.achieved_rr.data = str(signal.achieved_rr or '')
        form.notes.data = signal.notes
        form.linked_video_id.data = signal.linked_video_id or 0
    
    if form.validate_on_submit():
        try:
            # Update signal
            signal.trader_name = form.trader_name.data
            signal.pair_name = form.pair_name.data
            signal.trade_type = form.trade_type.data
            signal.entry_price = float(form.entry_price.data)
            signal.stop_loss_price = float(form.stop_loss_price.data)
            signal.target_price = float(form.target_price.data)
            signal.risk_reward_ratio = float(form.risk_reward_ratio.data)
            signal.outcome = form.outcome.data
            signal.actual_rr = float(form.actual_rr.data)
            signal.achieved_rr = float(form.achieved_rr.data) if form.achieved_rr.data else None
            signal.notes = form.notes.data
            signal.linked_video_id = form.linked_video_id.data if form.linked_video_id.data != 0 else None
            
            db.session.commit()
            
            # Update aggregated stats
            update_trading_stats(signal)
            
            flash('Trading signal updated successfully!', 'success')
            return redirect(url_for('admin_trading_signals'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating trading signal: {str(e)}', 'error')
    
    return render_template('admin/trading_signal_form.html', 
                         form=form, 
                         signal=signal, 
                         title='Edit Trading Signal')

@app.route('/admin/trading-signal/<int:signal_id>', methods=['DELETE'])
@login_required
def admin_delete_trading_signal(signal_id):
    """Delete trading signal"""
    if not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    try:
        signal = TradingSignal.query.get_or_404(signal_id)
        trader_name = signal.trader_name
        date = signal.date
        
        db.session.delete(signal)
        db.session.commit()
        
        # Update stats after deletion
        # Recalculate for the affected date
        remaining_signals = TradingSignal.query.filter_by(
            trader_name=trader_name,
            date=date
        ).first()
        
        if remaining_signals:
            update_trading_stats(remaining_signals)
        else:
            # Delete the stats record if no signals remain for this date
            stats = TradingStats.query.filter_by(
                trader_name=trader_name,
                date=date
            ).first()
            if stats:
                db.session.delete(stats)
                db.session.commit()
        
        return jsonify({'success': True})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

# API Routes for Analytics
@app.route('/api/trading-stats/analytics')
@login_required
def api_trading_analytics():
    """Get trading analytics with filters"""
    try:
        trader_name = request.args.get('trader')
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        start_dt = datetime.strptime(start_date, '%Y-%m-%d').date() if start_date else None
        end_dt = datetime.strptime(end_date, '%Y-%m-%d').date() if end_date else None
        
        analytics = get_trading_analytics(trader_name, start_dt, end_dt)
        
        return jsonify({
            'success': True,
            'analytics': analytics
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/trading-stats/compare')
@login_required
def api_compare_traders():
    """Compare performance between Ray and Jordan"""
    try:
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        start_dt = datetime.strptime(start_date, '%Y-%m-%d').date() if start_date else None
        end_dt = datetime.strptime(end_date, '%Y-%m-%d').date() if end_date else None
        
        ray_stats = get_trading_analytics('Ray', start_dt, end_dt)
        jordan_stats = get_trading_analytics('Jordan', start_dt, end_dt)
        
        return jsonify({
            'success': True,
            'ray': ray_stats,
            'jordan': jordan_stats
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/trading-stats/hypothetical', methods=['POST'])
@login_required
def api_hypothetical_analysis():
    """Calculate hypothetical scenarios using achieved_rr data - ENHANCED VERSION"""
    # Check if user has active subscription
    if not current_user.has_active_subscription():
        return jsonify({
            'error': 'Premium subscription required',
            'message': 'Hypothetical analysis is a premium feature. Upgrade to unlock advanced trading analytics.',
            'upgrade_url': url_for('manage_subscription')
        }), 403
    
    try:
        data = request.get_json()
        target_reward = float(data.get('target_reward', 2.0))
        trader_name = data.get('trader')
        start_date = data.get('start_date')
        end_date = data.get('end_date')
        analysis_type = data.get('analysis_type', 'take_profit')  # NEW: Different analysis types
        
        # Get all signals
        query = TradingSignal.query
        
        if trader_name:
            query = query.filter_by(trader_name=trader_name)
        
        if start_date:
            start_dt = datetime.strptime(start_date, '%Y-%m-%d').date()
            query = query.filter(TradingSignal.date >= start_dt)
        
        if end_date:
            end_dt = datetime.strptime(end_date, '%Y-%m-%d').date()
            query = query.filter(TradingSignal.date <= end_dt)
        
        signals = query.all()
        
        # ENHANCED: Different analysis types
        if analysis_type == 'take_profit':
            return calculate_take_profit_analysis(signals, target_reward)
        elif analysis_type == 'trailing_stop':
            return calculate_trailing_stop_analysis(signals, data)
        elif analysis_type == 'partial_profit':
            return calculate_partial_profit_analysis(signals, data)
        else:
            return calculate_take_profit_analysis(signals, target_reward)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def calculate_take_profit_analysis(signals, target_reward):
    """Analyze what would happen if we took profit at a specific R level - FIXED"""
    try:
        hypothetical_wins = 0
        hypothetical_losses = 0
        hypothetical_total_r = 0
        modified_signals = []
        missed_opportunities = 0
        
        for signal in signals:
            # Use actual_rr for backward compatibility
            actual_r = float(getattr(signal, 'actual_rr', getattr(signal, 'achieved_reward', 0)))
            achieved_r = float(getattr(signal, 'achieved_rr', 0)) if hasattr(signal, 'achieved_rr') and signal.achieved_rr else None
            
            # FIXED LOGIC: Only count as hypothetical win if achieved_r reached the target
            if achieved_r is not None and achieved_r >= target_reward:
                # Trade reached our hypothetical take profit level
                hypothetical_outcome = 'Win'
                hypothetical_r = target_reward
                hypothetical_wins += 1
                
                # Calculate missed opportunity for losses that reached target
                if signal.outcome == 'Loss':
                    missed_opportunities += target_reward - actual_r
                    
            else:
                # Trade did NOT reach target - keep original result
                hypothetical_outcome = signal.outcome
                hypothetical_r = actual_r
                
                if signal.outcome == 'Win':
                    hypothetical_wins += 1
                else:
                    hypothetical_losses += 1
            
            hypothetical_total_r += hypothetical_r
            
            modified_signals.append({
                'id': signal.id,
                'date': signal.date.isoformat(),
                'pair': signal.pair_name,
                'trade_type': signal.trade_type,
                'original_outcome': signal.outcome,
                'original_actual_r': float(actual_r),
                'achieved_r': achieved_r,
                'hypothetical_outcome': hypothetical_outcome,
                'hypothetical_r': hypothetical_r,
                'analysis': get_signal_analysis(signal, target_reward, achieved_r, actual_r),
                'reached_target': achieved_r is not None and achieved_r >= target_reward
            })
        
        total_trades = len(signals)
        hypothetical_win_rate = (hypothetical_wins / total_trades * 100) if total_trades > 0 else 0
        
        # Calculate improvement metrics
        original_total_r = sum([float(getattr(s, 'actual_rr', getattr(s, 'achieved_reward', 0))) for s in signals])
        improvement = hypothetical_total_r - original_total_r
        
        return jsonify({
            'success': True,
            'analysis_type': 'take_profit',
            'target_reward': target_reward,
            'total_trades': total_trades,
            'original_performance': {
                'total_r': round(original_total_r, 2),
                'wins': len([s for s in signals if s.outcome == 'Win']),
                'losses': len([s for s in signals if s.outcome == 'Loss'])
            },
            'hypothetical_performance': {
                'total_r': round(hypothetical_total_r, 2),
                'wins': hypothetical_wins,
                'losses': hypothetical_losses,
                'win_rate': round(hypothetical_win_rate, 2),
                'average_r_per_trade': round(hypothetical_total_r / total_trades, 2) if total_trades > 0 else 0
            },
            'improvement_metrics': {
                'r_improvement': round(improvement, 2),
                'percentage_improvement': round((improvement / abs(original_total_r) * 100), 2) if original_total_r != 0 else 0,
                'missed_opportunities': round(missed_opportunities, 2),
                'signals_that_reached_target': len([s for s in signals if hasattr(s, 'achieved_rr') and s.achieved_rr and float(s.achieved_rr) >= target_reward])
            },
            'modified_signals': modified_signals
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def get_signal_analysis(signal, target_reward, achieved_r, actual_r):
    """Generate analysis text for individual signals"""
    if achieved_r is None:
        return "No achieved R data available for analysis"
    
    if achieved_r >= target_reward and signal.outcome == 'Loss':
        return f"Reached {target_reward}R target but reversed to loss. Perfect candidate for take-profit strategy."
    elif achieved_r >= target_reward * 0.8 and signal.outcome == 'Loss':
        return f"Reached {achieved_r}R ({int(achieved_r/target_reward*100)}% of target). Consider partial profits."
    elif achieved_r > 0 and signal.outcome == 'Loss':
        return f"Went {achieved_r}R favorable before reversal. Small profit opportunity missed."
    elif signal.outcome == 'Win' and achieved_r > actual_r:
        return f"Winner that peaked at {achieved_r}R. Could have captured more with trailing stops."
    elif signal.outcome == 'Win':
        return "Successful trade execution to target."
    else:
        return "Trade never moved favorably."

def calculate_trailing_stop_analysis(signals, data):
    """Analyze trailing stop strategies using achieved_rr data"""
    try:
        trailing_percentage = float(data.get('trailing_percentage', 20))  # 20% trailing stop
        
        modified_signals = []
        total_hypothetical_r = 0
        
        for signal in signals:
            actual_r = float(getattr(signal, 'actual_rr', getattr(signal, 'achieved_reward', 0)))
            achieved_r = float(getattr(signal, 'achieved_rr', 0)) if hasattr(signal, 'achieved_rr') and signal.achieved_rr else None
            
            if achieved_r and achieved_r > 0:
                # Calculate where trailing stop would have triggered
                trailing_stop_level = achieved_r * (1 - trailing_percentage / 100)
                
                if signal.outcome == 'Loss':
                    # Assume trailing stop would have saved some profit
                    hypothetical_r = max(trailing_stop_level, 0)
                else:
                    # For wins, trailing stop might have reduced profit slightly
                    hypothetical_r = min(actual_r, trailing_stop_level + 0.5)  # Small buffer
                
                total_hypothetical_r += hypothetical_r
                
                modified_signals.append({
                    'id': signal.id,
                    'original_r': actual_r,
                    'achieved_r': achieved_r,
                    'trailing_stop_level': round(trailing_stop_level, 2),
                    'hypothetical_r': round(hypothetical_r, 2),
                    'improvement': round(hypothetical_r - actual_r, 2)
                })
            else:
                total_hypothetical_r += actual_r
                modified_signals.append({
                    'id': signal.id,
                    'original_r': actual_r,
                    'achieved_r': achieved_r,
                    'trailing_stop_level': 0,
                    'hypothetical_r': actual_r,
                    'improvement': 0
                })
        
        original_total = sum([float(getattr(s, 'actual_rr', getattr(s, 'achieved_reward', 0))) for s in signals])
        
        return jsonify({
            'success': True,
            'analysis_type': 'trailing_stop',
            'trailing_percentage': trailing_percentage,
            'original_total_r': round(original_total, 2),
            'hypothetical_total_r': round(total_hypothetical_r, 2),
            'improvement': round(total_hypothetical_r - original_total, 2),
            'signals': modified_signals
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def calculate_partial_profit_analysis(signals, data):
    """Analyze partial profit taking strategies"""
    try:
        partial_level_1 = float(data.get('partial_level_1', 1.0))  # Take 50% at 1R
        partial_level_2 = float(data.get('partial_level_2', 2.0))  # Take 25% at 2R
        
        modified_signals = []
        total_hypothetical_r = 0
        
        for signal in signals:
            actual_r = float(getattr(signal, 'actual_rr', getattr(signal, 'achieved_reward', 0)))
            achieved_r = float(getattr(signal, 'achieved_rr', 0)) if hasattr(signal, 'achieved_rr') and signal.achieved_rr else None
            
            if achieved_r and achieved_r > 0:
                hypothetical_r = 0
                
                # Partial profit calculations
                if achieved_r >= partial_level_1:
                    hypothetical_r += partial_level_1 * 0.5  # 50% at level 1
                    
                if achieved_r >= partial_level_2:
                    hypothetical_r += partial_level_2 * 0.25  # 25% at level 2
                    
                # Remaining position
                remaining_percentage = 0.25 if achieved_r >= partial_level_2 else 0.5 if achieved_r >= partial_level_1 else 1.0
                
                if signal.outcome == 'Win':
                    hypothetical_r += actual_r * remaining_percentage
                elif signal.outcome == 'Loss':
                    hypothetical_r += -1 * remaining_percentage  # Remaining hits stop loss
                
                modified_signals.append({
                    'id': signal.id,
                    'original_r': actual_r,
                    'achieved_r': achieved_r,
                    'partial_profits': {
                        'level_1': partial_level_1 * 0.5 if achieved_r >= partial_level_1 else 0,
                        'level_2': partial_level_2 * 0.25 if achieved_r >= partial_level_2 else 0,
                        'remaining': (actual_r if signal.outcome == 'Win' else -1) * remaining_percentage
                    },
                    'hypothetical_r': round(hypothetical_r, 2),
                    'improvement': round(hypothetical_r - actual_r, 2)
                })
                
                total_hypothetical_r += hypothetical_r
            else:
                total_hypothetical_r += actual_r
                modified_signals.append({
                    'id': signal.id,
                    'original_r': actual_r,
                    'achieved_r': achieved_r,
                    'hypothetical_r': actual_r,
                    'improvement': 0
                })
        
        original_total = sum([float(getattr(s, 'actual_rr', getattr(s, 'achieved_reward', 0))) for s in signals])
        
        return jsonify({
            'success': True,
            'analysis_type': 'partial_profit',
            'strategy': {
                'level_1': f"50% at {partial_level_1}R",
                'level_2': f"25% at {partial_level_2}R",
                'remaining': "25% to target/stop"
            },
            'original_total_r': round(original_total, 2),
            'hypothetical_total_r': round(total_hypothetical_r, 2),
            'improvement': round(total_hypothetical_r - original_total, 2),
            'signals': modified_signals
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500



@app.route('/api/trading-stats/balance-calculator', methods=['POST'])
@login_required
def api_balance_calculator():
    """Calculate hypothetical balance growth - Premium Feature"""
    # Check if user has active subscription
    if not current_user.has_active_subscription():
        return jsonify({
            'error': 'Premium subscription required',
            'message': 'Balance growth calculator is a premium feature. Upgrade to unlock advanced portfolio analytics.',
            'upgrade_url': url_for('manage_subscription')
        }), 403
    
    try:
        data = request.get_json()
        starting_balance = float(data.get('starting_balance', 10000))
        risk_percentage = float(data.get('risk_percentage', 1.0))
        target_reward = float(data.get('target_reward', 2.0))
        trader_name = data.get('trader')
        start_date = data.get('start_date')
        end_date = data.get('end_date')
        
        # Get signals based on filters
        query = TradingSignal.query
        
        if trader_name:
            query = query.filter_by(trader_name=trader_name)
        
        if start_date:
            start_dt = datetime.strptime(start_date, '%Y-%m-%d').date()
            query = query.filter(TradingSignal.date >= start_dt)
        
        if end_date:
            end_dt = datetime.strptime(end_date, '%Y-%m-%d').date()
            query = query.filter(TradingSignal.date <= end_dt)
        
        signals = query.order_by(TradingSignal.date, TradingSignal.created_at).all()
        
        # Calculate balance progression
        current_balance = starting_balance
        balance_history = [{'date': 'Start', 'balance': current_balance, 'trade_result': 0}]
        
        for signal in signals:
            # Get actual R result
            actual_r = float(getattr(signal, 'actual_rr', getattr(signal, 'achieved_reward', 0)))
            
            # For hypothetical calculation, use target reward if trade was successful enough
            achieved_r = float(getattr(signal, 'achieved_rr', 0)) if hasattr(signal, 'achieved_rr') and signal.achieved_rr else None
            
            # Determine hypothetical outcome
            if achieved_r is not None and achieved_r >= target_reward:
                trade_r = target_reward  # Would have hit target
            elif signal.outcome == 'Win' and actual_r > 0:
                trade_r = min(actual_r, target_reward)  # Keep actual win if less than target
            else:
                trade_r = actual_r  # Keep actual result (loss or breakeven)
            
            # Calculate trade result in dollars
            risk_amount = current_balance * (risk_percentage / 100)
            trade_result = risk_amount * trade_r
            current_balance += trade_result
            
            balance_history.append({
                'date': signal.date.isoformat(),
                'balance': round(current_balance, 2),
                'trade_result': round(trade_result, 2),
                'trade_r': trade_r,
                'pair': signal.pair_name,
                'outcome': 'Win' if trade_r > 0 else 'Loss' if trade_r < 0 else 'Breakeven'
            })
        
        total_return = current_balance - starting_balance
        return_percentage = (total_return / starting_balance * 100) if starting_balance > 0 else 0
        
        return jsonify({
            'success': True,
            'starting_balance': starting_balance,
            'ending_balance': round(current_balance, 2),
            'total_return': round(total_return, 2),
            'return_percentage': round(return_percentage, 2),
            'risk_percentage': risk_percentage,
            'target_reward': target_reward,
            'balance_history': balance_history
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/trading-stats/trader-defaults')
@login_required
def api_get_trader_defaults():
    """Get default settings for current user"""
    try:
        trader_name, default_pair, default_rr = get_trader_defaults(current_user)
        
        return jsonify({
            'success': True,
            'trader_name': trader_name,
            'default_pair': default_pair,
            'default_risk_reward': default_rr
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/trading-stats/signals')
@login_required
def api_get_trading_signals():
    """Get trading signals with pagination and filters"""
    try:
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 20))
        trader_filter = request.args.get('trader')
        pair_filter = request.args.get('pair')
        outcome_filter = request.args.get('outcome')
        
        query = TradingSignal.query
        
        if trader_filter:
            query = query.filter_by(trader_name=trader_filter)
        
        if pair_filter:
            query = query.filter_by(pair_name=pair_filter)
        
        if outcome_filter:
            query = query.filter_by(outcome=outcome_filter)
        
        pagination = query.order_by(TradingSignal.date.desc(), TradingSignal.created_at.desc()).paginate(
            page=page, per_page=per_page, error_out=False
        )
        
        signals_data = [signal.to_dict() for signal in pagination.items]
        
        return jsonify({
            'success': True,
            'signals': signals_data,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': pagination.total,
                'pages': pagination.pages,
                'has_next': pagination.has_next,
                'has_prev': pagination.has_prev
            }
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/admin/user/<int:user_id>')
@login_required
def admin_user_details(user_id):
    """View user details in admin panel"""
    if not current_user.is_admin:
        flash('Access denied', 'error')
        return redirect(url_for('dashboard'))
    
    user = User.query.get_or_404(user_id)
    
    # Get user's subscription events if available
    try:
        subscription_events = SubscriptionEvent.query.filter_by(user_id=user_id).order_by(SubscriptionEvent.created_at.desc()).limit(10).all()
    except:
        subscription_events = []
    
    # Get user's progress
    progress_count = UserProgress.query.filter_by(user_id=user_id).count()
    completed_count = UserProgress.query.filter_by(user_id=user_id, completed=True).count()
    
    # Get user's favorites
    favorites_count = UserFavorite.query.filter_by(user_id=user_id).count()
    
    return render_template('admin/user_details.html', 
                         user=user,
                         subscription_events=subscription_events,
                         progress_count=progress_count,
                         completed_count=completed_count,
                         favorites_count=favorites_count)

@app.route('/admin/user/<int:user_id>/edit', methods=['GET', 'POST'])
@login_required
def admin_edit_user(user_id):
    """Edit user in admin panel"""
    if not current_user.is_admin:
        flash('Access denied', 'error')
        return redirect(url_for('dashboard'))
    
    user = User.query.get_or_404(user_id)
    
    if request.method == 'POST':
        try:
            data = request.get_json()
            
            # Update basic user info
            if 'username' in data:
                # Check if username is already taken by another user
                existing_user = User.query.filter_by(username=data['username']).first()
                if existing_user and existing_user.id != user_id:
                    return jsonify({'error': 'Username already exists'}), 400
                user.username = data['username']
            
            if 'email' in data:
                # Check if email is already taken by another user
                existing_user = User.query.filter_by(email=data['email']).first()
                if existing_user and existing_user.id != user_id:
                    return jsonify({'error': 'Email already exists'}), 400
                user.email = data['email']
            
            if 'is_admin' in data:
                user.is_admin = data['is_admin']
            
            if 'can_stream' in data:
                user.can_stream = data['can_stream']
            
            if 'display_name' in data:
                user.display_name = data['display_name']
            
            if 'timezone' in data:
                user.timezone = data['timezone']
            
            # Update subscription info
            if 'has_subscription' in data:
                user.has_subscription = data['has_subscription']
            
            if 'subscription_plan' in data:
                user.subscription_plan = data['subscription_plan']
            
            if 'subscription_status' in data:
                user.subscription_status = data['subscription_status']
            
            db.session.commit()
            
            return jsonify({
                'success': True,
                'message': 'User updated successfully'
            })
            
        except Exception as e:
            db.session.rollback()
            return jsonify({'error': str(e)}), 500
    
    # GET request - return user data for editing
    return jsonify({
        'user': {
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'is_admin': user.is_admin,
            'can_stream': user.can_stream,
            'display_name': user.display_name,
            'timezone': user.timezone,
            'has_subscription': user.has_subscription,
            'subscription_plan': user.subscription_plan,
            'subscription_status': user.subscription_status,
            'created_at': user.created_at.isoformat(),
            'total_revenue': float(user.total_revenue or 0)
        }
    })


@app.route('/api/subscription/timeline', methods=['GET'])
@login_required
def api_get_subscription_timeline():
    """Get subscription timeline/history for current user"""
    try:
        # Get subscription events
        events = SubscriptionEvent.query.filter_by(user_id=current_user.id)\
                                       .order_by(SubscriptionEvent.created_at.desc())\
                                       .limit(20).all()
        
        timeline = []
        for event in events:
            event_data = json.loads(event.event_data) if event.event_data else {}
            
            # Map event types to user-friendly descriptions
            event_descriptions = {
                'customer.subscription.created': 'Subscription started',
                'customer.subscription.updated': 'Subscription updated',
                'customer.subscription.deleted': 'Subscription canceled',
                'invoice.payment_succeeded': 'Payment successful',
                'invoice.payment_failed': 'Payment failed',
                'subscription_upgraded_to_annual': 'Upgraded to annual plan',
                'subscription_upgraded_to_lifetime': 'Upgraded to lifetime access',
                'subscription_canceled_by_user': 'Subscription canceled',
                'subscription_reactivated_by_user': 'Subscription reactivated'
            }
            
            timeline.append({
                'id': event.id,
                'type': event.event_type,
                'description': event_descriptions.get(event.event_type, event.event_type.replace('_', ' ').title()),
                'amount': float(event.amount) if event.amount else None,
                'currency': event.currency,
                'date': event.created_at.isoformat(),
                'processed': event.processed
            })
        
        return jsonify({
            'success': True,
            'timeline': timeline
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/subscription/cancel', methods=['POST'])
@login_required
def api_cancel_subscription():
    """Cancel current user's subscription"""
    try:
        data = request.get_json() or {}
        reason = data.get('reason', '')
        feedback = data.get('feedback', '')
        immediate = data.get('immediate', False)
        
        if not current_user.stripe_subscription_id:
            return jsonify({'error': 'No active subscription found'}), 400
        
        if immediate:
            # Cancel immediately
            subscription = stripe.Subscription.delete(current_user.stripe_subscription_id)
            current_user.has_subscription = False
            current_user.subscription_status = 'canceled'
        else:
            # Cancel at period end
            subscription = stripe.Subscription.modify(
                current_user.stripe_subscription_id,
                cancel_at_period_end=True,
                cancellation_details={
                    'comment': f"Reason: {reason}. Feedback: {feedback}"
                }
            )
            current_user.subscription_cancel_at_period_end = True
        
        db.session.commit()
        
        # Log the cancellation
        event = SubscriptionEvent(
            user_id=current_user.id,
            stripe_event_id=f"cancel_{uuid.uuid4().hex[:8]}",
            event_type='subscription_canceled_by_user',
            event_data=json.dumps({
                'reason': reason,
                'feedback': feedback,
                'immediate': immediate,
                'canceled_at': datetime.utcnow().isoformat()
            }),
            processed=True
        )
        db.session.add(event)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Subscription canceled successfully' if immediate else 'Subscription will cancel at the end of your billing period'
        })
        
    except stripe.error.StripeError as e:
        return jsonify({'error': f'Stripe error: {str(e)}'}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/subscription/reactivate', methods=['POST'])
@login_required
def api_reactivate_subscription():
    """Reactivate a canceled subscription"""
    try:
        if not current_user.stripe_subscription_id:
            return jsonify({'error': 'No subscription found'}), 400
        
        if not current_user.subscription_cancel_at_period_end:
            return jsonify({'error': 'Subscription is not canceled'}), 400
        
        # Reactivate in Stripe
        subscription = stripe.Subscription.modify(
            current_user.stripe_subscription_id,
            cancel_at_period_end=False
        )
        
        current_user.subscription_cancel_at_period_end = False
        db.session.commit()
        
        # Log reactivation
        event = SubscriptionEvent(
            user_id=current_user.id,
            stripe_event_id=f"reactivate_{uuid.uuid4().hex[:8]}",
            event_type='subscription_reactivated_by_user',
            event_data=json.dumps({
                'reactivated_at': datetime.utcnow().isoformat()
            }),
            processed=True
        )
        db.session.add(event)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Subscription reactivated successfully'
        })
        
    except stripe.error.StripeError as e:
        return jsonify({'error': f'Stripe error: {str(e)}'}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/subscription/billing-history', methods=['GET'])
@login_required
def api_get_billing_history():
    """Get user's billing history from Stripe"""
    try:
        if not current_user.stripe_customer_id:
            return jsonify({'success': True, 'invoices': []})
        
        # Get invoices from Stripe
        invoices = stripe.Invoice.list(
            customer=current_user.stripe_customer_id,
            limit=50
        )
        
        billing_history = []
        for invoice in invoices.data:
            billing_history.append({
                'id': invoice.id,
                'date': datetime.fromtimestamp(invoice.created).isoformat(),
                'amount': invoice.amount_paid / 100,
                'currency': invoice.currency.upper(),
                'status': invoice.status,
                'description': invoice.description or get_invoice_description(invoice),
                'pdf_url': invoice.invoice_pdf if invoice.status == 'paid' else None,
                'hosted_url': invoice.hosted_invoice_url
            })
        
        return jsonify({
            'success': True,
            'invoices': billing_history
        })
        
    except stripe.error.StripeError as e:
        return jsonify({'error': f'Stripe error: {str(e)}'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def get_invoice_description(invoice):
    """Generate a user-friendly description for an invoice"""
    if invoice.lines.data:
        line_item = invoice.lines.data[0]
        if line_item.price:
            interval = line_item.price.recurring.interval if line_item.price.recurring else None
            if interval == 'month':
                return 'Monthly Subscription'
            elif interval == 'year':
                return 'Annual Subscription'
            else:
                return 'Lifetime Access'
    return 'TGFX Trade Lab Subscription'


@app.route('/api/admin/analytics/dashboard', methods=['GET'])
@login_required
def api_admin_analytics_dashboard():
    """Get comprehensive analytics data for admin dashboard"""
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403
    
    try:
        period = request.args.get('period', '30')
        end_date = datetime.utcnow()
        
        if period == 'all':
            start_date = User.query.order_by(User.created_at).first().created_at
        else:
            days = int(period)
            start_date = end_date - timedelta(days=days)
        
        # Calculate metrics
        metrics = calculate_enhanced_analytics(start_date, end_date)
        
        # Get chart data
        charts = generate_enhanced_chart_data(start_date, end_date)
        
        # Get recent events
        recent_events = get_recent_revenue_events()
        
        # Get top customers
        top_customers = get_top_customers_by_revenue()
        
        return jsonify({
            'success': True,
            'period': period,
            'date_range': {
                'start': start_date.isoformat(),
                'end': end_date.isoformat()
            },
            'metrics': metrics,
            'charts': charts,
            'recent_events': recent_events,
            'top_customers': top_customers
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def calculate_enhanced_analytics(start_date, end_date):
    """Calculate enhanced analytics metrics"""
    try:
        # Current period metrics
        current_users = User.query.filter(User.created_at.between(start_date, end_date)).count()
        current_subscribers = User.query.filter(
            and_(User.has_subscription == True, User.created_at.between(start_date, end_date))
        ).count()
        current_revenue = db.session.query(func.sum(User.total_revenue)).filter(
            User.created_at.between(start_date, end_date)
        ).scalar() or 0
        
        # Total metrics
        total_users = User.query.count()
        total_subscribers = User.query.filter_by(has_subscription=True).count()
        total_revenue = db.session.query(func.sum(User.total_revenue)).scalar() or 0
        
        # Plan distribution
        monthly_subs = User.query.filter_by(subscription_plan='monthly', has_subscription=True).count()
        annual_subs = User.query.filter_by(subscription_plan='annual', has_subscription=True).count()
        lifetime_subs = User.query.filter_by(subscription_plan='lifetime', has_subscription=True).count()
        
        # Calculate MRR (Monthly Recurring Revenue)
        monthly_mrr = monthly_subs * 29
        annual_mrr = annual_subs * (299 / 12)
        total_mrr = monthly_mrr + annual_mrr
        
        # Calculate churn rate (simplified)
        canceled_subs = User.query.filter_by(subscription_status='canceled').count()
        churn_rate = (canceled_subs / max(total_subscribers, 1)) * 100 if total_subscribers > 0 else 0
        
        # Calculate ARPU
        arpu = (total_revenue / max(total_users, 1)) if total_users > 0 else 0
        
        # Previous period for comparison (simplified)
        prev_period_days = (end_date - start_date).days
        prev_start = start_date - timedelta(days=prev_period_days)
        prev_end = start_date
        
        prev_revenue = db.session.query(func.sum(User.total_revenue)).filter(
            User.created_at.between(prev_start, prev_end)
        ).scalar() or 0
        
        prev_subscribers = User.query.filter(
            and_(User.has_subscription == True, User.created_at.between(prev_start, prev_end))
        ).count()
        
        # Calculate changes
        revenue_change = ((current_revenue - prev_revenue) / max(prev_revenue, 1)) * 100 if prev_revenue > 0 else 0
        subscriber_change = ((current_subscribers - prev_subscribers) / max(prev_subscribers, 1)) * 100 if prev_subscribers > 0 else 0
        
        return {
            'total_revenue': float(total_revenue),
            'revenue_change': round(revenue_change, 1),
            'mrr': float(total_mrr),
            'mrr_change': 8.3,  # You can calculate this properly
            'new_subscribers': current_subscribers,
            'subscribers_change': round(subscriber_change, 1),
            'churn_rate': round(churn_rate, 1),
            'churn_change': -2.1,  # You can calculate this properly
            'lifetime_subscribers': lifetime_subs,
            'total_users': total_users,
            'total_subscribers': total_subscribers,
            'arpu': round(float(arpu), 2),
            'plan_distribution': {
                'monthly': monthly_subs,
                'annual': annual_subs,
                'lifetime': lifetime_subs
            }
        }
        
    except Exception as e:
        print(f"Error calculating analytics: {e}")
        return {}

def generate_enhanced_chart_data(start_date, end_date):
    """Generate enhanced chart data"""
    try:
        # Generate daily revenue data
        days = (end_date - start_date).days
        daily_revenue = []
        cumulative_revenue = []
        labels = []
        
        running_total = 0
        for i in range(days):
            current_day = start_date + timedelta(days=i)
            # Simulate daily revenue (in real app, query from database)
            day_revenue = 150 + (i * 5) + (i % 7) * 50  # Mock data
            daily_revenue.append(day_revenue)
            running_total += day_revenue
            cumulative_revenue.append(running_total)
            labels.append(current_day.strftime('%m/%d'))
        
        # Subscription trends
        subscriber_data = [50 + i * 2 for i in range(days)]
        churn_data = [5 - (i * 0.1) for i in range(days)]
        
        return {
            'revenue': {
                'labels': labels,
                'daily': daily_revenue,
                'cumulative': cumulative_revenue
            },
            'subscribers': {
                'labels': labels,
                'new_subscribers': subscriber_data,
                'churned_subscribers': churn_data
            },
            'plans': {
                'labels': ['Monthly', 'Annual', 'Lifetime'],
                'data': [45, 35, 20],
                'colors': ['#10b981', '#3b82f6', '#FFD700']
            }
        }
        
    except Exception as e:
        print(f"Error generating chart data: {e}")
        return {}

def get_recent_revenue_events():
    """Get recent revenue events for timeline"""
    try:
        # Get recent subscription events
        events = SubscriptionEvent.query.filter(
            SubscriptionEvent.event_type.in_([
                'invoice.payment_succeeded',
                'customer.subscription.created',
                'customer.subscription.deleted',
                'subscription_upgraded_to_lifetime'
            ])
        ).order_by(SubscriptionEvent.created_at.desc()).limit(10).all()
        
        timeline = []
        for event in events:
            user = event.user
            if not user:
                continue
                
            event_info = {
                'id': event.id,
                'type': event.event_type,
                'customer_email': user.email,
                'customer_name': user.username,
                'amount': float(event.amount) if event.amount else 0,
                'date': event.created_at.isoformat(),
                'icon': 'payment' if 'payment' in event.event_type else 'workspace_premium'
            }
            
            # Customize description based on event type
            if 'payment_succeeded' in event.event_type:
                if event.amount and event.amount >= 499:
                    event_info['description'] = 'Lifetime Upgrade'
                    event_info['icon'] = 'workspace_premium'
                elif event.amount and event.amount >= 299:
                    event_info['description'] = 'Annual Subscription Renewal'
                else:
                    event_info['description'] = 'Monthly Subscription'
            elif 'subscription.created' in event.event_type:
                event_info['description'] = 'New Subscription'
            elif 'subscription.deleted' in event.event_type:
                event_info['description'] = 'Subscription Canceled'
                event_info['icon'] = 'cancel'
            
            timeline.append(event_info)
        
        return timeline
        
    except Exception as e:
        print(f"Error getting recent events: {e}")
        return []

def get_top_customers_by_revenue():
    """Get top customers by total revenue"""
    try:
        top_customers = User.query.filter(User.total_revenue > 0)\
                                 .order_by(User.total_revenue.desc())\
                                 .limit(10).all()
        
        customers = []
        for user in top_customers:
            customers.append({
                'id': user.id,
                'name': user.username,
                'email': user.email,
                'total_revenue': float(user.total_revenue or 0),
                'subscription_plan': user.subscription_plan,
                'subscription_status': user.subscription_status,
                'created_at': user.created_at.isoformat(),
                'avatar_color': f"#{hash(user.username) % 16777215:06x}"  # Generate color from username
            })
        
        return customers
        
    except Exception as e:
        print(f"Error getting top customers: {e}")
        return []


# Webhook enhancement for better event handling
@app.route('/webhook/stripe/enhanced', methods=['POST'])
def stripe_webhook_enhanced():
    """Enhanced Stripe webhook handler with better event processing"""
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get('Stripe-Signature')
    endpoint_secret = app.config.get('STRIPE_WEBHOOK_SECRET')
    
    if not endpoint_secret:
        return jsonify({'error': 'Webhook secret not configured'}), 400
    
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)
    except Exception as e:
        return jsonify({'error': f'Webhook error: {str(e)}'}), 400
    
    try:
        # Enhanced event handling
        event_type = event['type']
        event_data = event['data']['object']
        
        # Create or update subscription event record
        subscription_event = SubscriptionEvent(
            stripe_event_id=event['id'],
            event_type=event_type,
            event_data=json.dumps(event_data),
            processed=False
        )
        
        # Process the event
        if event_type == 'customer.subscription.created':
            handle_subscription_created_enhanced(event_data, subscription_event)
        elif event_type == 'customer.subscription.updated':
            handle_subscription_updated_enhanced(event_data, subscription_event)
        elif event_type == 'customer.subscription.deleted':
            handle_subscription_deleted_enhanced(event_data, subscription_event)
        elif event_type == 'invoice.payment_succeeded':
            handle_payment_succeeded_enhanced(event_data, subscription_event)
        elif event_type == 'invoice.payment_failed':
            handle_payment_failed_enhanced(event_data, subscription_event)
        
        subscription_event.processed = True
        db.session.add(subscription_event)
        db.session.commit()
        
        return jsonify({'success': True})
        
    except Exception as e:
        if 'subscription_event' in locals():
            subscription_event.processed = False
            db.session.add(subscription_event)
            db.session.commit()
        
        print(f"Webhook processing error: {e}")
        return jsonify({'error': str(e)}), 500

def handle_subscription_created_enhanced(subscription_data, event_record):
    """Enhanced subscription creation handler - NO Discord notification for subscribers"""
    user = User.query.filter_by(stripe_customer_id=subscription_data['customer']).first()
    if not user:
        return
    
    event_record.user_id = user.id
    
    # Update user with subscription data
    update_user_from_stripe_subscription(user, subscription_data)
    
    # Send in-app welcome notification ONLY (no Discord)
    create_notification(
        user.id,
        'Welcome to Premium!',
        f'Your {user.get_subscription_plan_display()} subscription is now active!',
        'subscription'
    )
    
    # Create in-app activity log ONLY (no Discord)
    create_user_activity(
        user.id,
        'subscription_activated',
        f'Premium subscription activated ({user.subscription_plan})'
    )
    
    print(f"✅ User {user.username} subscription activated: {user.subscription_plan} (no Discord notification)")

def handle_payment_succeeded_enhanced(invoice_data, event_record):
    """Enhanced payment success handler - NO Discord notification for payments"""
    user = User.query.filter_by(stripe_customer_id=invoice_data['customer']).first()
    if not user:
        return
    
    event_record.user_id = user.id
    event_record.amount = Decimal(invoice_data['amount_paid']) / 100
    event_record.currency = invoice_data['currency']
    
    # Update user payment info
    user.last_payment_date = datetime.fromtimestamp(invoice_data['created'])
    user.last_payment_amount = event_record.amount
    user.total_revenue = (user.total_revenue or 0) + event_record.amount
    
    # Check if this is a lifetime payment
    if event_record.amount >= 499:
        user.subscription_plan = 'lifetime'
        user.has_subscription = True
        user.subscription_status = 'active'
        user.subscription_expires = datetime.utcnow() + timedelta(days=36500)  # 100 years
        
        # In-app notification ONLY (no Discord)
        create_notification(
            user.id,
            'Lifetime Access Activated! 🏆',
            'Congratulations! You now have lifetime access to TGFX Trade Lab!',
            'subscription'
        )
    
    db.session.commit()
    print(f"✅ Payment processed for {user.username}: ${event_record.amount:.2f} (no Discord notification)")

def update_user_from_stripe_subscription(user, subscription_data):
    """Update user model from Stripe subscription data"""
    user.stripe_subscription_id = subscription_data['id']
    user.subscription_status = subscription_data['status']
    user.has_subscription = subscription_data['status'] in ['active', 'trialing']
    user.subscription_current_period_start = datetime.fromtimestamp(subscription_data['current_period_start'])
    user.subscription_current_period_end = datetime.fromtimestamp(subscription_data['current_period_end'])
    user.subscription_expires = user.subscription_current_period_end
    user.subscription_cancel_at_period_end = subscription_data.get('cancel_at_period_end', False)
    
    if subscription_data.get('trial_end'):
        user.trial_end = datetime.fromtimestamp(subscription_data['trial_end'])
    
    # Determine plan from price
    if subscription_data.get('items', {}).get('data'):
        price_id = subscription_data['items']['data'][0]['price']['id']
        user.subscription_price_id = price_id
        
        # Map price IDs to plans (update these with your actual price IDs)
        price_plan_mapping = {
            app.config.get('STRIPE_MONTHLY_PRICE_ID'): 'monthly',
            app.config.get('STRIPE_ANNUAL_PRICE_ID'): 'annual',
            app.config.get('STRIPE_LIFETIME_PRICE_ID'): 'lifetime'
        }
        
        user.subscription_plan = price_plan_mapping.get(price_id, 'monthly')


@app.route('/admin/user/<int:user_id>/delete', methods=['POST'])
@login_required
def admin_delete_user(user_id):
    """Delete user (admin only)"""
    if not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    if user_id == current_user.id:
        return jsonify({'error': 'Cannot delete your own account'}), 400
    
    user = User.query.get_or_404(user_id)
    
    try:
        # Delete user (cascade will handle related records)
        db.session.delete(user)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'User {user.username} deleted successfully'
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/admin/user/<int:user_id>/grant-subscription', methods=['POST'])
@login_required
def admin_grant_user_subscription(user_id):
    """Grant subscription to user - updated for lifetime"""
    if not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    try:
        data = request.get_json()
        plan = data.get('plan', 'monthly')
        duration = data.get('duration', 1)
        
        user = User.query.get_or_404(user_id)
        
        # Calculate expiration date
        if plan == 'lifetime':
            # Lifetime never expires (set to 100 years from now)
            expiration_date = datetime.utcnow() + timedelta(days=36500)
        elif plan == 'annual':
            expiration_date = datetime.utcnow() + timedelta(days=365 * duration)
        else:  # monthly
            expiration_date = datetime.utcnow() + timedelta(days=30 * duration)
        
        # Update user subscription manually (admin grant)
        user.has_subscription = True
        user.subscription_status = 'active'
        user.subscription_plan = plan
        user.subscription_expires = expiration_date
        user.subscription_current_period_end = expiration_date
        
        db.session.commit()
        
        # Log the event if SubscriptionEvent model exists
        try:
            event = SubscriptionEvent(
                user_id=user.id,
                event_type='subscription_granted_by_admin',
                event_data=f"Granted {plan} subscription for {duration} {'months' if plan != 'lifetime' else 'lifetime'} by admin {current_user.username}",
                processed=True
            )
            db.session.add(event)
            db.session.commit()
        except:
            pass  # Skip if SubscriptionEvent model doesn't exist
        
        # Send notification to user
        try:
            if plan == 'lifetime':
                create_notification(
                    user.id,
                    'Lifetime Access Granted! 🏆',
                    'You have been granted lifetime access to TGFX Trade Lab! Enjoy permanent access to all content.',
                    'system'
                )
            else:
                create_notification(
                    user.id,
                    'Subscription Granted!',
                    f'You have been granted a {plan} subscription. Enjoy your premium access!',
                    'system'
                )
        except:
            pass  # Skip if notification system not available
        
        return jsonify({'success': True, 'message': f'{plan.title()} subscription granted successfully'})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/admin/discord-notifications')
@login_required
def admin_discord_notifications():
    """Admin page for managing Discord community notifications"""
    if not current_user.is_admin:
        flash('Access denied', 'error')
        return redirect(url_for('dashboard'))
    
    return render_template('admin/discord_notifications.html')

@app.route('/api/admin/webhook-status')
@login_required
def api_webhook_status():
    """Get Discord webhook configuration status"""
    if not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    webhook_url = app.config.get('APP_UPDATE_DISCORD_WEBHOOK_URL')
    
    return jsonify({
        'configured': bool(webhook_url),
        'url_preview': webhook_url[:50] + '***' if webhook_url else None,
        'enabled_notifications': [
            'New Videos',
            'Live Streams', 
            'Trade Signals (announcement only)',
            'Course Completions'
        ],
        'disabled_notifications': [
            'New Subscribers',
            'System Notifications',
            'Trade Signal Details'
        ]
    })



@app.route('/api/user/notifications')
@login_required
def api_get_user_notifications():
    """Get user's notifications"""
    try:
        notifications = Notification.query.filter_by(user_id=current_user.id)\
                                         .order_by(Notification.created_at.desc())\
                                         .limit(20).all()
        
        unread_count = Notification.query.filter_by(
            user_id=current_user.id,
            is_read=False
        ).count()
        
        notifications_data = []
        for notification in notifications:
            notifications_data.append({
                'id': notification.id,
                'title': notification.title,
                'message': notification.message,
                'notification_type': notification.notification_type,
                'is_read': notification.is_read,
                'created_at': notification.created_at.isoformat()
            })
        
        return jsonify({
            'success': True,
            'notifications': notifications_data,
            'unread_count': unread_count
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/user/notifications/<int:notification_id>/read', methods=['POST'])
@login_required
def api_mark_notification_read(notification_id):
    """Mark a notification as read"""
    try:
        notification = Notification.query.filter_by(
            id=notification_id,
            user_id=current_user.id
        ).first()
        
        if not notification:
            return jsonify({'error': 'Notification not found'}), 404
        
        notification.is_read = True
        db.session.commit()
        
        return jsonify({'success': True})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/user/notifications/mark-all-read', methods=['POST'])
@login_required
def api_mark_all_notifications_read():
    """Mark all notifications as read"""
    try:
        Notification.query.filter_by(
            user_id=current_user.id,
            is_read=False
        ).update({'is_read': True})
        
        db.session.commit()
        
        return jsonify({'success': True})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/user/notifications', methods=['DELETE'])
@login_required
def api_clear_all_notifications():
    """Clear all notifications for user"""
    try:
        Notification.query.filter_by(user_id=current_user.id).delete()
        db.session.commit()
        
        return jsonify({'success': True})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500
        

@app.route('/api/user/cancel-subscription', methods=['POST'])
@login_required
def api_cancel_user_subscription():
    """Cancel current user's subscription"""
    try:
        data = request.get_json()
        reason = data.get('reason', '')
        feedback = data.get('feedback', '')
        
        if not current_user.stripe_subscription_id:
            return jsonify({'error': 'No active subscription found'}), 400
        
        # Cancel subscription at period end in Stripe
        subscription = stripe.Subscription.modify(
            current_user.stripe_subscription_id,
            cancel_at_period_end=True,
            cancellation_details={
                'comment': f"Reason: {reason}. Feedback: {feedback}"
            }
        )
        
        # Update user record
        current_user.subscription_cancel_at_period_end = True
        db.session.commit()
        
        # Log the cancellation
        event = SubscriptionEvent(
            user_id=current_user.id,
            stripe_customer_id=current_user.stripe_customer_id,
            stripe_subscription_id=current_user.stripe_subscription_id,
            event_type='subscription_canceled_by_user',
            event_data=json.dumps({
                'reason': reason,
                'feedback': feedback,
                'canceled_at': datetime.utcnow().isoformat()
            }),
            processed=True
        )
        db.session.add(event)
        db.session.commit()
        
        # Send notification
        create_notification(
            current_user.id,
            'Subscription Canceled',
            f'Your subscription has been canceled and will end on {current_user.subscription_expires.strftime("%B %d, %Y") if current_user.subscription_expires else "the end of your billing period"}. You can reactivate anytime before then.',
            'subscription'
        )
        
        return jsonify({
            'success': True,
            'message': 'Subscription canceled successfully. You will retain access until the end of your current billing period.'
        })
        
    except stripe.error.StripeError as e:
        return jsonify({'error': f'Stripe error: {str(e)}'}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/user/reactivate-subscription', methods=['POST'])
@login_required
def api_reactivate_user_subscription():
    """Reactivate a canceled subscription"""
    try:
        if not current_user.stripe_subscription_id:
            return jsonify({'error': 'No subscription found'}), 400
        
        if not current_user.subscription_cancel_at_period_end:
            return jsonify({'error': 'Subscription is not canceled'}), 400
        
        # Reactivate subscription in Stripe
        subscription = stripe.Subscription.modify(
            current_user.stripe_subscription_id,
            cancel_at_period_end=False
        )
        
        # Update user record
        current_user.subscription_cancel_at_period_end = False
        db.session.commit()
        
        # Log the reactivation
        event = SubscriptionEvent(
            user_id=current_user.id,
            stripe_customer_id=current_user.stripe_customer_id,
            stripe_subscription_id=current_user.stripe_subscription_id,
            event_type='subscription_reactivated_by_user',
            event_data=json.dumps({
                'reactivated_at': datetime.utcnow().isoformat()
            }),
            processed=True
        )
        db.session.add(event)
        db.session.commit()
        
        # Send notification
        create_notification(
            current_user.id,
            'Subscription Reactivated',
            'Your subscription has been reactivated and will continue to renew automatically.',
            'subscription'
        )
        
        return jsonify({
            'success': True,
            'message': 'Subscription reactivated successfully'
        })
        
    except stripe.error.StripeError as e:
        return jsonify({'error': f'Stripe error: {str(e)}'}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/user/upgrade-to-annual', methods=['POST'])
@login_required
def api_upgrade_to_annual():
    """Upgrade user from monthly to annual plan"""
    try:
        if not current_user.stripe_subscription_id:
            return jsonify({'error': 'No active subscription found'}), 400
        
        if current_user.subscription_plan == 'annual':
            return jsonify({'error': 'Already on annual plan'}), 400
        
        # Get price IDs
        price_ids = initialize_stripe_price_ids()
        annual_price_id = price_ids.get('annual')
        
        if not annual_price_id:
            return jsonify({'error': 'Annual plan not configured'}), 500
        
        # Update subscription in Stripe
        subscription = stripe.Subscription.retrieve(current_user.stripe_subscription_id)
        
        stripe.Subscription.modify(
            current_user.stripe_subscription_id,
            items=[{
                'id': subscription['items']['data'][0].id,
                'price': annual_price_id,
            }],
            proration_behavior='create_prorations'
        )
        
        # Update user record
        current_user.subscription_plan = 'annual'
        current_user.subscription_price_id = annual_price_id
        db.session.commit()
        
        # Log the upgrade
        event = SubscriptionEvent(
            user_id=current_user.id,
            stripe_customer_id=current_user.stripe_customer_id,
            stripe_subscription_id=current_user.stripe_subscription_id,
            event_type='subscription_upgraded_to_annual',
            event_data=json.dumps({
                'upgraded_at': datetime.utcnow().isoformat(),
                'previous_plan': 'monthly',
                'new_plan': 'annual'
            }),
            processed=True
        )
        db.session.add(event)
        db.session.commit()
        
        # Send notification
        create_notification(
            current_user.id,
            'Upgraded to Annual!',
            'You have successfully upgraded to the annual plan. You\'ll save $183 per year and get exclusive annual benefits!',
            'subscription'
        )
        
        return jsonify({
            'success': True,
            'message': 'Successfully upgraded to annual plan'
        })
        
    except stripe.error.StripeError as e:
        return jsonify({'error': f'Stripe error: {str(e)}'}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/stripe/subscription/<subscription_id>')
@login_required
def api_get_stripe_subscription(subscription_id):
    """Get subscription details from Stripe - Fixed Version"""
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403
    
    try:
        # Validate subscription ID format
        if not subscription_id.startswith('sub_'):
            return jsonify({'error': 'Invalid subscription ID format'}), 400
        
        # Get subscription from Stripe
        subscription = stripe.Subscription.retrieve(subscription_id)
        
        # Get customer info
        customer = stripe.Customer.retrieve(subscription.customer)
        
        # Get subscription items (this is the fix - call the method properly)
        items_data = []
        try:
            # Get items using the proper Stripe method
            items = stripe.SubscriptionItem.list(subscription=subscription_id)
            
            for item in items.data:
                price_info = {
                    'id': item.price.id,
                    'unit_amount': item.price.unit_amount,
                    'currency': item.price.currency,
                    'nickname': getattr(item.price, 'nickname', None),
                }
                
                # Add recurring info if it exists
                if hasattr(item.price, 'recurring') and item.price.recurring:
                    price_info['recurring'] = {
                        'interval': item.price.recurring.interval,
                        'interval_count': item.price.recurring.interval_count
                    }
                
                items_data.append({
                    'id': item.id,
                    'quantity': item.quantity,
                    'price': price_info
                })
        except Exception as items_error:
            print(f"Error getting subscription items: {items_error}")
            items_data = []
        
        # Format subscription data
        subscription_data = {
            'id': subscription.id,
            'status': subscription.status,
            'customer_id': subscription.customer,
            'customer_email': getattr(customer, 'email', None),
            'created': subscription.created,
            'current_period_start': subscription.current_period_start,
            'current_period_end': subscription.current_period_end,
            'cancel_at_period_end': subscription.cancel_at_period_end,
            'trial_end': getattr(subscription, 'trial_end', None),
            'canceled_at': getattr(subscription, 'canceled_at', None),
            'items': {
                'data': items_data
            }
        }
        
        return jsonify({
            'success': True,
            'subscription': subscription_data
        })
        
    except stripe.error.InvalidRequestError:
        return jsonify({'error': 'Subscription not found'}), 404
    except stripe.error.StripeError as e:
        return jsonify({'error': f'Stripe error: {str(e)}'}), 400
    except Exception as e:
        print(f"Error in get_stripe_subscription: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/user/<int:user_id>/link-subscription', methods=['POST'])
@login_required
def api_link_user_subscription(user_id):
    """Link a Stripe subscription to a user - FIXED VERSION"""
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403
    
    try:
        data = request.get_json()
        subscription_id = data.get('subscription_id')
        
        if not subscription_id:
            return jsonify({'error': 'Subscription ID required'}), 400
        
        user = User.query.get_or_404(user_id)
        
        # Get subscription from Stripe to validate and get details
        subscription = stripe.Subscription.retrieve(subscription_id)
        customer = stripe.Customer.retrieve(subscription.customer)
        
        # Update user with subscription data
        user.stripe_customer_id = subscription.customer
        user.stripe_subscription_id = subscription.id
        user.subscription_status = subscription.status
        user.has_subscription = subscription.status in ['active', 'trialing']
        
        # Set subscription dates
        user.subscription_current_period_start = datetime.fromtimestamp(subscription.current_period_start)
        user.subscription_current_period_end = datetime.fromtimestamp(subscription.current_period_end)
        user.subscription_expires = user.subscription_current_period_end
        user.subscription_cancel_at_period_end = subscription.cancel_at_period_end
        
        # Determine plan type from price - FIXED
        try:
            # Get subscription items properly
            items = stripe.SubscriptionItem.list(subscription=subscription_id)
            
            if items.data:
                price_id = items.data[0].price.id
                user.subscription_price_id = price_id
                
                # Map your actual Stripe price IDs here
                price_ids = initialize_stripe_price_ids()
                for plan_name, plan_price_id in price_ids.items():
                    if price_id == plan_price_id:
                        user.subscription_plan = plan_name
                        break
                
                # If no match found, determine by amount or interval
                if not user.subscription_plan:
                    price = items.data[0].price
                    if price.unit_amount == 8000:  # $80.00 in cents
                        user.subscription_plan = 'monthly'
                    elif price.unit_amount == 77700:  # $777.00 in cents
                        user.subscription_plan = 'annual'
                    elif price.unit_amount == 49900:  # $499.00 in cents
                        user.subscription_plan = 'lifetime'
                    else:
                        # Fallback to interval-based detection
                        if hasattr(price, 'recurring') and price.recurring:
                            if price.recurring.interval == 'month':
                                user.subscription_plan = 'monthly'
                            elif price.recurring.interval == 'year':
                                user.subscription_plan = 'annual'
                        else:
                            user.subscription_plan = 'lifetime'  # One-time payment
        except Exception as e:
            print(f"Error determining subscription plan: {e}")
            # Default fallback based on subscription status
            user.subscription_plan = 'monthly'  # Safe default
        
        db.session.commit()
        
        # Log the linking event
        try:
            event = SubscriptionEvent(
                user_id=user.id,
                stripe_customer_id=user.stripe_customer_id,
                stripe_subscription_id=user.stripe_subscription_id,
                event_type='subscription_linked_by_admin',
                event_data=json.dumps({
                    'linked_by': current_user.username,
                    'linked_at': datetime.utcnow().isoformat(),
                    'subscription_status': subscription.status
                }),
                processed=True
            )
            db.session.add(event)
            db.session.commit()
        except Exception as e:
            print(f"Warning: Could not log subscription event: {e}")
            pass  # Don't fail the main operation if logging fails
        
        return jsonify({
            'success': True,
            'message': f'Subscription {subscription_id} linked to user {user.username}',
            'subscription_status': user.subscription_status,
            'subscription_plan': user.subscription_plan
        })
        
    except stripe.error.InvalidRequestError:
        return jsonify({'error': 'Subscription not found in Stripe'}), 404
    except stripe.error.StripeError as e:
        return jsonify({'error': f'Stripe error: {str(e)}'}), 400
    except Exception as e:
        db.session.rollback()
        print(f"Error linking subscription: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/user/<int:user_id>/unlink-subscription', methods=['POST'])
@login_required
def api_unlink_user_subscription(user_id):
    """Unlink Stripe subscription from a user"""
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403
    
    try:
        user = User.query.get_or_404(user_id)
        
        # Store old values for logging
        old_subscription_id = user.stripe_subscription_id
        old_customer_id = user.stripe_customer_id
        
        # Clear subscription data
        user.stripe_customer_id = None
        user.stripe_subscription_id = None
        user.subscription_status = None
        user.subscription_plan = None
        user.subscription_price_id = None
        user.has_subscription = False
        user.subscription_current_period_start = None
        user.subscription_current_period_end = None
        user.subscription_expires = None
        user.subscription_cancel_at_period_end = False
        
        db.session.commit()
        
        # Log the unlinking event
        try:
            event = SubscriptionEvent(
                user_id=user.id,
                stripe_customer_id=old_customer_id,
                stripe_subscription_id=old_subscription_id,
                event_type='subscription_unlinked_by_admin',
                event_data=json.dumps({
                    'unlinked_by': current_user.username,
                    'unlinked_at': datetime.utcnow().isoformat(),
                    'old_subscription_id': old_subscription_id
                }),
                processed=True
            )
            db.session.add(event)
            db.session.commit()
        except:
            pass  # Skip if SubscriptionEvent model doesn't exist
        
        return jsonify({
            'success': True,
            'message': f'Subscription unlinked from user {user.username}'
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/user/<int:user_id>/edit', methods=['GET', 'POST'])
@login_required
def api_admin_edit_user(user_id):
    """Get user data for editing (GET) or save user changes (POST)"""
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403
    
    user = User.query.get_or_404(user_id)
    
    if request.method == 'GET':
        # Return user data for editing
        return jsonify({
            'success': True,
            'user': {
                'id': user.id,
                'username': user.username,
                'email': user.email,
                'display_name': user.display_name,
                'timezone': user.timezone,
                'is_admin': user.is_admin,
                'can_stream': user.can_stream,
                'has_subscription': user.has_subscription,
                'subscription_status': user.subscription_status,
                'subscription_plan': user.subscription_plan,
                'subscription_expires': user.subscription_expires.isoformat() if user.subscription_expires else None,
                'stripe_customer_id': user.stripe_customer_id,
                'stripe_subscription_id': user.stripe_subscription_id,
                'subscription_cancel_at_period_end': user.subscription_cancel_at_period_end,
                'total_revenue': float(user.total_revenue or 0),
                'created_at': user.created_at.isoformat()
            }
        })
    
    elif request.method == 'POST':
        # Save user changes
        try:
            data = request.get_json()
            
            # Update basic user info
            if 'username' in data:
                # Check if username is already taken by another user
                existing_user = User.query.filter_by(username=data['username']).first()
                if existing_user and existing_user.id != user_id:
                    return jsonify({'error': 'Username already exists'}), 400
                user.username = data['username']
            
            if 'email' in data:
                # Check if email is already taken by another user
                existing_user = User.query.filter_by(email=data['email']).first()
                if existing_user and existing_user.id != user_id:
                    return jsonify({'error': 'Email already exists'}), 400
                user.email = data['email']
            
            if 'display_name' in data:
                user.display_name = data['display_name'] or None
            
            if 'timezone' in data:
                user.timezone = data['timezone']
            
            if 'is_admin' in data:
                user.is_admin = data['is_admin']
            
            if 'can_stream' in data:
                user.can_stream = data['can_stream']
            
            # Update subscription info (manual override)
            if 'subscription_plan' in data:
                user.subscription_plan = data['subscription_plan'] or None
            
            if 'subscription_status' in data:
                user.subscription_status = data['subscription_status'] or None
            
            if 'has_subscription' in data:
                user.has_subscription = data['has_subscription']
            
            db.session.commit()
            
            # Log the change
            try:
                event = SubscriptionEvent(
                    user_id=user.id,
                    event_type='user_updated_by_admin',
                    event_data=json.dumps({
                        'updated_by': current_user.username,
                        'updated_at': datetime.utcnow().isoformat(),
                        'changes': data
                    }),
                    processed=True
                )
                db.session.add(event)
                db.session.commit()
            except:
                pass  # Skip if SubscriptionEvent model doesn't exist
            
            return jsonify({
                'success': True,
                'message': 'User updated successfully'
            })
            
        except Exception as e:
            db.session.rollback()
            return jsonify({'error': str(e)}), 500

# Enhanced sync endpoint that works with individual users
@app.route('/api/admin/user/<int:user_id>/sync-stripe', methods=['POST'])
@login_required
def api_sync_user_with_stripe(user_id):
    """Sync a specific user with Stripe"""
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403
    
    try:
        user = User.query.get_or_404(user_id)
        
        if sync_user_with_stripe(user_id):
            return jsonify({
                'success': True,
                'message': f'User {user.username} synced with Stripe successfully'
            })
        else:
            return jsonify({
                'error': 'Failed to sync user with Stripe'
            }), 500
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Export users endpoint
@app.route('/api/admin/users/export')
@login_required
def api_export_users():
    """Export users to CSV"""
    if not current_user.is_admin:
        return redirect(url_for('admin'))
    
    try:
        import io
        import csv
        from flask import Response
        
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Write header
        writer.writerow([
            'ID', 'Username', 'Email', 'Display Name', 'Timezone',
            'Is Admin', 'Can Stream', 'Has Subscription', 'Subscription Plan',
            'Subscription Status', 'Total Revenue', 'Created At'
        ])
        
        # Write user data
        users = User.query.all()
        for user in users:
            writer.writerow([
                user.id,
                user.username,
                user.email,
                user.display_name or '',
                user.timezone,
                user.is_admin,
                user.can_stream,
                user.has_subscription,
                user.subscription_plan or '',
                user.subscription_status or '',
                float(user.total_revenue or 0),
                user.created_at.isoformat()
            ])
        
        output.seek(0)
        
        return Response(
            output.getvalue(),
            mimetype='text/csv',
            headers={
                'Content-Disposition': f'attachment; filename=users_export_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
            }
        )
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/user/change-plan', methods=['POST'])
@login_required
def api_change_plan():
    """Change subscription plan"""
    try:
        data = request.get_json()
        new_plan = data.get('plan')
        
        if new_plan not in ['monthly', 'annual']:
            return jsonify({'error': 'Invalid plan type'}), 400
        
        if not current_user.stripe_subscription_id:
            return jsonify({'error': 'No active subscription found'}), 400
        
        if current_user.subscription_plan == new_plan:
            return jsonify({'error': f'Already on {new_plan} plan'}), 400
        
        # Get price IDs
        price_ids = initialize_stripe_price_ids()
        new_price_id = price_ids.get(new_plan)
        
        if not new_price_id:
            return jsonify({'error': f'{new_plan.title()} plan not configured'}), 500
        
        # Update subscription in Stripe
        subscription = stripe.Subscription.retrieve(current_user.stripe_subscription_id)
        
        stripe.Subscription.modify(
            current_user.stripe_subscription_id,
            items=[{
                'id': subscription['items']['data'][0].id,
                'price': new_price_id,
            }],
            proration_behavior='immediate_with_remainder'
        )
        
        # Update user record
        old_plan = current_user.subscription_plan
        current_user.subscription_plan = new_plan
        current_user.subscription_price_id = new_price_id
        db.session.commit()
        
        # Log the change
        event = SubscriptionEvent(
            user_id=current_user.id,
            stripe_customer_id=current_user.stripe_customer_id,
            stripe_subscription_id=current_user.stripe_subscription_id,
            event_type='subscription_plan_changed',
            event_data=json.dumps({
                'changed_at': datetime.utcnow().isoformat(),
                'previous_plan': old_plan,
                'new_plan': new_plan
            }),
            processed=True
        )
        db.session.add(event)
        db.session.commit()
        
        # Send notification
        create_notification(
            current_user.id,
            'Plan Changed',
            f'Your subscription plan has been changed from {old_plan} to {new_plan}.',
            'subscription'
        )
        
        return jsonify({
            'success': True,
            'message': f'Successfully changed to {new_plan} plan'
        })
        
    except stripe.error.StripeError as e:
        return jsonify({'error': f'Stripe error: {str(e)}'}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/user/update-payment-method', methods=['POST'])
@login_required
def api_update_payment_method():
    """Create a Stripe Customer Portal session for payment method updates"""
    try:
        if not current_user.stripe_customer_id:
            return jsonify({'error': 'No Stripe customer found'}), 400
        
        # Create a Customer Portal session
        session = stripe.billing_portal.Session.create(
            customer=current_user.stripe_customer_id,
            return_url=f"{request.host_url}manage-subscription"
        )
        
        return jsonify({
            'success': True,
            'url': session.url
        })
        
    except stripe.error.StripeError as e:
        return jsonify({'error': f'Stripe error: {str(e)}'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/sync-all-subscriptions', methods=['POST'])
@login_required
def api_sync_all_subscriptions():
    """API endpoint to sync all subscriptions"""
    if not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    try:
        success_count, error_count = sync_all_subscriptions_with_stripe()
        
        return jsonify({
            'success': True,
            'synced': success_count,
            'errors': error_count,
            'message': f'Synced {success_count} users, {error_count} errors'
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/user/subscription-status')
@login_required
def api_get_subscription_status():
    """Get current user's subscription status"""
    try:
        # Sync with Stripe if needed
        if current_user.stripe_customer_id:
            sync_user_with_stripe(current_user.id)
        
        return jsonify({
            'success': True,
            'subscription': {
                'has_subscription': current_user.has_subscription,
                'status': current_user.subscription_status,
                'plan': current_user.subscription_plan,
                'expires': current_user.subscription_expires.isoformat() if current_user.subscription_expires else None,
                'cancel_at_period_end': current_user.subscription_cancel_at_period_end,
                'total_revenue': float(current_user.total_revenue or 0),
                'last_payment_date': current_user.last_payment_date.isoformat() if current_user.last_payment_date else None,
                'last_payment_amount': float(current_user.last_payment_amount or 0)
            }
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Enhanced subscription route with plan selection
@app.route('/subscription')
@login_required
def subscription():
    stripe_key = app.config.get('STRIPE_PUBLISHABLE_KEY')
    
    if not stripe_key:
        flash('Payment system is currently unavailable. Please try again later.', 'warning')
        return redirect(url_for('dashboard'))
    
    # Get plan from query parameter
    selected_plan = request.args.get('plan', 'monthly')
    
    return render_template('subscription.html', 
                         stripe_key=stripe_key,
                         selected_plan=selected_plan)


# Admin routes referenced in navigation
@app.route('/admin/users')
@login_required
def admin_users():
    """User management page"""
    if not current_user.is_admin:
        flash('Access denied', 'error')
        return redirect(url_for('dashboard'))
    
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template('admin/user_management.html', users=users)

@app.route('/admin/revenue')
@login_required
def admin_revenue():
    """Revenue analytics page - redirect to existing analytics"""
    if not current_user.is_admin:
        flash('Access denied', 'error')
        return redirect(url_for('dashboard'))
    
    return redirect(url_for('admin_analytics'))

# Fix for billing_history route (redirect to manage_subscription)
@app.route('/billing-history')
@login_required  
def billing_history():
    """Billing history page - redirects to subscription management"""
    return redirect(url_for('manage_subscription'))


@app.route('/webhook/stripe', methods=['POST'])
def stripe_webhook():
    """Handle Stripe webhooks"""
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get('Stripe-Signature')
    endpoint_secret = app.config.get('STRIPE_WEBHOOK_SECRET')
    
    if not endpoint_secret:
        print("⚠️ Stripe webhook secret not configured")
        return jsonify({'error': 'Webhook secret not configured'}), 400
    
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, endpoint_secret
        )
    except ValueError as e:
        print(f"❌ Invalid payload: {e}")
        return jsonify({'error': 'Invalid payload'}), 400
    except stripe.error.SignatureVerificationError as e:
        print(f"❌ Invalid signature: {e}")
        return jsonify({'error': 'Invalid signature'}), 400
    
    # Handle the event
    try:
        if event['type'] == 'customer.subscription.created':
            handle_subscription_created(event['data']['object'])
        
        elif event['type'] == 'customer.subscription.updated':
            handle_subscription_updated(event['data']['object'])
        
        elif event['type'] == 'customer.subscription.deleted':
            handle_subscription_deleted(event['data']['object'])
        
        elif event['type'] == 'invoice.payment_succeeded':
            handle_payment_succeeded(event['data']['object'])
        
        elif event['type'] == 'invoice.payment_failed':
            handle_payment_failed(event['data']['object'])
        
        elif event['type'] == 'customer.subscription.trial_will_end':
            handle_trial_will_end(event['data']['object'])
        
        elif event['type'] == 'checkout.session.completed':
            handle_checkout_completed(event['data']['object'])
        
        else:
            print(f"ℹ️ Unhandled event type: {event['type']}")
        
        # Log all events for analytics
        log_stripe_event(event)
        
        return jsonify({'success': True})
        
    except Exception as e:
        print(f"❌ Error handling webhook: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/subscription-success')
@login_required
def subscription_success():
    """Handle successful subscription"""
    session_id = request.args.get('session_id')
    
    if session_id:
        try:
            # Retrieve the session
            session = stripe.checkout.Session.retrieve(session_id)
            
            if session.payment_status == 'paid':
                # Sync user with Stripe to get latest subscription info
                sync_user_with_stripe(current_user.id)
                
                flash('Subscription activated successfully! Welcome to TGFX Trade Lab Premium!', 'success')
                return redirect(url_for('dashboard'))
        except stripe.error.StripeError as e:
            flash(f'Error verifying subscription: {str(e)}', 'error')
    
    flash('Subscription completed!', 'success')
    return redirect(url_for('dashboard'))
    

@app.route('/api/admin/user/<int:user_id>/grant-subscription', methods=['POST'])
@login_required
def api_grant_user_subscription():
    """Grant subscription to user - updated for lifetime"""
    if not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    try:
        data = request.get_json()
        plan = data.get('plan', 'monthly')
        duration = data.get('duration', 1)
        
        user = User.query.get_or_404(user_id)
        
        # Calculate expiration date
        if plan == 'lifetime':
            # Lifetime never expires (set to 100 years from now)
            expiration_date = datetime.utcnow() + timedelta(days=36500)
        elif plan == 'annual':
            expiration_date = datetime.utcnow() + timedelta(days=365 * duration)
        else:  # monthly
            expiration_date = datetime.utcnow() + timedelta(days=30 * duration)
        
        # Update user subscription manually (admin grant)
        user.has_subscription = True
        user.subscription_status = 'active'
        user.subscription_plan = plan
        user.subscription_expires = expiration_date
        user.subscription_current_period_end = expiration_date
        
        db.session.commit()
        
        # Log the event
        event = SubscriptionEvent(
            user_id=user.id,
            event_type='subscription_granted_by_admin',
            event_data=f"Granted {plan} subscription for {duration} {'months' if plan != 'lifetime' else 'lifetime'} by admin {current_user.username}",
            processed=True
        )
        db.session.add(event)
        db.session.commit()
        
        # Send notification to user
        if plan == 'lifetime':
            create_notification(
                user.id,
                'Lifetime Access Granted! 🏆',
                'You have been granted lifetime access to TGFX Trade Lab! Enjoy permanent access to all content.',
                'system'
            )
        else:
            create_notification(
                user.id,
                'Subscription Granted!',
                f'You have been granted a {plan} subscription. Enjoy your premium access!',
                'system'
            )
        
        return jsonify({'success': True, 'message': f'{plan.title()} subscription granted successfully'})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

# REPLACE your calculate_analytics_summary() function in app.py with this fixed version:

def calculate_analytics_summary():
    """Calculate summary analytics - updated for lifetime and fixed data types"""
    try:
        # Basic metrics
        total_users = User.query.count()
        premium_users = User.query.filter_by(has_subscription=True).count()
        
        # Revenue calculation
        total_revenue = db.session.query(db.func.sum(User.total_revenue)).scalar() or 0
        
        # MRR calculation (Monthly Recurring Revenue) - lifetime doesn't count toward MRR
        monthly_subscribers = User.query.filter_by(subscription_plan='monthly', has_subscription=True).count()
        annual_subscribers = User.query.filter_by(subscription_plan='annual', has_subscription=True).count()
        lifetime_subscribers = User.query.filter_by(subscription_plan='lifetime', has_subscription=True).count()
        
        monthly_mrr = monthly_subscribers * 29  # $29/month
        annual_mrr = annual_subscribers * (299 / 12)  # $299/year = ~$24.92/month
        total_mrr = monthly_mrr + annual_mrr
        
        # Calculate lifetime revenue impact
        lifetime_revenue = lifetime_subscribers * 499
        
        # Churn rate (last 30 days) - lifetime users can't churn
        thirty_days_ago = datetime.utcnow() - timedelta(days=30)
        
        # Get canceled subscriptions (if you have the SubscriptionEvent model)
        try:
            canceled_last_30_days = SubscriptionEvent.query.filter(
                SubscriptionEvent.event_type.like('%cancel%'),
                SubscriptionEvent.created_at >= thirty_days_ago
            ).count()
        except:
            canceled_last_30_days = 0
        
        # Only count non-lifetime users for churn calculation
        non_lifetime_subscribers = premium_users - lifetime_subscribers
        churn_rate = (canceled_last_30_days / non_lifetime_subscribers * 100) if non_lifetime_subscribers > 0 else 0
        
        # FIXED: Return numeric values for change calculations
        return {
            'total_revenue': f"{total_revenue:.2f}",
            'revenue_change': 12.5,  # NUMERIC, not string
            'mrr': f"{total_mrr:.2f}",
            'mrr_change': 8.3,  # NUMERIC, not string
            'new_subscribers': monthly_subscribers + annual_subscribers + lifetime_subscribers,
            'subscribers_change': 15.2,  # NUMERIC, not string
            'churn_rate': f"{churn_rate:.1f}",
            'churn_change': -2.1,  # NUMERIC, not string
            'total_subscribers': premium_users,
            'lifetime_subscribers': lifetime_subscribers,
            'lifetime_revenue': f"{lifetime_revenue:.2f}",
            'prev_total_subscribers': max(0, premium_users - 5),
            'active_subscriptions': premium_users,
            'prev_active_subscriptions': max(0, premium_users - 3),
            'canceled_subscriptions': canceled_last_30_days,
            'prev_canceled_subscriptions': canceled_last_30_days + 2,
            'avg_order_value': f"{(total_revenue / total_users):.2f}" if total_users > 0 else "0.00",
            'prev_avg_order_value': 28.50,  # NUMERIC, not string
            'aov_change': 5.2,  # NUMERIC, not string
            'active_subs_change': 10.5,  # NUMERIC, not string
            'canceled_subs_change': -15.3  # NUMERIC, not string
        }
        
    except Exception as e:
        print(f"Error calculating analytics: {e}")
        # Return safe defaults with numeric values
        return {
            'total_revenue': "0.00",
            'revenue_change': 0,
            'mrr': "0.00", 
            'mrr_change': 0,
            'new_subscribers': 0,
            'subscribers_change': 0,
            'churn_rate': "0.0",
            'churn_change': 0,
            'total_subscribers': 0,
            'lifetime_subscribers': 0,
            'lifetime_revenue': "0.00",
            'prev_total_subscribers': 0,
            'active_subscriptions': 0,
            'prev_active_subscriptions': 0,
            'canceled_subscriptions': 0,
            'prev_canceled_subscriptions': 0,
            'avg_order_value': "0.00",
            'prev_avg_order_value': 0,
            'aov_change': 0,
            'active_subs_change': 0,
            'canceled_subs_change': 0
        }
        
    except Exception as e:
        print(f"Error calculating analytics: {e}")
        return {}

def generate_chart_data(start_date, end_date):
    """Generate data for analytics charts - updated for lifetime"""
    try:
        # Daily revenue data (same as before)
        daily_revenue = []
        cumulative_revenue = []
        labels = []
        
        current_date = start_date
        running_total = 0
        
        while current_date <= end_date:
            day_revenue = 150  # Mock daily revenue
            daily_revenue.append(day_revenue)
            running_total += day_revenue
            cumulative_revenue.append(running_total)
            labels.append(current_date.strftime('%m/%d'))
            current_date += timedelta(days=1)
        
        # Subscription plans distribution - now includes lifetime
        monthly_subs = User.query.filter_by(subscription_plan='monthly', has_subscription=True).count()
        annual_subs = User.query.filter_by(subscription_plan='annual', has_subscription=True).count()
        lifetime_subs = User.query.filter_by(subscription_plan='lifetime', has_subscription=True).count()
        free_users = User.query.filter_by(has_subscription=False).count()
        
        # Cohort analysis (mock data)
        cohort_labels = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun']
        new_subs = [15, 22, 18, 25, 30, 28]
        churned_subs = [3, 5, 4, 6, 8, 7]
        
        # ARPU data (mock)
        arpu_labels = ['Week 1', 'Week 2', 'Week 3', 'Week 4']
        arpu_data = [25.50, 27.20, 26.80, 28.30]
        
        return {
            'revenue': {
                'labels': labels,
                'daily': daily_revenue,
                'cumulative': cumulative_revenue
            },
            'plans': {
                'labels': ['Monthly', 'Annual', 'Lifetime', 'Free'],
                'data': [monthly_subs, annual_subs, lifetime_subs, free_users],
                'colors': ['#10B981', '#3B82F6', '#FFD700', '#6B7280']
            },
            'cohort': {
                'labels': cohort_labels,
                'new_subs': new_subs,
                'churned_subs': churned_subs
            },
            'arpu': {
                'labels': arpu_labels,
                'data': arpu_data
            }
        }
        
    except Exception as e:
        print(f"Error generating chart data: {e}")
        return {}

# Update the checkout session creation to handle lifetime
@app.route('/api/create-checkout-session', methods=['POST'])
@login_required
def create_checkout_session():
    """Create a Stripe Checkout session for subscription - updated for coupons"""
    try:
        data = request.get_json()
        plan_type = data.get('plan_type', 'monthly')
        coupon_code = data.get('coupon_code')  # Optional coupon code
        
        # Get price IDs
        price_ids = initialize_stripe_price_ids()
        price_id = price_ids.get(plan_type)
        
        if not price_id:
            return jsonify({'error': 'Invalid plan selected'}), 400
        
        # Create or get Stripe customer
        if current_user.stripe_customer_id:
            customer_id = current_user.stripe_customer_id
        else:
            customer = stripe.Customer.create(
                email=current_user.email,
                name=current_user.username,
                metadata={'user_id': current_user.id}
            )
            current_user.stripe_customer_id = customer.id
            db.session.commit()
            customer_id = customer.id
        
        # Determine checkout mode based on plan type
        if plan_type == 'lifetime':
            mode = 'payment'  # One-time payment
        else:
            mode = 'subscription'  # Recurring subscription
        
        line_items = [{
            'price': price_id,
            'quantity': 1,
        }]
        
        # Prepare checkout session parameters
        checkout_params = {
            'customer': customer_id,
            'payment_method_types': ['card'],
            'line_items': line_items,
            'mode': mode,
            'success_url': f"{request.host_url}subscription-success?session_id={{CHECKOUT_SESSION_ID}}&plan={plan_type}",
            'cancel_url': f"{request.host_url}subscription?canceled=true",
            'metadata': {
                'user_id': current_user.id,
                'plan_type': plan_type
            },
            'allow_promotion_codes': True,  # Enable promotion code field
        }
        
        # Apply specific coupon if provided
        if coupon_code:
            try:
                # Validate the coupon exists and is active
                coupon = stripe.Coupon.retrieve(coupon_code)
                if coupon.valid:
                    checkout_params['discounts'] = [{
                        'coupon': coupon_code
                    }]
                else:
                    return jsonify({'error': 'Invalid or expired coupon code'}), 400
            except stripe.error.InvalidRequestError:
                return jsonify({'error': 'Invalid coupon code'}), 400
        
        # Create checkout session
        session = stripe.checkout.Session.create(**checkout_params)
        
        return jsonify({
            'success': True,
            'checkout_url': session.url
        })
        
    except stripe.error.StripeError as e:
        return jsonify({'error': f'Stripe error: {str(e)}'}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500
        
    

def start_livekit_egress_recording(room_name, stream_id, streamer_name):
    """
    Start LiveKit Egress recording with proper recording_id tracking
    """
    try:
        print(f"🎬 Starting LiveKit recording for {streamer_name} in room {room_name}")
        
        # Generate API token
        api_token = generate_livekit_api_token()
        if not api_token:
            print("❌ Failed to generate API token")
            return {'success': False, 'error': 'Failed to generate API token'}
        
        # Get configuration
        livekit_url = app.config.get('LIVEKIT_URL')
        aws_access_key = app.config.get('AWS_ACCESS_KEY_ID')
        aws_secret_key = app.config.get('AWS_SECRET_ACCESS_KEY')
        aws_region = app.config.get('AWS_REGION', 'us-east-1')
        s3_bucket = app.config.get('STREAM_RECORDINGS_BUCKET', 'tgfx-tradelab')
        prefix = app.config.get('STREAM_RECORDINGS_PREFIX', 'livestream-recordings/')
        
        if not all([aws_access_key, aws_secret_key, s3_bucket]):
            print("❌ AWS credentials missing")
            return {'success': False, 'error': 'AWS credentials not configured'}
        
        # Generate S3 path with date-based folder structure
        timestamp = datetime.utcnow().strftime('%Y%m%d-%H%M%S')
        date_folder = datetime.utcnow().strftime('%Y/%m/%d')
        filename = f"{streamer_name}-stream-{stream_id}-{timestamp}.mp4"
        s3_key = f"{prefix}livekit/{date_folder}/{filename}"
        
        print(f"📁 Recording will be saved to: s3://{s3_bucket}/{s3_key}")
        
        # Extract API URL from WebSocket URL
        if '.livekit.cloud' in livekit_url:
            api_url = livekit_url.replace('wss://', 'https://').replace('ws://', 'https://')
        else:
            api_url = livekit_url.replace('wss://', 'https://').replace('ws://', 'http://')
        
        # Create headers with Bearer token
        headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json"
        }
        
        # Create Egress request with enhanced settings
        egress_request = {
            "room_name": room_name,
            "file": {
                "filepath": s3_key,
                "s3": {
                    "access_key": aws_access_key,
                    "secret": aws_secret_key,
                    "region": aws_region,
                    "bucket": s3_bucket
                }
            },
            "preset": "H264_1080P_30",  # High quality preset
            "advanced": {
                "video": {
                    "codec": "H264_MAIN",
                    "profile": "main"
                },
                "audio": {
                    "codec": "OPUS",
                    "bitrate": 128000
                }
            }
        }
        
        # Make API request
        endpoint = f"{api_url}/twirp/livekit.Egress/StartRoomCompositeEgress"
        
        print(f"🔗 Calling LiveKit Egress API: {endpoint}")
        
        response = requests.post(
            endpoint,
            json=egress_request,
            headers=headers,
            timeout=30
        )
        
        print(f"📡 Response Status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            egress_id = data.get("egress_id")
            
            if egress_id:
                # Build the expected S3 URL
                s3_url = f"https://{s3_bucket}.s3.{aws_region}.amazonaws.com/{s3_key}"
                
                print(f"✅ Recording started successfully!")
                print(f"🔑 Egress ID: {egress_id}")
                print(f"📁 S3 Path: s3://{s3_bucket}/{s3_key}")
                
                return {
                    'success': True,
                    'egress_id': egress_id,
                    's3_path': f"s3://{s3_bucket}/{s3_key}",
                    's3_url': s3_url,
                    'filepath': s3_key,
                    'status': 'recording'
                }
            else:
                print(f"❌ No egress_id in response: {data}")
                return {'success': False, 'error': 'No egress_id returned'}
        else:
            error_msg = f"API returned {response.status_code}: {response.text}"
            print(f"❌ LiveKit Egress API error: {error_msg}")
            return {'success': False, 'error': error_msg}
            
    except Exception as e:
        print(f"❌ Unexpected error starting recording: {e}")
        import traceback
        traceback.print_exc()
        return {'success': False, 'error': str(e)}

def stop_livekit_egress_recording(egress_id):
    """
    Stop LiveKit Egress recording and get final file information
    """
    try:
        if not egress_id:
            print("⚠️ No egress_id provided")
            return {'success': False, 'error': 'No recording to stop'}
        
        # Generate API token
        api_token = generate_livekit_api_token()
        if not api_token:
            return {'success': False, 'error': 'Failed to generate API token'}
        
        # Get LiveKit URL
        livekit_url = app.config.get('LIVEKIT_URL')
        
        # Extract API URL
        if '.livekit.cloud' in livekit_url:
            api_url = livekit_url.replace('wss://', 'https://').replace('ws://', 'https://')
        else:
            api_url = livekit_url.replace('wss://', 'https://').replace('ws://', 'http://')
        
        # Create headers with Bearer token
        headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json"
        }
        
        print(f"🛑 Stopping recording: {egress_id}")
        
        # First, get the egress info to retrieve the file path and status
        info_response = requests.post(
            f"{api_url}/twirp/livekit.Egress/ListEgress",
            json={"egress_id": egress_id},
            headers=headers,
            timeout=15
        )
        
        recording_info = None
        if info_response.status_code == 200:
            egress_list = info_response.json().get('items', [])
            if egress_list:
                egress_info = egress_list[0]
                recording_info = {
                    'status': egress_info.get('status'),
                    'started_at': egress_info.get('started_at'),
                    'filepath': egress_info.get('file', {}).get('filepath'),
                    'room_name': egress_info.get('room_name')
                }
                print(f"📊 Egress status: {recording_info['status']}")
        
        # Stop the recording
        stop_response = requests.post(
            f"{api_url}/twirp/livekit.Egress/StopEgress",
            json={"egress_id": egress_id},
            headers=headers,
            timeout=15
        )
        
        if stop_response.status_code == 200:
            print(f"✅ Recording stopped: {egress_id}")
            
            # Generate S3 URL if we have the filepath
            recording_url = None
            if recording_info and recording_info.get('filepath'):
                s3_key = recording_info['filepath']
                aws_region = app.config.get('AWS_REGION', 'us-east-1')
                s3_bucket = app.config.get('STREAM_RECORDINGS_BUCKET', 'tgfx-tradelab')
                recording_url = f"https://{s3_bucket}.s3.{aws_region}.amazonaws.com/{s3_key}"
                print(f"🔗 Recording URL: {recording_url}")
            
            return {
                'success': True, 
                'egress_id': egress_id,
                'recording_url': recording_url,
                'recording_info': recording_info
            }
        else:
            print(f"❌ Failed to stop recording: {stop_response.status_code} - {stop_response.text}")
            return {'success': False, 'error': f"API error: {stop_response.status_code}"}
            
    except Exception as e:
        print(f"❌ Error stopping recording: {e}")
        return {'success': False, 'error': str(e)}

def verify_s3_recording_exists(s3_url, max_wait_time=60):
    """
    Verify that the recording file exists in S3 before syncing
    """
    print(f"⏳ Verifying recording file exists: {s3_url}")
    
    if not s3_url:
        return False
    
    try:
        # Parse S3 URL to get bucket and key
        # Format: https://bucket.s3.region.amazonaws.com/key
        url_parts = s3_url.replace('https://', '').split('/')
        bucket_and_region = url_parts[0]
        s3_key = '/'.join(url_parts[1:])
        bucket = bucket_and_region.split('.s3.')[0]
        
        # Initialize S3 client
        s3_client = boto3.client(
            's3',
            aws_access_key_id=app.config['AWS_ACCESS_KEY_ID'],
            aws_secret_access_key=app.config['AWS_SECRET_ACCESS_KEY'],
            region_name=app.config.get('AWS_REGION', 'us-east-1')
        )
        
        start_time = time.time()
        while time.time() - start_time < max_wait_time:
            try:
                # Check if file exists and has content
                response = s3_client.head_object(Bucket=bucket, Key=s3_key)
                file_size = response.get('ContentLength', 0)
                
                if file_size > 1000:  # At least 1KB to ensure it's not empty
                    print(f"✅ Recording file verified! Size: {file_size} bytes")
                    return True
                else:
                    print(f"⏳ File exists but too small ({file_size} bytes), waiting...")
                    
            except s3_client.exceptions.NoSuchKey:
                print(f"⏳ File not yet available, waiting...")
            except Exception as e:
                print(f"⚠️ Error checking file: {e}")
            
            time.sleep(5)  # Check every 5 seconds
        
        print(f"⚠️ Timeout waiting for recording file after {max_wait_time}s")
        return False
        
    except Exception as e:
        print(f"❌ Error verifying recording file: {e}")
        return False

def get_egress_info(egress_id):
    """Get information about a specific egress"""
    try:
        import requests
        
        # Generate API token
        api_token = generate_livekit_api_token()
        if not api_token:
            return None
        
        livekit_url = app.config.get('LIVEKIT_URL')
        
        # Extract API URL
        if '.livekit.cloud' in livekit_url:
            api_url = livekit_url.replace('wss://', 'https://').replace('ws://', 'https://')
        else:
            api_url = livekit_url.replace('wss://', 'https://').replace('ws://', 'http://')
        
        # Create headers with Bearer token
        headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json"
        }
        
        # Get egress info
        response = requests.post(
            f"{api_url}/twirp/livekit.Egress/ListEgress",
            json={"egress_id": egress_id},
            headers=headers,
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            egresses = data.get('items', [])
            
            if egresses:
                egress = egresses[0]
                print(f"📊 Egress info: {egress}")
                
                # Extract recording URL if available
                recording_url = None
                if 'file' in egress and 'filepath' in egress['file']:
                    s3_key = egress['file']['filepath']
                    aws_region = app.config.get('AWS_REGION', 'us-east-1')
                    s3_bucket = app.config.get('STREAM_RECORDINGS_BUCKET', 'tgfx-tradelab')
                    recording_url = f"https://{s3_bucket}.s3.{aws_region}.amazonaws.com/{s3_key}"
                
                return {
                    'egress_id': egress.get('egress_id'),
                    'room_name': egress.get('room_name'),
                    'status': egress.get('status'),
                    'started_at': egress.get('started_at'),
                    'recording_url': recording_url,
                    'filepath': egress.get('file', {}).get('filepath')
                }
        
        return None
        
    except Exception as e:
        print(f"Error getting egress info: {e}")
        return None

def get_or_create_tag(tag_name):
    """Helper function to get or create a tag"""
    import re
    
    if not tag_name:
        return None
        
    # Create slug from tag name
    slug = re.sub(r'[^a-zA-Z0-9\s-]', '', tag_name.lower())
    slug = re.sub(r'\s+', '-', slug.strip())
    
    # Try to find existing tag
    tag = Tag.query.filter_by(slug=slug).first()
    if not tag:
        # Create new tag
        tag = Tag(
            name=tag_name.strip().title(),
            slug=slug,
            color='#10B981'
        )
        db.session.add(tag)
        db.session.flush()
    
    return tag

def broadcast_notification(title, message, notification_type, target_users='all'):
    """Send notification to multiple users"""
    try:
        if target_users == 'all':
            users = User.query.all()
        elif target_users == 'premium':
            users = User.query.filter_by(has_subscription=True).all()
        elif target_users == 'free':
            users = User.query.filter_by(has_subscription=False).all()
        else:
            users = []
        
        for user in users:
            notification = Notification(
                user_id=user.id,
                title=title,
                message=message,
                notification_type=notification_type
            )
            db.session.add(notification)
        
        db.session.flush()
        print(f"📢 Sent notification to {len(users)} users: {title}")
        
    except Exception as e:
        print(f"Error sending notifications: {e}")

# Routes (keeping all existing routes but updating stream-related ones)
@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return render_template('index.html')

@app.route('/dashboard')
@login_required
def dashboard():
    # Get user progress stats
    user_progress = {p.video_id: p for p in current_user.progress}
    total_videos = Video.query.count()
    completed_videos = UserProgress.query.filter_by(user_id=current_user.id, completed=True).count()
    progress_percentage = (completed_videos / total_videos * 100) if total_videos > 0 else 0
    
    # Get favorite count
    favorite_count = UserFavorite.query.filter_by(user_id=current_user.id).count()
    
    # Get recent activity
    recent_activity = UserActivity.query.filter_by(user_id=current_user.id)\
                                       .order_by(UserActivity.timestamp.desc())\
                                       .limit(5).all()
    
    # Get notifications
    notifications = Notification.query.filter_by(user_id=current_user.id, is_read=False)\
                                     .order_by(Notification.created_at.desc())\
                                     .limit(5).all()
    
    return render_template('dashboard.html',
                         progress_percentage=progress_percentage,
                         completed_videos=completed_videos,
                         total_videos=total_videos,
                         favorite_count=favorite_count,
                         recent_activity=recent_activity,
                         notifications=notifications)


# API route for regenerating single thumbnail
@app.route('/api/admin/regenerate-thumbnail', methods=['POST'])
@login_required
def api_regenerate_thumbnail():
    if not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    try:
        data = request.get_json()
        video_id = data.get('video_id')
        
        video = Video.query.get_or_404(video_id)
        category = video.category
        
        if not category.background_image_url:
            return jsonify({'error': 'Category has no background image set'}), 400
        
        # Generate new thumbnail
        thumbnail_image = generate_thumbnail(
            category.background_image_url,
            category.name,
            video.title
        )
        
        # Upload to S3
        thumbnail_url = upload_thumbnail_to_s3(thumbnail_image, video.id, video.title)
        
        if thumbnail_url:
            video.thumbnail_url = thumbnail_url
            db.session.commit()
            
            return jsonify({
                'success': True,
                'thumbnail_url': thumbnail_url,
                'message': 'Thumbnail regenerated successfully'
            })
        else:
            return jsonify({'error': 'Failed to upload thumbnail'}), 500
            
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

# API route for regenerating all thumbnails in a category
@app.route('/api/admin/regenerate-category-thumbnails', methods=['POST'])
@login_required
def api_regenerate_category_thumbnails():
    if not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    try:
        data = request.get_json()
        category_id = data.get('category_id')
        
        category = Category.query.get_or_404(category_id)
        
        if not category.background_image_url:
            return jsonify({'error': 'Category has no background image set'}), 400
        
        success_count = 0
        error_count = 0
        
        for video in category.videos:
            try:
                # Generate thumbnail
                thumbnail_image = generate_thumbnail(
                    category.background_image_url,
                    category.name,
                    video.title
                )
                
                # Upload to S3
                thumbnail_url = upload_thumbnail_to_s3(thumbnail_image, video.id, video.title)
                
                if thumbnail_url:
                    video.thumbnail_url = thumbnail_url
                    success_count += 1
                else:
                    error_count += 1
                    
            except Exception as e:
                print(f"Error regenerating thumbnail for video {video.id}: {e}")
                error_count += 1
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'Regenerated {success_count} thumbnails successfully',
            'success_count': success_count,
            'error_count': error_count
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

# API route for updating video order
@app.route('/api/admin/video/order', methods=['POST'])
@login_required
def api_update_video_order():
    if not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    try:
        data = request.get_json()
        video_id = data.get('video_id')
        order_index = data.get('order_index')
        
        video = Video.query.get_or_404(video_id)
        video.order_index = order_index
        db.session.commit()
        
        return jsonify({'success': True})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/login', methods=['GET', 'POST'])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user and check_password_hash(user.password_hash, form.password.data):
            login_user(user)
            return redirect(url_for('dashboard'))
        flash('Invalid username or password', 'error')
    return render_template('auth/login.html', form=form)

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    form = SignupForm()
    if form.validate_on_submit():
        if User.query.filter_by(username=form.username.data).first():
            flash('Username already exists', 'error')
            return render_template('auth/signup.html', form=form)
        
        if User.query.filter_by(email=form.email.data).first():
            flash('Email already exists', 'error')
            return render_template('auth/signup.html', form=form)
        
        user = User(
            username=form.username.data,
            email=form.email.data,
            password_hash=generate_password_hash(form.password.data),
            timezone='America/Chicago'  # Default timezone for new users
        )
        db.session.add(user)
        db.session.commit()
        
        # Create welcome activity
        create_user_activity(user.id, 'account_created', 'Welcome to TGFX Trade Lab! Your trading journey begins now.')
        create_notification(user.id, 'Welcome!', 'Welcome to TGFX Trade Lab! Start with our beginner courses.', 'system')
        
        login_user(user)
        flash('Account created successfully!', 'success')
        return redirect(url_for('dashboard'))
    return render_template('auth/signup.html', form=form)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/admin/tags')
@login_required
def admin_tags():
    if not current_user.is_admin:
        flash('Access denied', 'error')
        return redirect(url_for('dashboard'))
    
    tags = Tag.query.order_by(Tag.name).all()
    
    tag_stats = []
    for tag in tags:
        video_count = len(tag.videos)
        tag_stats.append({
            'tag': tag,
            'video_count': video_count,
            'free_videos': len([v for v in tag.videos if v.is_free]),
            'premium_videos': len([v for v in tag.videos if not v.is_free])
        })
    
    return render_template('admin/tags.html', tags=tags, tag_stats=tag_stats)

# ALSO ADD THIS MISSING ROUTE:

@app.route('/admin/tag/add', methods=['GET', 'POST'])
@login_required
def admin_add_tag():
    if not current_user.is_admin:
        flash('Access denied', 'error')
        return redirect(url_for('dashboard'))
    
    form = TagForm()
    
    if form.validate_on_submit():
        slug = re.sub(r'[^a-zA-Z0-9\s-]', '', form.name.data.lower())
        slug = re.sub(r'\s+', '-', slug.strip())
        
        if Tag.query.filter_by(slug=slug).first():
            flash('A tag with this name already exists', 'error')
            return render_template('admin/tag_form.html', form=form, title='Add Tag')
        
        tag = Tag(
            name=form.name.data.strip().title(),
            slug=slug,
            description=form.description.data,
            color=form.color.data or '#10B981'
        )
        db.session.add(tag)
        db.session.commit()
        flash('Tag added successfully!', 'success')
        return redirect(url_for('admin_tags'))
    
    return render_template('admin/tag_form.html', form=form, title='Add Tag')

@app.route('/api/admin/video-stats')
@login_required
def get_video_completion_stats():
    """Get video completion statistics for admin dashboard"""
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403
    
    try:
        # Overall stats
        total_videos = Video.query.count()
        total_users = User.query.count()
        total_progress_records = UserProgress.query.count()
        total_completions = UserProgress.query.filter_by(completed=True).count()
        
        # Completion rate
        completion_rate = (total_completions / total_progress_records * 100) if total_progress_records > 0 else 0
        
        # Most popular videos (by view count)
        popular_videos = db.session.query(
            Video.title,
            Video.id,
            db.func.count(UserProgress.id).label('view_count'),
            db.func.count(db.case([(UserProgress.completed == True, 1)])).label('completion_count')
        ).join(UserProgress).group_by(Video.id, Video.title)\
         .order_by(db.desc('view_count')).limit(10).all()
        
        # Videos with highest completion rates
        high_completion_videos = db.session.query(
            Video.title,
            Video.id,
            db.func.count(UserProgress.id).label('view_count'),
            db.func.count(db.case([(UserProgress.completed == True, 1)])).label('completion_count'),
            (db.func.count(db.case([(UserProgress.completed == True, 1)])).cast(db.Float) / 
             db.func.count(UserProgress.id) * 100).label('completion_rate')
        ).join(UserProgress).group_by(Video.id, Video.title)\
         .having(db.func.count(UserProgress.id) >= 5)\
         .order_by(db.desc('completion_rate')).limit(10).all()
        
        return jsonify({
            'success': True,
            'overall_stats': {
                'total_videos': total_videos,
                'total_users': total_users,
                'total_progress_records': total_progress_records,
                'total_completions': total_completions,
                'completion_rate': round(completion_rate, 2)
            },
            'popular_videos': [
                {
                    'title': v.title,
                    'id': v.id,
                    'view_count': v.view_count,
                    'completion_count': v.completion_count,
                    'completion_rate': round((v.completion_count / v.view_count * 100), 2) if v.view_count > 0 else 0
                } for v in popular_videos
            ],
            'high_completion_videos': [
                {
                    'title': v.title,
                    'id': v.id,
                    'view_count': v.view_count,
                    'completion_count': v.completion_count,
                    'completion_rate': round(float(v.completion_rate), 2)
                } for v in high_completion_videos
            ]
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# NEW SETTINGS ROUTE

@app.route('/api/admin/add-past-recordings-to-courses', methods=['POST'])
@login_required
def add_past_recordings_to_courses():
    """Add all past stream recordings to the course library"""
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403
    
    try:
        # Get all streams with recording URLs that aren't in the video table
        streams_with_recordings = Stream.query.filter(
            Stream.recording_url.isnot(None),
            Stream.recording_url != ''
        ).all()
        
        # Get or create Live Trading Sessions category
        live_sessions_category = Category.query.filter_by(
            name='Live Trading Sessions'
        ).first()
        
        if not live_sessions_category:
            live_sessions_category = Category(
                name='Live Trading Sessions',
                description='Recorded live trading sessions from our professional traders',
                order_index=1
            )
            db.session.add(live_sessions_category)
            db.session.flush()
        
        added_count = 0
        skipped_count = 0
        
        for stream in streams_with_recordings:
            # Check if video already exists with this URL
            existing_video = Video.query.filter_by(
                s3_url=stream.recording_url
            ).first()
            
            if existing_video:
                skipped_count += 1
                continue
            
            # Calculate duration
            duration_seconds = 0
            if stream.started_at and stream.ended_at:
                duration = stream.ended_at - stream.started_at
                duration_seconds = int(duration.total_seconds())
            
            # Create video entry
            video_date = stream.started_at.strftime('%B %d, %Y') if stream.started_at else 'Unknown Date'
            video_title = f"{stream.streamer_name} - {video_date}"
            
            new_video = Video(
                title=video_title,
                description=f"Live trading session with {stream.streamer_name}\nOriginal stream: {stream.title}",
                s3_url=stream.recording_url,
                duration=duration_seconds,
                is_free=False,
                category_id=live_sessions_category.id,
                created_at=stream.created_at
            )
            db.session.add(new_video)
            db.session.flush()
            
            # Add tags
            trader_tag = get_or_create_tag(stream.streamer_name)
            live_tag = get_or_create_tag('Live Session')
            new_video.tags.append(trader_tag)
            new_video.tags.append(live_tag)
            
            added_count += 1
            print(f"✅ Added video: {video_title}")
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'Added {added_count} recordings to course library',
            'added': added_count,
            'skipped': skipped_count,
            'total_checked': len(streams_with_recordings)
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/admin/analytics')
@login_required
def admin_analytics():
    if not current_user.is_admin:
        flash('Access denied', 'error')
        return redirect(url_for('dashboard'))
    
    # Calculate basic analytics for initial page load
    analytics = calculate_analytics_summary()
    
    return render_template('admin/analytics.html', analytics=analytics)

@app.route('/api/admin/analytics')
@login_required
def api_get_analytics():
    if not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    try:
        period = request.args.get('period', '30')
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        # Calculate date range
        if start_date and end_date:
            start_dt = datetime.strptime(start_date, '%Y-%m-%d')
            end_dt = datetime.strptime(end_date, '%Y-%m-%d')
        else:
            end_dt = datetime.utcnow()
            if period == 'all':
                start_dt = User.query.order_by(User.created_at).first().created_at
            else:
                days = int(period)
                start_dt = end_dt - timedelta(days=days)
        
        # Get metrics
        metrics = calculate_period_metrics(start_dt, end_dt)
        
        # Get chart data
        charts = generate_chart_data(start_dt, end_dt)
        
        return jsonify({
            'success': True,
            'metrics': metrics,
            'charts': charts
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/recent-transactions')
@login_required
def api_get_recent_transactions():
    if not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    try:
        # Get recent transactions from Stripe
        transactions = []
        
        # Get all customers
        customers = stripe.Customer.list(limit=100)
        
        for customer in customers.data:
            # Get recent payment intents for this customer
            payments = stripe.PaymentIntent.list(
                customer=customer.id,
                limit=10
            )
            
            for payment in payments.data:
                if payment.status in ['succeeded', 'processing']:
                    # Find associated user
                    user = User.query.filter_by(stripe_customer_id=customer.id).first()
                    
                    transactions.append({
                        'id': payment.id,
                        'date': datetime.fromtimestamp(payment.created).strftime('%m/%d/%Y'),
                        'customer_name': user.username if user else customer.name or 'Unknown',
                        'customer_email': user.email if user else customer.email or 'Unknown',
                        'amount': f"{payment.amount / 100:.2f}",
                        'status': payment.status,
                        'plan': determine_plan_from_payment(payment)
                    })
        
        # Sort by date (most recent first)
        transactions.sort(key=lambda x: x['date'], reverse=True)
        
        return jsonify({
            'success': True,
            'transactions': transactions[:50]  # Return last 50 transactions
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/analytics/refresh', methods=['POST'])
@login_required
def api_refresh_analytics():
    if not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    try:
        # Sync all users with Stripe
        users_with_stripe = User.query.filter(User.stripe_customer_id.isnot(None)).all()
        synced_count = 0
        
        for user in users_with_stripe:
            if sync_user_with_stripe(user.id):
                synced_count += 1
        
        # Update revenue analytics
        update_revenue_analytics()
        
        return jsonify({
            'success': True,
            'message': f'Refreshed analytics for {synced_count} users'
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/analytics/export')
@login_required
def api_export_analytics():
    if not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    try:
        format_type = request.args.get('format', 'csv')
        period = request.args.get('period', '30')
        
        # Calculate date range
        end_dt = datetime.utcnow()
        if period == 'all':
            start_dt = User.query.order_by(User.created_at).first().created_at
        else:
            days = int(period)
            start_dt = end_dt - timedelta(days=days)
        
        if format_type == 'csv':
            return export_analytics_csv(start_dt, end_dt)
        elif format_type == 'pdf':
            return export_analytics_pdf(start_dt, end_dt)
        else:
            return jsonify({'error': 'Invalid format'}), 400
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/stream/join', methods=['POST'])
@login_required
def api_stream_join():
    """
    Called after client successfully connects to LiveKit room
    This endpoint handles post-join tasks like starting recording
    """
    try:
        data = request.get_json()
        stream_id = data.get('stream_id')
        room_name = data.get('room_name')
        participant_identity = data.get('participant_identity')
        
        if not all([stream_id, room_name]):
            return jsonify({'error': 'Missing required parameters'}), 400
        
        # Get the stream
        stream = Stream.query.get(stream_id)
        if not stream or not stream.is_active:
            return jsonify({'error': 'Stream not found or inactive'}), 404
        
        # Check if user is the stream owner/admin
        is_stream_owner = (
            current_user.is_authenticated and 
            stream.created_by == current_user.id
        )
        
        is_admin_user = current_user.is_admin and current_user.can_stream
        can_start_recording = is_stream_owner or is_admin_user
        
        print(f"🎬 User {current_user.username} joined stream {stream_id}")
        print(f"📊 Recording permissions check:")
        print(f"  - Stream Owner: {is_stream_owner}")
        print(f"  - Admin User: {is_admin_user}")
        print(f"  - Can Start Recording: {can_start_recording}")
        
        result = {
            'success': True,
            'stream_id': stream_id,
            'is_admin': can_start_recording,
            'recording': {'started': False}
        }
        
        # Start recording if user has permission and recording not already started
        if can_start_recording and not stream.is_recording:
            print(f"🔴 Starting recording for stream {stream_id}...")
            
            recording_result = start_livekit_egress_recording(
                room_name=room_name,
                stream_id=stream_id,
                streamer_name=stream.streamer_name
            )
            
            if recording_result and recording_result.get('success'):
                stream.is_recording = True
                stream.recording_id = recording_result.get('egress_id')
                db.session.commit()
                
                result['recording'] = {
                    'started': True,
                    'egress_id': recording_result.get('egress_id'),
                    's3_path': recording_result.get('s3_path')
                }
                
                print(f"✅ Recording started successfully")
                
                # Notify all participants via WebSocket
                if socketio:
                    socketio.emit('recording_started', {
                        'stream_id': stream_id,
                        'message': 'Recording has started'
                    }, room=f"stream_{stream_id}")
            else:
                print(f"⚠️ Recording failed to start")
                result['recording'] = {
                    'started': False,
                    'error': 'Failed to start recording'
                }
        elif stream.is_recording:
            print(f"📹 Recording already active for stream {stream_id}")
            result['recording'] = {
                'started': True,
                'already_recording': True,
                'egress_id': stream.recording_id
            }
        
        return jsonify(result)
        
    except Exception as e:
        print(f"❌ Error in stream join: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
        
@app.route('/settings', methods=['GET', 'POST'])
@login_required
def user_settings():
    """User settings page"""
    form = UserSettingsForm(obj=current_user)
    
    if form.validate_on_submit():
        # Validate timezone
        try:
            import pytz
            pytz.timezone(form.timezone.data)  # This will raise an exception if invalid
            current_user.timezone = form.timezone.data
            db.session.commit()
            flash('Settings updated successfully!', 'success')
            return redirect(url_for('user_settings'))
        except:
            flash('Invalid timezone selected.', 'error')
    
    return render_template('settings.html', form=form)

@app.route('/api/admin/check-livekit-egress', methods=['GET'])
@login_required
def check_livekit_egress():
    """Check LiveKit egress capability with Bearer token"""
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403
    
    try:
        import requests
        
        # Generate API token
        api_token = generate_livekit_api_token()
        if not api_token:
            return jsonify({'error': 'Failed to generate API token'})
        
        livekit_url = app.config.get('LIVEKIT_URL')
        
        # Extract API URL
        if '.livekit.cloud' in livekit_url:
            api_url = livekit_url.replace('wss://', 'https://').replace('ws://', 'https://')
        else:
            api_url = livekit_url.replace('wss://', 'https://').replace('ws://', 'http://')
        
        # Create headers with Bearer token
        headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json"
        }
        
        # List all egresses
        response = requests.post(
            f"{api_url}/twirp/livekit.Egress/ListEgress",
            json={},
            headers=headers,
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            return jsonify({
                'success': True,
                'api_url': api_url,
                'egresses': data.get('items', []),
                'count': len(data.get('items', [])),
                'message': f"Found {len(data.get('items', []))} egress(es)",
                'auth_method': 'Bearer token'
            })
        else:
            return jsonify({
                'success': False,
                'error': f"API returned {response.status_code}: {response.text}",
                'api_url': api_url
            })
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/courses')
@login_required
def courses():
    tag_filter = request.args.get('tag')
    all_tags = Tag.query.order_by(Tag.name).all()
    
    if tag_filter:
        tag = Tag.query.filter_by(slug=tag_filter).first()
        if tag:
            categories_with_tagged_videos = []
            all_categories = Category.query.order_by(Category.order_index).all()
            
            for category in all_categories:
                tagged_videos = [v for v in category.videos if tag in v.tags]
                if tagged_videos:
                    categories_with_tagged_videos.append({
                        'category': category,
                        'videos': tagged_videos
                    })
            
            categories = categories_with_tagged_videos
        else:
            categories = []
    else:
        all_categories = Category.query.order_by(Category.order_index).all()
        categories = [{'category': cat, 'videos': list(cat.videos)} for cat in all_categories if cat.videos]
    
    user_progress = {p.video_id: p for p in current_user.progress}
    user_favorites = {f.video_id for f in current_user.favorites}
    
    total_videos = Video.query.count()
    completed_videos = UserProgress.query.filter_by(user_id=current_user.id, completed=True).count()
    progress_percentage = (completed_videos / total_videos * 100) if total_videos > 0 else 0
    
    return render_template('courses/index.html', 
                         categories=categories,
                         all_tags=all_tags,
                         selected_tag=tag_filter,
                         user_progress=user_progress,
                         user_favorites=user_favorites,
                         progress_percentage=progress_percentage,
                         completed_videos=completed_videos,
                         total_videos=total_videos)

@app.route('/courses/category/<int:category_id>')
@login_required
def category_videos(category_id):
    category = Category.query.get_or_404(category_id)
    videos = Video.query.filter_by(category_id=category_id).order_by(Video.order_index).all()
    
    user_progress = {p.video_id: p for p in current_user.progress}
    user_favorites = {f.video_id for f in current_user.favorites}
    
    return render_template('courses/category.html', 
                         category=category,
                         videos=videos,
                         user_progress=user_progress,
                         user_favorites=user_favorites)

@app.route('/video/<int:video_id>')
@login_required
def watch_video(video_id):
    video = Video.query.get_or_404(video_id)
    
    if not user_can_access_video(video):
        flash('This video requires an active subscription', 'warning')
        return redirect(url_for('manage_subscription'))
    
    progress = UserProgress.query.filter_by(user_id=current_user.id, video_id=video_id).first()
    if not progress:
        progress = UserProgress(user_id=current_user.id, video_id=video_id)
        db.session.add(progress)
        db.session.commit()
        
        # Create activity for starting video
        create_user_activity(current_user.id, 'video_started', f'Started watching "{video.title}"')
    
    is_favorited = UserFavorite.query.filter_by(user_id=current_user.id, video_id=video_id).first() is not None
    user_progress = {p.video_id: p for p in current_user.progress}
    
    return render_template('courses/watch.html', 
                         video=video, 
                         progress=progress,
                         is_favorited=is_favorited,
                         user_progress=user_progress)

@app.route('/api/video/completion', methods=['POST'])
@login_required
def toggle_video_completion():
    """Enhanced video completion with course completion tracking and Discord notifications"""
    try:
        data = request.get_json()
        video_id = data.get('video_id')
        completed = data.get('completed', False)
        
        if not video_id:
            return jsonify({'error': 'Video ID is required'}), 400
        
        # Check if video exists
        video = Video.query.get(video_id)
        if not video:
            return jsonify({'error': 'Video not found'}), 404
        
        # Get or create progress record
        progress = UserProgress.query.filter_by(
            user_id=current_user.id, 
            video_id=video_id
        ).first()
        
        if not progress:
            progress = UserProgress(
                user_id=current_user.id,
                video_id=video_id,
                watched_duration=0
            )
            db.session.add(progress)
        
        # Update completion status
        old_completed = progress.completed
        progress.completed = completed
        progress.last_watched = datetime.utcnow()
        
        # If marking as complete and no previous watch time, set to full duration
        if completed and progress.watched_duration == 0 and video.duration:
            progress.watched_duration = video.duration
        elif completed and video.duration:
            # Ensure watched duration is at least 90% when manually completing
            min_duration = int(video.duration * 0.9)
            if progress.watched_duration < min_duration:
                progress.watched_duration = video.duration
        elif not completed:
            # When marking as incomplete, keep the actual watched duration
            pass
        
        db.session.commit()
        
        # Check for course completion if video was just completed
        if not old_completed and completed:
            category = video.category
            
            # Count total and completed videos in this category
            total_videos_in_category = Video.query.filter_by(category_id=category.id).count()
            completed_videos_in_category = db.session.query(UserProgress)\
                .join(Video)\
                .filter(Video.category_id == category.id)\
                .filter(UserProgress.user_id == current_user.id)\
                .filter(UserProgress.completed == True)\
                .count()
            
            # If user completed all videos in category AND it's a real course (more than 1 video)
            if (completed_videos_in_category == total_videos_in_category and 
                total_videos_in_category > 1):
                
                # 🆕 DISCORD WEBHOOK: Course completion notification
                try:
                    completion_stats = {
                        'completed': completed_videos_in_category,
                        'total': total_videos_in_category
                    }
                    webhook_success = send_course_completion_webhook(current_user, category, completion_stats)
                    if webhook_success:
                        print(f"✅ Discord notified about course completion: {category.name}")
                    else:
                        print(f"⚠️ Failed to notify Discord about course completion: {category.name}")
                except Exception as e:
                    print(f"❌ Discord webhook error for course completion: {e}")
                
                # In-app notification for course completion
                create_notification(
                    current_user.id,
                    'Course Completed! 🎓',
                    f'Congratulations! You completed the "{category.name}" course!',
                    'achievement'
                )
                
                # Create course completion activity
                create_user_activity(
                    current_user.id,
                    'course_completed',
                    f'Completed entire "{category.name}" course ({completed_videos_in_category} videos)'
                )
            else:
                # Regular video completion activity
                create_user_activity(
                    current_user.id, 
                    'video_completed', 
                    f'Completed "{video.title}"'
                )
        elif old_completed != completed and not completed:
            # Video marked as incomplete
            create_user_activity(
                current_user.id, 
                'video_progress_reset', 
                f'Marked "{video.title}" as incomplete'
            )
        
        return jsonify({
            'success': True,
            'completed': progress.completed,
            'watched_duration': progress.watched_duration,
            'message': 'Completion status updated successfully'
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/video/reset-progress', methods=['POST'])
@login_required
def reset_video_progress():
    """Reset video progress to 0 and mark as incomplete"""
    try:
        data = request.get_json()
        video_id = data.get('video_id')
        
        if not video_id:
            return jsonify({'error': 'Video ID is required'}), 400
        
        # Check if video exists
        video = Video.query.get(video_id)
        if not video:
            return jsonify({'error': 'Video not found'}), 404
        
        # Get progress record
        progress = UserProgress.query.filter_by(
            user_id=current_user.id, 
            video_id=video_id
        ).first()
        
        if progress:
            # Reset progress
            progress.watched_duration = 0
            progress.completed = False
            progress.last_watched = datetime.utcnow()
            
            db.session.commit()
            
            # Create activity
            create_user_activity(
                current_user.id, 
                'video_progress_reset', 
                f'Reset progress for "{video.title}"'
            )
            
            return jsonify({
                'success': True,
                'message': 'Progress reset successfully'
            })
        else:
            return jsonify({
                'success': True,
                'message': 'No progress to reset'
            })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/recommendations')
@login_required
def recommendations():
    category_filter = request.args.get('category')
    
    query = Recommendation.query.filter_by(is_active=True)
    
    if category_filter:
        query = query.filter_by(category=category_filter)
    
    recommendations = query.order_by(Recommendation.is_featured.desc(), 
                                   Recommendation.order_index,
                                   Recommendation.created_at.desc()).all()
    
    # Get available categories
    categories = db.session.query(Recommendation.category)\
                          .filter_by(is_active=True)\
                          .distinct().all()
    categories = [cat[0] for cat in categories]
    
    return render_template('recommendations.html',
                         recommendations=recommendations,
                         categories=categories,
                         selected_category=category_filter)

@app.route('/favorites')
@login_required
def favorites():
    user_favorites = db.session.query(Video).join(UserFavorite).filter(
        UserFavorite.user_id == current_user.id
    ).all()
    
    user_progress = {p.video_id: p for p in current_user.progress}
    
    return render_template('courses/favorites.html', 
                         videos=user_favorites,
                         user_progress=user_progress)

@app.route('/download/<int:file_id>')
@login_required
def download_file(file_id):
    video_file = VideoFile.query.get_or_404(file_id)
    
    if not user_can_access_video(video_file.video):
        flash('This file requires an active subscription', 'warning')
        return redirect(url_for('subscription'))
    
    return redirect(video_file.s3_url)


@app.route('/donate')
@login_required
def donate():
    stripe_key = app.config.get('STRIPE_PUBLISHABLE_KEY')
    
    if not stripe_key:
        flash('Donation system is currently unavailable. Please try again later.', 'warning')
        return redirect(url_for('dashboard'))
    
    return render_template('donate.html', stripe_key=stripe_key)


@app.route('/admin/tag/edit/<int:tag_id>', methods=['GET', 'POST'])
@login_required
def admin_edit_tag(tag_id):
    if not current_user.is_admin:
        flash('Access denied', 'error')
        return redirect(url_for('dashboard'))
    
    tag = Tag.query.get_or_404(tag_id)
    form = TagForm(obj=tag)
    
    if form.validate_on_submit():
        slug = re.sub(r'[^a-zA-Z0-9\s-]', '', form.name.data.lower())
        slug = re.sub(r'\s+', '-', slug.strip())
        
        existing_tag = Tag.query.filter_by(slug=slug).first()
        if existing_tag and existing_tag.id != tag.id:
            flash('A tag with this name already exists', 'error')
            return render_template('admin/tag_form.html', form=form, tag=tag, title='Edit Tag')
        
        tag.name = form.name.data.strip().title()
        tag.slug = slug
        tag.description = form.description.data
        tag.color = form.color.data or '#10B981'
        
        db.session.commit()
        flash('Tag updated successfully!', 'success')
        return redirect(url_for('admin_tags'))
    
    return render_template('admin/tag_form.html', form=form, tag=tag, title='Edit Tag')

# ALSO ADD THIS API ROUTE:
@app.route('/api/admin/auto-fill-title', methods=['POST'])
@login_required
def api_auto_fill_title():
    if not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    try:
        # Get trader defaults
        trader_defaults = {
            'jordan': ('Jordan', 'XAUUSD'),
            'jwill24': ('Jordan', 'XAUUSD'),
            'admin': ('Ray', 'EURUSD'),
            'ray': ('Ray', 'EURUSD')
        }
        
        username = current_user.username.lower()
        trader_name, trading_pair = trader_defaults.get(username, (current_user.display_name or current_user.username, 'EURUSD'))
        
        # Generate title with current date
        from datetime import datetime
        current_date = datetime.now().strftime('%m-%d-%y')
        auto_title = f"{trader_name} - {current_date} {trading_pair}"
        
        return jsonify({
            'success': True,
            'title': auto_title
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
        
@app.route('/api/video/progress', methods=['POST'])
@login_required
def update_progress():
    """Update video watch progress"""
    try:
        data = request.get_json()
        video_id = data.get('video_id')
        watched_duration = data.get('watched_duration', 0)
        total_duration = data.get('total_duration', 0)
        force_complete = data.get('force_complete', False)
        
        if not video_id:
            return jsonify({'error': 'Video ID is required'}), 400
        
        # Get or create progress record
        progress = UserProgress.query.filter_by(
            user_id=current_user.id, 
            video_id=video_id
        ).first()
        
        if not progress:
            progress = UserProgress(
                user_id=current_user.id,
                video_id=video_id
            )
            db.session.add(progress)
        
        # Update watched duration
        progress.watched_duration = max(progress.watched_duration, watched_duration)
        progress.last_watched = datetime.utcnow()
        
        # Handle completion logic
        was_completed = progress.completed
        
        if force_complete:
            progress.completed = True
        elif total_duration > 0:
            # Auto-complete if watched 90% or more
            completion_threshold = total_duration * 0.9
            if progress.watched_duration >= completion_threshold:
                progress.completed = True
        
        db.session.commit()
        
        # Create activity if newly completed
        if not was_completed and progress.completed:
            video = Video.query.get(video_id)
            if video:
                create_user_activity(
                    current_user.id, 
                    'video_completed', 
                    f'Completed "{video.title}"'
                )
        
        return jsonify({
            'success': True,
            'completed': progress.completed,
            'watched_duration': progress.watched_duration,
            'progress_percentage': (progress.watched_duration / total_duration * 100) if total_duration > 0 else 0
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/video/favorite', methods=['POST'])
@login_required
def toggle_favorite():
    data = request.get_json()
    video_id = data.get('video_id')
    
    favorite = UserFavorite.query.filter_by(user_id=current_user.id, video_id=video_id).first()
    
    if favorite:
        db.session.delete(favorite)
        is_favorited = False
        action = 'removed from'
    else:
        favorite = UserFavorite(user_id=current_user.id, video_id=video_id)
        db.session.add(favorite)
        is_favorited = True
        action = 'added to'
        
        # Create activity
        video = Video.query.get(video_id)
        create_user_activity(current_user.id, 'video_favorited', f'Added "{video.title}" to favorites')
    
    db.session.commit()
    
    return jsonify({'success': True, 'is_favorited': is_favorited})

# NEW TIMEZONE API ROUTES
@app.route('/api/user/timezone', methods=['POST'])
@login_required
def update_user_timezone():
    try:
        data = request.get_json()
        timezone_name = data.get('timezone')
        
        if not timezone_name:
            return jsonify({'error': 'Timezone is required'}), 400
        
        # Validate timezone
        try:
            pytz.timezone(timezone_name)
        except pytz.exceptions.UnknownTimeZoneError:
            return jsonify({'error': 'Invalid timezone'}), 400
        
        current_user.timezone = timezone_name
        db.session.commit()
        
        return jsonify({
            'success': True,
            'timezone': timezone_name,
            'message': 'Timezone updated successfully'
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/trading-sessions')
@login_required
def get_trading_sessions():
    try:
        user_tz = pytz.timezone(current_user.timezone or 'America/Chicago')
        est_tz = pytz.timezone('America/New_York')
        
        # Define session times in EST
        sessions_est = {
            'sydney': {'open': 17, 'close': 2, 'days': [0, 1, 2, 3, 4]},
            'tokyo': {'open': 19, 'close': 4, 'days': [0, 1, 2, 3, 4]},
            'london': {'open': 3, 'close': 12, 'days': [1, 2, 3, 4, 5]},
            'new_york': {'open': 8, 'close': 17, 'days': [1, 2, 3, 4, 5]}
        }
        
        # Convert to user's timezone
        sessions_user_tz = {}
        for session_name, session_data in sessions_est.items():
            # Create EST datetime for today
            now = datetime.now(est_tz)
            est_open = now.replace(hour=session_data['open'], minute=0, second=0, microsecond=0)
            est_close = now.replace(hour=session_data['close'], minute=0, second=0, microsecond=0)
            
            # Handle overnight sessions
            if session_data['close'] < session_data['open']:
                est_close = est_close + timedelta(days=1)
            
            # Convert to user timezone
            user_open = est_open.astimezone(user_tz)
            user_close = est_close.astimezone(user_tz)
            
            sessions_user_tz[session_name] = {
                'open': user_open.strftime('%H:%M'),
                'close': user_close.strftime('%H:%M'),
                'open_hour': user_open.hour,
                'open_minute': user_open.minute,
                'close_hour': user_close.hour,
                'close_minute': user_close.minute,
                'days': session_data['days']
            }
        
        return jsonify({
            'sessions': sessions_user_tz,
            'user_timezone': current_user.timezone,
            'current_time': datetime.now(user_tz).isoformat()
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Recommendations API
@app.route('/api/recommendations/track-click', methods=['POST'])
@login_required
def track_recommendation_click():
    data = request.get_json()
    recommendation_id = data.get('recommendation_id')
    
    if not recommendation_id:
        return jsonify({'error': 'Missing recommendation_id'}), 400
    
    recommendation = Recommendation.query.get(recommendation_id)
    if not recommendation:
        return jsonify({'error': 'Recommendation not found'}), 404
    
    # Increment click count
    recommendation.click_count += 1
    
    # Track individual click
    click = RecommendationClick(
        recommendation_id=recommendation_id,
        user_id=current_user.id,
        ip_address=request.remote_addr,
        user_agent=request.user_agent.string
    )
    db.session.add(click)
    db.session.commit()
    
    return jsonify({'success': True})

# Admin Routes
@app.route('/admin')
@login_required
def admin():
    if not current_user.is_admin:
        flash('Access denied', 'error')
        return redirect(url_for('dashboard'))
    
    video_count = Video.query.count()
    user_count = User.query.count()
    category_count = Category.query.count()
    subscription_count = User.query.filter_by(has_subscription=True).count()
    
    recent_videos = Video.query.order_by(Video.created_at.desc()).limit(5).all()
    
    return render_template('admin/dashboard.html',
                         video_count=video_count,
                         user_count=user_count,
                         category_count=category_count,
                         subscription_count=subscription_count,
                         recent_videos=recent_videos)

@app.route('/admin/videos')
@login_required
def admin_videos():
    if not current_user.is_admin:
        flash('Access denied', 'error')
        return redirect(url_for('dashboard'))
    
    videos = Video.query.order_by(Video.created_at.desc()).all()
    return render_template('admin/videos.html', videos=videos)

    
@app.route('/admin/video/edit/<int:video_id>', methods=['GET', 'POST'])
@login_required
def admin_edit_video(video_id):
    if not current_user.is_admin:
        flash('Access denied', 'error')
        return redirect(url_for('dashboard'))
    
    video = Video.query.get_or_404(video_id)
    
    try:
        form = VideoFormWithTags(obj=video)
    except NameError:
        form = VideoForm(obj=video)
    
    form.category_id.choices = [(c.id, c.name) for c in Category.query.all()]
    
    if request.method == 'GET' and hasattr(form, 'tags'):
        try:
            form.tags.data = ', '.join([tag.name for tag in video.tags])
        except:
            form.tags.data = ''
    
    if form.validate_on_submit():
        tags_data = None
        if hasattr(form, 'tags'):
            tags_data = form.tags.data
        
        # Store old values to check for changes
        old_title = video.title
        old_category_id = video.category_id
        regenerate_thumbnail = False
        
        video.title = form.title.data
        video.description = form.description.data
        video.s3_url = form.s3_url.data
        video.category_id = form.category_id.data
        video.is_free = form.is_free.data
        video.order_index = form.order_index.data
        
        # Handle thumbnail updates
        manual_thumbnail = form.thumbnail_url.data if form.thumbnail_url.data else None
        
        if manual_thumbnail:
            # Manual thumbnail provided
            video.thumbnail_url = manual_thumbnail
        else:
            # Check if we need to regenerate auto-thumbnail
            if (old_title != video.title or old_category_id != video.category_id):
                regenerate_thumbnail = True
        
        # Auto-regenerate thumbnail if needed
        if regenerate_thumbnail and not manual_thumbnail:
            category = Category.query.get(video.category_id)
            if category and category.background_image_url:
                try:
                    # Generate new thumbnail
                    thumbnail_image = generate_thumbnail(
                        category.background_image_url,
                        category.name,
                        video.title
                    )
                    
                    # Upload to S3
                    auto_thumbnail_url = upload_thumbnail_to_s3(thumbnail_image, video.id, video.title)
                    
                    if auto_thumbnail_url:
                        video.thumbnail_url = auto_thumbnail_url
                        print(f"✅ Auto-regenerated thumbnail for video: {video.title}")
                        
                except Exception as e:
                    print(f"❌ Error auto-regenerating thumbnail: {e}")
        
        # Process tags
        if tags_data is not None:
            try:
                process_video_tags(video, tags_data)
            except:
                pass
        
        db.session.commit()
        flash('Video updated successfully!', 'success')
        return redirect(url_for('admin_videos'))
    
    return render_template('admin/video_form.html', form=form, video=video, title='Edit Video')


@app.route('/admin/categories')
@login_required
def admin_categories():
    if not current_user.is_admin:
        flash('Access denied', 'error')
        return redirect(url_for('dashboard'))
    
    categories = Category.query.order_by(Category.order_index).all()
    
    total_videos = Video.query.count()
    empty_categories = len([cat for cat in categories if len(cat.videos) == 0])
    avg_videos_per_category = (total_videos / len(categories)) if categories else 0
    
    return render_template('admin/categories.html', 
                         categories=categories,
                         total_videos=total_videos,
                         avg_videos_per_category=avg_videos_per_category,
                         empty_categories=empty_categories)

@app.route('/admin/category/add', methods=['GET', 'POST'])
@login_required
def admin_add_category():
    if not current_user.is_admin:
        flash('Access denied', 'error')
        return redirect(url_for('dashboard'))
    
    form = CategoryForm()
    existing_categories = Category.query.order_by(Category.order_index).all()
    
    if form.validate_on_submit():
        category = Category(
            name=form.name.data,
            description=form.description.data,
            image_url=form.image_url.data,
            background_image_url=form.background_image_url.data,
            order_index=form.order_index.data
        )
        db.session.add(category)
        db.session.commit()
        
        # 🆕 DISCORD WEBHOOK: Send Discord notification for new course
        try:
            send_new_course_webhook(category)
        except Exception as e:
            print(f"Failed to send Discord webhook for new category: {e}")
        
        flash('Category added successfully!', 'success')
        return redirect(url_for('admin_categories'))
    
    return render_template('admin/category_form.html', 
                         form=form, 
                         title='Add Category',
                         existing_categories=existing_categories)

@app.route('/admin/category/edit/<int:category_id>', methods=['GET', 'POST'])
@login_required
def admin_edit_category(category_id):
    if not current_user.is_admin:
        flash('Access denied', 'error')
        return redirect(url_for('dashboard'))
    
    category = Category.query.get_or_404(category_id)
    form = CategoryForm(obj=category)
    existing_categories = Category.query.filter(Category.id != category_id).order_by(Category.order_index).all()
    
    if form.validate_on_submit():
        # Store old background URL to check for changes
        old_background_url = category.background_image_url
        
        form.populate_obj(category)
        db.session.commit()
        
        # If background image changed, offer to regenerate all thumbnails
        if old_background_url != category.background_image_url and category.background_image_url:
            flash('Category updated successfully! You can now regenerate thumbnails for all videos in this category.', 'success')
        else:
            flash('Category updated successfully!', 'success')
            
        return redirect(url_for('admin_categories'))
    
    return render_template('admin/category_form.html', 
                         form=form, 
                         category=category, 
                         title='Edit Category',
                         existing_categories=existing_categories)



# Admin Recommendations Routes
@app.route('/admin/recommendations')
@login_required
def admin_recommendations():
    if not current_user.is_admin:
        flash('Access denied', 'error')
        return redirect(url_for('dashboard'))
    
    recommendations = Recommendation.query.order_by(Recommendation.created_at.desc()).all()
    
    # Calculate stats
    total_recommendations = len(recommendations)
    total_clicks = sum(r.click_count for r in recommendations)
    featured_count = len([r for r in recommendations if r.is_featured])
    category_count = len(set(r.category for r in recommendations))
    
    return render_template('admin/recommendations.html',
                         recommendations=recommendations,
                         total_recommendations=total_recommendations,
                         total_clicks=total_clicks,
                         featured_count=featured_count,
                         category_count=category_count)

# Livestreaming routes
@app.route('/livestream')
@login_required
def livestream():
    # Check if user has active subscription or lifetime access
    if not current_user.has_active_subscription():
        flash('Live streaming requires an active subscription. Upgrade now to access exclusive live trading sessions!', 'warning')
        return redirect(url_for('manage_subscription'))
    
    active_streams = Stream.query.filter_by(is_active=True).all()
    
    streams_by_streamer = {}
    tokens_by_stream = {}
    
    for stream in active_streams:
        streamer = stream.streamer_name or 'Unknown'
        streams_by_streamer[streamer] = stream
        
        # Generate viewer token for each active stream
        participant_identity = f"viewer-{current_user.id}-{uuid.uuid4().hex[:8]}"
        participant_name = current_user.username
        
        viewer_token = generate_livekit_token(
            stream.room_name,
            participant_identity,
            participant_name,
            is_publisher=False
        )
        
        if viewer_token:
            tokens_by_stream[stream.id] = {
                'token': viewer_token,
                'participant_identity': participant_identity,
                'room_name': stream.room_name
            }
    
    active_streams_dict = [stream.to_dict() for stream in active_streams] if active_streams else []
    
    return render_template('livestream.html', 
                         active_streams=active_streams,
                         active_streams_dict=active_streams_dict,
                         streams_by_streamer=streams_by_streamer,
                         tokens_by_stream=tokens_by_stream,
                         livekit_url=app.config.get('LIVEKIT_URL'))

@app.route('/admin/stream')
@login_required
def admin_stream():
    if not current_user.is_admin:
        flash('Access denied', 'error')
        return redirect(url_for('dashboard'))
    
    form = DualStreamForm()
    active_streams = Stream.query.filter_by(is_active=True).all()
    recent_streams = Stream.query.order_by(Stream.created_at.desc()).limit(10).all()
    
    can_start_stream = len(active_streams) < 2 and current_user.can_stream
    
    user_active_stream = Stream.query.filter_by(
        created_by=current_user.id,
        is_active=True
    ).first()
    
    active_streams_dict = [stream.to_dict() for stream in active_streams] if active_streams else []
    user_active_stream_dict = user_active_stream.to_dict() if user_active_stream else None
    
    return render_template('admin/stream.html',
                         form=form,
                         active_streams=active_streams,
                         active_streams_dict=active_streams_dict,
                         recent_streams=recent_streams,
                         can_start_stream=can_start_stream,
                         user_active_stream=user_active_stream,
                         user_active_stream_dict=user_active_stream_dict)


@app.route('/api/admin/recommendations', methods=['POST'])
@login_required
def api_add_recommendation():
    if not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    data = request.get_json()
    
    try:
        # Convert empty strings to None for integer fields
        integer_fields = ['discount_percentage', 'order_index']
        data = convert_empty_strings_to_none(data, integer_fields)
        
        recommendation = Recommendation(
            title=data.get('title'),
            description=data.get('description'),
            category=data.get('category'),
            affiliate_url=data.get('affiliate_url'),
            image_url=data.get('image_url'),
            demo_url=data.get('demo_url'),
            price_info=data.get('price_info'),
            coupon_code=data.get('coupon_code'),
            discount_percentage=data.get('discount_percentage'),
            features=data.get('features'),
            is_featured=data.get('is_featured', False),
            is_active=data.get('is_active', True),
            order_index=data.get('order_index', 0)
        )
        
        db.session.add(recommendation)
        db.session.commit()
        
        return jsonify({'success': True, 'id': recommendation.id})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/recommendations/<int:recommendation_id>', methods=['GET'])
@login_required
def api_get_recommendation(recommendation_id):
    if not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    recommendation = Recommendation.query.get_or_404(recommendation_id)
    
    return jsonify({
        'success': True,
        'recommendation': {
            'id': recommendation.id,
            'title': recommendation.title,
            'description': recommendation.description,
            'category': recommendation.category,
            'affiliate_url': recommendation.affiliate_url,
            'image_url': recommendation.image_url,
            'demo_url': recommendation.demo_url,
            'price_info': recommendation.price_info,
            'coupon_code': recommendation.coupon_code,
            'discount_percentage': recommendation.discount_percentage,
            'features': recommendation.features,
            'is_featured': recommendation.is_featured,
            'is_active': recommendation.is_active,
            'order_index': recommendation.order_index,
            'click_count': recommendation.click_count,
            'created_at': recommendation.created_at.isoformat()
        }
    })

@app.route('/api/admin/recommendations/<int:recommendation_id>', methods=['PUT'])
@login_required
def api_update_recommendation(recommendation_id):
    if not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    recommendation = Recommendation.query.get_or_404(recommendation_id)
    data = request.get_json()
    
    try:
        # Convert empty strings to None for integer fields
        integer_fields = ['discount_percentage', 'order_index']
        data = convert_empty_strings_to_none(data, integer_fields)
        
        recommendation.title = data.get('title', recommendation.title)
        recommendation.description = data.get('description', recommendation.description)
        recommendation.category = data.get('category', recommendation.category)
        recommendation.affiliate_url = data.get('affiliate_url', recommendation.affiliate_url)
        recommendation.image_url = data.get('image_url', recommendation.image_url)
        recommendation.demo_url = data.get('demo_url', recommendation.demo_url)
        recommendation.price_info = data.get('price_info', recommendation.price_info)
        recommendation.coupon_code = data.get('coupon_code', recommendation.coupon_code)
        recommendation.discount_percentage = data.get('discount_percentage', recommendation.discount_percentage)
        recommendation.features = data.get('features', recommendation.features)
        recommendation.is_featured = data.get('is_featured', recommendation.is_featured)
        recommendation.is_active = data.get('is_active', recommendation.is_active)
        recommendation.order_index = data.get('order_index', recommendation.order_index)
        
        db.session.commit()
        
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/recommendations/<int:recommendation_id>', methods=['DELETE'])
@login_required
def api_delete_recommendation(recommendation_id):
    if not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    recommendation = Recommendation.query.get_or_404(recommendation_id)
    
    try:
        db.session.delete(recommendation)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/recommendations/<int:recommendation_id>/toggle-featured', methods=['POST'])
@login_required
def api_toggle_featured_recommendation(recommendation_id):
    if not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    recommendation = Recommendation.query.get_or_404(recommendation_id)
    
    try:
        recommendation.is_featured = not recommendation.is_featured
        db.session.commit()
        
        return jsonify({'success': True, 'is_featured': recommendation.is_featured})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/tag/<int:tag_id>', methods=['DELETE'])
@login_required
def api_delete_tag(tag_id):
    if not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    tag = Tag.query.get_or_404(tag_id)
    
    try:
        db.session.delete(tag)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/categories/reorder', methods=['POST'])
@login_required
def api_reorder_categories():
    if not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    data = request.get_json()
    categories = data.get('categories', [])
    
    try:
        for cat_data in categories:
            category = Category.query.get(cat_data['id'])
            if category:
                category.order_index = cat_data['order_index']
        
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/category/<int:category_id>', methods=['DELETE'])
@login_required
def api_delete_category(category_id):
    if not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    category = Category.query.get_or_404(category_id)
    
    try:
        db.session.delete(category)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/video/<int:video_id>', methods=['DELETE'])
@login_required
def api_delete_video(video_id):
    if not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    video = Video.query.get_or_404(video_id)
    
    try:
        db.session.delete(video)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

def initialize_enhanced_livestream():
    """Initialize enhanced livestream recording functionality"""
    try:
        with app.app_context():
            # Run the migration
            print("🔄 Running livestream recording migration...")
            if migrate_stream_recording_id():
                print("✅ Stream recording_id migration completed")
            else:
                print("❌ Stream recording_id migration failed")
                return False
            
            # Verify LiveKit configuration
            print("🔄 Verifying LiveKit configuration...")
            required_config = [
                'LIVEKIT_URL',
                'LIVEKIT_API_KEY', 
                'LIVEKIT_API_SECRET',
                'AWS_ACCESS_KEY_ID',
                'AWS_SECRET_ACCESS_KEY',
                'STREAM_RECORDINGS_BUCKET'
            ]
            
            missing_config = []
            for config_var in required_config:
                if not app.config.get(config_var):
                    missing_config.append(config_var)
            
            if missing_config:
                print(f"❌ Missing required configuration: {', '.join(missing_config)}")
                return False
            
            print("✅ LiveKit configuration verified")
            
            # Test API token generation
            try:
                test_token = generate_livekit_api_token()
                if test_token:
                    print("✅ LiveKit API token generation working")
                else:
                    print("❌ LiveKit API token generation failed")
                    return False
            except Exception as e:
                print(f"❌ LiveKit API token test failed: {e}")
                return False
            
            print("🎬 Enhanced livestream recording autosync initialized successfully!")
            print("📋 Features enabled:")
            print("   • Automatic recording start/stop")
            print("   • S3 file verification") 
            print("   • Auto-sync to course library")
            print("   • Enhanced video titles with date format")
            print("   • Comprehensive tagging")
            print("   • Discord notifications")
            
            return True
            
    except Exception as e:
        print(f"❌ Enhanced livestream initialization failed: {e}")
        return False

def cleanup_old_streams():
    """Clean up old inactive streams (run periodically)"""
    try:
        with app.app_context():
            # Find streams that are marked as active but are old (>12 hours)
            cutoff_time = datetime.utcnow() - timedelta(hours=12)
            
            old_active_streams = Stream.query.filter(
                Stream.is_active == True,
                Stream.started_at < cutoff_time
            ).all()
            
            cleaned_count = 0
            for stream in old_active_streams:
                print(f"🧹 Cleaning up old stream: {stream.title} (ID: {stream.id})")
                
                # Mark as inactive
                stream.is_active = False
                stream.is_recording = False
                if not stream.ended_at:
                    stream.ended_at = datetime.utcnow()
                
                # Update viewer records
                StreamViewer.query.filter_by(stream_id=stream.id, is_active=True).update({
                    'is_active': False,
                    'left_at': datetime.utcnow()
                })
                
                cleaned_count += 1
            
            if cleaned_count > 0:
                db.session.commit()
                print(f"✅ Cleaned up {cleaned_count} old streams")
            else:
                print("ℹ️ No old streams to clean up")
            
            return cleaned_count
            
    except Exception as e:
        print(f"❌ Error cleaning up old streams: {e}")
        db.session.rollback()
        return 0

def get_recording_status(stream_id):
    """Get current recording status for a stream"""
    try:
        stream = Stream.query.get(stream_id)
        if not stream:
            return {'error': 'Stream not found'}
        
        if not stream.recording_id:
            return {
                'is_recording': False,
                'status': 'no_recording',
                'message': 'No recording active'
            }
        
        # Check with LiveKit API
        api_token = generate_livekit_api_token()
        if not api_token:
            return {'error': 'Failed to generate API token'}
        
        livekit_url = app.config.get('LIVEKIT_URL')
        if '.livekit.cloud' in livekit_url:
            api_url = livekit_url.replace('wss://', 'https://').replace('ws://', 'https://')
        else:
            api_url = livekit_url.replace('wss://', 'https://').replace('ws://', 'http://')
        
        headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json"
        }
        
        response = requests.post(
            f"{api_url}/twirp/livekit.Egress/ListEgress",
            json={"egress_id": stream.recording_id},
            headers=headers,
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            egresses = data.get('items', [])
            
            if egresses:
                egress = egresses[0]
                return {
                    'is_recording': stream.is_recording,
                    'status': egress.get('status', 'unknown'),
                    'egress_id': stream.recording_id,
                    'started_at': egress.get('started_at'),
                    'file_path': egress.get('file', {}).get('filepath')
                }
        
        return {
            'is_recording': stream.is_recording,
            'status': 'unknown',
            'egress_id': stream.recording_id,
            'message': 'Could not fetch status from LiveKit'
        }
        
    except Exception as e:
        return {'error': str(e)}

# API endpoint to check recording status
@app.route('/api/stream/<int:stream_id>/recording-status')
@login_required
def api_get_recording_status(stream_id):
    """Get recording status for a specific stream"""
    if not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    status = get_recording_status(stream_id)
    return jsonify(status)

# API endpoint to manually sync a completed recording
@app.route('/api/stream/<int:stream_id>/sync-recording', methods=['POST'])
@login_required
def api_manual_sync_recording(stream_id):
    """Manually sync a stream recording to the course library"""
    if not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    try:
        stream = Stream.query.get_or_404(stream_id)
        
        if not stream.recording_url:
            return jsonify({'error': 'No recording URL found for this stream'}), 400
        
        # Check if already synced
        existing_video = Video.query.filter_by(s3_url=stream.recording_url).first()
        if existing_video:
            return jsonify({
                'success': False,
                'message': 'Recording already synced to course library',
                'video_id': existing_video.id,
                'video_title': existing_video.title
            })
        
        # Verify recording exists in S3
        if not verify_s3_recording_exists(stream.recording_url, max_wait_time=10):
            return jsonify({'error': 'Recording file not found in S3'}), 400
        
        # Sync to course library (reuse logic from api_stop_stream)
        live_sessions_category = Category.query.filter_by(
            name='Live Trading Sessions'
        ).first()
        
        if not live_sessions_category:
            live_sessions_category = Category(
                name='Live Trading Sessions',
                description='Recorded live trading sessions from our professional traders',
                order_index=1
            )
            db.session.add(live_sessions_category)
            db.session.flush()
        
        # Create video title
        stream_date = stream.started_at.strftime('%m-%d-%y') if stream.started_at else datetime.utcnow().strftime('%m-%d-%y')
        core_title = stream.title
        if f"{stream.streamer_name}'s " in core_title:
            core_title = core_title.replace(f"{stream.streamer_name}'s ", "")
        
        video_title = f"{stream.streamer_name} - {stream_date} {core_title}"
        
        # Calculate duration
        duration_seconds = 0
        if stream.started_at and stream.ended_at:
            duration = stream.ended_at - stream.started_at
            duration_seconds = int(duration.total_seconds())
        
        # Create video entry
        new_video = Video(
            title=video_title,
            description=f"Live trading session with {stream.streamer_name}\nManually synced recording",
            s3_url=stream.recording_url,
            duration=duration_seconds,
            is_free=False,
            category_id=live_sessions_category.id,
            created_at=stream.started_at or datetime.utcnow()
        )
        db.session.add(new_video)
        db.session.flush()
        
        # Add tags
        tags_to_add = [stream.streamer_name, 'Live Session', 'Live Trading']
        for tag_name in tags_to_add:
            tag = get_or_create_tag(tag_name)
            if tag:
                new_video.tags.append(tag)
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Recording successfully synced to course library',
            'video_id': new_video.id,
            'video_title': new_video.title
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

def initialize_complete_app():
    """Complete app initialization including livestream enhancements"""
    try:
        with app.app_context():
            # Your existing initialization
            db.create_all()
            print("✅ Database tables created successfully")
            
            # Existing migrations
            migrate_user_timezones()
            
            # NEW: Enhanced livestream initialization
            if not initialize_enhanced_livestream():
                print("⚠️ Enhanced livestream initialization had issues, but continuing...")
            
            # Your existing admin user creation, etc.
            admin_user = User.query.filter_by(username='admin').first()
            if not admin_user:
                admin_user = User(
                    username='admin',
                    email='ray@tgfx-academy.com',
                    password_hash=generate_password_hash('admin123!345gdfb3f35'),
                    is_admin=True,
                    display_name='Ray',
                    can_stream=True,
                    stream_color='#10B981',
                    timezone='America/Chicago'
                )
                db.session.add(admin_user)
                db.session.commit()
                print("✅ Admin user created")
            
            # Your existing streamer initialization
            initialize_streamers()
            
            print("🎉 Complete app initialization finished!")
            
    except Exception as e:
        print(f"❌ Complete app initialization error: {e}")
        return False
    
    return True
    
def start_livekit_cloud_recording(room_name, stream_id, streamer_name):
    """Actually start LiveKit Cloud Recording with S3 output"""
    try:
        import requests
        import base64
        import json
        
        # Generate S3 path
        timestamp = datetime.utcnow().strftime('%Y%m%d-%H%M%S')
        s3_key = f"livestream-recordings/{streamer_name}/{streamer_name}-stream-{stream_id}-{timestamp}.mp4"
        
        print(f"📹 Starting recording to: {s3_key}")
        
        # Get LiveKit credentials
        livekit_api_key = app.config.get('LIVEKIT_API_KEY')
        livekit_api_secret = app.config.get('LIVEKIT_API_SECRET')
        livekit_url = app.config.get('LIVEKIT_URL')
        
        # Get AWS credentials
        aws_access_key = app.config.get('AWS_ACCESS_KEY_ID')
        aws_secret_key = app.config.get('AWS_SECRET_ACCESS_KEY')
        aws_region = app.config.get('AWS_REGION', 'us-east-1')
        s3_bucket = app.config.get('STREAM_RECORDINGS_BUCKET', 'tgfx-tradelab')
        
        if not all([livekit_api_key, livekit_api_secret, livekit_url]):
            print("❌ LiveKit credentials missing")
            return None
            
        if not all([aws_access_key, aws_secret_key, s3_bucket]):
            print("❌ AWS credentials missing")
            return None
        
        # Extract server URL from WebSocket URL
        server_url = livekit_url.replace('wss://', 'https://').replace('ws://', 'http://')
        if '.livekit.cloud' in server_url:
            # For LiveKit Cloud, use the API endpoint
            server_url = server_url.split('/')[0] + '//' + server_url.split('/')[2]
        
        # Create Egress request for Room Composite Recording
        egress_request = {
            "room_name": room_name,
            "file": {
                "filepath": s3_key,
                "s3": {
                    "access_key": aws_access_key,
                    "secret": aws_secret_key,
                    "region": aws_region,
                    "bucket": s3_bucket
                }
            },
            "preset": "HD_30"  # 720p 30fps
        }
        
        # Create Basic Auth header
        auth = base64.b64encode(f"{livekit_api_key}:{livekit_api_secret}".encode()).decode()
        
        headers = {
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/json"
        }
        
        # Make API request to start recording
        api_url = f"{server_url}/twirp/livekit.Egress/StartRoomCompositeEgress"
        
        print(f"📡 Calling LiveKit API: {api_url}")
        
        response = requests.post(
            api_url,
            json=egress_request,
            headers=headers,
            timeout=10
        )
        
        print(f"📡 LiveKit API Response: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"✅ Recording started: {data}")
            return {
                "recording_id": data.get("egress_id"),
                "s3_path": s3_key,
                "status": "recording"
            }
        else:
            print(f"❌ LiveKit API error: {response.status_code} - {response.text}")
            return None
            
    except Exception as e:
        print(f"❌ Error starting recording: {e}")
        import traceback
        traceback.print_exc()
        return None

def stop_livekit_cloud_recording(recording_id):
    """Stop LiveKit Cloud Recording"""
    try:
        livekit_api_key = app.config.get('LIVEKIT_API_KEY')
        livekit_api_secret = app.config.get('LIVEKIT_API_SECRET')
        livekit_url = app.config.get('LIVEKIT_URL')
        
        if not recording_id:
            print("No recording ID provided")
            return False
        
        server_url = livekit_url.replace('wss://', 'https://').replace('ws://', 'http://')
        auth = base64.b64encode(f"{livekit_api_key}:{livekit_api_secret}".encode()).decode()
        
        response = requests.post(
            f"{server_url}/twirp/livekit.Egress/StopEgress",
            json={"egress_id": recording_id},
            headers={
                "Authorization": f"Basic {auth}",
                "Content-Type": "application/json"
            }
        )
        
        if response.status_code == 200:
            print(f"✅ Recording stopped: {recording_id}")
            return True
        else:
            print(f"❌ Failed to stop recording: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"❌ Error stopping recording: {e}")
        return False

@app.route('/api/stream/upload-recording', methods=['POST'])
@login_required
def upload_stream_recording():
    """Handle client-side recording upload to S3"""
    if not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    try:
        video_file = request.files.get('video')
        stream_id = request.form.get('stream_id')
        streamer_name = request.form.get('streamer_name')
        
        if not video_file:
            return jsonify({'error': 'No video file provided'}), 400
        
        # Generate S3 key with folder structure
        timestamp = datetime.utcnow().strftime('%Y%m%d-%H%M%S')
        s3_key = f"livestream-recordings/{streamer_name}/{streamer_name}-stream-{stream_id}-{timestamp}.webm"
        
        print(f"📤 Uploading recording to S3: {s3_key}")
        
        # Initialize S3 client
        s3_client = boto3.client(
            's3',
            aws_access_key_id=app.config['AWS_ACCESS_KEY_ID'],
            aws_secret_access_key=app.config['AWS_SECRET_ACCESS_KEY'],
            region_name=app.config.get('AWS_REGION', 'us-east-1')
        )
        
        # Upload to S3
        s3_client.upload_fileobj(
            video_file,
            app.config.get('STREAM_RECORDINGS_BUCKET', 'tgfx-tradelab'),
            s3_key,
            ExtraArgs={
                'ContentType': 'video/webm',
                'Metadata': {
                    'streamer': streamer_name,
                    'stream_id': str(stream_id)
                }
            }
        )
        
        s3_url = f"https://{app.config.get('STREAM_RECORDINGS_BUCKET')}.s3.amazonaws.com/{s3_key}"
        
        # Update stream record with recording URL
        stream = Stream.query.get(stream_id)
        if stream:
            stream.recording_url = s3_url
            stream.is_recording = False
            db.session.commit()
        
        print(f"✅ Recording uploaded successfully: {s3_url}")
        
        return jsonify({
            'success': True,
            'url': s3_url,
            'message': 'Recording saved to S3'
        })
        
    except Exception as e:
        print(f"❌ Upload error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/test-discord-webhook', methods=['POST'])
@login_required
def api_test_discord_webhook():
    """Enhanced test endpoint for streamlined Discord webhooks"""
    if not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    try:
        data = request.get_json() or {}
        test_type = data.get('type', 'basic')
        
        test_results = {}
        
        if test_type == 'basic' or test_type == 'all':
            # Test basic webhook
            basic_test = test_discord_webhook()
            test_results['basic_webhook'] = basic_test
        
        if test_type == 'video' or test_type == 'all':
            # Test new video webhook with sample data
            try:
                sample_video = Video.query.first()
                sample_category = Category.query.first()
                if sample_video and sample_category:
                    video_test = send_new_video_webhook(sample_video, sample_category)
                    test_results['video_webhook'] = video_test
                else:
                    test_results['video_webhook'] = False
                    test_results['video_error'] = "No sample video/category found"
            except Exception as e:
                test_results['video_webhook'] = False
                test_results['video_error'] = str(e)
        
        if test_type == 'stream' or test_type == 'all':
            # Test stream webhook with mock data
            try:
                mock_stream = type('MockStream', (), {
                    'title': 'Test Live Trading Session',
                    'description': 'This is a test stream notification',
                    'streamer_name': 'Ray',
                    'stream_type': 'trading',
                    'started_at': datetime.utcnow(),
                    'is_recording': True,
                    'viewer_count': 0
                })()
                
                stream_test = send_live_stream_webhook(mock_stream, "started")
                test_results['stream_webhook'] = stream_test
            except Exception as e:
                test_results['stream_webhook'] = False
                test_results['stream_error'] = str(e)
        
        if test_type == 'signal' or test_type == 'all':
            # Test trading signal webhook with mock data (no sensitive details)
            try:
                mock_signal = type('MockSignal', (), {
                    'trader_name': 'Ray',
                    'pair_name': 'EURUSD',
                    'trade_type': 'Buy',
                    'date': datetime.utcnow().date(),
                    'linked_video_id': None
                })()
                
                signal_test = send_trading_signal_webhook(mock_signal)
                test_results['signal_webhook'] = signal_test
            except Exception as e:
                test_results['signal_webhook'] = False
                test_results['signal_error'] = str(e)
        
        if test_type == 'completion' or test_type == 'all':
            # Test course completion webhook with mock data
            try:
                mock_user = type('MockUser', (), {
                    'username': 'TestUser'
                })()
                
                mock_category = type('MockCategory', (), {
                    'name': 'Forex Fundamentals'
                })()
                
                mock_stats = {
                    'completed': 8,
                    'total': 8
                }
                
                completion_test = send_course_completion_webhook(mock_user, mock_category, mock_stats)
                test_results['completion_webhook'] = completion_test
            except Exception as e:
                test_results['completion_webhook'] = False
                test_results['completion_error'] = str(e)
        
        overall_success = any(test_results.get(key) for key in test_results if 'error' not in key)
        
        return jsonify({
            'success': overall_success,
            'message': f'Discord webhook test completed for: {test_type}',
            'results': test_results
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500
        
    
@app.route('/api/stream/status')
@login_required
def api_stream_status():
    active_streams = Stream.query.filter_by(is_active=True).all()
    
    if not active_streams:
        return jsonify({'active': False, 'streams': []})
    
    streams_data = []
    for stream in active_streams:
        active_viewers = StreamViewer.query.filter_by(
            stream_id=stream.id,
            is_active=True
        ).count()
        
        stream.viewer_count = active_viewers
        
        streams_data.append({
            'id': stream.id,
            'title': stream.title,
            'description': stream.description,
            'streamer_name': stream.streamer_name,
            'stream_type': stream.stream_type,
            'viewer_count': stream.viewer_count,
            'started_at': stream.started_at.isoformat() if stream.started_at else None,
            'is_recording': stream.is_recording,
            'created_by': stream.created_by,
            'stream_color': stream.creator.stream_color if stream.creator else '#10B981'
        })
    
    db.session.commit()
    
    return jsonify({
        'active': True,
        'count': len(active_streams),
        'streams': streams_data
    })

@app.route('/api/stream/recording/start', methods=['POST'])
@login_required
def api_start_stream_recording():
    """Start recording for an active stream"""
    if not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    data = request.get_json()
    stream_id = data.get('stream_id')
    
    if stream_id:
        stream = Stream.query.filter_by(
            id=stream_id,
            created_by=current_user.id,
            is_active=True
        ).first()
    else:
        stream = Stream.query.filter_by(
            created_by=current_user.id,
            is_active=True
        ).first()
    
    if not stream:
        return jsonify({'error': 'No active stream found or access denied'}), 400
    
    if stream.is_recording:
        return jsonify({'error': 'Recording already active'}), 400
    
    # Start recording
    recording_result = start_livekit_egress_recording(
        room_name=stream.room_name,
        stream_id=stream.id,
        streamer_name=stream.streamer_name
    )
    
    if recording_result and recording_result.get('success'):
        stream.is_recording = True
        stream.recording_id = recording_result.get('egress_id')
        db.session.commit()
        
        print(f"✅ Recording started for stream {stream.id}")
        
        # Notify via WebSocket
        if socketio:
            socketio.emit('recording_started', {
                'stream_id': stream.id,
                'message': 'Recording has started',
                'egress_id': recording_result.get('egress_id')
            }, room=f"stream_{stream.id}")
        
        return jsonify({
            'success': True,
            'message': 'Recording started successfully',
            'egress_id': recording_result.get('egress_id')
        })
    else:
        return jsonify({
            'error': 'Failed to start recording',
            'details': recording_result.get('error') if recording_result else 'Unknown error'
        }), 500
        
@app.route('/api/stream/recording/stop', methods=['POST'])
@login_required
def api_stop_recording():
    if not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    data = request.get_json()
    stream_id = data.get('stream_id')
    recording_id = data.get('recording_id')  # You'd need to track this
    
    if stream_id:
        stream = Stream.query.filter_by(
            id=stream_id,
            created_by=current_user.id,
            is_active=True
        ).first()
    else:
        stream = Stream.query.filter_by(
            created_by=current_user.id,
            is_active=True
        ).first()
    
    if not stream:
        return jsonify({'error': 'No active stream found or access denied'}), 400
    
    # Stop LiveKit recording
    if recording_id:
        success = stop_livekit_recording(recording_id)
        if success:
            stream.is_recording = False
            
            # Generate expected recording URL
            if stream.streamer_name:
                recording_url = f"s3://{app.config.get('STREAM_RECORDINGS_BUCKET')}/livestream-recordings/livekit/{datetime.utcnow().strftime('%Y/%m/%d')}/{stream.streamer_name}-stream-{stream.id}-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.mp4"
                stream.recording_url = recording_url
            
            db.session.commit()
            
            return jsonify({
                'success': True, 
                'message': f'Recording stopped for {stream.streamer_name}\'s stream',
                'recording_url': stream.recording_url
            })
    
    return jsonify({'error': 'Failed to stop recording'}), 500

# Test route to verify LiveKit setup
@app.route('/debug/livekit-setup')
@login_required
def debug_livekit_setup():
    """Check LiveKit configuration and connection"""
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403
    
    try:
        livekit_api_key = app.config.get('LIVEKIT_API_KEY')
        livekit_api_secret = app.config.get('LIVEKIT_API_SECRET')
        livekit_url = app.config.get('LIVEKIT_URL')
        
        test_result = {
            'livekit_url': livekit_url,
            'api_key_configured': bool(livekit_api_key),
            'api_secret_configured': bool(livekit_api_secret),
            'configuration_complete': bool(livekit_api_key and livekit_api_secret and livekit_url)
        }
        
        # Test API connection
        if test_result['configuration_complete']:
            try:
                lk_api = init_livekit_api()
                if lk_api:
                    # Try to list rooms (this will test the connection)
                    rooms = lk_api.room.list_rooms(api.ListRoomsRequest())
                    test_result['api_connection_successful'] = True
                    test_result['existing_rooms_count'] = len(rooms)
                    test_result['message'] = '✅ LiveKit is properly configured and connected!'
                else:
                    test_result['api_connection_successful'] = False
                    test_result['message'] = '❌ Failed to initialize LiveKit API client'
            except Exception as e:
                test_result['api_connection_successful'] = False
                test_result['connection_error'] = str(e)
                test_result['message'] = f'❌ LiveKit API connection failed: {str(e)}'
        else:
            test_result['message'] = '⚠️ LiveKit configuration incomplete. Check environment variables.'
        
        # Test token generation
        if test_result.get('api_connection_successful'):
            try:
                test_token = generate_livekit_token(
                    'test-room',
                    'test-user',
                    'Test User',
                    is_publisher=False
                )
                test_result['token_generation_successful'] = bool(test_token)
                if test_token:
                    test_result['sample_token'] = test_token[:50] + '...'
            except Exception as e:
                test_result['token_generation_successful'] = False
                test_result['token_error'] = str(e)
        
        return jsonify(test_result)
        
    except Exception as e:
        return jsonify({
            'error': str(e),
            'message': 'LiveKit test failed'
        }), 500

# FIXED: Enhanced application startup with better error handling
def initialize_app():
    """Initialize the application with comprehensive error handling"""
    try:
        with app.app_context():
            # Create database tables
            db.create_all()
            print("✓ Database tables created successfully")
            
            # Migrate user timezones if needed
            try:
                users_without_timezone = User.query.filter(
                    db.or_(User.timezone.is_(None), User.timezone == '')
                ).all()
                
                for user in users_without_timezone:
                    user.timezone = 'America/Chicago'
                
                if users_without_timezone:
                    db.session.commit()
                    print(f"✓ Updated {len(users_without_timezone)} users with default timezone")
            except Exception as e:
                print(f"⚠ Timezone migration warning: {e}")
                db.session.rollback()
            
            # Create admin user if needed
            try:
                admin_user = User.query.filter_by(username='admin').first()
                if not admin_user:
                    admin_user = User(
                        username='admin',
                        email='ray@tgfx-academy.com',
                        password_hash=generate_password_hash('admin123!345gdfb3f35'),
                        is_admin=True,
                        display_name='Ray',
                        can_stream=True,
                        stream_color='#10B981',
                        timezone='America/Chicago'
                    )
                    db.session.add(admin_user)
                    db.session.commit()
                    print("✓ Admin user created")
                else:
                    if not admin_user.timezone:
                        admin_user.timezone = 'America/Chicago'
                        db.session.commit()
                    print("✓ Admin user exists")
            except Exception as e:
                print(f"⚠ Admin user setup warning: {e}")
                db.session.rollback()
                
    except Exception as e:
        print(f"❌ Application initialization error: {e}")
        return False
    
    return True

# FIXED: Enhanced signal handlers for graceful shutdown
def signal_handler(signum, frame):
    """Handle shutdown signals gracefully"""
    print(f"\n📡 Received signal {signum}, shutting down gracefully...")
    
    # Close database connections
    if db:
        try:
            db.session.close()
            db.engine.dispose()
            print("✓ Database connections closed")
        except Exception as e:
            print(f"⚠ Database cleanup warning: {e}")
    
    # Clean up WebSocket connections
    if socketio and 'active_connections' in globals():
        print(f"🧹 Cleaning up {len(active_connections)} WebSocket connections")
        active_connections.clear()
        stream_rooms.clear()
    
    sys.exit(0)

# Register signal handlers
signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

# CONTEXT PROCESSORS
@app.context_processor
def utility_processor():
    return dict(
        user_can_access_video=user_can_access_video,
        get_category_progress=get_category_progress,
        get_course_tags=get_course_tags,
        get_total_duration=get_total_duration
    )

@app.context_processor
def inject_user_timezone():
    if current_user.is_authenticated:
        return dict(user_timezone=current_user.timezone or 'America/Chicago')
    return dict(user_timezone='America/Chicago')

@app.context_processor
def inject_datetime():
    """Make datetime available in all templates"""
    return dict(datetime=datetime)

# Initialize configuration
config_class.init_app(app)

if __name__ == '__main__':
    # Replace your existing initialize_app() call with:
    if not initialize_complete_app():
        print("Application initialization failed, exiting...")
        sys.exit(1)
    
    # Get port from environment (for Heroku deployment)
    port = int(os.environ.get('PORT', 5000))
    debug_mode = os.environ.get('FLASK_ENV') == 'development'
    
    print("🚀 Starting Flask app with LiveKit streaming...")
    if socketio:
        print(f"🔌 WebSocket endpoints available at /socket.io/")
        print(f"🎬 LiveKit livestream with real-time audio and video ready!")
    else:
        print("⚠ Running without WebSocket support")
    print(f"🌐 Running on port {port}")
    
    try:
        if socketio:
            socketio.run(
                app, 
                debug=debug_mode,
                host='0.0.0.0', 
                port=port,
                use_reloader=False  # Disable reloader in production
            )
        else:
            # Fallback to regular Flask if SocketIO failed
            app.run(
                debug=debug_mode,
                host='0.0.0.0',
                port=port,
                use_reloader=False
            )
    except Exception as e:
        print(f"❌ Server startup failed: {e}")
        sys.exit(1)
