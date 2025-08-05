web: gunicorn --worker-class eventlet -w 1 --bind 0.0.0.0:$PORT app:app
release: python init_db.py
