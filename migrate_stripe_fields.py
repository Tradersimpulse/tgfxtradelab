from app import app, db
from sqlalchemy import text

def add_stripe_columns():
    """Add Stripe-related columns to users table"""
    with app.app_context():
        try:
            # Check if columns already exist
            result = db.engine.execute(text("DESCRIBE users"))
            existing_columns = [row[0] for row in result]
            
            stripe_columns = {
                'stripe_customer_id': 'VARCHAR(100)',
                'stripe_subscription_id': 'VARCHAR(100)', 
                'subscription_status': 'VARCHAR(50)',
                'subscription_plan': 'VARCHAR(50)',
                'subscription_price_id': 'VARCHAR(100)',
                'subscription_current_period_start': 'DATETIME',
                'subscription_current_period_end': 'DATETIME',
                'subscription_cancel_at_period_end': 'BOOLEAN DEFAULT FALSE',
                'total_revenue': 'DECIMAL(10,2) DEFAULT 0.00',
                'last_payment_date': 'DATETIME',
                'last_payment_amount': 'DECIMAL(10,2)'
            }
            
            for column_name, column_type in stripe_columns.items():
                if column_name not in existing_columns:
                    query = f"ALTER TABLE users ADD COLUMN {column_name} {column_type}"
                    db.engine.execute(text(query))
                    print(f"✅ Added column: {column_name}")
                else:
                    print(f"⚠️ Column already exists: {column_name}")
                    
            print("🚀 Migration completed successfully!")
            
        except Exception as e:
            print(f"❌ Migration failed: {e}")

if __name__ == "__main__":
    add_stripe_columns()
