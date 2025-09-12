"""
Configuration module for SofCar Flask API
Centralized configuration management for all environment variables and settings
"""

import os
from datetime import timedelta
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


class Config:
    """Centralized configuration class for SofCar API"""
    
    # Flask Configuration
    SECRET_KEY = os.environ.get('SECRET_KEY')
    SESSION_COOKIE_SECURE = True  # True for HTTPS
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'None'  # For cross-origin (localhost → sof-car.eu)
    SESSION_COOKIE_DOMAIN = None
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)
    
    # CORS Configuration
    CORS_ORIGINS = [
        'https://sof-car.eu', 
        'https://sof-car-nextjs.vercel.app', 
        'http://localhost:3000', 
        'https://localhost:3000',
        'http://192.168.1.7:3000',
        'https://192.168.1.7:3000',
    ]
    CORS_SUPPORTS_CREDENTIALS = True
    CORS_ALLOW_HEADERS = [
        'Content-Type', 
        'Authorization',
        'X-Requested-With',
        'Accept',
        'Origin',
        'Cache-Control',
        'X-File-Name',
        'X-HTTP-Method-Override'
    ]
    CORS_METHODS = ['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS', 'HEAD', 'PATCH']
    CORS_EXPOSE_HEADERS = ['Content-Range', 'X-Content-Range']
    CORS_MAX_AGE = 86400  # Cache preflight for 24 hours
    
    # Logging Configuration
    LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO')
    
    # Supabase Configuration
    SUPABASE_URL = os.environ.get('SUPABASE_URL')
    SUPABASE_ANON_KEY = os.environ.get('SUPABASE_ANON_KEY')
    SUPABASE_SERVICE_ROLE_KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
    
    # Admin Configuration
    ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', 'admin')
    ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'change_this_password')
    
    # EmailJS Configuration
    EMAILJS_SERVICE_ID = os.environ.get('EMAILJS_SERVICE_ID')
    EMAILJS_PUBLIC_KEY = os.environ.get('EMAILJS_PUBLIC_KEY')
    EMAILJS_PRIVATE_KEY = os.environ.get('EMAILJS_PRIVATE_KEY')
    EMAILJS_CONTACT_TEMPLATE_ID = os.environ.get('EMAILJS_CONTACT_TEMPLATE_ID')
    EMAILJS_BOOKING_TEMPLATE_ID = os.environ.get('EMAILJS_BOOKING_TEMPLATE_ID')
    
    # File Upload Configuration
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
    MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB
    SUPABASE_BUCKET = 'cars'
    
    # Rate Limiting Configuration
    RATE_LIMIT_WINDOW = 3600  # 1 hour
    RATE_LIMIT_MAX_REQUESTS = 5
    
    # Security Configuration
    HONEYPOT_FIELDS = ['website', 'phone_number', 'company', 'subject', 'url', 'homepage']
    ALLOWED_PAYMENT_METHODS = ['vpos']
    
    # Business Rules
    MIN_RENTAL_DAYS = 5
    MAX_RENTAL_DAYS = 30
    MAX_ADVANCE_BOOKING_DAYS = 90  # 3 months
    
    # Car Configuration
    ALLOWED_FUEL_TYPES = ['petrol', 'diesel', 'hybrid', 'electric', 'lpg']
    ALLOWED_TRANSMISSIONS = ['manual', 'automatic', 'cvt', 'semi-automatic']
    ALLOWED_CAR_CLASSES = ['economy', 'standard', 'premium']
    
    # Booking Status Configuration
    VALID_BOOKING_STATUSES = ['pending', 'confirmed', 'cancelled', 'completed', 'deleted']
    VALID_DEPOSIT_STATUSES = ['pending', 'paid', 'refunded']
    
    # Usage Limits (Free Tier)
    DATABASE_LIMIT_MB = 500
    STORAGE_LIMIT_GB = 1
    
    @classmethod
    def validate_required_config(cls):
        """Validate that all required configuration is present"""
        required_vars = [
            'SECRET_KEY',
            'SUPABASE_URL', 
            'SUPABASE_ANON_KEY'
        ]
        
        missing_vars = []
        for var in required_vars:
            if not getattr(cls, var):
                missing_vars.append(var)
        
        if missing_vars:
            raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")
        
        return True
