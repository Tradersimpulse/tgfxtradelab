#!/usr/bin/env python3
"""
Database initialization script for TGFX Trade Lab - MySQL Compatible
This script creates all database tables and initial data
Updated for dual streaming functionality (Ray + Jordan)
"""

import os
import sys
from datetime import datetime
from werkzeug.security import generate_password_hash

# Add the current directory to the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from app import app, db, User, Category, Video, Stream, StreamViewer
except ImportError as e:
    print(f"❌ Import error: {e}")
    print("Make sure app.py is in the same directory")
    sys.exit(1)

def test_database_connection():
    """Test the database connection - SQLAlchemy 2.0 compatible"""
    try:
        with app.app_context():
            # Test connection by attempting to create tables
            db.create_all()
            print("✓ Database connection successful")
            return True
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        print("Please check your database configuration and ensure MySQL is running")
        return False

def create_admin_user():
    """Create default admin user (Ray) if it doesn't exist"""
    admin_username = os.environ.get('ADMIN_USERNAME', 'admin')
    admin_email = os.environ.get('ADMIN_EMAIL', 'ray@tgfx-academy.com')
    admin_password = os.environ.get('ADMIN_PASSWORD', 'admin123!345gdfb3f35')
    
    try:
        # Check if admin user already exists
        admin_user = User.query.filter_by(username=admin_username).first()
        if not admin_user:
            admin_user = User(
                username=admin_username,
                email=admin_email,
                password_hash=generate_password_hash(admin_password),
                is_admin=True,
                has_subscription=True,
                display_name='Ray',           # NEW: Display name for streaming
                can_stream=True,              # NEW: Streaming permission
                stream_color='#10B981'        # NEW: Green color for Ray
            )
            db.session.add(admin_user)
            print(f"✓ Created admin user (Ray): {admin_username}")
        else:
            # Update existing admin user with streaming capabilities
            admin_user.display_name = 'Ray'
            admin_user.can_stream = True
            admin_user.stream_color = '#10B981'  # Green
            admin_user.email = admin_email  # Update email if needed
            print(f"✓ Updated existing admin user (Ray): {admin_username}")
        
        return admin_user
    except Exception as e:
        print(f"❌ Error creating/updating admin user: {e}")
        return None

def create_jordan_user():
    """Create Jordan as a streaming user"""
    try:
        jordan_user = User.query.filter_by(username='jordan').first()
        if not jordan_user:
            jordan_user = User(
                username='jordan',
                email='jordan@tgfx-academy.com',
                password_hash=generate_password_hash('jordan123!secure'),
                is_admin=True,                # Jordan is also an admin
                has_subscription=True,
                display_name='Jordan',        # Display name for streaming
                can_stream=True,              # Streaming permission
                stream_color='#3B82F6'        # Blue color for Jordan
            )
            db.session.add(jordan_user)
            print("✓ Created Jordan streaming user: jordan")
        else:
            # Update existing Jordan user with streaming capabilities
            jordan_user.display_name = 'Jordan'
            jordan_user.can_stream = True
            jordan_user.stream_color = '#3B82F6'  # Blue
            jordan_user.is_admin = True
            jordan_user.has_subscription = True
            print("✓ Updated existing Jordan user with streaming capabilities")
        
        return jordan_user
    except Exception as e:
        print(f"❌ Error creating/updating Jordan user: {e}")
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
                has_subscription=False,
                display_name='Demo User',     # NEW: Display name
                can_stream=False,             # NEW: No streaming permission
                stream_color='#10B981'        # NEW: Default color
            )
            db.session.add(demo_user)
            print("✓ Created demo user: demo")
        else:
            # Update existing demo user with new fields
            if not demo_user.display_name:
                demo_user.display_name = 'Demo User'
            if demo_user.stream_color is None:
                demo_user.stream_color = '#10B981'
            demo_user.can_stream = False  # Ensure demo can't stream
            print("✓ Updated existing demo user")
        
        return demo_user
    except Exception as e:
        print(f"❌ Error creating/updating demo user: {e}")
        return None

def update_existing_users():
    """Update existing users with new streaming fields"""
    try:
        # Get all users that aren't admin, jordan, or demo
        existing_users = User.query.filter(
            ~User.username.in_(['admin', 'jordan', 'demo'])
        ).all()
        
        updated_count = 0
        for user in existing_users:
            # Add display name if missing
            if not user.display_name:
                user.display_name = user.username.title()
                
            # Add stream color if missing
            if user.stream_color is None:
                user.stream_color = '#10B981'  # Default green
                
            # Ensure can_stream is set (default to False for regular users)
            if not hasattr(user, 'can_stream') or user.can_stream is None:
                user.can_stream = False
                
            updated_count += 1
        
        if updated_count > 0:
            print(f"✓ Updated {updated_count} existing users with streaming fields")
        else:
            print("✓ No existing users to update")
            
    except Exception as e:
        print(f"❌ Error updating existing users: {e}")

def cleanup_old_streams():
    """Clean up any old streams that might be stuck as 'active'"""
    try:
        # Mark any old streams as inactive (safety measure)
        old_streams = Stream.query.filter_by(is_active=True).all()
        
        for stream in old_streams:
            stream.is_active = False
            if not stream.ended_at:
                stream.ended_at = datetime.utcnow()
        
        if old_streams:
            print(f"✓ Cleaned up {len(old_streams)} old active streams")
        else:
            print("✓ No old streams to clean up")
            
        # Also clean up orphaned stream viewers
        from sqlalchemy import text
        orphaned_viewers = StreamViewer.query.filter_by(is_active=True).all()
        for viewer in orphaned_viewers:
            viewer.is_active = False
            viewer.left_at = datetime.utcnow()
            
        if orphaned_viewers:
            print(f"✓ Cleaned up {len(orphaned_viewers)} orphaned stream viewers")
            
    except Exception as e:
        print(f"❌ Error cleaning up old streams: {e}")

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
        },
        {
            'name': 'Live Trading Sessions',  # NEW: Category for live streams
            'description': 'Join Ray and Jordan for live trading sessions and market analysis.',
            'order_index': 7
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
        },
        {
            'title': 'Ray\'s Morning Market Analysis',
            'description': 'Recorded live session: Ray breaks down the morning market setup and key levels to watch.',
            'category': 'Live Trading Sessions',
            'is_free': False,
            'order_index': 1,
            's3_url': 'https://tgfx-tradelab.s3.amazonaws.com/livestream-recordings/sample/Ray-stream-1.mp4',
            'duration': 2700  # 45 minutes
        },
        {
            'title': 'Jordan\'s Technical Analysis Deep Dive',
            'description': 'Recorded live session: Jordan explains advanced chart patterns and trading setups.',
            'category': 'Live Trading Sessions',
            'is_free': False,
            'order_index': 2,
            's3_url': 'https://tgfx-tradelab.s3.amazonaws.com/livestream-recordings/sample/Jordan-stream-1.mp4',
            'duration': 3300  # 55 minutes
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
    """Set up proper charset for MySQL - SQLAlchemy 2.0 compatible"""
    try:
        # Only run if using MySQL
        db_uri = app.config.get('SQLALCHEMY_DATABASE_URI', '')
        if 'mysql' in db_uri:
            from sqlalchemy import text
            with db.engine.connect() as conn:
                conn.execute(text('SET NAMES utf8mb4'))
                conn.execute(text('SET CHARACTER SET utf8mb4'))
                conn.execute(text('SET character_set_connection=utf8mb4'))
                conn.commit()
            print("✓ MySQL charset configured")
    except Exception as e:
        print(f"⚠ MySQL charset setup warning: {e}")

def display_streaming_info():
    """Display streaming setup information"""
    try:
        with app.app_context():
            ray = User.query.filter_by(username='admin').first()
            jordan = User.query.filter_by(username='jordan').first()
            
            print("\n🎥 STREAMING SETUP COMPLETE!")
            print("=" * 50)
            
            if ray:
                print(f"🟢 Ray (admin):")
                print(f"   • Username: {ray.username}")
                print(f"   • Display Name: {ray.display_name}")
                print(f"   • Can Stream: {ray.can_stream}")
                print(f"   • Stream Color: {ray.stream_color} (Green)")
                print(f"   • Admin: {ray.is_admin}")
            
            if jordan:
                print(f"🔵 Jordan:")
                print(f"   • Username: {jordan.username}")
                print(f"   • Display Name: {jordan.display_name}")
                print(f"   • Can Stream: {jordan.can_stream}")
                print(f"   • Stream Color: {jordan.stream_color} (Blue)")
                print(f"   • Admin: {jordan.is_admin}")
            
            total_users = User.query.count()
            streaming_users = User.query.filter_by(can_stream=True).count()
            
            print(f"\n📊 Database Stats:")
            print(f"   • Total Users: {total_users}")
            print(f"   • Streaming Users: {streaming_users}")
            print(f"   • Categories: {Category.query.count()}")
            print(f"   • Videos: {Video.query.count()}")
            print(f"   • Total Streams (all time): {Stream.query.count()}")
            print(f"   • Active Streams: {Stream.query.filter_by(is_active=True).count()}")
            
            print(f"\n🌐 Access URLs:")
            print(f"   • Live Streams: /livestream")
            print(f"   • Admin Stream Control: /admin/stream")
            print(f"   • Admin Dashboard: /admin")
            
    except Exception as e:
        print(f"❌ Error displaying streaming info: {e}")

def main():
    """Main initialization function"""
    print("🚀 Initializing TGFX Trade Lab database with dual streaming...")
    
    # Test database connection first
    if not test_database_connection():
        print("❌ Cannot proceed without database connection")
        sys.exit(1)
    
    with app.app_context():
        try:
            # Set up MySQL charset
            setup_mysql_charset()
            
            # Create all database tables (already done in test_database_connection)
            print("✓ Database tables created successfully")
            
            # Clean up any old streams first
            print("🧹 Cleaning up old streams...")
            cleanup_old_streams()
            
            # Create admin user (Ray)
            print("👤 Setting up Ray (admin user)...")
            admin_user = create_admin_user()
            
            # Create Jordan user
            print("👤 Setting up Jordan (streaming user)...")
            jordan_user = create_jordan_user()
            
            # Create demo user
            print("🎭 Setting up demo user...")
            demo_user = create_demo_user()
            
            # Update existing users with new fields
            print("🔄 Updating existing users...")
            update_existing_users()
            
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
            
            # Display streaming setup info
            display_streaming_info()
            
            # Display database info
            db_uri = app.config.get('SQLALCHEMY_DATABASE_URI', '')
            if 'mysql' in db_uri:
                print(f"\n🗄️ Using MySQL database")
            else:
                print(f"\n🗄️ Using database: {db_uri}")
            
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
