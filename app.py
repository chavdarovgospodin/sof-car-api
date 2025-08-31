import os
import sys
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, g, session, make_response
from flask_cors import CORS
import logging
from supabase import create_client, Client
from functools import wraps
import hashlib
import time
from typing import Optional, Dict, Any
import json
from werkzeug.exceptions import BadRequest, TooManyRequests, Unauthorized
from werkzeug.utils import secure_filename
import ipaddress
from dotenv import load_dotenv
import uuid
import mimetypes
import base64

# Load environment variables
load_dotenv()

# Initialize Flask app
app = Flask(__name__)

app.config.update(
    SECRET_KEY=os.environ.get('SECRET_KEY'),  # Нов key
    SESSION_COOKIE_SECURE=True,   # True за HTTPS
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='None',  # За cross-origin (localhost → sof-car.eu)
    SESSION_COOKIE_DOMAIN=None,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8)
)

CORS(app, 
     origins=[
         'https://sof-car.eu', 
         'http://localhost:3000', 
         'https://localhost:3000',
     ], 
     supports_credentials=True,
     allow_headers=[
         'Content-Type', 
         'Authorization',
         'X-Requested-With',
         'Accept',
         'Origin',
         'Cache-Control',
         'X-File-Name',
         'X-HTTP-Method-Override'
     ],
     methods=['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS', 'HEAD', 'PATCH'],
     expose_headers=['Content-Range', 'X-Content-Range'],
     max_age=86400  # Cache preflight for 24 hours
)

# Add explicit OPTIONS handler for all admin routes
@app.before_request
def handle_preflight():
    """Handle CORS preflight requests"""
    if request.method == "OPTIONS":
        response = make_response()
        response.headers.add("Access-Control-Allow-Origin", request.headers.get('Origin', '*'))
        response.headers.add('Access-Control-Allow-Headers', "Content-Type,Authorization,X-Requested-With,Accept,Origin,Cache-Control")
        response.headers.add('Access-Control-Allow-Methods', "GET,POST,PUT,DELETE,OPTIONS,HEAD,PATCH")
        response.headers.add('Access-Control-Allow-Credentials', 'true')
        response.headers.add('Access-Control-Max-Age', '86400')
        return response

# Configure logging
log_level = os.environ.get('LOG_LEVEL', 'INFO')
logging.basicConfig(
    level=getattr(logging, log_level),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize Supabase client
try:
    supabase_url = os.environ.get('SUPABASE_URL')
    supabase_key = os.environ.get('SUPABASE_ANON_KEY')
    
    if not supabase_url or not supabase_key:
        raise ValueError("Missing Supabase environment variables")
    
    supabase: Client = create_client(supabase_url, supabase_key)
    logger.info("Supabase client initialized successfully")
except Exception as e:
    logger.error(f"Failed to initialize Supabase client: {e}")
    supabase = None

# Admin credentials from environment
ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', 'admin')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'change_this_password')

# File upload settings
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB
SUPABASE_BUCKET = 'cars'

# Simple rate limiting storage (in-memory for development)
rate_limit_storage = {}

# Security settings
HONEYPOT_FIELDS = ['website', 'phone_number', 'company', 'subject', 'message', 'url', 'homepage']
ALLOWED_PAYMENT_METHODS = ['cash', 'card', 'bank_transfer', 'online']
rate_limit_window = 3600  # 1 hour
rate_limit_max_requests = 3

# Concurrency protection
booking_locks = {}  # Simple in-memory locks per car_id

def get_client_ip():
    """Get client IP address"""
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    return request.remote_addr or 'unknown'

def allowed_file(filename):
    """Check if uploaded file is allowed"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def validate_image_file(file):
    """Validate uploaded image file"""
    if not file or file.filename == '':
        raise BadRequest("No file selected")
    
    if not allowed_file(file.filename):
        raise BadRequest(f"File type not allowed. Allowed types: {', '.join(ALLOWED_EXTENSIONS)}")
    
    # Check file size (Flask doesn't automatically enforce this)
    file.seek(0, os.SEEK_END)
    file_length = file.tell()
    file.seek(0)  # Reset file pointer
    
    if file_length > MAX_FILE_SIZE:
        raise BadRequest(f"File size too large. Maximum size: {MAX_FILE_SIZE/1024/1024:.1f}MB")
    
    return True

def upload_image_to_supabase(file, car_id, is_main=False):
    """Upload image to Supabase Storage and create car_images record"""
    try:
        # Generate unique filename
        file_ext = file.filename.rsplit('.', 1)[1].lower()
        timestamp = int(time.time())
        filename = f"car_{car_id}_{timestamp}_{uuid.uuid4().hex[:8]}.{file_ext}"
        
        # Read file content
        file_content = file.read()
        file.seek(0)  # Reset file pointer
        
        # Upload to Supabase Storage
        response = supabase.storage.from_(SUPABASE_BUCKET).upload(
            filename, 
            file_content,
            file_options={
                "content-type": mimetypes.guess_type(file.filename)[0] or 'image/jpeg',
                "upsert": False
            }
        )
        
        if response.status_code != 200:
            logger.error(f"Failed to upload image to Supabase: {response}")
            raise Exception("Failed to upload image to storage")
        
        # Get public URL
        public_url = supabase.storage.from_(SUPABASE_BUCKET).get_public_url(filename)
        
        # Create car_images record
        image_data = {
            'car_id': car_id,
            'image_url': public_url,
            'image_name': secure_filename(file.filename),
            'is_main': is_main,
            'sort_order': 1 if is_main else 999,  # Main image first
            'created_at': datetime.now().isoformat()
        }
        
        image_response = supabase.table('car_images').insert(image_data).execute()
        
        if not image_response.data:
            # Rollback storage upload if database insert fails
            try:
                supabase.storage.from_(SUPABASE_BUCKET).remove([filename])
            except:
                pass
            raise Exception("Failed to create image record")
        
        return {
            'filename': filename,
            'public_url': public_url,
            'image_record': image_response.data[0]
        }
        
    except Exception as e:
        logger.error(f"Error uploading image: {e}")
        raise Exception(f"Failed to upload image: {str(e)}")

def delete_image_from_supabase(image_id):
    """Delete image from both storage and database"""
    try:
        # Get image record first
        image_response = supabase.table('car_images').select('*').eq('id', image_id).execute()
        
        if not image_response.data:
            return False
        
        image = image_response.data[0]
        
        # Extract filename from URL
        filename = image['image_url'].split('/')[-1]
        
        # Delete from storage
        try:
            supabase.storage.from_(SUPABASE_BUCKET).remove([filename])
        except Exception as e:
            logger.warning(f"Failed to delete image from storage: {e}")
        
        # Delete from database
        delete_response = supabase.table('car_images').delete().eq('id', image_id).execute()
        
        return len(delete_response.data) > 0
        
    except Exception as e:
        logger.error(f"Error deleting image: {e}")
        return False

# Admin Authentication Decorator
def admin_required(f):
    """Decorator to require admin authentication"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin_logged_in' not in session or not session['admin_logged_in']:
            return jsonify({'error': 'Admin authentication required'}), 401
        
        # Check session expiry
        if 'admin_login_time' in session:
            login_time = datetime.fromisoformat(session['admin_login_time'])
            if datetime.now() - login_time > timedelta(hours=8):  # 8 hour session
                session.clear()
                return jsonify({'error': 'Session expired'}), 401
        
        return f(*args, **kwargs)
    return decorated_function

def check_rate_limit():
    """Enhanced rate limiting check"""
    client_ip = get_client_ip()
    current_time = time.time()
    
    if client_ip not in rate_limit_storage:
        rate_limit_storage[client_ip] = {'count': 0, 'reset_time': current_time + rate_limit_window}
    
    # Reset counter if window expired
    if current_time > rate_limit_storage[client_ip]['reset_time']:
        rate_limit_storage[client_ip] = {'count': 0, 'reset_time': current_time + rate_limit_window}
    
    # Check if limit exceeded
    if rate_limit_storage[client_ip]['count'] >= rate_limit_max_requests:
        logger.warning(f"Rate limit exceeded for IP: {client_ip}")
        raise TooManyRequests("Rate limit exceeded. Maximum 3 bookings per hour per IP.")
    
    # Increment counter
    rate_limit_storage[client_ip]['count'] += 1

def validate_email(email: str) -> bool:
    """Validate email format"""
    import re
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

def validate_phone(phone: str) -> bool:
    """Validate Bulgarian phone format"""
    import re
    # Remove all non-digit characters
    clean_phone = re.sub(r'\D', '', phone)
    # Bulgarian phone numbers: +359 + 8 or 9 digits, or just 8 or 9 digits
    if clean_phone.startswith('359'):
        clean_phone = clean_phone[3:]
    return len(clean_phone) >= 8 and len(clean_phone) <= 10 and clean_phone.startswith(('8', '9'))

def validate_date_format(date_str: str) -> bool:
    """Validate date format YYYY-MM-DD"""
    try:
        datetime.strptime(date_str, '%Y-%m-%d')
        return True
    except ValueError:
        return False

def validate_car_data(data):
    """Validate car data for create/update operations"""
    required_fields = ['brand', 'model', 'year', 'class', 'price_per_day']
    
    for field in required_fields:
        if field not in data or not data[field]:
            raise BadRequest(f"Missing required field: {field}")
    
    # Validate year
    try:
        year = int(data['year'])
        current_year = datetime.now().year
        if year < 1900 or year > current_year + 2:
            raise BadRequest("Invalid year")
    except (ValueError, TypeError):
        raise BadRequest("Year must be a valid number")
    
    # Validate price
    try:
        price = float(data['price_per_day'])
        if price <= 0:
            raise BadRequest("Price must be positive")
    except (ValueError, TypeError):
        raise BadRequest("Price must be a valid number")
    
    # Validate deposit amount if provided
    if 'deposit_amount' in data and data['deposit_amount'] is not None:
        try:
            deposit = float(data['deposit_amount'])
            if deposit < 0:
                raise BadRequest("Deposit amount cannot be negative")
        except (ValueError, TypeError):
            raise BadRequest("Deposit amount must be a valid number")
    
    # Validate features if provided
    if 'features' in data and data['features'] is not None:
        if isinstance(data['features'], str):
            try:
                data['features'] = json.loads(data['features'])
            except json.JSONDecodeError:
                raise BadRequest("Features must be valid JSON array")
        
        if not isinstance(data['features'], list):
            raise BadRequest("Features must be an array")
        
            # Validate fuel type if provided
    if 'fuel_type' in data and data['fuel_type']:
        allowed_fuel_types = ['petrol', 'diesel', 'hybrid', 'electric', 'lpg']
        if data['fuel_type'] not in allowed_fuel_types:
            raise BadRequest(f"Invalid fuel type. Allowed: {', '.join(allowed_fuel_types)}")

            # Validate transmission if provided
    if 'transmission' in data and data['transmission']:
        allowed_transmissions = ['manual', 'automatic', 'cvt', 'semi-automatic']
        if data['transmission'] not in allowed_transmissions:
            raise BadRequest(f"Invalid transmission. Allowed: {', '.join(allowed_transmissions)}")

            # Validate car class if provided
    allowed_classes = ['economy', 'standard', 'premium']
    if data['class'] not in allowed_classes:
        raise BadRequest(f"Invalid car class. Allowed: {', '.join(allowed_classes)}")
    

    return data

def validate_booking_data(data):
    """Enhanced validation for booking data"""
    required_fields = ['car_id', 'start_date', 'end_date', 'client_name', 'client_email', 'client_phone']
    
    for field in required_fields:
        if field not in data or not data[field]:
            raise BadRequest(f"Missing required field: {field}")
    
    # Honeypot check - reject if any honeypot field is filled
    for honeypot in HONEYPOT_FIELDS:
        if honeypot in data and data[honeypot]:
            logger.warning(f"Honeypot field '{honeypot}' was filled from IP: {get_client_ip()}")
            raise BadRequest("Invalid form submission")
    
    # Validate dates
    try:
        start_date = datetime.strptime(data['start_date'], '%Y-%m-%d').date()
        end_date = datetime.strptime(data['end_date'], '%Y-%m-%d').date()
        today = datetime.now().date()
        
        if start_date >= end_date:
            raise BadRequest("Start date must be before end date")
        
        if start_date < today:
            raise BadRequest("Start date cannot be in the past")
        
        # Maximum rental period (30 days)
        if (end_date - start_date).days > 30:
            raise BadRequest("Maximum rental period is 30 days")
        
        # Cannot book too far in advance (1 year)
        if (start_date - today).days > 365:
            raise BadRequest("Cannot book more than 1 year in advance")
            
    except ValueError:
        raise BadRequest("Invalid date format. Use YYYY-MM-DD")
    
    # Validate client name (minimum 2 characters, letters, spaces and common characters)
    import re
    if not re.match(r'^[a-zA-Zа-яА-Я\s\-\.]{2,50}$', data['client_name'].strip()):
        raise BadRequest("Invalid client name format")
    
    # Validate email
    if not validate_email(data['client_email'].strip()):
        raise BadRequest("Invalid email format")
    
    # Validate phone
    if not validate_phone(data['client_phone'].strip()):
        raise BadRequest("Invalid phone number format")
    
    # Validate car_id is positive integer
    try:
        car_id = int(data['car_id'])
        if car_id <= 0:
            raise BadRequest("Invalid car ID")
    except (ValueError, TypeError):
        raise BadRequest("Car ID must be a valid number")
    
    # Validate payment method
    payment_method = data.get('payment_method', 'cash')
    if payment_method not in ALLOWED_PAYMENT_METHODS:
        raise BadRequest(f"Invalid payment method. Allowed: {', '.join(ALLOWED_PAYMENT_METHODS)}")
    
    return data

def calculate_total_price(car_price, start_date, end_date):
    """Calculate total price for booking"""
    start = datetime.strptime(start_date, '%Y-%m-%d').date()
    end = datetime.strptime(end_date, '%Y-%m-%d').date()
    days = (end - start).days
    total_price = car_price * days
    
    return total_price

def generate_booking_reference():
    """Generate unique booking reference"""
    timestamp = datetime.now().strftime("%y%m%d")
    unique_id = str(uuid.uuid4())[:8].upper()
    return f"SOF{timestamp}{unique_id}"

def check_car_availability_atomic(car_id, start_date, end_date):
    """Atomic availability check with better logic"""
    try:
        logger.info(f"Checking availability for car {car_id} from {start_date} to {end_date}")
        
        # Check for overlapping confirmed bookings
        overlap_query = supabase.table("bookings").select("id").eq("car_id", car_id).in_("status", ["confirmed", "pending"]).or_(
            f"start_date.lte.{end_date},end_date.gt.{start_date}"
        ).execute()
        
        logger.info(f"Overlap query result: {overlap_query.data}")
        
        if overlap_query.data:
            logger.info(f"Car {car_id} is not available - has overlapping bookings")
            return False, f"Car is booked for overlapping dates"
        
        logger.info(f"Car {car_id} is available for the requested dates")
        return True, None
    except Exception as e:
        logger.error(f"Error checking availability: {e}")
        return False, "Error checking availability"
    try:
        # Check for overlapping confirmed bookings
        overlap_query = supabase.table('bookings').select('id').eq('car_id', car_id).in_('status', ['confirmed', 'pending']).or_(
            f"start_date.lte.{end_date},end_date.gt.{start_date}"
        ).execute()
        
        if overlap_query.data:
            return False, f"Car is booked for overlapping dates"
        
        return True, None
    except Exception as e:
        logger.error(f"Error checking availability: {e}")
        return False, "Error checking availability"

# ADMIN API ENDPOINTS

@app.route('/admin/login', methods=['POST'])
def admin_login():
    """Admin login endpoint"""
    try:
        data = request.get_json()
        if not data or 'username' not in data or 'password' not in data:
            return jsonify({'error': 'Username and password required'}), 400
        
        username = data['username']
        password = data['password']
        
        # Simple credential check
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session['admin_logged_in'] = True
            session['admin_username'] = username
            session['admin_login_time'] = datetime.now().isoformat()
            session.permanent = True
            
            logger.info(f"Admin login successful for {username} from IP: {get_client_ip()}")
            
            return jsonify({
                'success': True,
                'message': 'Login successful',
                'admin': username,
                'session_expires': (datetime.now() + timedelta(hours=8)).isoformat()
            })
        else:
            logger.warning(f"Failed admin login attempt for username '{username}' from IP: {get_client_ip()}")
            return jsonify({'error': 'Invalid credentials'}), 401
    
    except Exception as e:
        logger.error(f"Error in admin login: {e}")
        return jsonify({'error': 'Login failed'}), 500

@app.route('/admin/logout', methods=['POST'])
@admin_required
def admin_logout():
    """Admin logout endpoint"""
    session.clear()
    return jsonify({'success': True, 'message': 'Logged out successfully'})

@app.route('/admin/status', methods=['GET'])
@admin_required
def admin_status():
    """Get admin session status"""
    return jsonify({
        'logged_in': True,
        'admin': session.get('admin_username'),
        'login_time': session.get('admin_login_time'),
        'session_expires': (datetime.fromisoformat(session.get('admin_login_time', datetime.now().isoformat())) + timedelta(hours=8)).isoformat()
    })

@app.route('/admin/cars', methods=['GET'])
@admin_required
def admin_get_cars():
    """Get all cars for admin (including inactive)"""
    try:
        if not supabase:
            return jsonify({"error": "Database not available"}), 503
        
        response = supabase.table('cars').select('*').order('created_at', desc=True).execute()
        
        cars = response.data
        
        for car in cars:
            try:
                images_response = supabase.table('car_images').select('*').eq('car_id', car['id']).order('sort_order').execute()
                car['car_images'] = images_response.data
            except Exception as e:
                logger.warning(f"Failed to get images for car {car['id']}: {e}")
                car['car_images'] = []
        
        # Add summary statistics
        total_cars = len(cars)
        active_cars = len([car for car in cars if car.get('is_active', True)])
        inactive_cars = total_cars - active_cars
        
        return jsonify({
            "cars": cars,
            "statistics": {
                "total": total_cars,
                "active": active_cars,
                "inactive": inactive_cars
            }
        })
    except Exception as e:
        logger.error(f"Error getting cars for admin: {e}")
        return jsonify({"error": "Failed to fetch cars"}), 500

@app.route('/admin/cars', methods=['POST'])
@admin_required
def admin_create_car():
    """Create new car with optional image upload"""
    try:
        if not supabase:
            return jsonify({"error": "Database not available"}), 503
        
        # Handle multipart/form-data for file upload
        if request.content_type and request.content_type.startswith('multipart/form-data'):
            car_data = {}
            
            # Extract form fields
            for key in request.form:
                value = request.form[key]
                if key == 'features' and value:
                    try:
                        car_data[key] = json.loads(value)
                    except json.JSONDecodeError:
                        car_data[key] = [feature.strip() for feature in value.split(',') if feature.strip()]
                elif key in ['year', 'price_per_day', 'deposit_amount']:
                    try:
                        car_data[key] = float(value) if key in ['price_per_day', 'deposit_amount'] else int(value)
                    except (ValueError, TypeError):
                        pass
                elif key == 'is_active':
                    car_data[key] = value.lower() in ['true', '1', 'yes', 'on']
                else:
                    car_data[key] = value
            
            # Handle file upload
            uploaded_image = request.files.get('image')
        else:
            # Handle JSON data
            car_data = request.get_json()
            if not car_data:
                return jsonify({"error": "No data provided"}), 400
            uploaded_image = None
        
        # Validate car data
        validated_data = validate_car_data(car_data)
        
        # Set defaults
        validated_data['is_active'] = validated_data.get('is_active', True)
        validated_data['deposit_amount'] = validated_data.get('deposit_amount', 500.00)
        validated_data['created_at'] = datetime.now().isoformat()
        validated_data['updated_at'] = datetime.now().isoformat()
        
        # Create car record
        car_response = supabase.table('cars').insert(validated_data).execute()
        
        if not car_response.data:
            return jsonify({"error": "Failed to create car"}), 500
        
        car = car_response.data[0]
        car_id = car['id']
        
        # Handle image upload if provided
        image_info = None
        if uploaded_image and uploaded_image.filename:
            try:
                validate_image_file(uploaded_image)
                image_info = upload_image_to_supabase(uploaded_image, car_id, is_main=True)
                logger.info(f"Image uploaded for car {car_id}: {image_info['filename']}")
            except Exception as e:
                logger.error(f"Failed to upload image for car {car_id}: {e}")
                # Don't fail car creation if image upload fails
                image_info = {"error": str(e)}
        
        try:
            images_response = supabase.table('car_images').select('*').eq('car_id', car_id).order('sort_order').execute()
            car['car_images'] = images_response.data
        except Exception as e:
            logger.warning(f"Failed to get images for car {car_id}: {e}")
            car['car_images'] = []
        
        logger.info(f"Car created by admin {session['admin_username']}: {car['brand']} {car['model']} (ID: {car_id})")
        
        return jsonify({
            "success": True,
            "car": car,
            "image_upload": image_info,
            "message": "Car created successfully"
        }), 201
        
    except BadRequest as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Error creating car: {e}")
        return jsonify({"error": "Failed to create car"}), 500

@app.route('/admin/cars/<car_id>', methods=['PUT'])
@admin_required
def admin_update_car(car_id):
    """Update existing car with optional image upload/replacement"""
    try:
        if not supabase:
            return jsonify({"error": "Database not available"}), 503
        
        try:
            uuid.UUID(car_id)
        except ValueError:
            return jsonify({"error": "Invalid car ID format"}), 400
        
        # Check if car exists
        existing_car_response = supabase.table('cars').select('*').eq('id', car_id).execute()
        if not existing_car_response.data:
            return jsonify({"error": "Car not found"}), 404
        
        # Handle multipart/form-data for file upload
        if request.content_type and request.content_type.startswith('multipart/form-data'):
            car_data = {}
            
            # Extract form fields
            for key in request.form:
                value = request.form[key]
                if key == 'features' and value:
                    try:
                        car_data[key] = json.loads(value)
                    except json.JSONDecodeError:
                        car_data[key] = [feature.strip() for feature in value.split(',') if feature.strip()]
                elif key in ['year', 'price_per_day', 'deposit_amount']:
                    try:
                        car_data[key] = float(value) if key in ['price_per_day', 'deposit_amount'] else int(value)
                    except (ValueError, TypeError):
                        pass
                elif key == 'is_active':
                    car_data[key] = value.lower() in ['true', '1', 'yes', 'on']
                else:
                    car_data[key] = value
            
            # Handle file uploads
            uploaded_image = request.files.get('image')
            replace_main_image = request.form.get('replace_main_image', 'false').lower() == 'true'
        else:
            # Handle JSON data
            car_data = request.get_json()
            if not car_data:
                return jsonify({"error": "No data provided"}), 400
            uploaded_image = None
            replace_main_image = False
        
        # Remove empty fields to avoid overwriting with None
        car_data = {k: v for k, v in car_data.items() if v is not None and v != ''}
        
        updated_car = existing_car_response.data[0]  # Start with existing data
        
        if car_data:
            # Validate car data if there are updates
            validated_data = validate_car_data({**existing_car_response.data[0], **car_data})
            
            # Set update timestamp
            validated_data['updated_at'] = datetime.now().isoformat()
            
            # Update car record
            car_response = supabase.table('cars').update(validated_data).eq('id', car_id).execute()
            
            if not car_response.data:
                return jsonify({"error": "Failed to update car"}), 500
            
            updated_car = car_response.data[0]
        
        # Handle image upload/replacement if provided
        image_info = None
        if uploaded_image and uploaded_image.filename:
            try:
                validate_image_file(uploaded_image)
                
                # If replacing main image, delete the old main image first
                if replace_main_image:
                    old_main_images = supabase.table('car_images').select('*').eq('car_id', car_id).eq('is_main', True).execute()
                    for old_image in old_main_images.data:
                        delete_image_from_supabase(old_image['id'])
                
                # Upload new image
                image_info = upload_image_to_supabase(uploaded_image, car_id, is_main=True)
                logger.info(f"Image uploaded for car {car_id}: {image_info['filename']}")
                
            except Exception as e:
                logger.error(f"Failed to upload image for car {car_id}: {e}")
                image_info = {"error": str(e)}
        
        try:
            images_response = supabase.table('car_images').select('*').eq('car_id', car_id).order('sort_order').execute()
            updated_car['car_images'] = images_response.data
        except Exception as e:
            logger.warning(f"Failed to get images for car {car_id}: {e}")
            updated_car['car_images'] = []
        
        logger.info(f"Car updated by admin {session['admin_username']}: Car ID {car_id}")
        
        return jsonify({
            "success": True,
            "car": updated_car,
            "image_upload": image_info,
            "message": "Car updated successfully"
        })
        
    except BadRequest as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Error updating car {car_id}: {e}")
        return jsonify({"error": "Failed to update car"}), 500

@app.route('/admin/cars/<int:car_id>', methods=['DELETE'])
@admin_required
def admin_delete_car(car_id):
    """Delete car and all its images"""
    try:
        if not supabase:
            return jsonify({"error": "Database not available"}), 503
        
        try:
            uuid.UUID(car_id)  # Validate it's a proper UUID
        except ValueError:
            return jsonify({"error": "Invalid car ID format"}), 400
        
        # Check if car exists
        existing_car_response = supabase.table('cars').select('*').eq('id', car_id).execute()
        if not existing_car_response.data:
            return jsonify({"error": "Car not found"}), 404
        
        car = existing_car_response.data[0]
        
        # Check for existing bookings
        bookings_response = supabase.table('bookings').select('id').eq('car_id', car_id).in_('status', ['confirmed', 'pending']).execute()
        if bookings_response.data:
            return jsonify({"error": "Cannot delete car with existing bookings"}), 409
        
        # Delete all car images first
        images_response = supabase.table('car_images').select('*').eq('car_id', car_id).execute()
        deleted_images = []
        
        for image in images_response.data:
            if delete_image_from_supabase(image['id']):
                deleted_images.append(image['id'])
        
        # Delete car record
        car_delete_response = supabase.table('cars').delete().eq('id', car_id).execute()
        
        if not car_delete_response.data:
            return jsonify({"error": "Failed to delete car"}), 500
        
        logger.info(f"Car deleted by admin {session['admin_username']}: {car['brand']} {car['model']} (ID: {car_id})")
        
        return jsonify({
            "success": True,
            "deleted_car": car,
            "deleted_images": deleted_images,
            "message": "Car and all images deleted successfully"
        })
        
    except Exception as e:
        logger.error(f"Error deleting car {car_id}: {e}")
        return jsonify({"error": "Failed to delete car"}), 500

@app.route('/admin/cars/<car_id>/images/<image_id>', methods=['DELETE'])
@admin_required
def admin_delete_car_image(car_id, image_id):
    """Delete specific car image"""
    try:
        if not supabase:
            return jsonify({"error": "Database not available"}), 503
        
        try:
            uuid.UUID(car_id)
            uuid.UUID(image_id)
        except ValueError:
            return jsonify({"error": "Invalid ID format"}), 400
        
        # Verify image belongs to the car
        image_response = supabase.table('car_images').select('*').eq('id', image_id).eq('car_id', car_id).execute()
        
        if not image_response.data:
            return jsonify({"error": "Image not found"}), 404
        
        image = image_response.data[0]
        
        # Delete image
        if delete_image_from_supabase(image_id):
            logger.info(f"Image deleted by admin {session['admin_username']}: Image ID {image_id} for car {car_id}")
            return jsonify({
                "success": True,
                "deleted_image": image,
                "message": "Image deleted successfully"
            })
        else:
            return jsonify({"error": "Failed to delete image"}), 500
            
    except Exception as e:
        logger.error(f"Error deleting image {image_id}: {e}")
        return jsonify({"error": "Failed to delete image"}), 500

@app.route('/admin/bookings', methods=['GET'])
@admin_required
def admin_get_bookings():
    """Get all bookings for admin with filtering options"""
    try:
        if not supabase:
            return jsonify({"error": "Database not available"}), 503
        
        # Get query parameters
        status = request.args.get('status')
        car_id = request.args.get('car_id')
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        limit = request.args.get('limit', 100)
        offset = request.args.get('offset', 0)
        
        # Build query
        query = supabase.table('bookings').select('*, cars(brand, model, year, class)')
        
        # Apply filters
        if status:
            query = query.eq('status', status)
        
        if car_id:
            try:
                query = query.eq('car_id', int(car_id))
            except ValueError:
                return jsonify({"error": "Invalid car_id"}), 400
        
        if start_date:
            if not validate_date_format(start_date):
                return jsonify({"error": "Invalid start_date format"}), 400
            query = query.gte('start_date', start_date)
        
        if end_date:
            if not validate_date_format(end_date):
                return jsonify({"error": "Invalid end_date format"}), 400
            query = query.lte('end_date', end_date)
        
        # Apply pagination and ordering
        try:
            limit = min(int(limit), 500)  # Max 500 records
            offset = max(int(offset), 0)
        except ValueError:
            return jsonify({"error": "Invalid limit or offset"}), 400
        
        query = query.order('created_at', desc=True).limit(limit).offset(offset)
        
        # Execute query
        response = query.execute()
        bookings = response.data
        
        # Get summary statistics
        stats_query = supabase.table('bookings').select('status, total_price')
        if start_date:
            stats_query = stats_query.gte('start_date', start_date)
        if end_date:
            stats_query = stats_query.lte('end_date', end_date)
        
        stats_response = stats_query.execute()
        
        # Calculate statistics
        total_bookings = len(stats_response.data)
        pending_bookings = len([b for b in stats_response.data if b['status'] == 'pending'])
        confirmed_bookings = len([b for b in stats_response.data if b['status'] == 'confirmed'])
        cancelled_bookings = len([b for b in stats_response.data if b['status'] == 'cancelled'])
        total_revenue = sum([float(b['total_price'] or 0) for b in stats_response.data if b['status'] == 'confirmed'])
        
        return jsonify({
            "bookings": bookings,
            "pagination": {
                "limit": limit,
                "offset": offset,
                "returned": len(bookings)
            },
            "filters": {
                "status": status,
                "car_id": car_id,
                "start_date": start_date,
                "end_date": end_date
            },
            "statistics": {
                "total": total_bookings,
                "pending": pending_bookings,
                "confirmed": confirmed_bookings,
                "cancelled": cancelled_bookings,
                "total_revenue": total_revenue
            }
        })
        
    except Exception as e:
        logger.error(f"Error getting bookings for admin: {e}")
        return jsonify({"error": "Failed to fetch bookings"}), 500

# EXISTING PUBLIC API ENDPOINTS (unchanged)

@app.route('/', methods=['GET'])
def root():
    """Root endpoint"""
    return jsonify({
        "message": "Sof Car API",
        "version": "1.2.0",
        "status": "running",
        "timestamp": datetime.now().isoformat(),
        "admin_endpoints": "/admin/*"
    })

@app.route('/cars', methods=['GET'])
def get_cars():
    """Get all available cars with optional filtering"""
    try:
        if not supabase:
            return jsonify({"error": "Database not available"}), 503
        
        # Get query parameters
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        car_class = request.args.get('class')
        
        # Base query - only active cars
        query = supabase.table('cars').select('*').eq('is_active', True)
        
        if car_class:
            query = query.eq('class', car_class)
        
        response = query.order('brand').execute()
        cars = response.data
        
        # If date range provided, filter out unavailable cars
        # if start_date and end_date:
            # available_cars = []
            # for car in cars:
                # is_available, _ = check_car_availability_atomic(car[.id.], start_date, end_date)
                # if is_available:
                    # available_cars.append(car)
            # cars = available_cars
        
        return jsonify({
            "cars": cars,
            "count": len(cars),
            "filters": {
                "start_date": start_date,
                "end_date": end_date,
                "class": car_class
            }
        })
    except Exception as e:
        logger.error(f"Error getting cars: {e}")
        return jsonify({"error": "Failed to fetch cars"}), 500

@app.route('/cars/<car_id>', methods=['GET'])
def get_car(car_id):
    """Get specific car by ID"""
    try:
        if not supabase:
            return jsonify({"error": "Database not available"}), 503
        
        try:
            uuid.UUID(car_id)
        except ValueError:
            return jsonify({"error": "Invalid car ID format"}), 400
        
        response = supabase.table('cars').select('*').eq('id', car_id).eq('is_active', True).execute()
        
        if not response.data:
            return jsonify({"error": "Car not found"}), 404
        
        car = response.data[0]
        
        # Get car images if they exist
        try:
            images_response = supabase.table('car_images').select('*').eq('car_id', car_id).order('sort_order').execute()
            car['images'] = images_response.data
        except:
            car['images'] = []
        
        return jsonify(car)
    except Exception as e:
        logger.error(f"Error getting car {car_id}: {e}")
        return jsonify({"error": "Failed to fetch car"}), 500

@app.route('/cars/<car_id>/availability', methods=['GET'])
def get_car_availability(car_id):
    """Get car availability for date range with pricing"""
    try:
        if not supabase:
            return jsonify({"error": "Database not available"}), 503
        
        try:
            uuid.UUID(car_id)
        except ValueError:
            return jsonify({"error": "Invalid car ID format"}), 400
        
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        if not start_date or not end_date:
            return jsonify({"error": "start_date and end_date are required"}), 400
        
        # Validate date format
        if not validate_date_format(start_date) or not validate_date_format(end_date):
            return jsonify({"error": "Invalid date format. Use YYYY-MM-DD"}), 400
        
        # Get car details
        car_response = supabase.table('cars').select('*').eq('id', car_id).eq('is_active', True).execute()
        if not car_response.data:
            return jsonify({"error": "Car not found"}), 404
        
        car = car_response.data[0]
        
        # Check availability
        is_available, error_msg = check_car_availability_atomic(car_id, start_date, end_date)
        
        result = {
            "car": car,
            "start_date": start_date,
            "end_date": end_date,
            "is_available": is_available
        }
        
        if is_available:
            total_price = calculate_total_price(car['price_per_day'], start_date, end_date)
            result["total_price"] = total_price
            result["rental_days"] = (datetime.strptime(end_date, '%Y-%m-%d').date() - datetime.strptime(start_date, '%Y-%m-%d').date()).days
        else:
            result["error"] = error_msg
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error getting availability for car {car_id}: {e}")
        return jsonify({"error": "Failed to check availability"}), 500

@app.route('/bookings', methods=['POST'])
def create_booking():
    """Create a new booking with optimistic locking"""
    try:
        # Rate limiting check
        check_rate_limit()
        
        # Validate input data
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        validated_data = validate_booking_data(data)
        
        if not supabase:
            return jsonify({"error": "Database not available"}), 503
        
        car_id = int(validated_data['car_id'])
        
        # Simple lock mechanism (in production use Redis or database locks)
        if car_id in booking_locks:
            return jsonify({"error": "Car is being booked by another user. Please try again."}), 409
        
        booking_locks[car_id] = time.time()
        
        try:
            # Get car details
            car_response = supabase.table('cars').select('*').eq('id', car_id).eq('is_active', True).execute()
            if not car_response.data:
                return jsonify({"error": "Car not found"}), 404
            
            car = car_response.data[0]
            
            # Atomic availability check
            is_available, error_msg = check_car_availability_atomic(
                car_id, validated_data['start_date'], validated_data['end_date']
            )
            
            if not is_available:
                return jsonify({"error": error_msg}), 409
            
            # Calculate total price
            total_price = calculate_total_price(car['price_per_day'], validated_data['start_date'], validated_data['end_date'])
            rental_days = (datetime.strptime(validated_data['end_date'], '%Y-%m-%d').date() - datetime.strptime(validated_data['start_date'], '%Y-%m-%d').date()).days
            
            # Create booking with all necessary data
            booking_data = {
                'car_id': car_id,
                'start_date': validated_data['start_date'],
                'end_date': validated_data['end_date'],
                'client_name': validated_data['client_name'].strip(),
                'client_email': validated_data['client_email'].strip().lower(),
                'client_phone': validated_data['client_phone'].strip(),
                'total_price': total_price,
                'rental_days': rental_days,
                'status': 'pending',  # Start as pending, confirm after payment
                'payment_method': validated_data.get('payment_method', 'cash'),
                'deposit_amount': car['deposit_amount'],
                'deposit_status': 'pending',
                'booking_reference': generate_booking_reference(),
                'ip_address': get_client_ip(),
                'notes': validated_data.get('notes', ''),
                'created_at': datetime.now().isoformat(),
                'version': 1  # For optimistic locking
            }
            
            # Insert booking
            booking_response = supabase.table('bookings').insert(booking_data).execute()
            
            if not booking_response.data:
                return jsonify({"error": "Failed to create booking"}), 500
            
            booking = booking_response.data[0]
            
            logger.info(f"Booking created: {booking['booking_reference']} for car {car_id} by {booking['client_email']}")
            
            return jsonify({
                "success": True,
                "booking": booking,
                "message": "Booking created successfully",
                "next_steps": "Please proceed with payment confirmation"
            }), 201
            
        finally:
            # Release lock
            if car_id in booking_locks:
                del booking_locks[car_id]
        
    except TooManyRequests as e:
        return jsonify({"error": str(e)}), 429
    except BadRequest as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Error creating booking: {e}")
        return jsonify({"error": "Failed to create booking"}), 500

@app.route('/bookings/<int:booking_id>', methods=['GET'])
def get_booking(booking_id):
    """Get booking by ID"""
    try:
        if not supabase:
            return jsonify({"error": "Database not available"}), 503
        
        response = supabase.table('bookings').select('*, cars(brand, model, year, class)').eq('id', booking_id).execute()
        
        if not response.data:
            return jsonify({"error": "Booking not found"}), 404
        
        return jsonify(response.data[0])
    except Exception as e:
        logger.error(f"Error getting booking {booking_id}: {e}")
        return jsonify({"error": "Failed to fetch booking"}), 500

@app.route('/bookings/reference/<booking_reference>', methods=['GET'])
def get_booking_by_reference(booking_reference):
    """Get booking by reference number"""
    try:
        if not supabase:
            return jsonify({"error": "Database not available"}), 503
        
        response = supabase.table('bookings').select('*, cars(brand, model, year, class)').eq('booking_reference', booking_reference).execute()
        
        if not response.data:
            return jsonify({"error": "Booking not found"}), 404
        
        return jsonify(response.data[0])
    except Exception as e:
        logger.error(f"Error getting booking {booking_reference}: {e}")
        return jsonify({"error": "Failed to fetch booking"}), 500

@app.route('/health', methods=['GET'])
def health_check():
    """Comprehensive health check endpoint"""
    health_data = {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": "1.2.0",
        "environment": os.environ.get('FLASK_ENV', 'development')
    }
    
    status_code = 200
    
    # Test database connection
    if supabase:
        try:
            response = supabase.table('cars').select('id').limit(1).execute()
            health_data['database'] = 'connected'
            health_data['database_response_time'] = 'fast'
        except Exception as e:
            health_data['database'] = f'error: {str(e)}'
            health_data['status'] = 'degraded'
            status_code = 503
    else:
        health_data['database'] = 'not_configured'
        health_data['status'] = 'degraded'
        status_code = 503
    
    # Test other components
    health_data['rate_limiting'] = 'active' if rate_limit_storage is not None else 'inactive'
    health_data['active_booking_locks'] = len(booking_locks)
    health_data['rate_limit_entries'] = len(rate_limit_storage)
    health_data['admin_session'] = 'active' if session.get('admin_logged_in') else 'inactive'
    
    return jsonify(health_data), status_code

# Error handlers
@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Endpoint not found'}), 404

@app.errorhandler(500)
def internal_server_error(error):
    logger.error(f"Internal server error: {error}")
    return jsonify({'error': 'Internal server error'}), 500

@app.errorhandler(TooManyRequests)
def handle_rate_limit_exceeded(error):
    return jsonify({'error': 'Rate limit exceeded', 'retry_after': '1 hour'}), 429

@app.errorhandler(BadRequest)
def handle_bad_request(error):
    return jsonify({'error': 'Bad request', 'details': str(error)}), 400

@app.errorhandler(Unauthorized)
def handle_unauthorized(error):
    return jsonify({'error': 'Unauthorized access'}), 401

if __name__ == '__main__':
    # Development server
    app.run(debug=True, host='0.0.0.0', port=5002)