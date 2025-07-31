"""
Configuration settings for TGFX Trade Lab - MySQL + Heroku Optimized
"""

import os
from datetime import timedelta

class Config:
    """Base configuration class"""
    
    # Flask Core Settings
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-change-in-production'
    
    # MySQL Database Configuration
    @staticmethod
    def get_database_uri():
        """Build MySQL database URI from environment variables"""
        # Check for direct DATABASE_URL first (Heroku style)
        database_url = os.environ.get('DATABASE_URL')
        if database_url:
            # Handle potential postgres:// URLs from Heroku and convert to mysql://
            if database_url.startswith('postgres://'):
                # Convert postgres to mysql format if needed
                pass
            return database_url
        
        # Build MySQL URI from individual components
        db_host = os.environ.get('DB_HOST', 'localhost')
        db_user = os.environ.get('DB_USER', 'root')
        db_password = os.environ.get('DB_PASSWORD', '')
        db_name = os.environ.get('DB_NAME', 'tgfx_trade_lab')
        db_port = os.environ.get('DB_PORT', '3306')
        
        if all([db_host, db_user, db_name]):
            return f"mysql+pymysql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
        
        # Fallback to SQLite for local development
        return 'sqlite:///tgfx_trade_lab.db'
    
    SQLALCHEMY_DATABASE_URI = get_database_uri.__func__()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # MySQL-specific engine options
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,
        'pool_recycle': 300,
        'pool_size': 10,
        'max_overflow': 20,
        'pool_timeout': 30,
        'echo': False
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
    AWS_CHIME_REGION = os.environ.get('AWS_CHIME_REGION') or 'us-east-1'
    
    # S3 bucket for stream recordings
    STREAM_RECORDINGS_BUCKET = os.environ.get('STREAM_RECORDINGS_BUCKET') or 'tgfx-tradelab'
    STREAM_RECORDINGS_PREFIX = os.environ.get('STREAM_RECORDINGS_PREFIX') or 'livestream-recordings/'
    
    # Chime SDK settings
    CHIME_MEETING_EXPIRY_MINUTES = int(os.environ.get('CHIME_MEETING_EXPIRY_MINUTES', 240))  # 4 hours default
    
    @staticmethod
    def init_app(app):
        """Initialize application-specific configuration"""
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
    
    # Enable SQL logging in development
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,
        'pool_recycle': 300,
        'echo': True  # Log SQL queries in development
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
    
    # Optimized MySQL settings for production
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,
        'pool_recycle': 300,
        'pool_size': 10,
        'max_overflow': 20,
        'pool_timeout': 30,
        'connect_args': {
            'connect_timeout': 60,
            'read_timeout': 60,
            'write_timeout': 60,
            'charset': 'utf8mb4',
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
    """Heroku-specific configuration"""
    
    @classmethod
    def init_app(cls, app):
        ProductionConfig.init_app(app)
        
        # Handle proxy headers for Heroku
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
        
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
    elif os.environ.get('DATABASE_URL') and 'mysql' in os.environ.get('DATABASE_URL', ''):
        env = 'production'
    
    return config.get(env, config['default'])
