"""
Enhanced API Routes for TGFX Trade Lab
Handles additional AJAX requests and API endpoints
"""

from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from app import db, User, Video, Category, VideoFile, UserProgress, UserFavorite, UserActivity, Notification, Recommendation, RecommendationClick
from datetime import datetime
import os

api = Blueprint('api', __name__, url_prefix='/api')

# Enhanced Video API Endpoints
@api.route('/video/search', methods=['GET'])
@login_required
def search_videos():
    """Advanced video search with filters"""
    try:
        query = request.args.get('q', '').strip()
        category_id = request.args.get('category_id')
        is_free = request.args.get('is_free')
        tag = request.args.get('tag')
        limit = min(int(request.args.get('limit', 20)), 50)  # Max 50 results
        
        if not query and not category_id and not tag:
            return jsonify({'videos': []})
        
        # Build search query
        search_query = Video.query
        
        # Text search
        if query:
            search_query = search_query.filter(
                db.or_(
                    Video.title.ilike(f'%{query}%'),
                    Video.description.ilike(f'%{query}%')
                )
            )
        
        # Category filter
        if category_id:
            search_query = search_query.filter_by(category_id=category_id)
        
        # Free/Premium filter
        if is_free is not None:
            is_free_bool = is_free.lower() == 'true'
            search_query = search_query.filter_by(is_free=is_free_bool)
        
        # Tag filter
        if tag:
            search_query = search_query.join(Video.tags).filter_by(slug=tag)
        
        # Execute search
        videos = search_query.order_by(Video.created_at.desc()).limit(limit).all()
        
        # Get user's progress and favorites
        user_progress = {p.video_id: p for p in current_user.progress}
        user_favorites = {f.video_id for f in current_user.favorites}
        
        # Format results
        results = []
        for video in videos:
            progress = user_progress.get(video.id)
            results.append({
                'id': video.id,
                'title': video.title,
                'description': video.description[:200] + '...' if len(video.description or '') > 200 else video.description,
                'category': {
                    'id': video.category.id,
                    'name': video.category.name
                },
                'is_free': video.is_free,
                'duration': video.duration,
                'thumbnail_url': video.thumbnail_url,
                'progress': {
                    'completed': progress.completed if progress else False,
                    'watched_duration': progress.watched_duration if progress else 0
                },
                'is_favorited': video.id in user_favorites,
                'tags': [{'name': tag.name, 'color': tag.color} for tag in video.tags[:3]]
            })
        
        return jsonify({'videos': results, 'total': len(results)})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@api.route('/video/<int:video_id>/related', methods=['GET'])
@login_required
def get_related_videos(video_id):
    """Get related videos based on category and tags"""
    try:
        video = Video.query.get_or_404(video_id)
        limit = min(int(request.args.get('limit', 6)), 12)
        
        # Get videos from same category with shared tags
        related_query = Video.query.filter(
            Video.id != video_id,
            Video.category_id == video.category_id
        )
        
        # If video has tags, prioritize videos with shared tags
        if video.tags:
            tag_ids = [tag.id for tag in video.tags]
            related_query = related_query.join(Video.tags).filter(
                db.any_(Video.tags.any(id=tag_id) for tag_id in tag_ids)
            )
        
        related_videos = related_query.order_by(Video.order_index, Video.created_at.desc()).limit(limit).all()
        
        # If not enough related videos, fill with recent videos from category
        if len(related_videos) < limit:
            additional_videos = Video.query.filter(
                Video.id != video_id,
                Video.category_id == video.category_id,
                Video.id.notin_([v.id for v in related_videos])
            ).order_by(Video.created_at.desc()).limit(limit - len(related_videos)).all()
            related_videos.extend(additional_videos)
        
        # Format results
        user_progress = {p.video_id: p for p in current_user.progress}
        results = []
        for v in related_videos:
            progress = user_progress.get(v.id)
            results.append({
                'id': v.id,
                'title': v.title,
                'thumbnail_url': v.thumbnail_url,
                'duration': v.duration,
                'is_free': v.is_free,
                'completed': progress.completed if progress else False
            })
        
        return jsonify({'videos': results})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# User Activity and Analytics
@api.route('/user/activity', methods=['GET'])
@login_required
def get_user_activity():
    """Get user's recent activity"""
    try:
        limit = min(int(request.args.get('limit', 10)), 50)
        
        activities = UserActivity.query.filter_by(user_id=current_user.id)\
                                     .order_by(UserActivity.timestamp.desc())\
                                     .limit(limit).all()
        
        results = []
        for activity in activities:
            results.append({
                'id': activity.id,
                'type': activity.activity_type,
                'description': activity.description,
                'timestamp': activity.timestamp.isoformat(),
                'time_ago': get_time_ago(activity.timestamp)
            })
        
        return jsonify({'activities': results})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@api.route('/user/stats', methods=['GET'])
@login_required
def get_user_stats():
    """Get comprehensive user statistics"""
    try:
        # Progress stats
        total_videos = Video.query.count()
        user_progress = UserProgress.query.filter_by(user_id=current_user.id).all()
        completed_videos = len([p for p in user_progress if p.completed])
        in_progress_videos = len([p for p in user_progress if not p.completed and p.watched_duration > 0])
        
        # Time stats
        total_watch_time = sum(p.watched_duration for p in user_progress)
        
        # Favorites
        favorites_count = UserFavorite.query.filter_by(user_id=current_user.id).count()
        
        # Category progress
        categories = Category.query.all()
        category_progress = []
        for category in categories:
            cat_videos = category.videos
            cat_completed = len([v for v in cat_videos 
                               if any(p.video_id == v.id and p.completed for p in user_progress)])
            if cat_videos:
                category_progress.append({
                    'name': category.name,
                    'completed': cat_completed,
                    'total': len(cat_videos),
                    'percentage': round((cat_completed / len(cat_videos)) * 100, 1)
                })
        
        return jsonify({
            'total_videos': total_videos,
            'completed_videos': completed_videos,
            'in_progress_videos': in_progress_videos,
            'progress_percentage': round((completed_videos / total_videos) * 100, 1) if total_videos > 0 else 0,
            'total_watch_time_minutes': round(total_watch_time / 60),
            'favorites_count': favorites_count,
            'category_progress': category_progress[:5]  # Top 5 categories
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Notifications API
@api.route('/notifications', methods=['GET'])
@login_required
def get_notifications():
    """Get user notifications"""
    try:
        limit = min(int(request.args.get('limit', 20)), 50)
        unread_only = request.args.get('unread_only', 'false').lower() == 'true'
        
        query = Notification.query.filter_by(user_id=current_user.id)
        
        if unread_only:
            query = query.filter_by(is_read=False)
        
        notifications = query.order_by(Notification.created_at.desc()).limit(limit).all()
        
        results = []
        for notification in notifications:
            results.append({
                'id': notification.id,
                'title': notification.title,
                'message': notification.message,
                'type': notification.notification_type,
                'is_read': notification.is_read,
                'created_at': notification.created_at.isoformat(),
                'time_ago': get_time_ago(notification.created_at)
            })
        
        return jsonify({'notifications': results})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@api.route('/notifications/<int:notification_id>/read', methods=['POST'])
@login_required
def mark_notification_read(notification_id):
    """Mark notification as read"""
    try:
        notification = Notification.query.filter_by(
            id=notification_id,
            user_id=current_user.id
        ).first_or_404()
        
        notification.is_read = True
        db.session.commit()
        
        return jsonify({'success': True})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@api.route('/notifications/mark-all-read', methods=['POST'])
@login_required
def mark_all_notifications_read():
    """Mark all notifications as read"""
    try:
        Notification.query.filter_by(
            user_id=current_user.id,
            is_read=False
        ).update({'is_read': True})
        
        db.session.commit()
        
        return jsonify({'success': True})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

# Admin Analytics API
@api.route('/admin/analytics/overview', methods=['GET'])
@login_required
def admin_analytics_overview():
    """Get admin analytics overview"""
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403
    
    try:
        # Basic counts
        total_users = User.query.count()
        premium_users = User.query.filter_by(has_subscription=True).count()
        total_videos = Video.query.count()
        total_categories = Category.query.count()
        
        # Recent activity
        recent_signups = User.query.filter(
            User.created_at >= datetime.utcnow() - timedelta(days=7)
        ).count()
        
        recent_video_views = UserProgress.query.filter(
            UserProgress.last_watched >= datetime.utcnow() - timedelta(days=7)
        ).count()
        
        # Popular videos
        popular_videos = db.session.query(
            Video.title, 
            db.func.count(UserProgress.id).label('view_count')
        ).join(UserProgress).group_by(Video.id, Video.title)\
         .order_by(db.desc('view_count')).limit(5).all()
        
        # Popular categories
        popular_categories = db.session.query(
            Category.name,
            db.func.count(UserProgress.id).label('view_count')
        ).join(Video).join(UserProgress)\
         .group_by(Category.id, Category.name)\
         .order_by(db.desc('view_count')).limit(5).all()
        
        return jsonify({
            'overview': {
                'total_users': total_users,
                'premium_users': premium_users,
                'total_videos': total_videos,
                'total_categories': total_categories,
                'conversion_rate': round((premium_users / total_users) * 100, 1) if total_users > 0 else 0
            },
            'recent_activity': {
                'new_signups_week': recent_signups,
                'video_views_week': recent_video_views
            },
            'popular_content': {
                'videos': [{'title': v.title, 'views': v.view_count} for v in popular_videos],
                'categories': [{'name': c.name, 'views': c.view_count} for c in popular_categories]
            }
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Bulk Operations API
@api.route('/admin/videos/bulk-update', methods=['POST'])
@login_required
def bulk_update_videos():
    """Bulk update video properties"""
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403
    
    try:
        data = request.get_json()
        video_ids = data.get('video_ids', [])
        updates = data.get('updates', {})
        
        if not video_ids or not updates:
            return jsonify({'error': 'Video IDs and updates are required'}), 400
        
        # Validate updates
        allowed_fields = ['is_free', 'category_id']
        updates = {k: v for k, v in updates.items() if k in allowed_fields}
        
        # Perform bulk update
        updated_count = Video.query.filter(Video.id.in_(video_ids)).update(
            updates, synchronize_session=False
        )
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'updated_count': updated_count
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@api.route('/admin/videos/bulk-delete', methods=['POST'])
@login_required
def bulk_delete_videos():
    """Bulk delete videos"""
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403
    
    try:
        data = request.get_json()
        video_ids = data.get('video_ids', [])
        
        if not video_ids:
            return jsonify({'error': 'Video IDs are required'}), 400
        
        # Delete related records first
        UserProgress.query.filter(UserProgress.video_id.in_(video_ids)).delete(synchronize_session=False)
        UserFavorite.query.filter(UserFavorite.video_id.in_(video_ids)).delete(synchronize_session=False)
        VideoFile.query.filter(VideoFile.video_id.in_(video_ids)).delete(synchronize_session=False)
        
        # Delete videos
        deleted_count = Video.query.filter(Video.id.in_(video_ids)).delete(synchronize_session=False)
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'deleted_count': deleted_count
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

# Export/Import API
@api.route('/admin/export/videos', methods=['GET'])
@login_required
def export_videos():
    """Export videos data as JSON"""
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403
    
    try:
        videos = Video.query.all()
        
        export_data = []
        for video in videos:
            export_data.append({
                'title': video.title,
                'description': video.description,
                's3_url': video.s3_url,
                'thumbnail_url': video.thumbnail_url,
                'duration': video.duration,
                'is_free': video.is_free,
                'order_index': video.order_index,
                'category_name': video.category.name,
                'tags': [tag.name for tag in video.tags],
                'created_at': video.created_at.isoformat()
            })
        
        return jsonify({
            'videos': export_data,
            'exported_at': datetime.utcnow().isoformat(),
            'total_count': len(export_data)
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@api.route('/admin/export/recommendations', methods=['GET'])
@login_required
def export_recommendations():
    """Export recommendations data"""
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403
    
    try:
        recommendations = Recommendation.query.all()
        
        export_data = []
        for rec in recommendations:
            export_data.append({
                'title': rec.title,
                'description': rec.description,
                'category': rec.category,
                'affiliate_url': rec.affiliate_url,
                'image_url': rec.image_url,
                'demo_url': rec.demo_url,
                'price_info': rec.price_info,
                'coupon_code': rec.coupon_code,
                'discount_percentage': rec.discount_percentage,
                'features': rec.features,
                'is_featured': rec.is_featured,
                'is_active': rec.is_active,
                'order_index': rec.order_index,
                'click_count': rec.click_count,
                'created_at': rec.created_at.isoformat()
            })
        
        return jsonify({
            'recommendations': export_data,
            'exported_at': datetime.utcnow().isoformat(),
            'total_count': len(export_data)
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Utility Functions
def get_time_ago(timestamp):
    """Get human-readable time ago string"""
    now = datetime.utcnow()
    diff = now - timestamp
    
    if diff.days > 0:
        return f"{diff.days} day{'s' if diff.days != 1 else ''} ago"
    elif diff.seconds > 3600:
        hours = diff.seconds // 3600
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    elif diff.seconds > 60:
        minutes = diff.seconds // 60
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    else:
        return "Just now"

# Error handlers
@api.errorhandler(404)
def api_not_found(error):
    return jsonify({'error': 'API endpoint not found'}), 404

@api.errorhandler(500)
def api_internal_error(error):
    db.session.rollback()
    return jsonify({'error': 'Internal server error'}), 500

@api.errorhandler(403)
def api_forbidden(error):
    return jsonify({'error': 'Access forbidden'}), 403

@api.errorhandler(400)
def api_bad_request(error):
    return jsonify({'error': 'Bad request'}), 400
