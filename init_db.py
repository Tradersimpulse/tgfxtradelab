#!/usr/bin/env python3
"""
Database initialization script for TGFX Trade Lab - MySQL Compatible
This script creates all database tables and initial data
"""

import os
import sys
from datetime import datetime
from werkzeug.security import generate_password_hash

# Add the current directory to the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from app import app, db, User, Category, Video
except ImportError as e:
    print(f"❌ Import error: {e}")
    print("Make sure flask_app_main.py is in the same directory")
    sys.exit(1)

def test_database_connection():
    """Test the database connection"""
    try:
        with app.app_context():
            # Try to execute a simple query
            db.engine.execute('SELECT 1')
            print("✓ Database connection successful")
            return True
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        print("Please check your database configuration and ensure MySQL is running")
        return False

def create_admin_user():
    """Create default admin user if it doesn't exist"""
    admin_username = os.environ.get('ADMIN_USERNAME', 'admin')
    admin_email = os.environ.get('ADMIN_EMAIL', 'admin@tgfxtradelab.com')
    admin_password = os.environ.get('ADMIN_PASSWORD', 'admin123')
    
    try:
        # Check if admin user already exists
        admin_user = User.query.filter_by(username=admin_username).first()
        if not admin_user:
            admin_user = User(
                username=admin_username,
                email=admin_email,
                password_hash=generate_password_hash(admin_password),
                is_admin=True,
                has_subscription=True
            )
            db.session.add(admin_user)
            print(f"✓ Created admin user: {admin_username}")
        else:
            print(f"✓ Admin user already exists: {admin_username}")
        
        return admin_user
    except Exception as e:
        print(f"❌ Error creating admin user: {e}")
        return None

def create_demo_user():
    """Create demo user for testing"""
    try:
        demo_user = User.query.filter_by(username='demo').first()
        if not demo_user:
            demo_user = User(
                username='demo',
                email='demo@tgfxtradelab.com',
                password_hash=generate_password_hash('demo123'),
                is_admin=False,
                has_subscription=False
            )
            db.session.add(demo_user)
            print("✓ Created demo user: demo")
        else:
            print("✓ Demo user already exists: demo")
        
        return demo_user
    except Exception as e:
        print(f"❌ Error creating demo user: {e}")
        return None

def create_initial_categories():
    """Create initial course categories"""
    initial_categories = [
        {
            'name': 'Beginner Fundamentals',
            'description': 'Start your trading journey with essential concepts and basic strategies.',
            'order_index': 1
        },
        {
            'name': 'Technical Analysis',
            'description': 'Learn to read charts, identify patterns, and use technical indicators.',
            'order_index': 2
        },
        {
            'name': 'Risk Management',
            'description': 'Master the art of protecting your capital and managing trading risk.',
            'order_index': 3
        },
        {
            'name': 'Trading Psychology',
            'description': 'Develop the mental skills needed for consistent trading success.',
            'order_index': 4
        },
        {
            'name': 'Advanced Strategies',
            'description': 'Professional trading techniques and advanced market analysis.',
            'order_index': 5
        },
        {
            'name': 'Market Analysis',
            'description': 'Stay ahead with current market trends and economic analysis.',
            'order_index': 6
        }
    ]
    
    created_categories = []
    try:
        for cat_data in initial_categories:
            existing_category = Category.query.filter_by(name=cat_data['name']).first()
            if not existing_category:
                category = Category(**cat_data)
                db.session.add(category)
                created_categories.append(category)
                print(f"✓ Created category: {cat_data['name']}")
            else:
                created_categories.append(existing_category)
                print(f"✓ Category already exists: {cat_data['name']}")
        
        return created_categories
    except Exception as e:
        print(f"❌ Error creating categories: {e}")
        return []

def create_sample_videos(categories):
    """Create sample videos for demonstration"""
    if not categories:
        print("⚠ No categories found, skipping sample videos")
        return
    
    # Sample video data
    sample_videos = [
        {
            'title': 'What is Trading? Complete Beginner Guide',
            'description': 'Learn the basics of trading, different markets, and how to get started as a complete beginner.',
            'category': 'Beginner Fundamentals',
            'is_free': True,
            'order_index': 1,
            's3_url': 'https://sample-bucket.s3.amazonaws.com/sample-video-1.mp4',
            'duration': 1200  # 20 minutes
        },
        {
            'title': 'Understanding Market Orders and Limit Orders',
            'description': 'Master the different types of orders and when to use each one for optimal trade execution.',
            'category': 'Beginner Fundamentals',
            'is_free': True,
            'order_index': 2,
            's3_url': 'https://sample-bucket.s3.amazonaws.com/sample-video-2.mp4',
            'duration': 900  # 15 minutes
        },
        {
            'title': 'Introduction to Candlestick Patterns',
            'description': 'Learn to read candlestick charts and identify common reversal and continuation patterns.',
            'category': 'Technical Analysis',
            'is_free': False,
            'order_index': 1,
            's3_url': 'https://sample-bucket.s3.amazonaws.com/sample-video-3.mp4',
            'duration': 1800  # 30 minutes
        },
        {
            'title': 'Position Sizing and Risk Management',
            'description': 'Discover how to calculate proper position sizes and protect your trading capital.',
            'category': 'Risk Management',
            'is_free': False,
            'order_index': 1,
            's3_url': 'https://sample-bucket.s3.amazonaws.com/sample-video-4.mp4',
            'duration': 1500  # 25 minutes
        }
    ]
    
    try:
        # Create category lookup
        category_lookup = {cat.name: cat for cat in categories}
        
        for video_data in sample_videos:
            category_name = video_data.pop('category')
            category = category_lookup.get(category_name)
            
            if category:
                existing_video = Video.query.filter_by(title=video_data['title']).first()
                if not existing_video:
                    video = Video(
                        category_id=category.id,
                        **video_data
                    )
                    db.session.add(video)
                    print(f"✓ Created sample video: {video_data['title']}")
                else:
                    print(f"✓ Sample video already exists: {video_data['title']}")
    except Exception as e:
        print(f"❌ Error creating sample videos: {e}")

def setup_mysql_charset():
    """Set up proper charset for MySQL"""
    try:
        # Only run if using MySQL
        db_uri = app.config.get('SQLALCHEMY_DATABASE_URI', '')
        if 'mysql' in db_uri:
            db.engine.execute('SET NAMES utf8mb4')
            db.engine.execute('SET CHARACTER SET utf8mb4')
            db.engine.execute('SET character_set_connection=utf8mb4')
            print("✓ MySQL charset configured")
    except Exception as e:
        print(f"⚠ MySQL charset setup warning: {e}")

def main():
    """Main initialization function"""
    print("🚀 Initializing TGFX Trade Lab database with MySQL...")
    
    # Test database connection first
    if not test_database_connection():
        print("❌ Cannot proceed without database connection")
        sys.exit(1)
    
    with app.app_context():
        try:
            # Set up MySQL charset
            setup_mysql_charset()
            
            # Create all database tables
            print("📊 Creating database tables...")
            db.create_all()
            print("✓ Database tables created successfully")
            
            # Create admin user
            print("👤 Setting up admin user...")
            admin_user = create_admin_user()
            
            # Create demo user
            print("🎭 Setting up demo user...")
            demo_user = create_demo_user()
            
            # Create initial categories
            print("📁 Creating initial categories...")
            categories = create_initial_categories()
            
            # Create sample videos (only in development)
            env = os.environ.get('FLASK_ENV', 'development')
            if env == 'development' and categories:
                print("🎬 Creating sample videos...")
                create_sample_videos(categories)
            else:
                print("⏭ Skipping sample videos (production environment)")
            
            # Commit all changes
            db.session.commit()
            
            print("✅ Database initialization completed successfully!")
            print(f"🔑 Admin credentials: {admin_user.username if admin_user else 'admin'} / {os.environ.get('ADMIN_PASSWORD', 'admin123')}")
            print(f"🎭 Demo credentials: demo / demo123")
            
            # Display database info
            db_uri = app.config.get('SQLALCHEMY_DATABASE_URI', '')
            if 'mysql' in db_uri:
                print(f"🗄️ Using MySQL database")
            else:
                print(f"🗄️ Using database: {db_uri}")
            
        except Exception as e:
            print(f"❌ Error during database initialization: {str(e)}")
            db.session.rollback()
            
            # Print more detailed error info for debugging
            import traceback
            print("\n📝 Detailed error trace:")
            traceback.print_exc()
            
            sys.exit(1)

if __name__ == '__main__':
    main()
