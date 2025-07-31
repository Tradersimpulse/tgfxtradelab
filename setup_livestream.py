#!/usr/bin/env python3
"""
Quick setup script to get your livestreaming working
Run this after updating your files
"""

import os
import sys
from app import app, db, User, Stream
from werkzeug.security import generate_password_hash
from sqlalchemy import text

def setup_database():
    """Set up database with required changes"""
    print("🗄️  Setting up database...")
    
    with app.app_context():
        try:
            # Try to add the signaling_url column
            print("📡 Adding signaling_url column to streams table...")
            db.engine.execute(text("ALTER TABLE streams ADD COLUMN signaling_url VARCHAR(500)"))
            print("✅ signaling_url column added successfully")
        except Exception as e:
            if "Duplicate column name" in str(e) or "already exists" in str(e):
                print("ℹ️  signaling_url column already exists")
            else:
                print(f"⚠️  Error adding signaling_url column: {e}")
        
        # Update existing users to have streaming permissions
        print("👤 Setting up streaming permissions...")
        
        # Find admin user (Ray)
        ray = User.query.filter_by(username='admin').first()
        if ray:
            ray.display_name = 'Ray'
            ray.can_stream = True
            ray.stream_color = '#10B981'  # Green
            print("✅ Updated Ray's streaming permissions")
        else:
            print("⚠️  Admin user not found")
        
        # Create or update Jordan
        jordan = User.query.filter_by(username='jordan').first()
        if not jordan:
            # Check if there's a user with jordan in the email
            jordan = User.query.filter(User.email.like('%jordan%')).first()
        
        if not jordan:
            jordan = User(
                username='jordan',
                email='jordan@tgfx-academy.com', 
                password_hash=generate_password_hash('jordan123secure'),
                is_admin=True,
                display_name='Jordan',
                can_stream=True,
                stream_color='#3B82F6'  # Blue
            )
            db.session.add(jordan)
            print("✅ Created Jordan user with streaming permissions")
        else:
            jordan.display_name = 'Jordan'
            jordan.can_stream = True
            jordan.stream_color = '#3B82F6'  # Blue
            jordan.is_admin = True
            print("✅ Updated Jordan's streaming permissions")
        
        # Commit changes
        db.session.commit()
        print("💾 Database changes committed")
        
        # Show current streamers
        streamers = User.query.filter_by(can_stream=True).all()
        print(f"\n📺 Current streamers ({len(streamers)}):")
        for streamer in streamers:
            print(f"  - {streamer.display_name or streamer.username} ({streamer.username}) - {streamer.stream_color}")
            
        return True
        
    except Exception as e:
        print(f"❌ Database setup failed: {e}")
        return False

def check_environment():
    """Check if environment is properly configured"""
    print("🌍 Checking environment configuration...")
    
    required_vars = [
        'AWS_ACCESS_KEY_ID',
        'AWS_SECRET_ACCESS_KEY', 
        'AWS_REGION'
    ]
    
    missing_vars = []
    for var in required_vars:
        if not os.getenv(var):
            missing_vars.append(var)
        else:
            print(f"✅ {var}: {os.getenv(var)[:8]}..." if 'KEY' in var else f"✅ {var}: {os.getenv(var)}")
    
    if missing_vars:
        print(f"⚠️  Missing environment variables: {', '.join(missing_vars)}")
        return False
    else:
        print("✅ All required environment variables are set")
        return True

def test_basic_functionality():
    """Test basic app functionality"""
    print("🧪 Testing basic functionality...")
    
    with app.app_context():
        try:
            # Test database connection
            user_count = User.query.count()
            print(f"✅ Database connection working - {user_count} users found")
            
            # Test streaming users
            streaming_users = User.query.filter_by(can_stream=True).count()
            print(f"✅ Streaming setup working - {streaming_users} users can stream")
            
            return True
            
        except Exception as e:
            print(f"❌ Basic functionality test failed: {e}")
            return False

def main():
    """Main setup function"""
    print("🚀 TGFX Trade Lab Livestream Setup")
    print("=" * 50)
    
    # Check environment
    env_ok = check_environment()
    
    # Set up database
    db_ok = setup_database()
    
    # Test functionality  
    test_ok = test_basic_functionality()
    
    print("\n" + "=" * 50)
    
    if env_ok and db_ok and test_ok:
        print("🎉 Setup completed successfully!")
        print("\n📋 Next steps:")
        print("1. Replace your livestream.html with the new version")
        print("2. Deploy to Heroku")
        print("3. Test streaming from /admin/stream")
        print("4. Test viewing from /livestream")
        print("\n💡 The new livestream page works in demo mode")
        print("   Users will see animated demo content until real")
        print("   Chime SDK integration is complete.")
        
    else:
        print("❌ Setup completed with issues")
        print("\n🔧 Issues to fix:")
        if not env_ok:
            print("- Set missing environment variables")
        if not db_ok:
            print("- Fix database setup issues")
        if not test_ok:
            print("- Resolve functionality test failures")
            
    print(f"\n📊 Summary:")
    print(f"Environment: {'✅' if env_ok else '❌'}")
    print(f"Database: {'✅' if db_ok else '❌'}")
    print(f"Functionality: {'✅' if test_ok else '❌'}")

if __name__ == '__main__':
    main()
