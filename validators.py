"""
Validation module for SofCar Flask API
Contains all validation functions for data validation
"""

import re
import json
import uuid
from datetime import datetime
from werkzeug.exceptions import BadRequest
from config import Config

def validate_email(email: str) -> bool:
    """Validate email format"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

def validate_phone(phone: str) -> bool:
    """Validate Bulgarian phone format"""
    # Remove all non-digit characters
    clean_phone = re.sub(r'\D', '', phone)
    return len(clean_phone) >= 10 and len(clean_phone) <= 15

def validate_date_format(date_str: str) -> bool:
    """Validate date format YYYY-MM-DD"""
    try:
        datetime.strptime(date_str, '%Y-%m-%d')
        return True
    except ValueError:
        return False

def validate_car_data(data: dict) -> dict:
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
        if data['fuel_type'] not in Config.ALLOWED_FUEL_TYPES:
            raise BadRequest(f"Invalid fuel type. Allowed: {', '.join(Config.ALLOWED_FUEL_TYPES)}")
    
    # Validate transmission if provided
    if 'transmission' in data and data['transmission']:
        if data['transmission'] not in Config.ALLOWED_TRANSMISSIONS:
            raise BadRequest(f"Invalid transmission. Allowed: {', '.join(Config.ALLOWED_TRANSMISSIONS)}")
    
    # Validate car class
    if data['class'] not in Config.ALLOWED_CAR_CLASSES:
        raise BadRequest(f"Invalid car class. Allowed: {', '.join(Config.ALLOWED_CAR_CLASSES)}")
    
    return data

def validate_booking_data(data: dict) -> dict:
    """Enhanced validation for booking data"""
    required_fields = ['car_id', 'start_date', 'end_date', 'client_last_name', 'client_first_name', 'client_email', 'client_phone']
    
    for field in required_fields:
        if field not in data or not data[field]:
            raise BadRequest(f"Missing required field: {field}")
    
    # Honeypot check - reject if any honeypot field is filled
    for honeypot in Config.HONEYPOT_FIELDS:
        if honeypot in data and data[honeypot]:
            from utils import get_client_ip
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Honeypot field '{honeypot}' was filled from IP: {get_client_ip()}")
            raise BadRequest("Invalid form submission")
    
    # Validate dates
    try:
        start_date = datetime.strptime(data['start_date'], '%Y-%m-%d').date()
        end_date = datetime.strptime(data['end_date'], '%Y-%m-%d').date()
        today = datetime.now().date()
        
        if start_date >= end_date:
            raise BadRequest("Start date must be before end date")
        
        if start_date <= today:
            raise BadRequest("Start date must be from tomorrow onwards")
        
        # Minimum rental period
        if (end_date - start_date).days < Config.MIN_RENTAL_DAYS:
            raise BadRequest(f"Minimum rental period is {Config.MIN_RENTAL_DAYS} days")
        
        # Maximum rental period
        if (end_date - start_date).days > Config.MAX_RENTAL_DAYS:
            raise BadRequest(f"Maximum rental period is {Config.MAX_RENTAL_DAYS} days")
        
        # Cannot book too far in advance
        if (start_date - today).days > Config.MAX_ADVANCE_BOOKING_DAYS:
            raise BadRequest(f"Cannot book more than {Config.MAX_ADVANCE_BOOKING_DAYS} days in advance")
            
    except ValueError:
        raise BadRequest("Invalid date format. Use YYYY-MM-DD")
    
    # Validate client last name (minimum 2 characters, letters, spaces and common characters)
    if not re.match(r'^[a-zA-Zа-яА-Я\s\-\.]{2,50}$', data['client_last_name'].strip()):
        raise BadRequest("Invalid client last name format")
    
    # Validate client first name (minimum 2 characters, letters, spaces and common characters)
    if not re.match(r'^[a-zA-Zа-яА-Я\s\-\.]{2,50}$', data['client_first_name'].strip()):
        raise BadRequest("Invalid client first name format")
    
    # Validate email
    if not validate_email(data['client_email'].strip()):
        raise BadRequest("Invalid email format")
    
    # Validate phone
    if not validate_phone(data['client_phone'].strip()):
        raise BadRequest("Invalid phone number format")
    
    # Validate car_id is valid UUID
    try:
        uuid.UUID(data['car_id'])
    except ValueError:
        raise BadRequest("Invalid car ID format")
    
    # Validate payment method
    payment_method = data.get('payment_method', 'cash')
    if payment_method not in Config.ALLOWED_PAYMENT_METHODS:
        raise BadRequest(f"Invalid payment method. Allowed: {', '.join(Config.ALLOWED_PAYMENT_METHODS)}")
    
    return data

def validate_image_file(file) -> bool:
    """Validate uploaded image file"""
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        logger.debug(f"Validating file: {file.filename}")
        
        if not file or not hasattr(file, 'filename') or file.filename == '':
            raise BadRequest("No file selected")
        
        # Check if it's actually a file object
        if not hasattr(file, 'seek') or not hasattr(file, 'tell') or not hasattr(file, 'read'):
            logger.error(f"Invalid file object: {type(file)}")
            raise BadRequest("Invalid file object")
        
        # Check file extension
        if not allowed_file(file.filename):
            raise BadRequest(f"File type not allowed. Allowed types: {', '.join(Config.ALLOWED_EXTENSIONS)}")
        
        # Check file size
        file.seek(0, 2)  # Seek to end
        file_length = file.tell()
        file.seek(0)  # Reset file pointer
        
        logger.debug(f"File size: {file_length} bytes")
        if file_length > Config.MAX_FILE_SIZE:
            raise BadRequest(f"File size too large. Maximum size: {Config.MAX_FILE_SIZE/1024/1024:.1f}MB")
        
        if file_length == 0:
            raise BadRequest("File is empty")
        
        logger.debug(f"File validation passed for: {file.filename}")
        return True
        
    except BadRequest:
        raise
    except Exception as e:
        logger.error(f"Error validating file: {e}")
        raise BadRequest(f"Error validating file: {str(e)}")

def allowed_file(filename: str) -> bool:
    """Check if uploaded file is allowed"""
    if not filename:
        return False
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in Config.ALLOWED_EXTENSIONS

def validate_contact_form_data(data: dict) -> dict:
    """Validate contact form data"""
    required_fields = ['name', 'email', 'message', 'phone']
    for field in required_fields:
        if field not in data or not data[field].strip():
            raise BadRequest(f"Missing required field: {field}")
    
    # Honeypot check
    for honeypot in Config.HONEYPOT_FIELDS:
        if honeypot in data and data[honeypot]:
            from utils import get_client_ip
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Honeypot field '{honeypot}' was filled from IP: {get_client_ip()}")
            raise BadRequest("Invalid form submission")
    
    # Validate email
    if not validate_email(data['email'].strip()):
        raise BadRequest("Invalid email format")
    
    # Validate name (minimum 2 characters)
    if len(data['name'].strip()) < 2:
        raise BadRequest("Name must be at least 2 characters")
    
    # Validate message (minimum 10 characters)
    if len(data['message'].strip()) < 10:
        raise BadRequest("Message must be at least 10 characters")
    
    return {
        'name': data['name'].strip(),
        'email': data['email'].strip().lower(),
        'phone': data['phone'].strip(),
        'message': data['message'].strip()
    }

def validate_booking_update_data(data: dict) -> dict:
    """Validate booking update data - only allow specific fields"""
    allowed_fields = ['status', 'deposit_status', 'notes']
    update_data = {}
    
    for field in allowed_fields:
        if field in data:
            update_data[field] = data[field]
    
    if not update_data:
        raise BadRequest("No valid fields to update")
    
    # Validate status values
    if 'status' in update_data:
        if update_data['status'] not in Config.VALID_BOOKING_STATUSES:
            raise BadRequest(f"Invalid status. Allowed: {', '.join(Config.VALID_BOOKING_STATUSES)}")
    
    # Validate deposit_status values
    if 'deposit_status' in update_data:
        if update_data['deposit_status'] not in Config.VALID_DEPOSIT_STATUSES:
            raise BadRequest(f"Invalid deposit_status. Allowed: {', '.join(Config.VALID_DEPOSIT_STATUSES)}")
    
    return update_data
