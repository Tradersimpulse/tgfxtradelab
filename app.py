from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, TextAreaField, SelectField, FileField, BooleanField, IntegerField
from wtforms.validators import DataRequired, Email, Length, Optional
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime
import os
import boto3
from botocore.exceptions import NoCredentialsError, ClientError
import stripe
from config import get_config
import re
import json
import uuid

# Initialize Flask app
app = Flask(__name__)

# Load configuration
config_class = get_config()
app.config.from_object(config_class)

# Initialize Stripe
stripe.api_key = app.config.get('STRIPE_SECRET_KEY')

# Initialize database with MySQL optimizations
db = SQLAlchemy(app)

# Configure Login Manager
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

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
    
    # ADD THESE NEW FIELDS:
    display_name = db.Column(db.String(100), nullable=True)  # For stream titles (Ray, Jordan)
    can_stream = db.Column(db.Boolean, default=False, nullable=False)  # Who can create streams
    stream_color = db.Column(db.String(7), default='#10B981', nullable=False)  # Color for their streams
    
    # Relationships
    progress = db.relationship('UserProgress', backref='user', lazy=True, cascade='all, delete-orphan')
    favorites = db.relationship('UserFavorite', backref='user', lazy=True, cascade='all, delete-orphan')
    created_streams = db.relationship('Stream', backref='creator', lazy=True, cascade='all, delete-orphan')
    

class Category(db.Model):
    __tablename__ = 'categories'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(100), nullable=False, index=True)
    description = db.Column(db.Text, nullable=True)
    image_url = db.Column(db.String(500), nullable=True)  # ADD THIS LINE
    order_index = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationships
    videos = db.relationship('Video', backref='category', lazy=True, cascade='all, delete-orphan')


class Tag(db.Model):
    __tablename__ = 'tags'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(50), unique=True, nullable=False, index=True)
    slug = db.Column(db.String(50), unique=True, nullable=False, index=True)  # URL-friendly version
    description = db.Column(db.Text, nullable=True)
    color = db.Column(db.String(7), default='#10B981', nullable=False)  # Hex color for the tag
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
    duration = db.Column(db.Integer, nullable=True)  # Duration in seconds
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
    file_type = db.Column(db.String(50), nullable=True)  # pdf, doc, etc
    s3_url = db.Column(db.String(500), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    
    # Foreign Keys
    video_id = db.Column(db.Integer, db.ForeignKey('videos.id'), nullable=False, index=True)

class UserProgress(db.Model):
    __tablename__ = 'user_progress'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    watched_duration = db.Column(db.Integer, default=0, nullable=False)  # Seconds watched
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

class Stream(db.Model):
    __tablename__ = 'streams'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    meeting_id = db.Column(db.String(100), unique=True, nullable=True)
    attendee_id = db.Column(db.String(100), nullable=True)
    external_meeting_id = db.Column(db.String(100), unique=True, nullable=True)
    media_region = db.Column(db.String(50), nullable=True)
    media_placement_audio_host_url = db.Column(db.String(500), nullable=True)
    media_placement_screen_sharing_url = db.Column(db.String(500), nullable=True)
    media_placement_screen_data_url = db.Column(db.String(500), nullable=True)
    is_active = db.Column(db.Boolean, default=False, nullable=False)
    is_recording = db.Column(db.Boolean, default=False, nullable=False)
    recording_url = db.Column(db.String(500), nullable=True)
    viewer_count = db.Column(db.Integer, default=0, nullable=False)
    started_at = db.Column(db.DateTime, nullable=True)
    ended_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    
    # ADD THESE NEW FIELDS:
    streamer_name = db.Column(db.String(100), nullable=True)  # Ray, Jordan, etc.
    stream_type = db.Column(db.String(50), default='general', nullable=False)  # general, trading, education
    
    # Foreign Keys
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    
    # Relationships
    viewers = db.relationship('StreamViewer', backref='stream', lazy=True, cascade='all, delete-orphan')

class StreamViewer(db.Model):
    __tablename__ = 'stream_viewers'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    attendee_id = db.Column(db.String(100), nullable=False)  # Chime attendee ID
    external_user_id = db.Column(db.String(100), nullable=False)
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
    image_url = StringField('Category Image URL', validators=[Optional()])  # ADD THIS LINE
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
    """Get existing tag or create new one"""
    # Clean and create slug
    slug = re.sub(r'[^a-zA-Z0-9\s-]', '', tag_name.lower())
    slug = re.sub(r'\s+', '-', slug.strip())
    
    tag = Tag.query.filter_by(slug=slug).first()
    if not tag:
        tag = Tag(
            name=tag_name.strip().title(),
            slug=slug,
            color='#10B981'  # Default color
        )
        db.session.add(tag)
        db.session.flush()  # Get the ID without committing
    
    return tag

def process_video_tags(video, tags_string):
    """Process comma-separated tags string and associate with video"""
    if not tags_string:
        video.tags.clear()
        return
    
    # Clear existing tags
    video.tags.clear()
    
    # Process new tags
    tag_names = [name.strip() for name in tags_string.split(',') if name.strip()]
    
    for tag_name in tag_names:
        if tag_name:
            tag = get_or_create_tag(tag_name)
            video.tags.append(tag)

def initialize_streamers():
    """Initialize Ray and Jordan as streamers - run this once after deployment"""
    try:
        # Update existing admin user (assuming username 'admin' is Ray)
        ray = User.query.filter_by(username='admin').first()
        if ray:
            ray.display_name = 'Ray'
            ray.can_stream = True
            ray.stream_color = '#10B981'  # Green
        
        # Create or update Jordan
        jordan = User.query.filter_by(username='jordan').first()
        if not jordan:
            jordan = User(
                username='jwill24',
                email='williamsjordan947@gmail.com',
                password_hash=generate_password_hash('jordan123!secure'),
                is_admin=True,
                display_name='Jordan',
                can_stream=True,
                stream_color='#3B82F6'  # Blue
            )
            db.session.add(jordan)
        else:
            jordan.display_name = 'Jordan'
            jordan.can_stream = True
            jordan.stream_color = '#3B82F6'  # Blue
            jordan.is_admin = True
        
        db.session.commit()
        print("✅ Streamers initialized: Ray (Green) and Jordan (Blue)")
        
    except Exception as e:
        print(f"❌ Error initializing streamers: {e}")
        db.session.rollback()

# Custom Jinja2 filters
@app.template_filter('nl2br')
def nl2br_filter(text):
    """Convert newlines to <br> tags"""
    if text is None:
        return ''
    return text.replace('\n', '<br>\n')

@app.template_filter('extract')
def extract_filter(dictionary, key):
    """Extract a value from a dictionary - robust version"""
    try:
        if isinstance(dictionary, dict):
            return dictionary.get(key)
        elif hasattr(dictionary, '__getitem__'):
            return dictionary[key]
        else:
            # If it's not a dictionary or indexable, return None
            return None
    except (KeyError, IndexError, TypeError, AttributeError):
        return None

def get_category_progress(category_id, user_progress):
    """Calculate progress for a category"""
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
    """Get all unique tags from a list of videos"""
    tags = set()
    for video in videos:
        for tag in video.tags:
            tags.add(tag)
    return list(tags)

def get_total_duration(videos):
    """Calculate total duration of videos in seconds"""
    total = 0
    for video in videos:
        if video.duration:
            total += video.duration
    return total

# Update the courses route
@app.route('/courses')
@login_required
def courses():
    # Get filter parameters
    tag_filter = request.args.get('tag')
    
    # Get all tags for the filter dropdown
    all_tags = Tag.query.order_by(Tag.name).all()
    
    # Get categories and their videos
    if tag_filter:
        # Filter by tag - get categories that have videos with the specific tag
        tag = Tag.query.filter_by(slug=tag_filter).first()
        if tag:
            categories_with_tagged_videos = []
            all_categories = Category.query.order_by(Category.order_index).all()
            
            for category in all_categories:
                # Get videos in this category that have the specified tag
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
        # No filter - get all categories with all their videos
        all_categories = Category.query.order_by(Category.order_index).all()
        categories = [{'category': cat, 'videos': list(cat.videos)} for cat in all_categories if cat.videos]
    
    # Get user progress and favorites
    user_progress = {p.video_id: p for p in current_user.progress}
    user_favorites = {f.video_id for f in current_user.favorites}
    
    # Calculate progress stats
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

def init_chime_client():
    """Initialize AWS Chime SDK Meetings client"""
    try:
        chime_client = boto3.client(
            'chime-sdk-meetings',  # ← NEW endpoint for meetings
            aws_access_key_id=app.config['AWS_ACCESS_KEY_ID'],
            aws_secret_access_key=app.config['AWS_SECRET_ACCESS_KEY'],
            region_name=app.config.get('AWS_CHIME_REGION', 'us-east-1')
        )
        return chime_client
    except (NoCredentialsError, KeyError):
        return None

def create_chime_meeting(stream_title, external_meeting_id):
    """Create a new Chime meeting for the stream"""
    chime_client = init_chime_client()
    if not chime_client:
        return None
    
    try:
        response = chime_client.create_meeting(
            ClientRequestToken=str(uuid.uuid4()),
            ExternalMeetingId=external_meeting_id,
            MediaRegion=app.config.get('AWS_CHIME_REGION', 'us-east-1'),
            Tags=[
                {
                    'Key': 'StreamTitle',
                    'Value': stream_title
                },
                {
                    'Key': 'CreatedBy',
                    'Value': current_user.username
                }
            ]
        )
        return response['Meeting']
    except ClientError as e:
        print(f"Error creating Chime meeting: {e}")
        return None

def create_chime_attendee(meeting_id, external_user_id, user_name):
    """Create a Chime attendee for the meeting"""
    chime_client = init_chime_client()
    if not chime_client:
        return None
    
    try:
        response = chime_client.create_attendee(
            MeetingId=meeting_id,
            ExternalUserId=external_user_id,
            Tags=[
                {
                    'Key': 'UserName',
                    'Value': user_name
                }
            ]
        )
        return response['Attendee']
    except ClientError as e:
        print(f"Error creating Chime attendee: {e}")
        return None

def get_recording_s3_key(stream_id, streamer_name, timestamp=None):
    """Generate S3 key for stream recording with streamer name"""
    if not timestamp:
        timestamp = datetime.utcnow()
    
    date_str = timestamp.strftime('%Y/%m/%d')
    # Include streamer name in filename
    filename = f"{streamer_name}-stream-{stream_id}-{timestamp.strftime('%Y%m%d-%H%M%S')}.mp4"
    
    prefix = app.config.get('STREAM_RECORDINGS_PREFIX', 'livestream-recordings/')
    return f"{prefix}{date_str}/{filename}"

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
        
        # Generate the S3 URL
        s3_url = f"https://{bucket}.s3.amazonaws.com/{s3_key}"
        return s3_url
        
    except ClientError as e:
        print(f"Error uploading recording to S3: {e}")
        return None

def delete_chime_meeting(meeting_id):
    """Delete a Chime meeting"""
    chime_client = init_chime_client()
    if not chime_client:
        return False
    
    try:
        chime_client.delete_meeting(MeetingId=meeting_id)
        return True
    except ClientError as e:
        print(f"Error deleting Chime meeting: {e}")
        return False
    """Generate S3 key for stream recording"""
    if not timestamp:
        timestamp = datetime.utcnow()
    
    date_str = timestamp.strftime('%Y/%m/%d')
    filename = f"stream-{stream_id}-{timestamp.strftime('%Y%m%d-%H%M%S')}.mp4"
    
    prefix = app.config.get('STREAM_RECORDINGS_PREFIX', 'livestream-recordings/')
    return f"{prefix}{date_str}/{filename}"

def upload_recording_to_s3(local_file_path, stream_id):
    """Upload recording file to S3"""
    s3_client = init_s3_client()
    if not s3_client:
        return None
    
    try:
        bucket = app.config['STREAM_RECORDINGS_BUCKET']
        s3_key = get_recording_s3_key(stream_id)
        
        s3_client.upload_file(
            local_file_path,
            bucket,
            s3_key,
            ExtraArgs={
                'ContentType': 'video/mp4',
                'ServerSideEncryption': 'AES256'
            }
        )
        
        # Generate the S3 URL
        s3_url = f"https://{bucket}.s3.amazonaws.com/{s3_key}"
        return s3_url
        
    except ClientError as e:
        print(f"Error uploading recording to S3: {e}")
        return None
    """Delete a Chime meeting"""
    chime_client = init_chime_client()
    if not chime_client:
        return False
    
    try:
        chime_client.delete_meeting(MeetingId=meeting_id)
        return True
    except ClientError as e:
        print(f"Error deleting Chime meeting: {e}")
        return False
        
# Routes
@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('courses'))
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user and check_password_hash(user.password_hash, form.password.data):
            login_user(user)
            return redirect(url_for('courses'))
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
            password_hash=generate_password_hash(form.password.data)
        )
        db.session.add(user)
        db.session.commit()
        login_user(user)
        flash('Account created successfully!', 'success')
        return redirect(url_for('courses'))
    return render_template('auth/signup.html', form=form)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))


@app.route('/courses/category/<int:category_id>')
@login_required
def category_videos(category_id):
    category = Category.query.get_or_404(category_id)
    videos = Video.query.filter_by(category_id=category_id).order_by(Video.order_index).all()
    
    # Get user progress and favorites
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
    
    # Get or create user progress
    progress = UserProgress.query.filter_by(user_id=current_user.id, video_id=video_id).first()
    if not progress:
        progress = UserProgress(user_id=current_user.id, video_id=video_id)
        db.session.add(progress)
        db.session.commit()
    
    # Check if user has favorited this video
    is_favorited = UserFavorite.query.filter_by(user_id=current_user.id, video_id=video_id).first() is not None
    
    # Get user progress for all videos (needed by template)
    user_progress = {p.video_id: p for p in current_user.progress}
    
    return render_template('courses/watch.html', 
                         video=video, 
                         progress=progress,
                         is_favorited=is_favorited,
                         user_progress=user_progress)

@app.route('/api/video/progress', methods=['POST'])
@login_required
def update_progress():
    data = request.get_json()
    video_id = data.get('video_id')
    watched_duration = data.get('watched_duration')
    total_duration = data.get('total_duration')
    
    progress = UserProgress.query.filter_by(user_id=current_user.id, video_id=video_id).first()
    if not progress:
        progress = UserProgress(user_id=current_user.id, video_id=video_id)
        db.session.add(progress)
    
    progress.watched_duration = watched_duration
    progress.last_watched = datetime.utcnow()
    
    # Mark as completed if watched 90% or more
    if total_duration and watched_duration >= (total_duration * 0.9):
        progress.completed = True
    
    db.session.commit()
    
    return jsonify({'success': True})

@app.route('/api/video/favorite', methods=['POST'])
@login_required
def toggle_favorite():
    data = request.get_json()
    video_id = data.get('video_id')
    
    favorite = UserFavorite.query.filter_by(user_id=current_user.id, video_id=video_id).first()
    
    if favorite:
        db.session.delete(favorite)
        is_favorited = False
    else:
        favorite = UserFavorite(user_id=current_user.id, video_id=video_id)
        db.session.add(favorite)
        is_favorited = True
    
    db.session.commit()
    
    return jsonify({'success': True, 'is_favorited': is_favorited})

@app.route('/favorites')
@login_required
def favorites():
    user_favorites = db.session.query(Video).join(UserFavorite).filter(
        UserFavorite.user_id == current_user.id
    ).all()
    
    # Get user progress
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
    
    # In a real app, you'd generate a signed URL for S3
    return redirect(video_file.s3_url)

@app.route('/subscription')
@login_required
def subscription():
    return render_template('subscription.html', 
                         stripe_key=app.config['STRIPE_PUBLISHABLE_KEY'])

@app.route('/donate')
@login_required
def donate():
    return render_template('donate.html', 
                         stripe_key=app.config['STRIPE_PUBLISHABLE_KEY'])

# Admin Routes
@app.route('/admin')
@login_required
def admin():
    if not current_user.is_admin:
        flash('Access denied', 'error')
        return redirect(url_for('courses'))
    
    video_count = Video.query.count()
    user_count = User.query.count()
    category_count = Category.query.count()
    subscription_count = User.query.filter_by(has_subscription=True).count()
    
    # Get recent videos for dashboard
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
        return redirect(url_for('courses'))
    
    videos = Video.query.order_by(Video.created_at.desc()).all()
    return render_template('admin/videos.html', videos=videos)

@app.route('/admin/video/add', methods=['GET', 'POST'])
@login_required
def admin_add_video():
    if not current_user.is_admin:
        flash('Access denied', 'error')
        return redirect(url_for('courses'))
    
    # Use VideoFormWithTags if it exists, otherwise fall back to VideoForm
    try:
        form = VideoFormWithTags()
    except NameError:
        form = VideoForm()
    
    form.category_id.choices = [(c.id, c.name) for c in Category.query.all()]
    
    if form.validate_on_submit():
        # Store tags data before creating video
        tags_data = None
        if hasattr(form, 'tags'):
            tags_data = form.tags.data
        
        video = Video(
            title=form.title.data,
            description=form.description.data,
            s3_url=form.s3_url.data,
            thumbnail_url=form.thumbnail_url.data,
            category_id=form.category_id.data,
            is_free=form.is_free.data,
            order_index=form.order_index.data
        )
        db.session.add(video)
        db.session.flush()  # Get video ID
        
        # Process tags if the form has tags field and the function exists
        if tags_data is not None:
            try:
                process_video_tags(video, tags_data)
            except:
                # If tag processing fails, just continue without tags
                pass
        
        db.session.commit()
        flash('Video added successfully!', 'success')
        return redirect(url_for('admin_videos'))
    
    return render_template('admin/video_form.html', form=form, title='Add Video')

@app.route('/admin/video/edit/<int:video_id>', methods=['GET', 'POST'])
@login_required
def admin_edit_video(video_id):
    if not current_user.is_admin:
        flash('Access denied', 'error')
        return redirect(url_for('courses'))
    
    video = Video.query.get_or_404(video_id)
    
    # Use VideoFormWithTags if it exists, otherwise fall back to VideoForm
    try:
        form = VideoFormWithTags(obj=video)
    except NameError:
        # Fallback to regular VideoForm if VideoFormWithTags doesn't exist
        form = VideoForm(obj=video)
    
    form.category_id.choices = [(c.id, c.name) for c in Category.query.all()]
    
    # Pre-populate tags field if the form has tags and it's a GET request
    if request.method == 'GET' and hasattr(form, 'tags'):
        try:
            form.tags.data = ', '.join([tag.name for tag in video.tags])
        except:
            form.tags.data = ''
    
    if form.validate_on_submit():
        # Store tags data before populate_obj
        tags_data = None
        if hasattr(form, 'tags'):
            tags_data = form.tags.data
        
        # Manually populate fields to avoid the tags collection error
        video.title = form.title.data
        video.description = form.description.data
        video.s3_url = form.s3_url.data
        video.thumbnail_url = form.thumbnail_url.data
        video.category_id = form.category_id.data
        video.is_free = form.is_free.data
        video.order_index = form.order_index.data
        
        # Process tags separately if the function exists
        if tags_data is not None:
            try:
                process_video_tags(video, tags_data)
            except:
                # If tag processing fails, just continue without tags
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
        return redirect(url_for('courses'))
    
    categories = Category.query.order_by(Category.order_index).all()
    
    # Calculate stats that the template expects
    total_videos = Video.query.count()
    empty_categories = len([cat for cat in categories if len(cat.videos) == 0])
    avg_videos_per_category = (total_videos / len(categories)) if categories else 0
    
    return render_template('admin/categories.html', 
                         categories=categories,
                         total_videos=total_videos,
                         avg_videos_per_category=avg_videos_per_category,
                         empty_categories=empty_categories)

@app.route('/admin/category/edit/<int:category_id>', methods=['GET', 'POST'])
@login_required
def admin_edit_category(category_id):
    if not current_user.is_admin:
        flash('Access denied', 'error')
        return redirect(url_for('courses'))
    
    category = Category.query.get_or_404(category_id)
    form = CategoryForm(obj=category)
    
    # Get existing categories for the sidebar
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

@app.route('/admin/category/add', methods=['GET', 'POST'])
@login_required
def admin_add_category():
    if not current_user.is_admin:
        flash('Access denied', 'error')
        return redirect(url_for('courses'))
    
    form = CategoryForm()
    
    # Get existing categories for the sidebar
    existing_categories = Category.query.order_by(Category.order_index).all()
    
    if form.validate_on_submit():
        category = Category(
            name=form.name.data,
            description=form.description.data,
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

# Tag Management Routes
@app.route('/admin/tags')
@login_required
def admin_tags():
    if not current_user.is_admin:
        flash('Access denied', 'error')
        return redirect(url_for('courses'))
    
    tags = Tag.query.order_by(Tag.name).all()
    
    # Calculate tag statistics
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
        return redirect(url_for('courses'))
    
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
        return redirect(url_for('courses'))
    
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

# API Routes
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

#Livestreaming app routes
@app.route('/livestream')
@login_required
def livestream():
    """User page to watch live streams - now supports multiple streams"""
    active_streams = Stream.query.filter_by(is_active=True).all()
    
    # Group streams by streamer for easier display
    streams_by_streamer = {}
    attendee_data_by_stream = {}
    
    for stream in active_streams:
        streamer = stream.streamer_name or 'Unknown'
        streams_by_streamer[streamer] = stream
        
        # Check if user already has an attendee record for this stream
        existing_viewer = StreamViewer.query.filter_by(
            stream_id=stream.id,
            user_id=current_user.id,
            is_active=True
        ).first()
        
        if existing_viewer:
            attendee_data_by_stream[stream.id] = {
                'AttendeeId': existing_viewer.attendee_id,
                'ExternalUserId': existing_viewer.external_user_id
            }
        else:
            # Create new attendee for this stream
            external_user_id = f"user-{current_user.id}-{uuid.uuid4().hex[:8]}"
            attendee = create_chime_attendee(
                stream.meeting_id,
                external_user_id,
                current_user.username
            )
            
            if attendee:
                # Save viewer record
                viewer = StreamViewer(
                    stream_id=stream.id,
                    user_id=current_user.id,
                    attendee_id=attendee['AttendeeId'],
                    external_user_id=external_user_id
                )
                db.session.add(viewer)
                
                # Update viewer count
                stream.viewer_count = StreamViewer.query.filter_by(
                    stream_id=stream.id,
                    is_active=True
                ).count() + 1
                
                db.session.commit()
                attendee_data_by_stream[stream.id] = attendee
    
    return render_template('livestream.html', 
                         active_streams=active_streams,
                         streams_by_streamer=streams_by_streamer,
                         attendee_data_by_stream=attendee_data_by_stream)

@app.route('/admin/stream')
@login_required
def admin_stream():
    """Admin page to control streams - supports dual streaming"""
    if not current_user.is_admin:
        flash('Access denied', 'error')
        return redirect(url_for('courses'))
    
    form = DualStreamForm()
    
    # Get active streams
    active_streams = Stream.query.filter_by(is_active=True).all()
    
    # Get recent streams
    recent_streams = Stream.query.order_by(Stream.created_at.desc()).limit(10).all()
    
    # Check if current user can start a stream (max 2 concurrent)
    can_start_stream = len(active_streams) < 2 and current_user.can_stream
    
    # Get current user's active stream if any
    user_active_stream = Stream.query.filter_by(
        created_by=current_user.id,
        is_active=True
    ).first()
    
    return render_template('admin/stream.html',
                         form=form,
                         active_streams=active_streams,
                         recent_streams=recent_streams,
                         can_start_stream=can_start_stream,
                         user_active_stream=user_active_stream)

@app.route('/api/stream/start', methods=['POST'])
@login_required
def api_start_stream():
    """Start a new live stream - supports concurrent streams"""
    if not current_user.is_admin or not current_user.can_stream:
        return jsonify({'error': 'Access denied - not authorized to stream'}), 403
    
    # Check if user already has an active stream
    user_active_stream = Stream.query.filter_by(
        created_by=current_user.id,
        is_active=True
    ).first()
    if user_active_stream:
        return jsonify({'error': 'You already have an active stream'}), 400
    
    # Check concurrent stream limit (max 2)
    active_stream_count = Stream.query.filter_by(is_active=True).count()
    if active_stream_count >= 2:
        return jsonify({'error': 'Maximum concurrent streams reached (2)'}), 400
    
    data = request.get_json()
    title = data.get('title', 'Live Stream')
    description = data.get('description', '')
    stream_type = data.get('stream_type', 'general')
    
    # Get streamer name
    streamer_name = current_user.display_name or current_user.username
    
    # Add streamer name to title if not already there
    if streamer_name not in title:
        title = f"{streamer_name}'s {title}"
    
    # Create unique meeting ID
    external_meeting_id = f"stream-{streamer_name.lower()}-{uuid.uuid4().hex[:12]}"
    
    # Create Chime meeting
    meeting_data = create_chime_meeting(title, external_meeting_id)
    if not meeting_data:
        return jsonify({'error': 'Failed to create meeting'}), 500
    
    # Create admin attendee
    admin_external_id = f"admin-{current_user.id}-{uuid.uuid4().hex[:8]}"
    admin_attendee = create_chime_attendee(
        meeting_data['MeetingId'],
        admin_external_id,
        f"{streamer_name}-Admin"
    )
    
    if not admin_attendee:
        delete_chime_meeting(meeting_data['MeetingId'])
        return jsonify({'error': 'Failed to create admin attendee'}), 500
    
    # Create stream record
    stream = Stream(
        title=title,
        description=description,
        meeting_id=meeting_data['MeetingId'],
        attendee_id=admin_attendee['AttendeeId'],
        external_meeting_id=external_meeting_id,
        media_region=meeting_data['MediaRegion'],
        media_placement_audio_host_url=meeting_data['MediaPlacement']['AudioHostUrl'],
        media_placement_screen_sharing_url=meeting_data['MediaPlacement']['ScreenSharingUrl'],
        media_placement_screen_data_url=meeting_data['MediaPlacement']['ScreenDataUrl'],
        is_active=True,
        started_at=datetime.utcnow(),
        created_by=current_user.id,
        streamer_name=streamer_name,
        stream_type=stream_type
    )
    
    db.session.add(stream)
    db.session.commit()
    
    return jsonify({
        'success': True,
        'stream': {
            'id': stream.id,
            'title': stream.title,
            'streamer_name': stream.streamer_name,
            'meeting_id': stream.meeting_id,
            'meeting_data': meeting_data,
            'admin_attendee': admin_attendee
        }
    })

# UPDATE the stop stream API
@app.route('/api/stream/stop', methods=['POST'])
@login_required
def api_stop_stream():
    """Stop a live stream - user can only stop their own stream"""
    if not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    data = request.get_json()
    stream_id = data.get('stream_id')
    
    if stream_id:
        # Stop specific stream (must be owned by current user)
        stream = Stream.query.filter_by(
            id=stream_id,
            created_by=current_user.id,
            is_active=True
        ).first()
    else:
        # Stop user's active stream
        stream = Stream.query.filter_by(
            created_by=current_user.id,
            is_active=True
        ).first()
    
    if not stream:
        return jsonify({'error': 'No active stream found or access denied'}), 400
    
    # Delete Chime meeting
    if stream.meeting_id:
        delete_chime_meeting(stream.meeting_id)
    
    # Update stream record
    stream.is_active = False
    stream.ended_at = datetime.utcnow()
    
    # Mark all viewers as inactive
    StreamViewer.query.filter_by(stream_id=stream.id, is_active=True).update({
        'is_active': False,
        'left_at': datetime.utcnow()
    })
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'message': f'{stream.streamer_name}\'s stream ended'
    })

# UPDATE the stream status API
@app.route('/api/stream/status')
@login_required
def api_stream_status():
    """Get current streams status - supports multiple streams"""
    active_streams = Stream.query.filter_by(is_active=True).all()
    
    if not active_streams:
        return jsonify({'active': False, 'streams': []})
    
    streams_data = []
    for stream in active_streams:
        # Update viewer count
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

# UPDATE recording functions
@app.route('/api/stream/recording/start', methods=['POST'])
@login_required
def api_start_recording():
    """Start recording a stream - user can only record their own stream"""
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
    
    stream.is_recording = True
    db.session.commit()
    
    return jsonify({
        'success': True, 
        'message': f'Recording started for {stream.streamer_name}\'s stream'
    })

@app.route('/api/stream/recording/stop', methods=['POST'])
@login_required
def api_stop_recording():
    """Stop recording a stream"""
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
    
    stream.is_recording = False
    
    # Here you would implement the actual recording save logic
    # For now, we'll just generate a placeholder URL
    if stream.streamer_name:
        recording_url = f"s3://tgfx-tradelab/livestream-recordings/{datetime.utcnow().strftime('%Y/%m/%d')}/{stream.streamer_name}-stream-{stream.id}-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.mp4"
        stream.recording_url = recording_url
    
    db.session.commit()
    
    return jsonify({
        'success': True, 
        'message': f'Recording stopped for {stream.streamer_name}\'s stream',
        'recording_url': stream.recording_url
    })

@app.context_processor
def utility_processor():
    return dict(
        user_can_access_video=user_can_access_video,
        get_category_progress=get_category_progress,
        get_course_tags=get_course_tags,
        get_total_duration=get_total_duration
    )

# Register API blueprint (if api.py exists)
try:
    from api_routes import api
    app.register_blueprint(api)
except ImportError:
    print("Warning: api_routes.py not found, skipping API blueprint registration")

# Initialize configuration
config_class.init_app(app)

if __name__ == '__main__':
    with app.app_context():
        # Create tables with error handling for MySQL
        try:
            db.create_all()
            print("✓ Database tables created successfully")
        except Exception as e:
            print(f"⚠ Database initialization error: {e}")
        
        # Create admin user if it doesn't exist
        try:
            admin_user = User.query.filter_by(username='admin').first()
            if not admin_user:
                admin_user = User(
                    username='admin',
                    email='ray@tgfx-academy.com',
                    password_hash=generate_password_hash('admin123!345gdfb3f35'),
                    is_admin=True
                )
                db.session.add(admin_user)
                db.session.commit()
                print("✓ Admin user created")
        except Exception as e:
            print(f"⚠ Admin user creation error: {e}")
    
    app.run(debug=True)
