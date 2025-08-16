from app import app, db, Category
import sys

def run_migration():
    """Add background_image_url column to categories table"""
    
    print("🔄 Starting database migration...")
    
    try:
        with app.app_context():
            # Check if column already exists
            inspector = db.inspect(db.engine)
            columns = [col['name'] for col in inspector.get_columns('categories')]
            
            if 'background_image_url' in columns:
                print("ℹ️  background_image_url column already exists")
                return True
            
            # Add the new column
            print("➕ Adding background_image_url column to categories table...")
            
            # For SQLite
            if 'sqlite' in str(db.engine.url):
                db.session.execute('ALTER TABLE categories ADD COLUMN background_image_url VARCHAR(500)')
            # For MySQL/PostgreSQL
            else:
                db.session.execute('ALTER TABLE categories ADD COLUMN background_image_url VARCHAR(500)')
            
            db.session.commit()
            print("✅ Successfully added background_image_url column")
            
            # Verify the column was added
            inspector = db.inspect(db.engine)
            columns = [col['name'] for col in inspector.get_columns('categories')]
            
            if 'background_image_url' in columns:
                print("✅ Migration completed successfully!")
                return True
            else:
                print("❌ Migration verification failed")
                return False
                
    except Exception as e:
        print(f"❌ Migration failed: {e}")
        db.session.rollback()
        return False

def create_sample_categories():
    """Create sample categories with background images"""
    
    print("🔄 Creating sample categories...")
    
    try:
        with app.app_context():
            # Check if categories already exist
            if Category.query.count() > 0:
                print("ℹ️  Categories already exist, skipping sample creation")
                return True
            
            # Sample categories with suggested background image URLs
            sample_categories = [
                {
                    'name': 'Live Trading Sessions',
                    'description': 'Real-time trading sessions with professional traders',
                    'background_image_url': 'https://your-bucket.s3.amazonaws.com/backgrounds/live-trading-bg.jpg',
                    'order_index': 1
                },
                {
                    'name': 'Market Structure',
                    'description': 'Understanding market dynamics and structure',
                    'background_image_url': 'https://your-bucket.s3.amazonaws.com/backgrounds/market-structure-bg.jpg',
                    'order_index': 2
                },
                {
                    'name': 'Technical Analysis',
                    'description': 'Chart analysis and technical indicators',
                    'background_image_url': 'https://your-bucket.s3.amazonaws.com/backgrounds/technical-analysis-bg.jpg',
                    'order_index': 3
                },
                {
                    'name': 'Risk Management',
                    'description': 'Managing risk and capital preservation',
                    'background_image_url': 'https://your-bucket.s3.amazonaws.com/backgrounds/risk-management-bg.jpg',
                    'order_index': 4
                }
            ]
            
            for cat_data in sample_categories:
                category = Category(**cat_data)
                db.session.add(category)
            
            db.session.commit()
            print(f"✅ Created {len(sample_categories)} sample categories")
            print("ℹ️  Remember to upload actual background images to S3 and update the URLs!")
            
    except Exception as e:
        print(f"❌ Failed to create sample categories: {e}")
        db.session.rollback()
        return False

if __name__ == '__main__':
    print("🚀 TGFX Trade Lab - Database Migration")
    print("=" * 50)
    
    # Run migration
    migration_success = run_migration()
    
    if migration_success:
        # Optionally create sample categories
        create_sample = input("\n🤔 Create sample categories? (y/n): ").lower().strip()
        if create_sample == 'y':
            create_sample_categories()
    
    print("\n" + "=" * 50)
    print("📋 Next Steps:")
    print("1. Upload Poppins-Bold.ttf font to static/fonts/ directory")
    print("2. Create background images (1280x720) and upload to S3")
    print("3. Update category background URLs in admin panel")
    print("4. Test thumbnail generation with sample videos")
    print("\n✨ Migration complete! Your enhanced video system is ready!")
