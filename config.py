"""
Configuration settings for TGFX Trade Lab - MySQL + Heroku Optimized
FIXED: DNS resolution, database connection, and eventlet compatibility issues
"""

import os
from datetime import timedelta

class Config:
    """Base configuration class"""
    
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

    # AWS Chime SDK Configuration
    AWS_CHIME_REGION = os.environ.get('AWS_CHIME_REGION', 'us-east-1')
    
    # S3 bucket for stream recordings
    STREAM_RECORDINGS_BUCKET = os.environ.get('STREAM_RECORDINGS_BUCKET', 'tgfx-tradelab')
    STREAM_RECORDINGS_PREFIX = os.environ.get('STREAM_RECORDINGS_PREFIX', 'livestream-recordings/')
    
    # Chime SDK settings
    CHIME_MEETING_EXPIRY_MINUTES = int(os.environ.get('CHIME_MEETING_EXPIRY_MINUTES', 240))  # 4 hours default
    
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
        
        # Validate required AWS Chime settings
        required_vars = [
            'AWS_ACCESS_KEY_ID',
            'AWS_SECRET_ACCESS_KEY',
            'AWS_CHIME_REGION'
        ]
        
        missing_vars = []
        for var in required_vars:
            if not app.config.get(var):
                missing_vars.append(var)
        
        if missing_vars:
            print(f"⚠ Warning: Missing AWS Chime configuration: {', '.join(missing_vars)}")
            print("  Live streaming features may not work properly.")
        else:
            print("✓ AWS Chime SDK configuration loaded")

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

    AWS_CHIME_REGION = 'us-east-1'

class ProductionConfig(Config):
    """Production configuration for Heroku"""
    DEBUG = False
    DEVELOPMENT = False
    
    # Force HTTPS in production
    SESSION_COOKIE_SECURE = True
    PREFERRED_URL_SCHEME = 'https'

    # Production Chime settings
    AWS_CHIME_REGION = os.environ.get('AWS_CHIME_REGION', 'us-east-1')
    
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
        app.logger.info('TGFX Trade Lab startup on Heroku')

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
        print("🚀 Detected Heroku environment")
    elif os.environ.get('DATABASE_URL') and 'mysql' in os.environ.get('DATABASE_URL', ''):
        env = 'production'
        print("🏭 Detected production environment with MySQL")
    else:
        print("💻 Using development environment")
    
    return config.get(env, config['default'])
