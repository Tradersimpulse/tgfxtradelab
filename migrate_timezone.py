#!/usr/bin/env python3
"""
Database Migration Script for TGFX Trade Lab
This script adds the missing timezone column to the users table
"""

import os
import sys
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
import pymysql

# Add the current directory to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import your config
from config import get_config

def create_app():
    """Create Flask app for migration"""
    app = Flask(__name__)
    config_class = get_config()
    app.config.from_object(config_class)
    return app

def run_migration():
    """Run the timezone column migration"""
    app = create_app()
    
    with app.app_context():
        # Create database connection directly
        try:
            # Parse database URL
            db_url = app.config['SQLALCHEMY_DATABASE_URI']
            print(f"Connecting to database...")
            
            # Create direct MySQL connection
            if 'mysql' in db_url:
                # Parse MySQL URL
                import re
                pattern = r'mysql\+pymysql://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)(?:\?.*)?'
                match = re.match(pattern, db_url)
                
                if match:
                    username, password, host, port, database = match.groups()
                    
                    connection = pymysql.connect(
                        host=host,
                        port=int(port),
                        user=username,
                        password=password,
                        database=database,
                        charset='utf8mb4'
                    )
                    
                    cursor = connection.cursor()
                    
                    print("✓ Connected to database")
                    
                    # Check if timezone column exists
                    cursor.execute("""
                        SELECT COLUMN_NAME 
                        FROM INFORMATION_SCHEMA.COLUMNS 
                        WHERE TABLE_SCHEMA = %s 
                        AND TABLE_NAME = 'users' 
                        AND COLUMN_NAME = 'timezone'
                    """, (database,))
                    
                    column_exists = cursor.fetchone()
                    
                    if not column_exists:
                        print("Adding timezone column to users table...")
                        
                        # Add the timezone column
                        cursor.execute("""
                            ALTER TABLE users 
                            ADD COLUMN timezone VARCHAR(50) 
                            DEFAULT 'America/Chicago' 
                            NOT NULL
                        """)
                        
                        print("✓ Added timezone column")
                        
                        # Update existing users with default timezone
                        cursor.execute("""
                            UPDATE users 
                            SET timezone = 'America/Chicago' 
                            WHERE timezone IS NULL OR timezone = ''
                        """)
                        
                        updated_count = cursor.rowcount
                        print(f"✓ Updated {updated_count} users with default timezone")
                        
                        # Commit changes
                        connection.commit()
                        print("✓ Migration completed successfully!")
                        
                    else:
                        print("✓ Timezone column already exists")
                        
                        # Still update any NULL values
                        cursor.execute("""
                            UPDATE users 
                            SET timezone = 'America/Chicago' 
                            WHERE timezone IS NULL OR timezone = ''
                        """)
                        
                        updated_count = cursor.rowcount
                        if updated_count > 0:
                            connection.commit()
                            print(f"✓ Updated {updated_count} users with default timezone")
                        else:
                            print("✓ All users already have timezone set")
                    
                    cursor.close()
                    connection.close()
                    
                else:
                    print("❌ Could not parse database URL")
                    return False
                    
            else:
                print("❌ This migration is designed for MySQL databases only")
                return False
                
        except Exception as e:
            print(f"❌ Migration failed: {e}")
            return False
    
    return True

if __name__ == '__main__':
    print("🚀 Starting database migration...")
    print("This will add the timezone column to the users table")
    print("-" * 50)
    
    success = run_migration()
    
    if success:
        print("-" * 50)
        print("✅ Migration completed successfully!")
        print("Your application should now work without errors.")
    else:
        print("-" * 50)
        print("❌ Migration failed!")
        print("Please check the error messages above and try again.")
        sys.exit(1)
