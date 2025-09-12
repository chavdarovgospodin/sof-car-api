"""
Authentication module for SofCar Flask API
Handles admin authentication and session management
"""

import logging
from datetime import datetime, timedelta
from functools import wraps
from flask import session, jsonify
from config import Config

logger = logging.getLogger(__name__)


def admin_required(f):
    """Decorator to require admin authentication"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin_logged_in' not in session or not session['admin_logged_in']:
            return jsonify({'error': 'Admin authentication required'}), 401
        
        # Check session expiry
        if 'admin_login_time' in session:
            login_time = datetime.fromisoformat(session['admin_login_time'])
            if datetime.now() - login_time > Config.PERMANENT_SESSION_LIFETIME:
                session.clear()
                return jsonify({'error': 'Session expired'}), 401
        
        return f(*args, **kwargs)
    return decorated_function


def admin_login(username: str, password: str) -> dict:
    """Handle admin login"""
    try:
        # Simple credential check
        if username == Config.ADMIN_USERNAME and password == Config.ADMIN_PASSWORD:
            session['admin_logged_in'] = True
            session['admin_username'] = username
            session['admin_login_time'] = datetime.now().isoformat()
            session.permanent = True
            
            logger.info(f"Admin login successful for {username}")
            
            return {
                'success': True,
                'message': 'Login successful',
                'admin': username,
                'session_expires': (datetime.now() + Config.PERMANENT_SESSION_LIFETIME).isoformat()
            }
        else:
            logger.warning(f"Failed admin login attempt for username '{username}'")
            return {'error': 'Invalid credentials'}
    
    except Exception as e:
        logger.error(f"Error in admin login: {e}")
        return {'error': 'Login failed'}


def admin_logout() -> dict:
    """Handle admin logout"""
    session.clear()
    return {'success': True, 'message': 'Logged out successfully'}


def get_admin_status() -> dict:
    """Get admin session status"""
    if 'admin_logged_in' not in session or not session['admin_logged_in']:
        return {
            'logged_in': False,
            'admin': None,
            'login_time': None,
            'session_expires': None
        }
    
    login_time = session.get('admin_login_time')
    if login_time:
        session_expires = (datetime.fromisoformat(login_time) + Config.PERMANENT_SESSION_LIFETIME).isoformat()
    else:
        session_expires = None
    
    return {
        'logged_in': True,
        'admin': session.get('admin_username'),
        'login_time': login_time,
        'session_expires': session_expires
    }
