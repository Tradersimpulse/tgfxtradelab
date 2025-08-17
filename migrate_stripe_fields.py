from app import app, db
from sqlalchemy import text

def add_stripe_columns():
    """Add Stripe-related columns to users table - SQLAlchemy 2.x compatible"""
    with app.app_context():
        try:
            # Use connection context for SQLAlchemy 2.x compatibility
            with db.engine.connect() as connection:
                # Check if columns already exist
                result = connection.execute(text("DESCRIBE users"))
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
                
                added_columns = []
                for column_name, column_type in stripe_columns.items():
                    if column_name not in existing_columns:
                        try:
                            query = text(f"ALTER TABLE users ADD COLUMN {column_name} {column_type}")
                            connection.execute(query)
                            connection.commit()
                            added_columns.append(column_name)
                            print(f"✅ Added column: {column_name}")
                        except Exception as col_error:
                            print(f"⚠️ Could not add {column_name}: {col_error}")
                    else:
                        print(f"⚠️ Column already exists: {column_name}")
                
                if added_columns:
                    print(f"🚀 Migration completed! Added {len(added_columns)} new columns:")
                    for col in added_columns:
                        print(f"   - {col}")
                else:
                    print("ℹ️ All Stripe columns already exist. No migration needed.")
                    
        except Exception as e:
            print(f"❌ Migration failed: {e}")
            print("🔧 Trying alternative approach...")
            
            # Alternative approach using db.session
            try:
                stripe_columns_alt = [
                    "ALTER TABLE users ADD COLUMN stripe_customer_id VARCHAR(100)",
                    "ALTER TABLE users ADD COLUMN stripe_subscription_id VARCHAR(100)",
                    "ALTER TABLE users ADD COLUMN subscription_status VARCHAR(50)",
                    "ALTER TABLE users ADD COLUMN subscription_plan VARCHAR(50)",
                    "ALTER TABLE users ADD COLUMN subscription_price_id VARCHAR(100)",
                    "ALTER TABLE users ADD COLUMN subscription_current_period_start DATETIME",
                    "ALTER TABLE users ADD COLUMN subscription_current_period_end DATETIME",
                    "ALTER TABLE users ADD COLUMN subscription_cancel_at_period_end BOOLEAN DEFAULT FALSE",
                    "ALTER TABLE users ADD COLUMN total_revenue DECIMAL(10,2) DEFAULT 0.00",
                    "ALTER TABLE users ADD COLUMN last_payment_date DATETIME",
                    "ALTER TABLE users ADD COLUMN last_payment_amount DECIMAL(10,2)"
                ]
                
                for query in stripe_columns_alt:
                    try:
                        db.session.execute(text(query))
                        db.session.commit()
                        column_name = query.split("ADD COLUMN ")[1].split(" ")[0]
                        print(f"✅ Added column: {column_name}")
                    except Exception as individual_error:
                        if "Duplicate column name" in str(individual_error):
                            column_name = query.split("ADD COLUMN ")[1].split(" ")[0]
                            print(f"⚠️ Column already exists: {column_name}")
                        else:
                            print(f"⚠️ Could not add column: {individual_error}")
                
                print("🚀 Alternative migration approach completed!")
                
            except Exception as alt_error:
                print(f"❌ Alternative approach also failed: {alt_error}")

if __name__ == "__main__":
    add_stripe_columns()
