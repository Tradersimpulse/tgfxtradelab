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

# LiveKit imports - NEW
from livekit import api
import jwt

# FIXED: Better error handling for database connections
import logging
import signal
import threading

# Initialize Flask app
app = Flask(__name__)

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
    has_subscription = db.Column(db.Boolean, default=False, nullable=False)
    subscription_expires = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    
    # Stream-related fields
    display_name = db.Column(db.String(100), nullable=True)
    can_stream = db.Column(db.Boolean, default=False, nullable=False)
    stream_color = db.Column(db.String(7), default='#10B981', nullable=False)
    
    # Timezone field - NEW
    timezone = db.Column(db.String(50), default='America/Chicago', nullable=False)
    
    # Relationships
    progress = db.relationship('UserProgress', backref='user', lazy=True, cascade='all, delete-orphan')
    favorites = db.relationship('UserFavorite', backref='user', lazy=True, cascade='all, delete-orphan')
    created_streams = db.relationship('Stream', backref='creator', lazy=True, cascade='all, delete-orphan')
    activities = db.relationship('UserActivity', backref='user', lazy=True, cascade='all, delete-orphan')
    notifications = db.relationship('Notification', backref='user', lazy=True, cascade='all, delete-orphan')

class Category(db.Model):
    __tablename__ = 'categories'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(100), nullable=False, index=True)
    description = db.Column(db.Text, nullable=True)
    image_url = db.Column(db.String(500), nullable=True)
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
    image_url = StringField('Category Image URL', validators=[Optional()])
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
    return User.query.get(int(user_id))

# Helper Functions
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
    """Initialize LiveKit API client"""
    try:
        livekit_api_key = app.config.get('LIVEKIT_API_KEY')
        livekit_api_secret = app.config.get('LIVEKIT_API_SECRET')
        livekit_url = app.config.get('LIVEKIT_URL')
        
        if not all([livekit_api_key, livekit_api_secret, livekit_url]):
            return None
            
        return api.LiveKitAPI(livekit_url, livekit_api_key, livekit_api_secret)
    except Exception as e:
        print(f"Error initializing LiveKit API: {e}")
        return None

def create_livekit_room(room_name, streamer_name):
    """Create a LiveKit room for streaming"""
    try:
        lk_api = init_livekit_api()
        if not lk_api:
            return None
        
        # Create room options
        room_opts = api.CreateRoomRequest(
            name=room_name,
            empty_timeout=300,  # 5 minutes
            max_participants=100,
            metadata=json.dumps({
                'streamer': streamer_name,
                'type': 'livestream'
            })
        )
        
        room_info = lk_api.room.create_room(room_opts)
        return room_info
    except Exception as e:
        print(f"Error creating LiveKit room: {e}")
        return None

def generate_livekit_token(room_name, participant_identity, participant_name, is_publisher=False):
    """Generate LiveKit access token for participant"""
    try:
        livekit_api_key = app.config.get('LIVEKIT_API_KEY')
        livekit_api_secret = app.config.get('LIVEKIT_API_SECRET')
        
        if not all([livekit_api_key, livekit_api_secret]):
            return None
        
        # Create token with appropriate permissions
        from livekit import AccessToken, VideoGrants
        
        token = AccessToken(livekit_api_key, livekit_api_secret)
        token.with_identity(participant_identity).with_name(participant_name)
        
        # Set grants based on role
        grants = VideoGrants()
        grants.room_join = True
        grants.room = room_name
        
        if is_publisher:
            # Publishers (streamers) can publish audio/video and screen share
            grants.can_publish = True
            grants.can_publish_data = True
            grants.can_subscribe = True
        else:
            # Viewers can only subscribe
            grants.can_publish = False
            grants.can_publish_data = False
            grants.can_subscribe = True
        
        token.with_grants(grants)
        
        # Token expires in 4 hours
        token.with_ttl(timedelta(hours=4))
        
        return token.to_jwt()
    except Exception as e:
        print(f"Error generating LiveKit token: {e}")
        return None

def delete_livekit_room(room_name):
    """Delete a LiveKit room"""
    try:
        lk_api = init_livekit_api()
        if not lk_api:
            return False
        
        lk_api.room.delete_room(api.DeleteRoomRequest(room=room_name))
        return True
    except Exception as e:
        print(f"Error deleting LiveKit room: {e}")
        return False

def start_livekit_recording(room_name):
    """Start recording a LiveKit room"""
    try:
        lk_api = init_livekit_api()
        if not lk_api:
            return None
        
        # Configure recording request
        recording_request = api.StartRecordingRequest(
            input=api.RecordingInput(
                type=api.RecordingInput.RecordingInputType.ROOM_COMPOSITE
            ),
            output=api.RecordingOutput(
                type=api.RecordingOutput.RecordingOutputType.FILE,
                file=api.S3Upload(
                    access_key=app.config.get('AWS_ACCESS_KEY_ID'),
                    secret=app.config.get('AWS_SECRET_ACCESS_KEY'),
                    region=app.config.get('AWS_REGION'),
                    bucket=app.config.get('STREAM_RECORDINGS_BUCKET'),
                    filename_prefix=f"{app.config.get('STREAM_RECORDINGS_PREFIX', '')}livekit/"
                )
            ),
            room_name=room_name
        )
        
        recording_info = lk_api.recording.start_recording(recording_request)
        return recording_info
    except Exception as e:
        print(f"Error starting LiveKit recording: {e}")
        return None

def stop_livekit_recording(recording_id):
    """Stop a LiveKit recording"""
    try:
        lk_api = init_livekit_api()
        if not lk_api:
            return False
        
        lk_api.recording.stop_recording(api.StopRecordingRequest(recording_id=recording_id))
        return True
    except Exception as e:
        print(f"Error stopping LiveKit recording: {e}")
        return False

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
        """Handle user joining a stream with enhanced verification"""
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
                    'created_at': time.time()
                }

            # Join the room
            join_room(room_id)
            
            # Enhanced admin check - check multiple conditions
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
            
            print(f"🔐 Admin check for {client_id}:")
            print(f"  - Authenticated: {is_authenticated}")
            print(f"  - Stream Owner: {is_stream_owner}")
            print(f"  - Admin User: {is_admin_user}")
            print(f"  - Can Stream: {can_stream_user}")
            print(f"  - Final Admin Status: {is_stream_admin}")
            
            if is_stream_admin:
                stream_rooms[room_id]['admin_client'] = client_id
                emit('admin_joined', {'stream_id': stream_id}, room=room_id)
                print(f"🎬 Admin joined stream room: {room_id}")
                
                # Send admin-specific data with LiveKit token
                participant_identity = f"streamer-{current_user.id}"
                participant_name = stream.streamer_name or current_user.username
                
                # Generate LiveKit token for publisher (streamer)
                livekit_token = generate_livekit_token(
                    stream.room_name, 
                    participant_identity, 
                    participant_name,
                    is_publisher=True
                )
                
                emit('admin_status', {
                    'is_admin': True,
                    'can_broadcast': True,
                    'stream_id': stream_id,
                    'livekit_token': livekit_token,
                    'livekit_url': app.config.get('LIVEKIT_URL'),
                    'room_name': stream.room_name,
                    'participant_identity': participant_identity
                })
            else:
                if client_id not in stream_rooms[room_id]['viewers']:
                    stream_rooms[room_id]['viewers'].append(client_id)
                
                emit('viewer_joined', {
                    'client_id': client_id,
                    'username': user_info.get('username', 'Anonymous'),
                    'viewer_count': len(stream_rooms[room_id]['viewers'])
                }, room=room_id)
                print(f"👥 Viewer joined stream room: {room_id}")
                
                # Send viewer-specific data with LiveKit token
                participant_identity = f"viewer-{current_user.id if is_authenticated else 'anon'}-{uuid.uuid4().hex[:8]}"
                participant_name = user_info.get('username', 'Anonymous')
                
                # Generate LiveKit token for subscriber (viewer)
                livekit_token = generate_livekit_token(
                    stream.room_name, 
                    participant_identity, 
                    participant_name,
                    is_publisher=False
                )
                
                emit('viewer_status', {
                    'is_admin': False,
                    'can_broadcast': False,
                    'stream_id': stream_id,
                    'livekit_token': livekit_token,
                    'livekit_url': app.config.get('LIVEKIT_URL'),
                    'room_name': stream.room_name,
                    'participant_identity': participant_identity
                })

            # Update database viewer count
            try:
                stream.viewer_count = len(stream_rooms[room_id]['viewers'])
                db.session.commit()
            except Exception as e:
                print(f"Error updating viewer count: {e}")
                db.session.rollback()

            # Send current stream status
            emit('stream_status', {
                'stream_id': stream_id,
                'is_active': stream.is_active,
                'title': stream.title,
                'streamer_name': stream.streamer_name,
                'viewer_count': len(stream_rooms[room_id]['viewers']),
                'is_admin': is_stream_admin
            })
            
        except Exception as e:
            print(f"❌ Error in join_stream handler: {e}")
            import traceback
            traceback.print_exc()
            emit('error', {'message': 'Failed to join stream'})

    @socketio.on('audio_chunk')
    def handle_audio_chunk(data):
        """Enhanced audio chunk handler - LiveKit handles audio natively"""
        try:
            client_id = request.sid
            stream_id = data.get('stream_id')
            
            # With LiveKit, audio is handled directly by the SDK
            # This is mainly for compatibility with existing frontend
            print(f"🎵 Audio chunk received for stream {stream_id} from {client_id}")
            
            # You might want to emit this for any custom audio visualizations
            room_id = f"stream_{stream_id}"
            if room_id in stream_rooms:
                emit('audio_activity', {
                    'stream_id': stream_id,
                    'has_audio': True,
                    'timestamp': time.time()
                }, room=room_id, include_self=False)
                
        except Exception as e:
            print(f"❌ Enhanced audio chunk handler error: {e}")

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

    # Utility function for debugging
    @socketio.on('debug_info')
    def handle_debug_info(data):
        """Debug endpoint to check user status"""
        try:
            client_id = request.sid
            user_info = active_connections.get(client_id, {})
            
            is_authenticated = (
                hasattr(current_user, 'is_authenticated') and 
                current_user.is_authenticated
            )
            
            debug_data = {
                'client_id': client_id,
                'user_info': user_info,
                'current_user_authenticated': is_authenticated,
                'current_user_id': getattr(current_user, 'id', None),
                'current_user_admin': getattr(current_user, 'is_admin', None),
                'current_user_can_stream': getattr(current_user, 'can_stream', None),
                'active_connections_count': len(active_connections),
                'stream_rooms': {k: {
                    'stream_id': v['stream_id'],
                    'admin_client': v['admin_client'],
                    'viewer_count': len(v['viewers'])
                } for k, v in stream_rooms.items()},
                'livekit_url': app.config.get('LIVEKIT_URL'),
                'livekit_configured': bool(app.config.get('LIVEKIT_API_KEY') and app.config.get('LIVEKIT_API_SECRET'))
            }
            
            print(f"🔍 Debug info for {client_id}: {debug_data}")
            emit('debug_response', debug_data)
            
        except Exception as e:
            print(f"❌ Error in debug handler: {e}")
            emit('error', {'message': f'Debug error: {str(e)}'})


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

# API Routes
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
        
        video = Video(
            title=form.title.data,
            description=form.description.data,
            s3_url=form.s3_url.data,
            thumbnail_url=form.thumbnail_url.data,
            category_id=form.category_id.data,
            is_free=form.is_free.data,
            order_index=order_index
        )
        db.session.add(video)
        db.session.flush()
        
        if tags_data is not None:
            try:
                process_video_tags(video, tags_data)
            except:
                pass
        
        db.session.commit()
        
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
        
        video.title = form.title.data
        video.description = form.description.data
        video.s3_url = form.s3_url.data
        video.thumbnail_url = form.thumbnail_url.data
        video.category_id = form.category_id.data
        video.is_free = form.is_free.data
        video.order_index = form.order_index.data
        
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
            order_index=form.order_index.data
        )
        db.session.add(category)
        db.session.commit()
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
        form.populate_obj(category)
        db.session.commit()
        flash('Category updated successfully!', 'success')
        return redirect(url_for('admin_categories'))
    
    return render_template('admin/category_form.html', 
                         form=form, 
                         category=category, 
                         title='Edit Category',
                         existing_categories=existing_categories)

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

# Update your existing /api/stream/start route to include WebSocket notification
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
        room_sid=room_info.sid if room_info else None,
        is_active=True,
        started_at=datetime.utcnow(),
        created_by=current_user.id,
        streamer_name=streamer_name,
        stream_type=stream_type
    )
    
    db.session.add(stream)
    db.session.commit()
    
    # Broadcast notification about new stream via WebSocket
    if socketio:
        socketio.emit('new_stream_started', {
            'stream_id': stream.id,
            'title': stream.title,
            'streamer_name': stream.streamer_name,
            'message': f'{streamer_name} is now live!'
        })
    
    # Also send traditional notification
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
            'participant_identity': participant_identity
        }
    })
    
# Update your existing /api/stream/stop route
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
    
    # Notify WebSocket clients before stopping
    room_id = f"stream_{stream.id}"
    if socketio and room_id in stream_rooms:
        socketio.emit('stream_ended', {
            'stream_id': stream.id,
            'message': f'{stream.streamer_name} has ended the stream'
        }, room=room_id)
    
    # Delete LiveKit room
    if stream.room_name:
        delete_livekit_room(stream.room_name)
    
    stream.is_active = False
    stream.ended_at = datetime.utcnow()
    
    StreamViewer.query.filter_by(stream_id=stream.id, is_active=True).update({
        'is_active': False,
        'left_at': datetime.utcnow()
    })
    
    db.session.commit()
    
    # Clean up WebSocket room
    if socketio and room_id in stream_rooms:
        del stream_rooms[room_id]
    
    return jsonify({
        'success': True,
        'message': f'{stream.streamer_name}\'s stream ended'
    })

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
            'stream_color': stream.creator.stream_color if stream.creator else '#10B981',
            'room_name': stream.room_name
        })
    
    db.session.commit()
    
    return jsonify({
        'active': True,
        'count': len(active_streams),
        'streams': streams_data
    })
    
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
