# migration_script.py - FIXED VERSION
from app import app, db, Category
from sqlalchemy import text  # Import text for raw SQL
import sys
import os

def run_migration():
    """Add background_image_url column to categories table"""
    
    print("🔄 Starting database migration...")
    
    try:
        with app.app_context():
            # Check if column already exists
            try:
                result = db.session.execute(text("SELECT background_image_url FROM categories LIMIT 1"))
                print("ℹ️  background_image_url column already exists")
                return True
            except Exception:
                # Column doesn't exist, need to add it
                print("📝 Column doesn't exist, proceeding with migration...")
            
            # Add the new column
            print("➕ Adding background_image_url column to categories table...")
            
            # Use text() wrapper for raw SQL (required by newer SQLAlchemy)
            if 'postgresql' in str(db.engine.url) or 'postgres' in str(db.engine.url):
                # PostgreSQL
                sql = text('ALTER TABLE categories ADD COLUMN background_image_url VARCHAR(500)')
            elif 'mysql' in str(db.engine.url):
                # MySQL (your case)
                sql = text('ALTER TABLE categories ADD COLUMN background_image_url VARCHAR(500)')
            else:
                # SQLite (local development)
                sql = text('ALTER TABLE categories ADD COLUMN background_image_url VARCHAR(500)')
            
            db.session.execute(sql)
            db.session.commit()
            print("✅ Successfully added background_image_url column")
            
            # Verify the column was added
            try:
                verification_sql = text("SELECT background_image_url FROM categories LIMIT 1")
                db.session.execute(verification_sql)
                print("✅ Migration completed successfully!")
                return True
            except Exception as e:
                print(f"❌ Migration verification failed: {e}")
                return False
                
    except Exception as e:
        print(f"❌ Migration failed: {e}")
        try:
            db.session.rollback()
        except Exception as rollback_error:
            print(f"⚠️  Rollback also failed: {rollback_error}")
        return False

def create_sample_categories():
    """Create sample categories with background images (optional)"""
    
    print("🔄 Creating sample categories...")
    
    try:
        with app.app_context():
            # Check if categories already exist
            if Category.query.count() > 0:
                print("ℹ️  Categories already exist, skipping sample creation")
                return True
            
            # Sample categories
            sample_categories = [
                {
                    'name': 'Live Trading Sessions',
                    'description': 'Real-time trading sessions with professional traders',
                    'background_image_url': None,  # You'll add this later
                    'order_index': 1
                },
                {
                    'name': 'Market Structure',
                    'description': 'Understanding market dynamics and structure',
                    'background_image_url': None,  # You'll add this later
                    'order_index': 2
                },
                {
                    'name': 'Technical Analysis',
                    'description': 'Chart analysis and technical indicators',
                    'background_image_url': None,  # You'll add this later
                    'order_index': 3
                },
                {
                    'name': 'Risk Management',
                    'description': 'Managing risk and capital preservation',
                    'background_image_url': None,  # You'll add this later
                    'order_index': 4
                }
            ]
            
            for cat_data in sample_categories:
                category = Category(**cat_data)
                db.session.add(category)
            
            db.session.commit()
            print(f"✅ Created {len(sample_categories)} sample categories")
            print("ℹ️  Add background image URLs through the admin panel!")
            return True
            
    except Exception as e:
        print(f"❌ Failed to create sample categories: {e}")
        try:
            db.session.rollback()
        except:
            pass
        return False

if __name__ == '__main__':
    print("🚀 TGFX Trade Lab - Database Migration")
    print("=" * 50)
    
    # Run migration
    migration_success = run_migration()
    
    if migration_success:
        print("\n🎉 Migration successful!")
        print("✅ Ready to use enhanced video system")
        
        # Ask about sample categories (only if running locally)
        if 'DATABASE_URL' not in os.environ:  # Local environment
            try:
                create_sample = input("\n🤔 Create sample categories? (y/n): ").lower().strip()
                if create_sample == 'y':
                    create_sample_categories()
            except:
                # Skip interactive input on Heroku
                pass
    else:
        print("\n❌ Migration failed")
        print("ℹ️  Check logs for more details")
        sys.exit(1)
    
    print("\n" + "=" * 50)
    print("📋 Next Steps:")
    print("1. Upload background images (1280x720) to S3")
    print("2. Update category background URLs in admin panel")
    print("3. Test thumbnail generation with sample videos")
    print("\n✨ Migration complete!")
