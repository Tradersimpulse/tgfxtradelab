#!/usr/bin/env python3
"""
Quick migration to add signaling_url field and clean up description
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from app import app, db
    from sqlalchemy import text, inspect
except ImportError as e:
    print(f"❌ Import error: {e}")
    sys.exit(1)

def add_signaling_url_field():
    """Add signaling_url field to streams table"""
    print("🔧 Adding signaling_url field to streams table...")
    
    try:
        # Check if column already exists
        inspector = inspect(db.engine)
        columns = [col['name'] for col in inspector.get_columns('streams')]
        
        if 'signaling_url' not in columns:
            with db.engine.connect() as conn:
                conn.execute(text("""
                    ALTER TABLE streams 
                    ADD COLUMN signaling_url VARCHAR(500) NULL
                """))
                conn.commit()
                print("✓ Added signaling_url column")
        else:
            print("✓ signaling_url column already exists")
            
    except Exception as e:
        print(f"❌ Error adding signaling_url field: {e}")
        raise

def clean_up_descriptions():
    """Clean up any descriptions that have signaling URLs in them"""
    print("🧹 Cleaning up stream descriptions...")
    
    try:
        with db.engine.connect() as conn:
            # Find and clean descriptions with signaling URLs
            result = conn.execute(text("""
                SELECT id, description FROM streams 
                WHERE description LIKE '%__SIGNALING_URL__%'
            """))
            
            streams_to_clean = result.fetchall()
            
            for stream in streams_to_clean:
                stream_id, description = stream
                
                # Split description and extract signaling URL
                if '__SIGNALING_URL__:' in description:
                    parts = description.split('__SIGNALING_URL__:')
                    clean_description = parts[0].strip()
                    signaling_url = parts[1].strip() if len(parts) > 1 else None
                    
                    # Update the stream
                    conn.execute(text("""
                        UPDATE streams 
                        SET description = :description, signaling_url = :signaling_url
                        WHERE id = :stream_id
                    """), {
                        'description': clean_description,
                        'signaling_url': signaling_url,
                        'stream_id': stream_id
                    })
                    
                    print(f"✓ Cleaned stream {stream_id}")
            
            conn.commit()
            
            if streams_to_clean:
                print(f"✓ Cleaned {len(streams_to_clean)} stream descriptions")
            else:
                print("✓ No descriptions needed cleaning")
                
    except Exception as e:
        print(f"❌ Error cleaning descriptions: {e}")
        raise

def main():
    """Main migration function"""
    print("🚀 Running signaling URL migration...")
    
    with app.app_context():
        try:
            add_signaling_url_field()
            clean_up_descriptions()
            
            print("✅ Migration completed successfully!")
            
        except Exception as e:
            print(f"❌ Migration failed: {e}")
            sys.exit(1)

if __name__ == '__main__':
    main()
