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
HONEYPOT_FIELDS = ['website', 'phone_number', 'company', 'subject', 'message']
ALLOWED_PAYMENT_METHODS = ['cash', 'card', 'bank_transfer']
rate_limit_window = 3600  # 1 hour
rate_limit_max_requests = 3

def get_client_ip():
    """Get client IP address"""
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0]
    return request.remote_addr

def check_rate_limit():
    """Simple rate limiting check"""
    client_ip = get_client_ip()
    current_time = time.time()
    
    if client_ip not in rate_limit_storage:
        rate_limit_storage[client_ip] = {'count': 0, 'reset_time': current_time + rate_limit_window}
    
    # Reset counter if window expired
    if current_time > rate_limit_storage[client_ip]['reset_time']:
        rate_limit_storage[client_ip] = {'count': 0, 'reset_time': current_time + rate_limit_window}
    
    # Check if limit exceeded
    if rate_limit_storage[client_ip]['count'] >= rate_limit_max_requests:
        raise TooManyRequests("Rate limit exceeded. Maximum 3 bookings per hour per IP.")
    
    # Increment counter
    rate_limit_storage[client_ip]['count'] += 1

def validate_email(email: str) -> bool:
    """Validate email format (same as frontend)"""
    import re
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

def validate_phone(phone: str) -> bool:
    """Validate phone format (same as frontend)"""
    import re
    # Remove all non-digit characters
    clean_phone = re.sub(r'\D', '', phone)
    # Bulgarian phone numbers: +359 + 8 or 9 digits, or just 8 or 9 digits
    if clean_phone.startswith('359'):
        clean_phone = clean_phone[3:]
    return len(clean_phone) >= 8 and clean_phone.startswith(('8', '9'))

def validate_date_format(date_str: str) -> bool:
    """Validate date format YYYY-MM-DD"""
    try:
        datetime.strptime(date_str, '%Y-%m-%d')
        return True
    except ValueError:
        return False

def validate_booking_data(data):
    """Enhanced validation for booking data (same as frontend)"""
    required_fields = ['car_id', 'start_date', 'end_date', 'client_name', 'client_email', 'client_phone']
    
    for field in required_fields:
        if field not in data or not data[field]:
            raise BadRequest(f"Missing required field: {field}")
    
    # Honeypot check - reject if any honeypot field is filled
    for honeypot in HONEYPOT_FIELDS:
        if honeypot in data and data[honeypot]:
            logger.warning(f"Honeypot field '{honeypot}' was filled - possible bot")
            raise BadRequest("Invalid form submission")
    
    # Validate dates
    try:
        start_date = datetime.strptime(data['start_date'], '%Y-%m-%d').date()
        end_date = datetime.strptime(data['end_date'], '%Y-%m-%d').date()
        
        if start_date >= end_date:
            raise BadRequest("Start date must be before end date")
        
        if start_date < datetime.now().date():
            raise BadRequest("Start date cannot be in the past")
            
    except ValueError:
        raise BadRequest("Invalid date format. Use YYYY-MM-DD")
    
    # Validate client name (minimum 2 characters, only letters and spaces)
    import re
    if not re.match(r'^[a-zA-Zа-яА-Я\s]{2,50}$', data['client_name']):
        raise BadRequest("Invalid client name format")
    
    # Validate email
    if not validate_email(data['client_email']):
        raise BadRequest("Invalid email format")
    
    # Validate phone
    if not validate_phone(data['client_phone']):
        raise BadRequest("Invalid phone number format")
    
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
    return car_price * days

# API Endpoints

@app.route('/', methods=['GET'])
def root():
    """Root endpoint"""
    return jsonify({
        "message": "Sof Car API",
        "version": "1.0.0",
        "status": "running"
    })

@app.route('/api/cars', methods=['GET'])
def get_cars():
    """Get all available cars"""
    try:
        if not supabase:
            return jsonify({"error": "Database not available"}), 503
        
        response = supabase.table('cars').select('*').eq('is_active', True).execute()
        return jsonify(response.data)
    except Exception as e:
        logger.error(f"Error getting cars: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/cars/<car_id>', methods=['GET'])
def get_car(car_id):
    """Get specific car by ID"""
    try:
        if not supabase:
            return jsonify({"error": "Database not available"}), 503
        
        response = supabase.table('cars').select('*').eq('id', car_id).execute()
        
        if not response.data:
            return jsonify({"error": "Car not found"}), 404
        
        return jsonify(response.data[0])
    except Exception as e:
        logger.error(f"Error getting car {car_id}: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/cars/<car_id>/availability', methods=['GET'])
def get_car_availability(car_id):
    """Get car availability for date range"""
    try:
        if not supabase:
            return jsonify({"error": "Database not available"}), 503
        
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        if not start_date or not end_date:
            return jsonify({"error": "start_date and end_date are required"}), 400
        
        # Get car details
        car_response = supabase.table('cars').select('*').eq('id', car_id).execute()
        if not car_response:
            return jsonify({"error": "Car not found"}), 404
        
        car = car_response.data[0]
        
        # Get availability for date range
        availability_response = supabase.table('availability').select('*').eq('car_id', car_id).gte('date', start_date).lte('date', end_date).execute()
        
        # Check if car is available for all dates
        available_dates = [a['date'] for a in availability_response.data if a['is_available']]
        requested_dates = []
        
        start = datetime.strptime(start_date, '%Y-%m-%d').date()
        end = datetime.strptime(end_date, '%Y-%m-%d').date()
        current = start
        
        while current < end:
            requested_dates.append(current.isoformat())
            current += timedelta(days=1)
        
        is_available = all(date in available_dates for date in requested_dates)
        
        return jsonify({
            "car": car,
            "start_date": start_date,
            "end_date": end_date,
            "is_available": is_available,
            "total_days": len(requested_dates),
            "total_price": car['price_per_day'] * len(requested_dirs) if is_available else None
        })
        
    except Exception as e:
        logger.error(f"Error getting availability for car {car_id}: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/bookings', methods=['POST'])
def create_booking():
    """Create a new booking"""
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
        
        # Get car details and calculate price
        car_response = supabase.table('cars').select('*').eq('id', validated_data['car_id']).execute()
        if not car_response.data:
            return jsonify({"error": "Car not found"}), 404
        
        car = car_response.data[0]
        total_price = calculate_total_price(car['price_per_day'], validated_data['start_date'], validated_data['end_date'])
        
        # Check availability
        availability_response = supabase.table('availability').select('*').eq('car_id', validated_data['car_id']).gte('date', validated_data['start_date']).lt('date', validated_data['end_date']).execute()
        
        unavailable_dates = [a['date'] for a in availability_response.data if not a['is_available']]
        if unavailable_dates:
            return jsonify({"error": f"Car not available for dates: {unavailable_dates}"}), 400
        
        # Create booking
        booking_data = {
            **validated_data,
            'total_price': total_price,
            'status': 'confirmed',
            'payment_method': 'cash',  # Default for now
            'deposit_amount': 500.00,  # Default deposit
            'deposit_status': 'pending',
            'ip_address': get_client_ip()
        }
        
        booking_response = supabase.table('bookings').insert(booking_data).execute()
        
        if not booking_response.data:
            return jsonify({"error": "Failed to create booking"}), 500
        
        booking = booking_response.data[0]
        
        # Update availability
        start = datetime.strptime(validated_data['start_date'], '%Y-%m-%d').date()
        end = datetime.strptime(validated_data['end_date'], '%Y-%m-%d').date()
        current = start
        
        while current < end:
            availability_data = {
                'car_id': validated_data['car_id'],
                'date': current.isoformat(),
                'is_available': False,
                'booking_id': booking['id']
            }
            supabase.table('availability').upsert(availability_data).execute()
            current += timedelta(days=1)
        
        return jsonify({
            "success": True,
            "booking": booking,
            "message": "Booking created successfully"
        }), 201
        
    except TooManyRequests as e:
        return jsonify({"error": str(e)}), 500
    except BadRequest as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Error creating booking: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/bookings/<booking_id>', methods=['GET'])
def get_booking(booking_id):
    """Get booking by ID"""
    try:
        if not supabase:
            return jsonify({"error": "Database not available"}), 500
        
        response = supabase.table('bookings').select('*, cars(*)').eq('id', booking_id).execute()
        
        if not response.data:
            return jsonify({"error": "Booking not found"}), 404
        
        return jsonify(response.data[0])
    except Exception as e:
        logger.error(f"Error getting booking {booking_id}: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    health_data = {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": "1.0.0"
    }
    
    # Test database connection
    if supabase:
        try:
            # Simple query to test connection
            response = supabase.table('cars').select('id').limit(1).execute()
            health_data['database'] = 'connected'
            status_code = 200
        except Exception as e:
            health_data['database'] = f'error: {str(e)}'
            status_code = 503
    else:
        health_data['database'] = 'not_configured'
        status_code = 503
    
    # Test rate limiting storage
    health_data['rate_limiting'] = 'active' if rate_limit_storage else 'inactive'
    
    return jsonify(health_data), status_code

if __name__ == '__main__':
    # Development server
    app.run(debug=True, host='0.0.0.0', port=5002)
