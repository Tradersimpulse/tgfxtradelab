"""
API Routes for TGFX Trade Lab
Handles AJAX requests from the frontend
"""

from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from app import db, User, Video, Category, VideoFile, UserProgress, UserFavorite  # Added User import
from datetime import datetime
import os

api = Blueprint('api', __name__, url_prefix='/api')

@api.route('/video/progress', methods=['POST'])
@login_required
def update_video_progress():
    """Update user's video watching progress"""
    try:
        data = request.get_json()
        video_id = data.get('video_id')
        watched_duration = data.get('watched_duration', 0)
        total_duration = data.get('total_duration', 0)
        
        if not video_id:
            return jsonify({'error': 'Video ID is required'}), 400
        
        # Get or create progress record
        progress = UserProgress.query.filter_by(
            user_id=current_user.id, 
            video_id=video_id
        ).first()
        
        if not progress:
            progress = UserProgress(
                user_id=current_user.id,
                video_id=video_id
            )
            db.session.add(progress)
        
        # Update progress
        progress.watched_duration = max(progress.watched_duration, watched_duration)
        progress.last_watched = datetime.utcnow()
        
        # Mark as completed if watched 90% or more
        if total_duration and watched_duration >= (total_duration * 0.9):
            progress.completed = True
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'completed': progress.completed,
            'watched_duration': progress.watched_duration
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@api.route('/video/favorite', methods=['POST'])
@login_required
def toggle_video_favorite():
    """Toggle video favorite status"""
    try:
        data = request.get_json()
        video_id = data.get('video_id')
        
        if not video_id:
            return jsonify({'error': 'Video ID is required'}), 400
        
        # Check if video exists
        video = Video.query.get(video_id)
        if not video:
            return jsonify({'error': 'Video not found'}), 404
        
        # Check if already favorited
        favorite = UserFavorite.query.filter_by(
            user_id=current_user.id,
            video_id=video_id
        ).first()
        
        if favorite:
            # Remove from favorites
            db.session.delete(favorite)
            is_favorited = False
        else:
            # Add to favorites
            favorite = UserFavorite(
                user_id=current_user.id,
                video_id=video_id
            )
            db.session.add(favorite)
            is_favorited = True
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'is_favorited': is_favorited
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@api.route('/admin/video/order', methods=['POST'])
@login_required
def update_video_order():
    """Update video order index (Admin only)"""
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403
    
    try:
        data = request.get_json()
        video_id = data.get('video_id')
        order_index = data.get('order_index')
        
        if not video_id or order_index is None:
            return jsonify({'error': 'Video ID and order index are required'}), 400
        
        video = Video.query.get(video_id)
        if not video:
            return jsonify({'error': 'Video not found'}), 404
        
        video.order_index = order_index
        db.session.commit()
        
        return jsonify({'success': True})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@api.route('/admin/video/<int:video_id>', methods=['DELETE'])
@login_required
def delete_video(video_id):
    """Delete a video (Admin only)"""
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403
    
    try:
        video = Video.query.get(video_id)
        if not video:
            return jsonify({'error': 'Video not found'}), 404
        
        # Delete associated records
        UserProgress.query.filter_by(video_id=video_id).delete()
        UserFavorite.query.filter_by(video_id=video_id).delete()
        VideoFile.query.filter_by(video_id=video_id).delete()
        
        # Delete the video
        db.session.delete(video)
        db.session.commit()
        
        return jsonify({'success': True})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@api.route('/admin/video/<int:video_id>/files', methods=['POST'])
@login_required
def add_video_file(video_id):
    """Add a file to a video (Admin only)"""
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403
    
    try:
        video = Video.query.get(video_id)
        if not video:
            return jsonify({'error': 'Video not found'}), 404
        
        data = request.get_json()
        filename = data.get('filename')
        file_type = data.get('file_type')
        s3_url = data.get('s3_url')
        
        if not all([filename, s3_url]):
            return jsonify({'error': 'Filename and S3 URL are required'}), 400
        
        video_file = VideoFile(
            video_id=video_id,
            filename=filename,
            file_type=file_type,
            s3_url=s3_url
        )
        
        db.session.add(video_file)
        db.session.commit()
        
        return jsonify({'success': True, 'file_id': video_file.id})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@api.route('/admin/file/<int:file_id>', methods=['DELETE'])
@login_required
def delete_video_file(file_id):
    """Delete a video file (Admin only)"""
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403
    
    try:
        video_file = VideoFile.query.get(file_id)
        if not video_file:
            return jsonify({'error': 'File not found'}), 404
        
        db.session.delete(video_file)
        db.session.commit()
        
        return jsonify({'success': True})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@api.route('/admin/categories/reorder', methods=['POST'])
@login_required
def reorder_categories():
    """Reorder categories (Admin only)"""
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403
    
    try:
        data = request.get_json()
        categories = data.get('categories', [])
        
        for cat_data in categories:
            category_id = cat_data.get('id')
            order_index = cat_data.get('order_index')
            
            if category_id and order_index is not None:
                category = Category.query.get(category_id)
                if category:
                    category.order_index = order_index
        
        db.session.commit()
        return jsonify({'success': True})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@api.route('/admin/category/<int:category_id>', methods=['DELETE'])
@login_required
def delete_category(category_id):
    """Delete a category and all its videos (Admin only)"""
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403
    
    try:
        category = Category.query.get(category_id)
        if not category:
            return jsonify({'error': 'Category not found'}), 404
        
        # Get all videos in this category
        videos = Video.query.filter_by(category_id=category_id).all()
        
        # Delete associated records for each video
        for video in videos:
            UserProgress.query.filter_by(video_id=video.id).delete()
            UserFavorite.query.filter_by(video_id=video.id).delete()
            VideoFile.query.filter_by(video_id=video.id).delete()
            db.session.delete(video)
        
        # Delete the category
        db.session.delete(category)
        db.session.commit()
        
        return jsonify({'success': True})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@api.route('/admin/video/<int:video_id>/remove-from-category', methods=['POST'])
@login_required
def remove_video_from_category(video_id):
    """Remove video from category by setting category_id to null (Admin only)"""
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403
    
    try:
        video = Video.query.get(video_id)
        if not video:
            return jsonify({'error': 'Video not found'}), 404
        
        # For now, we'll delete the video since our schema requires category_id
        # In a more complex system, you might have a default "Uncategorized" category
        UserProgress.query.filter_by(video_id=video_id).delete()
        UserFavorite.query.filter_by(video_id=video_id).delete()
        VideoFile.query.filter_by(video_id=video_id).delete()
        
        db.session.delete(video)
        db.session.commit()
        
        return jsonify({'success': True})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@api.route('/stats/dashboard', methods=['GET'])
@login_required
def get_dashboard_stats():
    """Get dashboard statistics"""
    try:
        if current_user.is_admin:
            # Admin stats
            total_videos = Video.query.count()
            total_users = User.query.count()  # Now User is imported
            total_categories = Category.query.count()
            premium_users = User.query.filter_by(has_subscription=True).count()  # Now User is imported
            
            return jsonify({
                'total_videos': total_videos,
                'total_users': total_users,
                'total_categories': total_categories,
                'premium_users': premium_users
            })
        else:
            # User stats
            user_progress = UserProgress.query.filter_by(user_id=current_user.id).all()
            completed_videos = len([p for p in user_progress if p.completed])
            total_videos = Video.query.count()
            user_favorites = UserFavorite.query.filter_by(user_id=current_user.id).count()
            
            return jsonify({
                'completed_videos': completed_videos,
                'total_videos': total_videos,
                'favorites_count': user_favorites,
                'progress_percentage': (completed_videos / total_videos * 100) if total_videos > 0 else 0
            })
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@api.route('/search/videos', methods=['GET'])
@login_required
def search_videos():
    """Search videos by title or description"""
    try:
        query = request.args.get('q', '').strip()
        category_id = request.args.get('category_id')
        is_free = request.args.get('is_free')
        
        if not query:
            return jsonify({'videos': []})
        
        # Build search query
        search_query = Video.query.filter(
            db.or_(
                Video.title.ilike(f'%{query}%'),
                Video.description.ilike(f'%{query}%')
            )
        )
        
        # Apply filters
        if category_id:
            search_query = search_query.filter_by(category_id=category_id)
        
        if is_free is not None:
            is_free_bool = is_free.lower() == 'true'
            search_query = search_query.filter_by(is_free=is_free_bool)
        
        # Execute search
        videos = search_query.order_by(Video.created_at.desc()).limit(20).all()
        
        # Format results
        results = []
        for video in videos:
            results.append({
                'id': video.id,
                'title': video.title,
                'description': video.description,
                'category': video.category.name,
                'is_free': video.is_free,
                'duration': video.duration,
                'thumbnail_url': video.thumbnail_url
            })
        
        return jsonify({'videos': results})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Error handlers
@api.errorhandler(404)
def api_not_found(error):
    return jsonify({'error': 'API endpoint not found'}), 404

@api.errorhandler(500)
def api_internal_error(error):
    db.session.rollback()
    return jsonify({'error': 'Internal server error'}), 500
