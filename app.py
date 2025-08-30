import os
import sys
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, g
from flask_cors import CORS
import logging
from supabase import create_client, Client
from functools import wraps
import hashlib
import time
from typing import Optional, Dict, Any
import json
from werkzeug.exceptions import BadRequest, TooManyRequests
import ipaddress
from dotenv import load_dotenv
import uuid

# Load environment variables
load_dotenv()

# Initialize Flask app
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-key-change-in-production')

# Enable CORS for frontend integration
CORS(app, origins=['https://sof-car.eu', 'http://localhost:3000'])

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
        # Check for overlapping confirmed bookings
        overlap_query = supabase.table('bookings').select('id').eq('car_id', car_id).in_('status', ['confirmed', 'pending']).or_(
            f"and(start_date.lte.{end_date},end_date.gt.{start_date})"
        ).execute()
        
        if overlap_query.data:
            return False, f"Car is booked for overlapping dates"
        
        return True, None
    except Exception as e:
        logger.error(f"Error checking availability: {e}")
        return False, "Error checking availability"

# API Endpoints

@app.route('/', methods=['GET'])
def root():
    """Root endpoint"""
    return jsonify({
        "message": "Sof Car API",
        "version": "1.1.0",
        "status": "running",
        "timestamp": datetime.now().isoformat()
    })

@app.route('/api/cars', methods=['GET'])
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
        if start_date and end_date:
            available_cars = []
            for car in cars:
                is_available, _ = check_car_availability_atomic(car['id'], start_date, end_date)
                if is_available:
                    available_cars.append(car)
            cars = available_cars
        
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

@app.route('/api/cars/<int:car_id>', methods=['GET'])
def get_car(car_id):
    """Get specific car by ID"""
    try:
        if not supabase:
            return jsonify({"error": "Database not available"}), 503
        
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

@app.route('/api/cars/<int:car_id>/availability', methods=['GET'])
def get_car_availability(car_id):
    """Get car availability for date range with pricing"""
    try:
        if not supabase:
            return jsonify({"error": "Database not available"}), 503
        
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

@app.route('/api/bookings', methods=['POST'])
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
                'deposit_amount': 500.00,
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

@app.route('/api/bookings/<int:booking_id>', methods=['GET'])
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

@app.route('/api/bookings/reference/<booking_reference>', methods=['GET'])
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

@app.route('/api/health', methods=['GET'])
def health_check():
    """Comprehensive health check endpoint"""
    health_data = {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": "1.1.0",
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

if __name__ == '__main__':
    # Development server
    app.run(debug=True, host='0.0.0.0', port=5002)