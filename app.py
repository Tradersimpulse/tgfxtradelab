# FIXED: Import order and gevent configuration for better compatibility
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

# Association table for many-to-many relationship between videos and tags
video_tags = db.Table('video_tags',
    db.Column('video_id', db.Integer, db.ForeignKey('videos.id'), primary_key=True),
    db.Column('tag_id', db.Integer, db.ForeignKey('tags.id'), primary_key=True)
)


# Models - MySQL Optimized
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

def send_discord_webhook(title, description, color=5814783, fields=None, thumbnail_url=None):
    """
    Send a Discord webhook notification
    """
    try:
        if not APP_UPDATE_DISCORD_WEBHOOK_URL:
            print("⚠️ Discord webhook URL not configured")
            return False
        
        embed = {
            "title": title,
            "description": description,
            "color": color,
            "timestamp": datetime.utcnow().isoformat(),
            "footer": {
                "text": "TGFX Trade Lab",
                "icon_url": "https://tgfx-tradelab.s3.amazonaws.com/logo.png"
            }
        }
        
        if fields:
            embed["fields"] = fields
            
        if thumbnail_url:
            embed["thumbnail"] = {"url": thumbnail_url}
        
        webhook_data = {
            "embeds": [embed],
            "username": "TGFX Trade Lab",
            "avatar_url": "https://tgfx-tradelab.s3.amazonaws.com/logo.png"
        }
        
        response = requests.post(
            APP_UPDATE_DISCORD_WEBHOOK_URL,
            json=webhook_data,
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        
        if response.status_code == 204:
            print(f"✅ Discord webhook sent: {title}")
            return True
        else:
            print(f"❌ Discord webhook failed: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ Discord webhook error: {e}")
        return False

def send_new_video_webhook(video, category):
    """Send Discord notification for new video"""
    fields = [
        {
            "name": "📁 Category",
            "value": category.name,
            "inline": True
        },
        {
            "name": "🎯 Access",
            "value": "Free" if video.is_free else "Premium",
            "inline": True
        },
        {
            "name": "⏱️ Duration",
            "value": f"{video.duration // 60} minutes" if video.duration else "Unknown",
            "inline": True
        }
    ]
    
    # Add tags if available
    if hasattr(video, 'tags') and video.tags:
        tag_names = [tag.name for tag in video.tags[:3]]  # Show first 3 tags
        fields.append({
            "name": "🏷️ Tags",
            "value": ", ".join(tag_names),
            "inline": True
        })
    
    description = f"🎥 **New video available in {category.name}**\n\n"
    if video.description:
        # Limit description to 150 characters for Discord
        desc_preview = video.description[:150] + "..." if len(video.description) > 150 else video.description
        description += f"{desc_preview}\n\n"
    
    description += f"{'🆓 **Free Access**' if video.is_free else '💎 **Premium Content**'}"
    
    send_discord_webhook(
        title=f"📹 {video.title}",
        description=description,
        color=3447003,  # Blue color for videos
        fields=fields,
        thumbnail_url=video.thumbnail_url
    )

def send_live_stream_webhook(stream, action="started"):
    """Send Discord notification for live stream events"""
    if action == "started":
        emoji = "🔴"
        color = 15158332  # Red color for live streams
        title = f"{emoji} {stream.streamer_name} is now LIVE!"
        description = f"**{stream.title}**\n\n"
        
        if stream.description:
            desc_preview = stream.description[:150] + "..." if len(stream.description) > 150 else stream.description
            description += f"{desc_preview}\n\n"
        
        description += "🎮 **Join the live stream now!**"
        
        fields = [
            {
                "name": "👤 Streamer",
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
                "value": stream.started_at.strftime("%I:%M %p UTC") if stream.started_at else "Now",
                "inline": True
            }
        ]
        
        if stream.is_recording:
            fields.append({
                "name": "📹 Recording",
                "value": "✅ Yes",
                "inline": True
            })
    
    elif action == "ended":
        emoji = "⏹️"
        color = 9807270  # Gray color for ended streams
        title = f"{emoji} {stream.streamer_name}'s stream has ended"
        description = f"**{stream.title}**\n\n"
        
        # Calculate duration
        if stream.started_at and stream.ended_at:
            duration = stream.ended_at - stream.started_at
            duration_minutes = int(duration.total_seconds() / 60)
            description += f"⏱️ **Duration:** {duration_minutes} minutes\n"
        
        if stream.recording_url:
            description += "📼 **Recording saved and added to course library**"
        
        fields = [
            {
                "name": "👤 Streamer",
                "value": stream.streamer_name,
                "inline": True
            },
            {
                "name": "👥 Peak Viewers",
                "value": str(stream.viewer_count),
                "inline": True
            }
        ]
    
    send_discord_webhook(
        title=title,
        description=description,
        color=color,
        fields=fields
    )

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

def get_trader_defaults(user):
    """
    Get default trading pair and name for auto-fill
    """
    if not user:
        return "Trader", "EURUSD"
    
    # Map users to their default trading pairs
    trader_defaults = {
        'jordan': ('Jordan', 'XAUUSD'),
        'jwill24': ('Jordan', 'XAUUSD'),
        'admin': ('Ray', 'EURUSD'),
        'ray': ('Ray', 'EURUSD')
    }
    
    username = user.username.lower()
    return trader_defaults.get(username, (user.display_name or user.username, 'EURUSD'))

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
        'monthly': 'Monthly ($29/month)',
        'annual': 'Annual ($299/year)',
        'lifetime': 'Lifetime Access ($499 one-time)'
    }
    
    return plan_map.get(self.subscription_plan, 'Unknown Plan')

def is_lifetime_subscriber(self):
    """Check if user has lifetime subscription"""
    return self.subscription_plan == 'lifetime' and self.has_subscription
        
# Custom Jinja2 filters
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

@app.route('/api/user/upgrade-to-lifetime', methods=['POST'])
@login_required
def api_upgrade_to_lifetime():
    """Upgrade user to lifetime plan"""
    try:
        if current_user.subscription_plan == 'lifetime':
            return jsonify({'error': 'Already on lifetime plan'}), 400
        
        # For lifetime upgrades, we'll redirect to a new checkout session
        # since it's a one-time payment rather than a subscription modification
        
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
        
        # If user has existing subscription, we'll need to cancel it after lifetime purchase
        existing_subscription_id = current_user.stripe_subscription_id
        
        # Create checkout session for lifetime purchase
        session = stripe.checkout.Session.create(
            customer=customer_id,
            payment_method_types=['card'],
            line_items=[{
                'price': lifetime_price_id,
                'quantity': 1,
            }],
            mode='payment',  # One-time payment, not subscription
            success_url=f"{request.host_url}subscription-success?session_id={{CHECKOUT_SESSION_ID}}&plan=lifetime",
            cancel_url=f"{request.host_url}manage-subscription?upgrade_canceled=true",
            metadata={
                'user_id': current_user.id,
                'plan_type': 'lifetime',
                'cancel_subscription_id': existing_subscription_id if existing_subscription_id else ''
            }
        )
        
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

def calculate_analytics_summary():
    """Calculate summary analytics - updated for lifetime"""
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
        # Note: Lifetime doesn't contribute to MRR since it's one-time payment
        total_mrr = monthly_mrr + annual_mrr
        
        # Calculate lifetime revenue impact
        lifetime_revenue = lifetime_subscribers * 499
        
        # Churn rate (last 30 days) - lifetime users can't churn
        thirty_days_ago = datetime.utcnow() - timedelta(days=30)
        canceled_last_30_days = SubscriptionEvent.query.filter(
            SubscriptionEvent.event_type.like('%cancel%'),
            SubscriptionEvent.created_at >= thirty_days_ago
        ).count()
        
        # Only count non-lifetime users for churn calculation
        non_lifetime_subscribers = premium_users - lifetime_subscribers
        churn_rate = (canceled_last_30_days / non_lifetime_subscribers * 100) if non_lifetime_subscribers > 0 else 0
        
        return {
            'total_revenue': f"{total_revenue:.2f}",
            'revenue_change': '+12.5',
            'mrr': f"{total_mrr:.2f}",
            'mrr_change': '+8.3',
            'new_subscribers': monthly_subscribers + annual_subscribers + lifetime_subscribers,
            'subscribers_change': '+15.2',
            'churn_rate': f"{churn_rate:.1f}",
            'churn_change': '-2.1',
            'total_subscribers': premium_users,
            'lifetime_subscribers': lifetime_subscribers,
            'lifetime_revenue': f"{lifetime_revenue:.2f}",
            'prev_total_subscribers': premium_users - 5,
            'active_subscriptions': premium_users,
            'prev_active_subscriptions': premium_users - 3,
            'canceled_subscriptions': canceled_last_30_days,
            'prev_canceled_subscriptions': canceled_last_30_days + 2,
            'avg_order_value': f"{(total_revenue / total_users):.2f}" if total_users > 0 else "0.00",
            'prev_avg_order_value': "28.50",
            'aov_change': '+5.2',
            'active_subs_change': '+10.5',
            'canceled_subs_change': '-15.3'
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
    """Create a Stripe Checkout session for subscription - updated for lifetime"""
    try:
        data = request.get_json()
        plan_type = data.get('plan_type', 'monthly')
        
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
            line_items = [{
                'price': price_id,
                'quantity': 1,
            }]
        else:
            mode = 'subscription'  # Recurring subscription
            line_items = [{
                'price': price_id,
                'quantity': 1,
            }]
        
        # Create checkout session
        session = stripe.checkout.Session.create(
            customer=customer_id,
            payment_method_types=['card'],
            line_items=line_items,
            mode=mode,
            success_url=f"{request.host_url}subscription-success?session_id={{CHECKOUT_SESSION_ID}}&plan={plan_type}",
            cancel_url=f"{request.host_url}subscription?canceled=true",
            metadata={
                'user_id': current_user.id,
                'plan_type': plan_type
            }
        )
        
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
    Start LiveKit Egress recording with proper Bearer token authentication
    """
    try:
        import requests
        from datetime import datetime
        
        # Generate API token
        api_token = generate_livekit_api_token()
        if not api_token:
            print("❌ Failed to generate API token")
            return {'success': False, 'error': 'Failed to generate API token'}
        
        # Get LiveKit URL
        livekit_url = app.config.get('LIVEKIT_URL')
        
        # AWS S3 configuration
        aws_access_key = app.config.get('AWS_ACCESS_KEY_ID')
        aws_secret_key = app.config.get('AWS_SECRET_ACCESS_KEY')
        aws_region = app.config.get('AWS_REGION', 'us-east-1')
        s3_bucket = app.config.get('STREAM_RECORDINGS_BUCKET', 'tgfx-tradelab')
        prefix = app.config.get('STREAM_RECORDINGS_PREFIX', 'livestream-recordings/')
        
        print(f"🔹 Starting LiveKit Egress recording")
        print(f"  Room: {room_name}")
        print(f"  Streamer: {streamer_name}")
        
        if not all([aws_access_key, aws_secret_key, s3_bucket]):
            print("❌ AWS credentials missing")
            return {'success': False, 'error': 'AWS credentials not configured'}
        
        # Generate S3 path
        timestamp = datetime.utcnow().strftime('%Y%m%d-%H%M%S')
        date_folder = datetime.utcnow().strftime('%Y/%m/%d')
        filename = f"{streamer_name}-stream-{stream_id}-{timestamp}.mp4"
        s3_key = f"{prefix}{streamer_name}/{date_folder}/{filename}"
        
        print(f"📁 Recording will be saved to: s3://{s3_bucket}/{s3_key}")
        
        # Extract API URL from WebSocket URL
        if '.livekit.cloud' in livekit_url:
            # For LiveKit Cloud: wss://tgfxtradelab-073fad95626o.livekit.cloud
            # Convert to: https://tgfxtradelab-073fad95626o.livekit.cloud
            api_url = livekit_url.replace('wss://', 'https://').replace('ws://', 'https://')
        else:
            api_url = livekit_url.replace('wss://', 'https://').replace('ws://', 'http://')
        
        # Create headers with Bearer token
        headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json"
        }
        
        # Create Egress request
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
            "preset": "H264_1080P_30"  # Valid preset for LiveKit
        }
        
        # Make API request
        endpoint = f"{api_url}/twirp/livekit.Egress/StartRoomCompositeEgress"
        
        print(f"🔗 Calling LiveKit Egress API: {endpoint}")
        print(f"🔑 Using Bearer token authentication")
        
        response = requests.post(
            endpoint,
            json=egress_request,
            headers=headers,
            timeout=10
        )
        
        print(f"📡 Response Status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            egress_id = data.get("egress_id")
            
            if egress_id:
                print(f"✅ Recording started successfully!")
                print(f"🔑 Egress ID: {egress_id}")
                print(f"📁 S3 Path: s3://{s3_bucket}/{s3_key}")
                
                return {
                    'success': True,
                    'egress_id': egress_id,
                    's3_path': f"s3://{s3_bucket}/{s3_key}",
                    's3_url': f"https://{s3_bucket}.s3.{aws_region}.amazonaws.com/{s3_key}",
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
    Stop LiveKit Egress recording with Bearer token authentication
    """
    try:
        if not egress_id:
            print("⚠️ No egress_id provided")
            return {'success': False, 'error': 'No recording to stop'}
        
        import requests
        
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
        
        # First, get the egress info to retrieve the file path
        info_response = requests.post(
            f"{api_url}/twirp/livekit.Egress/ListEgress",
            json={"egress_id": egress_id},
            headers=headers,
            timeout=10
        )
        
        recording_url = None
        if info_response.status_code == 200:
            egress_list = info_response.json().get('items', [])
            if egress_list:
                egress_info = egress_list[0]
                # Extract the S3 path from the egress info
                if 'file' in egress_info and 'filepath' in egress_info['file']:
                    s3_key = egress_info['file']['filepath']
                    aws_region = app.config.get('AWS_REGION', 'us-east-1')
                    s3_bucket = app.config.get('STREAM_RECORDINGS_BUCKET', 'tgfx-tradelab')
                    recording_url = f"https://{s3_bucket}.s3.{aws_region}.amazonaws.com/{s3_key}"
                    print(f"📁 Recording file path: {s3_key}")
        
        # Stop recording
        response = requests.post(
            f"{api_url}/twirp/livekit.Egress/StopEgress",
            json={"egress_id": egress_id},
            headers=headers,
            timeout=10
        )
        
        if response.status_code == 200:
            print(f"✅ Recording stopped: {egress_id}")
            return {
                'success': True, 
                'egress_id': egress_id,
                'recording_url': recording_url
            }
        else:
            print(f"❌ Failed to stop recording: {response.status_code} - {response.text}")
            return {'success': False, 'error': f"API error: {response.status_code}"}
            
    except Exception as e:
        print(f"❌ Error stopping recording: {e}")
        return {'success': False, 'error': str(e)}

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
    form = UserSettingsForm(obj=current_user)
    
    if form.validate_on_submit():
        # Validate timezone
        try:
            pytz.timezone(form.timezone.data)  # This will raise an exception if invalid
            current_user.timezone = form.timezone.data
            db.session.commit()
            flash('Settings updated successfully!', 'success')
            return redirect(url_for('user_settings'))
        except pytz.exceptions.UnknownTimeZoneError:
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
        return redirect(url_for('subscription'))
    
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
    """Manually toggle video completion status"""
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
        
        # Create activity if status changed
        if old_completed != completed:
            if completed:
                create_user_activity(
                    current_user.id, 
                    'video_completed', 
                    f'Completed "{video.title}"'
                )
            else:
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

@app.route('/subscription')
@login_required
def subscription():
    stripe_key = app.config.get('STRIPE_PUBLISHABLE_KEY')
    
    if not stripe_key:
        flash('Payment system is currently unavailable. Please try again later.', 'warning')
        return redirect(url_for('dashboard'))
    
    return render_template('subscription.html', stripe_key=stripe_key)

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
            send_new_video_webhook(video, category)
        except Exception as e:
            print(f"Failed to send Discord webhook for new video: {e}")
        
        # Broadcast notification about new video
        broadcast_notification(
            'New Video Available!',
            f'Check out our latest video: "{video.title}"',
            'new_video'
        )
        
        flash('Video added successfully!', 'success')
        return redirect(url_for('admin_videos'))
    
    return render_template('admin/video_form.html', form=form, title='Add Video')
    
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
        is_recording=False,  # Will be updated if recording starts
        started_at=datetime.utcnow(),
        created_by=current_user.id,
        streamer_name=streamer_name,
        stream_type=stream_type
    )
    
    db.session.add(stream)
    db.session.commit()
    
    # Try to start auto-recording for admin streams
    recording_started = False
    recording_info = None
    
    if current_user.is_admin:
        try:
            print(f"🎬 Attempting auto-recording for {streamer_name}'s stream...")
            
            recording_info = start_livekit_cloud_recording(
                room_name=room_name,
                stream_id=stream.id,
                streamer_name=streamer_name
            )
            
            if recording_info and recording_info.get('recording_id'):
                stream.is_recording = True
                stream.recording_id = recording_info.get('recording_id')
                db.session.commit()
                recording_started = True
                print(f"✅ Auto-recording started for {streamer_name}'s stream")
            else:
                print("⚠️ Recording function returned None or no recording_id")
                
        except Exception as e:
            print(f"⚠️ Auto-recording failed: {e}")
            # Continue with stream even if recording fails
    
    # 🆕 DISCORD WEBHOOK: Send Discord notification for new live stream
    try:
        send_live_stream_webhook(stream, action="started")
    except Exception as e:
        print(f"Failed to send Discord webhook for live stream: {e}")
    
    # Broadcast notification about new stream via WebSocket
    if socketio:
        socketio.emit('new_stream_started', {
            'stream_id': stream.id,
            'title': stream.title,
            'streamer_name': stream.streamer_name,
            'message': f'{streamer_name} is now live!',
            'is_recording': recording_started
        })
    
    # Also send traditional notification
    broadcast_notification(
        'Live Stream Started!',
        f'{streamer_name} is now live: "{title}"' + (' 🔴 Recording' if recording_started else ''),
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
            'is_recording': recording_started
        }
    })
    
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
    if not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    try:
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
        
        success = send_discord_webhook(
            title="🎉 Discord Integration Test",
            description="**Discord webhooks are now active!**\n\nYou'll receive notifications for:\n• 📹 New videos\n• 🔴 Live streams\n• 📚 New courses",
            color=5763719,  # Gold color
            fields=test_fields
        )
        
        if success:
            return jsonify({
                'success': True,
                'message': 'Discord webhook test sent successfully!'
            })
        else:
            return jsonify({
                'success': False,
                'message': 'Discord webhook test failed. Check console for errors.'
            })
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500
        
    
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
    
    # Get recording URL before stopping
    if stream.is_recording and stream.recording_id:
        print(f"🔴 Getting recording info for egress {stream.recording_id}...")
        
        # First, get the recording URL from egress info
        recording_info = get_egress_info(stream.recording_id)
        if recording_info and recording_info.get('recording_url'):
            recording_url = recording_info['recording_url']
            print(f"📁 Retrieved recording URL: {recording_url}")
        
        # Now stop the recording
        print(f"⏹️ Stopping recording {stream.recording_id}...")
        stop_result = stop_livekit_egress_recording(stream.recording_id)
        
        if stop_result.get('success'):
            print(f"✅ Recording stopped successfully")
            
            # Use URL from stop result if available, otherwise use the one we got earlier
            if stop_result.get('recording_url'):
                recording_url = stop_result['recording_url']
            elif not recording_url and recording_info and recording_info.get('filepath'):
                # Build URL from filepath
                s3_key = recording_info['filepath']
                aws_region = app.config.get('AWS_REGION', 'us-east-1')
                s3_bucket = app.config.get('STREAM_RECORDINGS_BUCKET', 'tgfx-tradelab')
                recording_url = f"https://{s3_bucket}.s3.{aws_region}.amazonaws.com/{s3_key}"
            
            # Save the recording URL to the database
            stream.recording_url = recording_url
            recording_saved = True
            
            print(f"💾 Saving recording URL to database: {recording_url}")
    
    # Calculate stream duration before updating ended_at
    duration_minutes = 0
    if stream.started_at:
        stream.ended_at = datetime.utcnow()
        duration = stream.ended_at - stream.started_at
        duration_minutes = int(duration.total_seconds() / 60)
    
    # AUTO-ADD TO LIVE TRADING SESSIONS COURSE
    if recording_saved and recording_url:
        try:
            # Find or create the Live Trading Sessions category
            live_sessions_category = Category.query.filter_by(
                name='Live Trading Sessions'
            ).first()
            
            if not live_sessions_category:
                # Create the category if it doesn't exist
                live_sessions_category = Category(
                    name='Live Trading Sessions',
                    description='Recorded live trading sessions from our professional traders',
                    order_index=1  # Put it at the top
                )
                db.session.add(live_sessions_category)
                db.session.flush()
                print(f"📁 Created Live Trading Sessions category")
            
            # Create video title with trader name and date
            video_date = stream.started_at.strftime('%B %d, %Y')  # August 15, 2025
            video_title = f"{stream.streamer_name} - {video_date}"
            
            # Create description
            video_description = f"Live trading session with {stream.streamer_name}\n"
            video_description += f"Duration: {duration_minutes} minutes\n"
            video_description += f"Stream Type: {stream.stream_type}\n"
            if stream.description:
                video_description += f"\n{stream.description}"
            
            # Get the next order index for this category
            max_order = db.session.query(db.func.max(Video.order_index)).filter_by(
                category_id=live_sessions_category.id
            ).scalar() or 0
            
            # Create the video entry
            new_video = Video(
                title=video_title,
                description=video_description,
                s3_url=recording_url,
                thumbnail_url=None,  # You could generate a thumbnail later
                duration=duration_minutes * 60,  # Store in seconds
                is_free=False,  # Set to True if you want free access
                order_index=max_order + 1,
                category_id=live_sessions_category.id,
                created_at=datetime.utcnow()
            )
            db.session.add(new_video)
            db.session.flush()
            
            # Add tags for the video
            trader_tag = get_or_create_tag(stream.streamer_name)
            live_tag = get_or_create_tag('Live Session')
            trading_tag = get_or_create_tag('Live Trading')
            
            new_video.tags.append(trader_tag)
            new_video.tags.append(live_tag)
            new_video.tags.append(trading_tag)
            
            # Add date tag (e.g., "August 2025")
            month_year_tag = get_or_create_tag(stream.started_at.strftime('%B %Y'))
            new_video.tags.append(month_year_tag)
            
            # Add stream type tag
            if stream.stream_type:
                type_tag = get_or_create_tag(stream.stream_type.replace('_', ' ').title())
                new_video.tags.append(type_tag)
            
            video_created = True
            print(f"📹 Created video entry: {video_title}")
            print(f"📺 Video ID: {new_video.id}")
            print(f"🔗 Video URL: {recording_url}")
            
            # Create notification for users
            broadcast_notification(
                'New Live Session Recording Available!',
                f"{stream.streamer_name}'s live trading session from {video_date} is now available to watch.",
                'new_video',
                target_users='all'
            )
            
        except Exception as e:
            print(f"❌ Error creating video entry: {e}")
            db.session.rollback()
            video_created = False
    
    # Notify all viewers BEFORE stopping the stream
    room_id = f"stream_{stream.id}"
    if socketio and room_id in stream_rooms:
        end_message = {
            'stream_id': stream.id,
            'message': f'{stream.streamer_name} has ended the stream',
            'redirect': True
        }
        
        if recording_url:
            end_message['recording_url'] = recording_url
            end_message['recording_message'] = 'Recording has been saved and added to courses'
        
        socketio.emit('stream_ending', {
            'stream_id': stream.id,
            'message': 'Stream is ending in 3 seconds...'
        }, room=room_id)
        
        time.sleep(1)
        
        socketio.emit('stream_ended', end_message, room=room_id)
        
        if room_id in stream_rooms:
            del stream_rooms[room_id]
    
    # Delete LiveKit room
    if stream.room_name:
        delete_livekit_room(stream.room_name)
    
    # Update database
    stream.is_active = False
    stream.is_recording = False
    
    # Update all viewer records
    StreamViewer.query.filter_by(stream_id=stream.id, is_active=True).update({
        'is_active': False,
        'left_at': datetime.utcnow()
    })
    
    # Commit all changes
    db.session.commit()
    print(f"✅ Stream {stream.id} ended. Recording URL saved: {recording_url}")
    
    response_data = {
        'success': True,
        'message': f'{stream.streamer_name}\'s stream ended',
        'duration_minutes': duration_minutes
    }
    
    if recording_saved and recording_url:
        response_data['recording'] = {
            'saved': True,
            'url': recording_url,
            'message': f'Recording saved and added to course library (Duration: {duration_minutes} minutes)',
            'video_created': video_created
        }
    
    return jsonify(response_data)
    
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
def api_start_recording():
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
    
    # Start LiveKit recording
    if stream.room_name:
        recording_info = start_livekit_recording(stream.room_name)
        if recording_info:
            stream.is_recording = True
            # You might want to store recording_info.id for later stopping
            db.session.commit()
            
            return jsonify({
                'success': True, 
                'message': f'Recording started for {stream.streamer_name}\'s stream',
                'recording_id': recording_info.id if recording_info else None
            })
    
    return jsonify({'error': 'Failed to start recording'}), 500

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
    # Initialize the application
    if not initialize_app():
        print("❌ Application initialization failed, exiting...")
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
