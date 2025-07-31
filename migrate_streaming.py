#!/usr/bin/env python3
"""
Database migration script for TGFX Trade Lab - Add Streaming Columns
This script safely adds the new streaming columns to existing database
"""

import os
import sys
from datetime import datetime

# Add the current directory to the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from app import app, db
    from sqlalchemy import text, inspect
except ImportError as e:
    print(f"❌ Import error: {e}")
    print("Make sure app.py is in the same directory")
    sys.exit(1)

def check_column_exists(table_name, column_name):
    """Check if a column exists in a table"""
    try:
        inspector = inspect(db.engine)
        columns = [col['name'] for col in inspector.get_columns(table_name)]
        return column_name in columns
    except Exception as e:
        print(f"Error checking column {column_name} in {table_name}: {e}")
        return False

def add_streaming_columns():
    """Add streaming columns to users table"""
    print("🔧 Adding streaming columns to users table...")
    
    try:
        with db.engine.connect() as conn:
            # Check and add display_name column
            if not check_column_exists('users', 'display_name'):
                conn.execute(text("""
                    ALTER TABLE users 
                    ADD COLUMN display_name VARCHAR(100) NULL
                """))
                print("✓ Added display_name column")
            else:
                print("✓ display_name column already exists")
            
            # Check and add can_stream column
            if not check_column_exists('users', 'can_stream'):
                conn.execute(text("""
                    ALTER TABLE users 
                    ADD COLUMN can_stream BOOLEAN NOT NULL DEFAULT FALSE
                """))
                print("✓ Added can_stream column")
            else:
                print("✓ can_stream column already exists")
            
            # Check and add stream_color column
            if not check_column_exists('users', 'stream_color'):
                conn.execute(text("""
                    ALTER TABLE users 
                    ADD COLUMN stream_color VARCHAR(7) NOT NULL DEFAULT '#10B981'
                """))
                print("✓ Added stream_color column")
            else:
                print("✓ stream_color column already exists")
            
            conn.commit()
            
    except Exception as e:
        print(f"❌ Error adding streaming columns: {e}")
        raise

def add_streaming_columns_to_streams():
    """Add streaming columns to streams table"""
    print("🔧 Adding streaming columns to streams table...")
    
    try:
        with db.engine.connect() as conn:
            # Check and add streamer_name column
            if not check_column_exists('streams', 'streamer_name'):
                conn.execute(text("""
                    ALTER TABLE streams 
                    ADD COLUMN streamer_name VARCHAR(100) NULL
                """))
                print("✓ Added streamer_name column to streams")
            else:
                print("✓ streamer_name column already exists in streams")
            
            # Check and add stream_type column
            if not check_column_exists('streams', 'stream_type'):
                conn.execute(text("""
                    ALTER TABLE streams 
                    ADD COLUMN stream_type VARCHAR(50) NOT NULL DEFAULT 'general'
                """))
                print("✓ Added stream_type column to streams")
            else:
                print("✓ stream_type column already exists in streams")
            
            conn.commit()
            
    except Exception as e:
        print(f"❌ Error adding streaming columns to streams: {e}")
        raise

def create_streaming_tables():
    """Create streaming tables if they don't exist"""
    print("🔧 Creating streaming tables...")
    
    try:
        # Check if streams table exists
        inspector = inspect(db.engine)
        tables = inspector.get_table_names()
        
        if 'streams' not in tables:
            print("Creating streams table...")
            with db.engine.connect() as conn:
                conn.execute(text("""
                    CREATE TABLE streams (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        title VARCHAR(200) NOT NULL,
                        description TEXT,
                        meeting_id VARCHAR(100) UNIQUE,
                        attendee_id VARCHAR(100),
                        external_meeting_id VARCHAR(100) UNIQUE,
                        media_region VARCHAR(50),
                        media_placement_audio_host_url VARCHAR(500),
                        media_placement_screen_sharing_url VARCHAR(500),
                        media_placement_screen_data_url VARCHAR(500),
                        is_active BOOLEAN NOT NULL DEFAULT FALSE,
                        is_recording BOOLEAN NOT NULL DEFAULT FALSE,
                        recording_url VARCHAR(500),
                        viewer_count INT NOT NULL DEFAULT 0,
                        started_at DATETIME,
                        ended_at DATETIME,
                        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        streamer_name VARCHAR(100),
                        stream_type VARCHAR(50) NOT NULL DEFAULT 'general',
                        created_by INT NOT NULL,
                        INDEX idx_created_by (created_by),
                        INDEX idx_is_active (is_active),
                        FOREIGN KEY (created_by) REFERENCES users(id)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """))
                conn.commit()
                print("✓ Created streams table")
        else:
            print("✓ Streams table already exists")
            # Add missing columns if table exists
            add_streaming_columns_to_streams()
        
        if 'stream_viewers' not in tables:
            print("Creating stream_viewers table...")
            with db.engine.connect() as conn:
                conn.execute(text("""
                    CREATE TABLE stream_viewers (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        attendee_id VARCHAR(100) NOT NULL,
                        external_user_id VARCHAR(100) NOT NULL,
                        joined_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        left_at DATETIME,
                        is_active BOOLEAN NOT NULL DEFAULT TRUE,
                        stream_id INT NOT NULL,
                        user_id INT NOT NULL,
                        INDEX idx_stream_id (stream_id),
                        INDEX idx_user_id (user_id),
                        INDEX idx_is_active (is_active),
                        UNIQUE KEY unique_stream_viewer (stream_id, user_id),
                        FOREIGN KEY (stream_id) REFERENCES streams(id) ON DELETE CASCADE,
                        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """))
                conn.commit()
                print("✓ Created stream_viewers table")
        else:
            print("✓ Stream_viewers table already exists")
            
    except Exception as e:
        print(f"❌ Error creating streaming tables: {e}")
        raise

def update_user_data():
    """Update existing users with streaming data"""
    print("👤 Updating user streaming data...")
    
    try:
        with db.engine.connect() as conn:
            # Update admin user (Ray)
            result = conn.execute(text("""
                UPDATE users 
                SET display_name = 'Ray', 
                    can_stream = TRUE, 
                    stream_color = '#10B981',
                    email = 'ray@tgfx-academy.com'
                WHERE username = 'admin'
            """))
            
            if result.rowcount > 0:
                print("✓ Updated Ray (admin) with streaming capabilities")
            else:
                print("⚠ Admin user not found - will be created later")
            
            # Create or update Jordan
            result = conn.execute(text("""
                SELECT id FROM users WHERE username = 'jordan'
            """))
            
            if result.fetchone():
                # Update existing Jordan
                conn.execute(text("""
                    UPDATE users 
                    SET display_name = 'Jordan',
                        can_stream = TRUE,
                        stream_color = '#3B82F6',
                        is_admin = TRUE,
                        has_subscription = TRUE
                    WHERE username = 'jordan'
                """))
                print("✓ Updated existing Jordan user")
            else:
                # Create Jordan
                from werkzeug.security import generate_password_hash
                password_hash = generate_password_hash('jordan123!secure')
                
                conn.execute(text("""
                    INSERT INTO users (username, email, password_hash, is_admin, has_subscription, display_name, can_stream, stream_color, created_at)
                    VALUES ('jordan', 'jordan@tgfx-academy.com', :password_hash, TRUE, TRUE, 'Jordan', TRUE, '#3B82F6', NOW())
                """), {"password_hash": password_hash})
                print("✓ Created Jordan user")
            
            # Update other users with default values
            conn.execute(text("""
                UPDATE users 
                SET display_name = COALESCE(display_name, CONCAT(UPPER(SUBSTRING(username, 1, 1)), SUBSTRING(username, 2))),
                    stream_color = COALESCE(stream_color, '#10B981'),
                    can_stream = COALESCE(can_stream, FALSE)
                WHERE display_name IS NULL OR stream_color IS NULL OR can_stream IS NULL
            """))
            
            conn.commit()
            print("✓ Updated all users with streaming defaults")
            
    except Exception as e:
        print(f"❌ Error updating user data: {e}")
        raise

def test_streaming_setup():
    """Test that streaming setup is working"""
    print("🧪 Testing streaming setup...")
    
    try:
        with db.engine.connect() as conn:
            # Test user queries
            result = conn.execute(text("""
                SELECT username, display_name, can_stream, stream_color, is_admin
                FROM users 
                WHERE can_stream = TRUE
            """))
            
            streamers = result.fetchall()
            print(f"✓ Found {len(streamers)} streaming users:")
            
            for streamer in streamers:
                print(f"   • {streamer[1]} ({streamer[0]}): {streamer[3]}")
            
            # Test table structure
            tables = ['users', 'streams', 'stream_viewers']
            for table in tables:
                result = conn.execute(text(f"SELECT COUNT(*) FROM {table}"))
                count = result.fetchone()[0]
                print(f"✓ {table} table: {count} records")
                
    except Exception as e:
        print(f"❌ Error testing streaming setup: {e}")
        raise

def main():
    """Main migration function"""
    print("🚀 Migrating TGFX Trade Lab database for streaming...")
    
    with app.app_context():
        try:
            # Add streaming columns to users table
            add_streaming_columns()
            
            # Create streaming tables
            create_streaming_tables()
            
            # Update user data
            update_user_data()
            
            # Test the setup
            test_streaming_setup()
            
            print("✅ Database migration completed successfully!")
            print("\n🎥 Streaming is now ready!")
            print("   • Ray can stream with green theme")
            print("   • Jordan can stream with blue theme")
            print("   • Visit /admin/stream to start streaming")
            print("   • Visit /livestream to watch streams")
            
        except Exception as e:
            print(f"❌ Migration failed: {e}")
            
            # Print detailed error info
            import traceback
            print("\n📝 Detailed error trace:")
            traceback.print_exc()
            
            sys.exit(1)

if __name__ == '__main__':
    main()
