"""
Configuration settings for TGFX Trade Lab - MySQL + Heroku + LiveKit Optimized
UPDATED: Added LiveKit streaming configuration and enhanced stability
"""

import os
from datetime import timedelta

class Config:
    """Base configuration class"""
    APP_UPDATE_DISCORD_WEBHOOK_URL = os.environ.get('APP_UPDATE_DISCORD_WEBHOOK_URL')
    # Flask Core Settings
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-change-in-production'
    
    # MySQL Database Configuration - FIXED
    @staticmethod
    def get_database_uri():
        """Build MySQL database URI from environment variables with proper error handling"""
        # Check for direct DATABASE_URL first (Heroku style)
        database_url = os.environ.get('DATABASE_URL')
        if database_url:
            # Ensure it's using pymysql driver
            if database_url.startswith('mysql://'):
                database_url = database_url.replace('mysql://', 'mysql+pymysql://', 1)
            return database_url
        
        # Build MySQL URI from individual components
        db_host = os.environ.get('DB_HOST')
        db_user = os.environ.get('DB_USER')
        db_password = os.environ.get('DB_PASSWORD')
        db_name = os.environ.get('DB_NAME')
        db_port = os.environ.get('DB_PORT', '3306')
        
        if all([db_host, db_user, db_password, db_name]):
            # Build URI with enhanced connection parameters for stability
            return (f"mysql+pymysql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
                   f"?charset=utf8mb4&autocommit=true&connect_timeout=30&read_timeout=30"
                   f"&write_timeout=30&binary_prefix=true&use_unicode=true")
        
        # Fallback to SQLite for local development
        return 'sqlite:///tgfx_trade_lab.db'
    
    # FIXED: Call the method properly
    SQLALCHEMY_DATABASE_URI = get_database_uri()
    SQLALCHEMY_TRACK_MODIFICATIONS = False

        # Discord Webhook Configuration
    APP_UPDATE_DISCORD_WEBHOOK_URL = os.environ.get(
        'APP_UPDATE_DISCORD_WEBHOOK_URL',
        'https://discord.com/api/webhooks/1404472981459173456/aT1uA1NANoNjzQPAYPrzb_GAzOOEOWJhgemj0DZbFFSP7IdqSuKb_vCpuu6rSzttj9EZ'
    )
    
    # FIXED: Enhanced MySQL engine options with better error handling
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,          # Validate connections before use
        'pool_recycle': 280,            # Recycle every 4.6 minutes
        'pool_size': 3,                 # Conservative for stability
        'max_overflow': 5,              # Reduced overflow
        'pool_timeout': 20,             # Shorter timeout
        'echo': False,                  # Set to True for SQL debugging
        'connect_args': {
            'connect_timeout': 30,      # Reduced connection timeout
            'read_timeout': 30,         # Reduced read timeout  
            'write_timeout': 30,        # Reduced write timeout
            'charset': 'utf8mb4',       # Full UTF-8 support
            'autocommit': True,         # Auto-commit transactions
            'use_unicode': True,        # Ensure unicode support
            'sql_mode': 'STRICT_TRANS_TABLES,NO_ZERO_DATE,NO_ZERO_IN_DATE,ERROR_FOR_DIVISION_BY_ZERO'
        }
    }
    
    # AWS S3 Configuration
    AWS_ACCESS_KEY_ID = os.environ.get('AWS_ACCESS_KEY_ID')
    AWS_SECRET_ACCESS_KEY = os.environ.get('AWS_SECRET_ACCESS_KEY')
    S3_BUCKET = os.environ.get('S3_BUCKET')
    AWS_REGION = os.environ.get('AWS_REGION', 'us-east-1')
    
    # Stripe Configuration
    STRIPE_PUBLISHABLE_KEY = os.environ.get('STRIPE_PUBLISHABLE_KEY')
    STRIPE_SECRET_KEY = os.environ.get('STRIPE_SECRET_KEY')
    
    # Email Configuration
    MAIL_SERVER = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
    MAIL_PORT = int(os.environ.get('MAIL_PORT', 587))
    MAIL_USE_TLS = os.environ.get('MAIL_USE_TLS', 'True').lower() == 'true'
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')
    
    # Application Settings
    APP_NAME = os.environ.get('APP_NAME', 'TGFX Trade Lab')
    APP_URL = os.environ.get('APP_URL', 'http://localhost:5000')
    
    # File Upload Settings
    MAX_CONTENT_LENGTH = 100 * 1024 * 1024  # 100MB max file size
    UPLOAD_FOLDER = 'uploads'
    ALLOWED_EXTENSIONS = {'txt', 'pdf', 'png', 'jpg', 'jpeg', 'gif', 'mp4', 'avi', 'mov'}
    
    # Session Configuration for Heroku
    PERMANENT_SESSION_LIFETIME = timedelta(days=7)
    SESSION_COOKIE_SECURE = os.environ.get('HTTPS', 'false').lower() == 'true'
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    
    # Security Settings
    WTF_CSRF_TIME_LIMIT = None
    WTF_CSRF_SSL_STRICT = False
    
    # Pagination
    VIDEOS_PER_PAGE = 12
    USERS_PER_PAGE = 25
    
    # Cache Configuration - Simple for Heroku
    CACHE_TYPE = 'simple'
    CACHE_DEFAULT_TIMEOUT = 300

    # ===== LIVEKIT STREAMING CONFIGURATION =====
    
    # LiveKit Core Settings - Required for streaming
    LIVEKIT_URL = os.environ.get('LIVEKIT_URL')  # e.g., 'wss://your-project.livekit.cloud'
    LIVEKIT_API_KEY = os.environ.get('LIVEKIT_API_KEY')
    LIVEKIT_API_SECRET = os.environ.get('LIVEKIT_API_SECRET')
    
    # Stream Quality Settings - Optional but recommended
    STREAM_VIDEO_MAX_BITRATE = int(os.environ.get('STREAM_VIDEO_MAX_BITRATE', 3000000))  # 3 Mbps default
    STREAM_AUDIO_MAX_BITRATE = int(os.environ.get('STREAM_AUDIO_MAX_BITRATE', 128000))   # 128 kbps default
    
    # Stream Management Settings
    MAX_CONCURRENT_STREAMS = int(os.environ.get('MAX_CONCURRENT_STREAMS', 2))  # Maximum concurrent streams
    STREAM_TOKEN_EXPIRY_HOURS = int(os.environ.get('STREAM_TOKEN_EXPIRY_HOURS', 4))  # Token expiry time
    
    # Stream Recording Settings
    STREAM_RECORDINGS_ENABLED = os.environ.get('STREAM_RECORDINGS_ENABLED', 'false').lower() == 'true'
    STREAM_RECORDINGS_BUCKET = os.environ.get('STREAM_RECORDINGS_BUCKET', S3_BUCKET)  # Use main bucket if not specified
    STREAM_RECORDINGS_PREFIX = os.environ.get('STREAM_RECORDINGS_PREFIX', 'livestream-recordings/')
    
    # Stream Notification Settings
    STREAM_NOTIFICATIONS_ENABLED = os.environ.get('STREAM_NOTIFICATIONS_ENABLED', 'true').lower() == 'true'
    STREAM_AUTO_NOTIFY_USERS = os.environ.get('STREAM_AUTO_NOTIFY_USERS', 'premium').lower()  # 'all', 'premium', 'none'
    
    # WebSocket Configuration for Real-time Features
    SOCKETIO_ASYNC_MODE = 'gevent'  # Use gevent for better performance
    SOCKETIO_PING_TIMEOUT = 60
    SOCKETIO_PING_INTERVAL = 25
    
    # Quality Presets for Different Network Conditions
    STREAM_QUALITY_PRESETS = {
        'low': {
            'video_bitrate': 1000000,    # 1 Mbps
            'audio_bitrate': 64000,      # 64 kbps
            'resolution': '480p'
        },
        'medium': {
            'video_bitrate': 2000000,    # 2 Mbps
            'audio_bitrate': 96000,      # 96 kbps
            'resolution': '720p'
        },
        'high': {
            'video_bitrate': 3000000,    # 3 Mbps
            'audio_bitrate': 128000,     # 128 kbps
            'resolution': '1080p'
        }
    }
    
    @staticmethod
    def init_app(app):
        """Initialize application-specific configuration"""
        # Debug database configuration
        db_uri = app.config.get('SQLALCHEMY_DATABASE_URI', '')
        if 'mysql' in db_uri:
            print(f"✓ MySQL Database configured: {db_uri.split('@')[1].split('/')[0] if '@' in db_uri else 'Unknown host'}")
        elif 'sqlite' in db_uri:
            print("⚠ Using SQLite database (development mode)")
        else:
            print("❌ Database configuration issue!")
        
        # Validate LiveKit Configuration
        required_livekit_vars = ['LIVEKIT_URL', 'LIVEKIT_API_KEY', 'LIVEKIT_API_SECRET']
        missing_livekit_vars = []
        
        for var in required_livekit_vars:
            if not app.config.get(var):
                missing_livekit_vars.append(var)
        
        if missing_livekit_vars:
            print(f"⚠ Warning: Missing LiveKit configuration: {', '.join(missing_livekit_vars)}")
            print("  Live streaming features will not work properly.")
            print("  Please set these environment variables:")
            for var in missing_livekit_vars:
                print(f"    {var}=your_value_here")
        else:
            print("✓ LiveKit streaming configuration loaded successfully")
            
            # Validate LiveKit URL format
            livekit_url = app.config.get('LIVEKIT_URL')
            if livekit_url and not (livekit_url.startswith('ws://') or livekit_url.startswith('wss://')):
                print(f"⚠ Warning: LIVEKIT_URL should start with ws:// or wss://, got: {livekit_url}")
        
        # Display stream configuration
        max_streams = app.config.get('MAX_CONCURRENT_STREAMS', 2)
        video_bitrate = app.config.get('STREAM_VIDEO_MAX_BITRATE', 3000000) / 1000000  # Convert to Mbps
        audio_bitrate = app.config.get('STREAM_AUDIO_MAX_BITRATE', 128000) / 1000  # Convert to kbps
        
        print(f"🎬 Stream Settings: Max {max_streams} concurrent, Video {video_bitrate}Mbps, Audio {audio_bitrate}kbps")
        
        # Validate S3 Configuration for recordings
        if app.config.get('STREAM_RECORDINGS_ENABLED'):
            if not app.config.get('STREAM_RECORDINGS_BUCKET'):
                print("⚠ Warning: Stream recordings enabled but no S3 bucket configured")
            else:
                print(f"📹 Stream recordings enabled → S3://{app.config.get('STREAM_RECORDINGS_BUCKET')}")

class DevelopmentConfig(Config):
    """Development configuration"""
    DEBUG = True
    DEVELOPMENT = True
    
    # Less strict security in development
    SESSION_COOKIE_SECURE = False
    WTF_CSRF_ENABLED = False
    
    # FIXED: Enable SQL logging in development with better connection settings
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,
        'pool_recycle': 300,
        'pool_size': 2,             # Smaller pool for development
        'max_overflow': 3,
        'pool_timeout': 20,
        'echo': True,               # Log SQL queries in development
        'connect_args': {
            'connect_timeout': 30,
            'read_timeout': 30,
            'write_timeout': 30,
            'charset': 'utf8mb4',
            'autocommit': True,
            'use_unicode': True
        }
    }

    # Development LiveKit Settings
    LIVEKIT_URL = os.environ.get('LIVEKIT_URL', 'ws://localhost:7880')  # Local LiveKit server
    STREAM_VIDEO_MAX_BITRATE = int(os.environ.get('STREAM_VIDEO_MAX_BITRATE', 2000000))  # Lower for dev
    STREAM_AUDIO_MAX_BITRATE = int(os.environ.get('STREAM_AUDIO_MAX_BITRATE', 96000))    # Lower for dev
    
    # Enable recordings in development
    STREAM_RECORDINGS_ENABLED = True

class ProductionConfig(Config):
    """Production configuration for Heroku"""
    DEBUG = False
    DEVELOPMENT = False
    
    # Force HTTPS in production
    SESSION_COOKIE_SECURE = True
    PREFERRED_URL_SCHEME = 'https'

    # Production LiveKit Settings
    LIVEKIT_URL = os.environ.get('LIVEKIT_URL')  # Must be provided in production
    STREAM_VIDEO_MAX_BITRATE = int(os.environ.get('STREAM_VIDEO_MAX_BITRATE', 3000000))  # 3 Mbps
    STREAM_AUDIO_MAX_BITRATE = int(os.environ.get('STREAM_AUDIO_MAX_BITRATE', 128000))   # 128 kbps
    
    # Production stream settings
    MAX_CONCURRENT_STREAMS = int(os.environ.get('MAX_CONCURRENT_STREAMS', 2))
    STREAM_RECORDINGS_ENABLED = os.environ.get('STREAM_RECORDINGS_ENABLED', 'true').lower() == 'true'
    
    # FIXED: Optimized MySQL settings for production with better timeouts
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,
        'pool_recycle': 280,            # Just under MySQL's 300s default
        'pool_size': 5,                 # Good for production load
        'max_overflow': 8,              # Handle traffic spikes
        'pool_timeout': 20,             # Shorter timeout for faster failures
        'echo': False,                  # No SQL logging in production
        'connect_args': {
            'connect_timeout': 30,      # Shorter timeouts for production
            'read_timeout': 30,
            'write_timeout': 30,
            'charset': 'utf8mb4',
            'autocommit': True,
            'use_unicode': True,
            'sql_mode': 'STRICT_TRANS_TABLES,NO_ZERO_DATE,NO_ZERO_IN_DATE,ERROR_FOR_DIVISION_BY_ZERO'
        }
    }
    
    @classmethod
    def init_app(cls, app):
        Config.init_app(app)
        
        # Validate production requirements
        if not app.config.get('LIVEKIT_URL'):
            print("❌ CRITICAL: LIVEKIT_URL not set in production!")
        
        # Log to stderr in production
        import logging
        from logging import StreamHandler
        file_handler = StreamHandler()
        file_handler.setLevel(logging.INFO)
        app.logger.addHandler(file_handler)

class TestingConfig(Config):
    """Testing configuration"""
    TESTING = True
    DEBUG = True
    
    # Use in-memory SQLite for testing
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    
    # Disable CSRF for testing
    WTF_CSRF_ENABLED = False
    
    # Disable email sending in tests
    MAIL_SUPPRESS_SEND = True
    
    # Use simple cache for testing
    CACHE_TYPE = 'simple'
    
    # Mock LiveKit settings for testing
    LIVEKIT_URL = 'ws://mock-livekit:7880'
    LIVEKIT_API_KEY = 'test-api-key'
    LIVEKIT_API_SECRET = 'test-secret'
    MAX_CONCURRENT_STREAMS = 1
    STREAM_RECORDINGS_ENABLED = False

class HerokuConfig(ProductionConfig):
    """Heroku-specific configuration with enhanced stability"""
    
    # FIXED: Heroku-optimized database settings with better error handling
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,
        'pool_recycle': 250,            # Shorter for Heroku's dynamic environment
        'pool_size': 3,                 # Conservative for Heroku dyno limits
        'max_overflow': 5,              # Reasonable overflow
        'pool_timeout': 15,             # Shorter timeout for Heroku
        'echo': False,
        'connect_args': {
            'connect_timeout': 20,      # Shorter timeouts for Heroku
            'read_timeout': 20,
            'write_timeout': 20,
            'charset': 'utf8mb4',
            'autocommit': True,
            'use_unicode': True,
            'init_command': "SET sql_mode='STRICT_TRANS_TABLES'",
            'binary_prefix': True
        }
    }
    
    # Heroku-optimized LiveKit settings
    SOCKETIO_ASYNC_MODE = 'gevent'  # Better for Heroku
    SOCKETIO_PING_TIMEOUT = 45      # Shorter for Heroku
    SOCKETIO_PING_INTERVAL = 20
    
    # Heroku dyno limits - conservative settings
    MAX_CONCURRENT_STREAMS = 2
    STREAM_VIDEO_MAX_BITRATE = int(os.environ.get('STREAM_VIDEO_MAX_BITRATE', 2500000))  # 2.5 Mbps
    STREAM_AUDIO_MAX_BITRATE = int(os.environ.get('STREAM_AUDIO_MAX_BITRATE', 96000))    # 96 kbps
    
    @classmethod
    def init_app(cls, app):
        ProductionConfig.init_app(app)
        
        # FIXED: Handle proxy headers for Heroku with better error handling
        try:
            from werkzeug.middleware.proxy_fix import ProxyFix
            app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
        except ImportError:
            print("⚠ ProxyFix not available, skipping proxy header handling")
        
        # Log to stdout for Heroku logs
        import logging
        from logging import StreamHandler
        import sys
        
        handler = StreamHandler(sys.stdout)
        handler.setLevel(logging.INFO)
        app.logger.addHandler(handler)
        app.logger.setLevel(logging.INFO)
        app.logger.info('TGFX Trade Lab startup on Heroku with LiveKit streaming')
        
        # Heroku-specific validations
        if not app.config.get('LIVEKIT_URL'):
            print("❌ CRITICAL: Set LIVEKIT_URL environment variable for streaming")
        if not app.config.get('LIVEKIT_API_KEY') or not app.config.get('LIVEKIT_API_SECRET'):
            print("❌ CRITICAL: Set LIVEKIT_API_KEY and LIVEKIT_API_SECRET for streaming")

# Configuration dictionary
config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'heroku': HerokuConfig,
    'default': DevelopmentConfig
}

def get_config():
    """Get configuration based on environment"""
    env = os.environ.get('FLASK_ENV', 'development')
    
    # Auto-detect Heroku environment
    if os.environ.get('DYNO'):
        env = 'heroku'
        print("🚀 Detected Heroku environment with LiveKit streaming support")
    elif os.environ.get('DATABASE_URL') and 'mysql' in os.environ.get('DATABASE_URL', ''):
        env = 'production'
        print("🏭 Detected production environment with MySQL and LiveKit")
    else:
        print("💻 Using development environment with LiveKit")
    
    config_class = config.get(env, config['default'])
    
    # Display LiveKit readiness status
    livekit_url = os.environ.get('LIVEKIT_URL')
    livekit_key = os.environ.get('LIVEKIT_API_KEY')
    livekit_secret = os.environ.get('LIVEKIT_API_SECRET')
    
    if all([livekit_url, livekit_key, livekit_secret]):
        print("✅ LiveKit streaming configuration is complete and ready!")
    else:
        print("⚠️ LiveKit streaming requires configuration - see deployment guide")
    
    return config_class

def validate_livekit_config():
    """Validate LiveKit configuration and provide helpful error messages"""
    errors = []
    warnings = []
    
    # Check required variables
    if not os.environ.get('LIVEKIT_URL'):
        errors.append("LIVEKIT_URL is required (e.g., 'wss://your-project.livekit.cloud')")
    
    if not os.environ.get('LIVEKIT_API_KEY'):
        errors.append("LIVEKIT_API_KEY is required")
    
    if not os.environ.get('LIVEKIT_API_SECRET'):
        errors.append("LIVEKIT_API_SECRET is required")
    
    # Check URL format
    livekit_url = os.environ.get('LIVEKIT_URL', '')
    if livekit_url and not (livekit_url.startswith('ws://') or livekit_url.startswith('wss://')):
        warnings.append(f"LIVEKIT_URL should use WebSocket protocol (ws:// or wss://), got: {livekit_url}")
    
    # Check quality settings
    try:
        video_bitrate = int(os.environ.get('STREAM_VIDEO_MAX_BITRATE', 3000000))
        if video_bitrate > 5000000:  # 5 Mbps
            warnings.append(f"STREAM_VIDEO_MAX_BITRATE is very high ({video_bitrate/1000000:.1f} Mbps) - may cause issues on slower connections")
        elif video_bitrate < 500000:  # 500 kbps
            warnings.append(f"STREAM_VIDEO_MAX_BITRATE is very low ({video_bitrate/1000000:.1f} Mbps) - video quality will be poor")
    except ValueError:
        errors.append("STREAM_VIDEO_MAX_BITRATE must be a valid integer")
    
    try:
        audio_bitrate = int(os.environ.get('STREAM_AUDIO_MAX_BITRATE', 128000))
        if audio_bitrate > 320000:  # 320 kbps
            warnings.append(f"STREAM_AUDIO_MAX_BITRATE is very high ({audio_bitrate/1000} kbps)")
        elif audio_bitrate < 32000:  # 32 kbps
            warnings.append(f"STREAM_AUDIO_MAX_BITRATE is very low ({audio_bitrate/1000} kbps) - audio quality will be poor")
    except ValueError:
        errors.append("STREAM_AUDIO_MAX_BITRATE must be a valid integer")
    
    # Print results
    if errors:
        print("\n❌ LiveKit Configuration Errors:")
        for error in errors:
            print(f"   • {error}")
    
    if warnings:
        print("\n⚠️ LiveKit Configuration Warnings:")
        for warning in warnings:
            print(f"   • {warning}")
    
    if not errors and not warnings:
        print("\n✅ LiveKit configuration validated successfully!")
    
    return len(errors) == 0

# Run validation if called directly
if __name__ == '__main__':
    validate_livekit_config()
