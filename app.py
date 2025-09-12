"""
SofCar Flask API - Main Application
Refactored modular structure for cPanel deployment
"""

import os
import sys
import time
import logging
from datetime import datetime
from flask import Flask, request, jsonify, make_response, session
from flask_cors import CORS
from werkzeug.exceptions import BadRequest, TooManyRequests, Unauthorized

# Import our modules
from config import Config
from database import DatabaseService
from validators import (
    validate_booking_data, validate_car_data, validate_contact_form_data,
    validate_booking_update_data, validate_date_format
)
from email_service import EmailService
from auth import admin_required, admin_login, admin_logout, get_admin_status
from utils import (
    get_client_ip, calculate_total_price, check_rate_limit,
    upload_multiple_images, delete_image_simple, get_usage_statistics
)

# Initialize Flask app
app = Flask(__name__)

# Configure Flask app
app.config.update(
    SECRET_KEY=Config.SECRET_KEY,
    SESSION_COOKIE_SECURE=Config.SESSION_COOKIE_SECURE,
    SESSION_COOKIE_HTTPONLY=Config.SESSION_COOKIE_HTTPONLY,
    SESSION_COOKIE_SAMESITE=Config.SESSION_COOKIE_SAMESITE,
    SESSION_COOKIE_DOMAIN=Config.SESSION_COOKIE_DOMAIN,
    PERMANENT_SESSION_LIFETIME=Config.PERMANENT_SESSION_LIFETIME
)

# Configure CORS
CORS(app, 
     origins=Config.CORS_ORIGINS,
     supports_credentials=Config.CORS_SUPPORTS_CREDENTIALS,
     allow_headers=Config.CORS_ALLOW_HEADERS,
     methods=Config.CORS_METHODS,
     expose_headers=Config.CORS_EXPOSE_HEADERS,
     max_age=Config.CORS_MAX_AGE
)

# Configure logging
logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize services
try:
    Config.validate_required_config()
    db_service = DatabaseService(Config.SUPABASE_URL, Config.SUPABASE_ANON_KEY, Config.SUPABASE_SERVICE_ROLE_KEY)
    email_service = EmailService()
    logger.info("All services initialized successfully")
except Exception as e:
    logger.error(f"Failed to initialize services: {e}")
    db_service = None
    email_service = None

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

# ADMIN API ENDPOINTS

@app.route('/admin/login', methods=['POST'])
def admin_login_endpoint():
    """Admin login endpoint"""
    try:
        data = request.get_json()
        if not data or 'username' not in data or 'password' not in data:
            return jsonify({'error': 'Username and password required'}), 400
        
        result = admin_login(data['username'], data['password'])
        
        if 'error' in result:
            return jsonify(result), 401
        
        logger.info(f"Admin login successful for {data['username']} from IP: {get_client_ip()}")
        return jsonify(result)
    
    except Exception as e:
        logger.error(f"Error in admin login: {e}")
        return jsonify({'error': 'Login failed'}), 500

@app.route('/admin/logout', methods=['POST'])
@admin_required
def admin_logout_endpoint():
    """Admin logout endpoint"""
    return jsonify(admin_logout())

@app.route('/admin/status', methods=['GET'])
@admin_required
def admin_status_endpoint():
    """Get admin session status"""
    return jsonify(get_admin_status())

@app.route('/admin/cars', methods=['GET'])
@admin_required
def admin_get_cars():
    """Get all cars for admin (including inactive)"""
    try:
        logger.info(f"Admin requesting cars list")
        
        if not db_service:
            return jsonify({"error": "Database not available"}), 503
        
        cars = db_service.get_admin_client().table('cars').select('*').order('created_at', desc=True).execute().data
        stats = db_service.get_car_statistics()
        
        logger.info(f"Retrieved {len(cars)} cars for admin (active: {stats['active']}, inactive: {stats['inactive']})")
        
        return jsonify({
            "cars": cars,
            "statistics": stats
        })
    except Exception as e:
        logger.error(f"Error getting cars for admin: {e}")
        return jsonify({"error": "Failed to fetch cars"}), 500

@app.route('/admin/cars', methods=['POST'])
@admin_required
def admin_create_car():
    """Create new car with optional single image upload"""
    try:
        logger.info(f"Admin creating new car")
        
        if not db_service:
            return jsonify({"error": "Database not available"}), 503
        
        # Handle multipart/form-data for file upload
        if request.content_type and request.content_type.startswith('multipart/form-data'):
            car_data = {}
            
            # Extract form fields
            for key in request.form:
                value = request.form[key]
                if key == 'features' and value:
                    try:
                        import json
                        car_data[key] = json.loads(value)
                    except json.JSONDecodeError:
                        car_data[key] = [feature.strip() for feature in value.split(',') if feature.strip()]
                elif key in ['year', 'price_per_day', 'deposit_amount', 'seats', 'large_luggage', 'small_luggage', 'doors', 'min_age']:
                    try:
                        car_data[key] = float(value) if key in ['price_per_day', 'deposit_amount'] else int(value)
                    except (ValueError, TypeError):
                        pass
                elif key in ['is_active', 'four_wd', 'ac']:
                    car_data[key] = value.lower() in ['true', '1', 'yes', 'on']
                else:
                    car_data[key] = value
            
            uploaded_images = request.files.getlist('images')
        else:
            # Handle JSON data
            car_data = request.get_json()
            if not car_data:
                return jsonify({"error": "No data provided"}), 400
            uploaded_images = []
        
        # Validate car data
        validated_data = validate_car_data(car_data)
        
        # Set defaults
        validated_data['is_active'] = validated_data.get('is_active', True)
        validated_data['deposit_amount'] = validated_data.get('deposit_amount', 500.00)
        
        # Handle image upload if provided
        if uploaded_images and any(img.filename for img in uploaded_images):
            try:
                # Create car first
                car = db_service.create_car(validated_data)
                car_id = car['id']
                
                # Upload multiple images and update car
                image_urls = upload_multiple_images(uploaded_images, car_id)
                
                # Update car with image URLs
                updated_car = db_service.update_car(car_id, {'image_urls': image_urls})
                
                logger.info(f"Car created with {len(image_urls)} images: {car['brand']} {car['model']} (ID: {car_id})")
                car = updated_car
                
            except Exception as e:
                logger.error(f"Image upload failed: {e}")
                # Delete the created car since image upload failed
                try:
                    db_service.delete_car(car_id)
                    logger.info(f"Deleted car {car_id} due to image upload failure")
                except Exception as delete_error:
                    logger.error(f"Failed to delete car {car_id} after image upload failure: {delete_error}")
                return jsonify({"error": f"Image upload failed: {str(e)}"}), 400
        else:
            # Create car without images
            car = db_service.create_car(validated_data)
            car_id = car['id']
            logger.info(f"Car created without images: {car['brand']} {car['model']} (ID: {car_id})")
        
        return jsonify({
            "success": True,
            "car": car,
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
        logger.info(f"Admin updating car {car_id}")
        
        if not db_service:
            return jsonify({"error": "Database not available"}), 503
        
        # Validate car_id format
        try:
            import uuid
            uuid.UUID(car_id)
        except ValueError:
            return jsonify({"error": "Invalid car ID format"}), 400
        
        # Check if car exists
        existing_car = db_service.get_admin_client().table('cars').select('*').eq('id', car_id).execute().data
        if not existing_car:
            return jsonify({"error": "Car not found"}), 404
        
        existing_car = existing_car[0]
        
        # Parse request data based on content type
        if request.content_type and request.content_type.startswith('multipart/form-data'):
            car_data = {}
            
            # Extract form fields
            for key in request.form:
                value = request.form[key]
                if key == 'features' and value:
                    try:
                        import json
                        car_data[key] = json.loads(value)
                    except json.JSONDecodeError:
                        car_data[key] = [feature.strip() for feature in value.split(',') if feature.strip()]
                elif key == 'image_urls' and value:
                    # Parse image_urls from frontend
                    try:
                        car_data[key] = json.loads(value) if value != 'null' else []
                    except json.JSONDecodeError:
                        car_data[key] = []
                elif key in ['year', 'price_per_day', 'deposit_amount', 'seats', 'large_luggage', 'small_luggage', 'doors', 'min_age']:
                    try:
                        car_data[key] = float(value) if key in ['price_per_day', 'deposit_amount'] else int(value)
                    except (ValueError, TypeError):
                        pass
                elif key in ['is_active', 'four_wd', 'ac']:
                    car_data[key] = value.lower() in ['true', '1', 'yes', 'on']
                else:
                    car_data[key] = value
            
            # Get uploaded files
            uploaded_images = request.files.getlist('images') or []
            uploaded_images = [img for img in uploaded_images if img and img.filename]  # Filter empty files
        else:
            # Handle JSON data
            car_data = request.get_json()
            if not car_data:
                return jsonify({"error": "No data provided"}), 400
            uploaded_images = []
        
        # Remove empty fields (but keep image_urls even if empty array)
        car_data = {k: v for k, v in car_data.items() 
                   if v is not None and v != '' and k != 'image_urls'} or {}
        
        # Special handling for image_urls - keep even if empty array
        if 'image_urls' in request.form or (request.get_json() and 'image_urls' in request.get_json()):
            if request.content_type and request.content_type.startswith('multipart/form-data'):
                value = request.form.get('image_urls', '[]')
                try:
                    car_data['image_urls'] = json.loads(value) if value != 'null' else []
                except json.JSONDecodeError:
                    car_data['image_urls'] = []
            else:
                car_data['image_urls'] = request.get_json().get('image_urls', [])
        
        update_data = {}
        
        # Validate and prepare non-image update data
        if car_data:
            # Remove image_urls from validation data
            validation_data = {k: v for k, v in car_data.items() if k != 'image_urls'}
            if validation_data:
                validation_data = {**existing_car, **validation_data}
                validated_data = validate_car_data(validation_data)
                update_data.update({k: v for k, v in validated_data.items() 
                                  if k in car_data and k != 'image_urls'})
        
        # IMAGE MANAGEMENT LOGIC
        # Get existing URLs (handle None case)
        existing_urls = existing_car.get('image_urls', [])
        if existing_urls is None:
            existing_urls = []
        
        logger.info(f"Image update for car {car_id}")
        logger.info(f"  Existing URLs: {existing_urls}")
        logger.info(f"  Frontend sent image_urls: {'image_urls' in car_data}")
        logger.info(f"  New files to upload: {len(uploaded_images)}")
        
        # Step 1: Upload new images if any
        new_image_urls = []
        if uploaded_images:
            try:
                logger.info(f"Uploading {len(uploaded_images)} new images")
                new_image_urls = upload_multiple_images(uploaded_images, car_id)
                logger.info(f"Successfully uploaded new images: {new_image_urls}")
            except Exception as e:
                logger.error(f"Image upload failed: {e}")
                return jsonify({"error": f"Image upload failed: {str(e)}"}), 400
        
        # Step 2: Determine final image URLs based on frontend changes
        if 'image_urls' in car_data:
            # Frontend has made changes (deletions/reordering)
            frontend_urls = car_data['image_urls']
            if frontend_urls is None:
                frontend_urls = []
            
            logger.info(f"  Frontend URLs (after changes): {frontend_urls}")
            
            # Find removed images (in existing but not in frontend)
            removed_urls = [url for url in existing_urls if url not in frontend_urls]
            
            if removed_urls:
                logger.info(f"  Deleting removed images: {removed_urls}")
                for removed_url in removed_urls:
                    try:
                        delete_image_simple(removed_url)
                        logger.info(f"    Deleted: {removed_url}")
                    except Exception as e:
                        logger.warning(f"    Failed to delete {removed_url}: {e}")
            
            # Check if frontend wants to reorder images (main_image_index parameter)
            main_image_index = car_data.get('main_image_index')
            if main_image_index is not None:
                try:
                    main_index = int(main_image_index)
                    # Create the combined list as frontend sees it
                    all_urls = frontend_urls + new_image_urls
                    
                    if 0 <= main_index < len(all_urls):
                        # Reorder: move the image at main_index to position 0
                        main_image = all_urls[main_index]
                        # Remove from current position and add to beginning
                        all_urls.pop(main_index)
                        all_urls.insert(0, main_image)
                        final_urls = all_urls
                    else:
                        # Invalid index, use default order
                        final_urls = frontend_urls + new_image_urls
                        logger.warning(f"  Invalid main_image_index {main_index}, using default order")
                except (ValueError, TypeError) as e:
                    # Invalid main_image_index, use default order
                    final_urls = frontend_urls + new_image_urls
                    logger.warning(f"  Invalid main_image_index format: {e}, using default order")
            else:
                # No reordering requested, use default order
                final_urls = frontend_urls + new_image_urls
                logger.info(f"  Final URLs (frontend + new): {final_urls}")
        else:
            # No frontend changes, just add new images to existing
            final_urls = existing_urls + new_image_urls
            logger.info(f"  Final URLs (existing + new): {final_urls}")
        
        # Step 3: Update database if images changed
        if final_urls != existing_urls:
            update_data['image_urls'] = final_urls
            logger.info(f"Images changed - updating database with {len(final_urls)} URLs")
        else:
            logger.info("No image changes needed")
        
        # Step 4: Remove frontend-only parameters before database update
        if 'main_image_index' in update_data:
            del update_data['main_image_index']
        
        # Step 5: Perform database update if there are changes
        if update_data:
            updated_car = db_service.update_car(car_id, update_data)
            logger.info(f"Car {car_id} updated successfully")
        else:
            updated_car = existing_car
            logger.info(f"No changes to update for car {car_id}")
        
        logger.info(f"Car update completed by admin: Car ID {car_id}")
        
        return jsonify({
            "success": True,
            "car": updated_car,
            "message": "Car updated successfully"
        })
        
    except BadRequest as e:
        logger.error(f"Bad request for car {car_id}: {e}")
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Error updating car {car_id}: {e}", exc_info=True)
        return jsonify({"error": "Failed to update car"}), 500

@app.route('/admin/cars/<car_id>', methods=['DELETE'])
@admin_required
def admin_delete_car(car_id):
    """Delete car and its images"""
    try:
        logger.info(f"Admin attempting to delete car {car_id}")
        
        if not db_service:
            return jsonify({"error": "Database not available"}), 503
        
        try:
            import uuid
            uuid.UUID(car_id)
        except ValueError:
            return jsonify({"error": "Invalid car ID format"}), 400
        
        # Check if car exists using admin client
        existing_car = db_service.get_admin_client().table('cars').select('*').eq('id', car_id).execute().data
        if not existing_car:
            return jsonify({"error": "Car not found"}), 404
        
        car = existing_car[0]
        
        # Check for existing bookings using admin client
        bookings = db_service.get_admin_client().table('bookings').select('id').eq('car_id', car_id).in_('status', ['confirmed', 'pending']).execute().data
        if bookings:
            return jsonify({"error": "Cannot delete car with existing bookings"}), 409
        
        # Delete images if exist
        if car.get('image_urls'):
            for image_url in car['image_urls']:
                delete_image_simple(image_url)
        
        # Delete car record using admin client (service role key)
        db_service.delete_car(car_id)
        
        logger.info(f"Car deleted by admin: {car['brand']} {car['model']} (ID: {car_id})")
        
        return jsonify({
            "success": True,
            "deleted_car": car,
            "message": "Car deleted successfully"
        })
        
    except Exception as e:
        logger.error(f"Error deleting car {car_id}: {e}")
        return jsonify({"error": "Failed to delete car"}), 500

@app.route('/admin/bookings/<booking_id>', methods=['PUT'])
@admin_required
def admin_update_booking(booking_id):
    """Update booking - only status, deposit_status, and notes allowed"""
    try:
        logger.info(f"Admin updating booking {booking_id}")
        
        if not db_service:
            return jsonify({"error": "Database not available"}), 503
        
        try:
            import uuid
            uuid.UUID(booking_id)
        except ValueError:
            return jsonify({"error": "Invalid booking ID format"}), 400
        
        # Check if booking exists using admin client
        existing_booking = db_service.get_admin_client().table('bookings').select('*').eq('id', booking_id).execute().data
        if not existing_booking:
            return jsonify({"error": "Booking not found"}), 404
        
        existing_booking = existing_booking[0]
        
        # Get update data
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        # Validate update data
        update_data = validate_booking_update_data(data)
        
        logger.info(f"Updating booking {booking_id} with data: {update_data}")
        
        # Use admin client for update operations (bypasses RLS)
        updated_booking = db_service.update_booking(booking_id, update_data)
        
        logger.info(f"Successfully updated booking {booking_id}. New values: {updated_booking}")
        logger.info(f"Booking updated by admin: Booking ID {booking_id}, Changes: {list(update_data.keys())}")
        
        return jsonify({
            "success": True,
            "booking": updated_booking,
            "message": "Booking updated successfully",
            "updated_fields": list(update_data.keys())
        })
        
    except BadRequest as e:
        logger.error(f"Bad request for booking {booking_id}: {e}")
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Error updating booking {booking_id}: {e}")
        return jsonify({"error": "Failed to update booking"}), 500

@app.route('/admin/bookings/<booking_id>', methods=['PATCH'])
@admin_required
def admin_delete_booking(booking_id):
    """Soft delete a booking by setting status to 'deleted'"""
    try:
        logger.info(f"Admin attempting to soft delete booking {booking_id}")
        
        # Get request data
        data = request.get_json()
        if not data or data.get('status') != 'deleted':
            return jsonify({"error": "Invalid request. Expected status: 'deleted'"}), 400
        
        # Check if booking exists using admin client
        existing_booking = db_service.get_admin_client().table('bookings').select('*').eq('id', booking_id).execute().data
        if not existing_booking:
            logger.warning(f"Booking {booking_id} not found")
            return jsonify({"error": "Booking not found"}), 404
        
        existing_booking = existing_booking[0]
        
        # Check if already deleted
        if existing_booking.get('status') == 'deleted':
            logger.warning(f"Booking {booking_id} is already deleted")
            return jsonify({"error": "Booking is already deleted"}), 400
        
        # Soft delete by setting status to 'deleted'
        deleted_booking = db_service.soft_delete_booking(booking_id)
        
        logger.info(f"Successfully soft deleted booking {booking_id}")
        logger.info(f"Booking soft deleted by admin: Booking ID {booking_id}")
        
        return jsonify({
            "success": True,
            "booking": deleted_booking,
            "message": "Booking soft deleted successfully"
        })
        
    except Exception as e:
        logger.error(f"Error deleting booking {booking_id}: {e}")
        return jsonify({"error": "Failed to delete booking"}), 500

@app.route('/admin/bookings', methods=['GET'])
@admin_required
def admin_get_bookings():
    """Get all bookings for admin with filtering options"""
    try:
        logger.info(f"Admin requesting bookings list")
        
        if not db_service:
            return jsonify({"error": "Database not available"}), 503
        
        # Get query parameters
        filters = {
            'status': request.args.get('status'),
            'car_id': request.args.get('car_id'),
            'start_date': request.args.get('start_date'),
            'end_date': request.args.get('end_date')
        }
        
        # Remove None values
        filters = {k: v for k, v in filters.items() if v is not None}
        
        limit = min(int(request.args.get('limit', 100)), 500)  # Max 500 records
        offset = max(int(request.args.get('offset', 0)), 0)
        
        # Get bookings with filters
        bookings = db_service.get_bookings_filtered(filters, limit, offset)
        
        # Get statistics
        stats = db_service.get_booking_statistics(filters)
        
        return jsonify({
            "bookings": bookings,
            "pagination": {
                "limit": limit,
                "offset": offset,
                "returned": len(bookings)
            },
            "filters": filters,
            "statistics": stats
        })
        
    except Exception as e:
        logger.error(f"Error getting bookings for admin: {e}")
        return jsonify({"error": "Failed to fetch bookings"}), 500

# PUBLIC API ENDPOINTS

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
    """Get available cars with mandatory date filtering"""
    try:
        if not db_service:
            return jsonify({"error": "Database not available"}), 503
        
        # Get query parameters - these are now mandatory
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        car_class = request.args.get('class')
        
        # Validate required parameters
        if not start_date or not end_date:
            return jsonify({"error": "start_date and end_date are required parameters"}), 400
        
        # Get all active cars
        all_cars = db_service.get_cars(include_inactive=False, car_class=car_class)
        
        # Filter cars by availability for the requested dates
        available_cars = []
        for car in all_cars:
            is_available, _ = db_service.check_car_availability(car['id'], start_date, end_date)
            if is_available:
                available_cars.append(car)
        
        logger.info(f"Found {len(available_cars)} available cars out of {len(all_cars)} total cars for dates {start_date} to {end_date}")
        
        return jsonify({
            "cars": available_cars,
            "total": len(available_cars)
        })
    except Exception as e:
        logger.error(f"Error getting cars: {e}")
        return jsonify({"error": "Failed to fetch cars"}), 500

@app.route('/cars/all', methods=['GET'])
def get_all_cars():
    """Get all cars (active and inactive) without any filtering"""
    try:
        if not db_service:
            return jsonify({"error": "Database not available"}), 503
        
        # Get all cars without any filtering
        all_cars = db_service.get_cars(include_inactive=True)
        
        logger.info(f"Returning all {len(all_cars)} cars (active and inactive)")
        
        return jsonify({
            "cars": all_cars,
            "total": len(all_cars)
        })
    except Exception as e:
        logger.error(f"Error getting all cars: {e}")
        return jsonify({"error": "Failed to fetch all cars"}), 500

@app.route('/cars/<car_id>', methods=['GET'])
def get_car(car_id):
    """Get specific car by ID"""
    try:
        if not db_service:
            return jsonify({"error": "Database not available"}), 503
        
        try:
            import uuid
            uuid.UUID(car_id)
        except ValueError:
            return jsonify({"error": "Invalid car ID format"}), 400
        
        car = db_service.get_car_by_id(car_id)
        if not car:
            return jsonify({"error": "Car not found"}), 404
        
        return jsonify(car)
    except Exception as e:
        logger.error(f"Error getting car {car_id}: {e}")
        return jsonify({"error": "Failed to fetch car"}), 500

@app.route('/cars/<car_id>/availability', methods=['GET'])
def get_car_availability(car_id):
    """Get car availability for date range with pricing"""
    try:
        if not db_service:
            return jsonify({"error": "Database not available"}), 503
        
        try:
            import uuid
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
        car = db_service.get_car_by_id(car_id)
        if not car:
            return jsonify({"error": "Car not found"}), 404
        
        # Check availability
        is_available, error_msg = db_service.check_car_availability(car_id, start_date, end_date)
        
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
        
        if not db_service:
            return jsonify({"error": "Database not available"}), 503
        
        car_id = validated_data['car_id']
        
        # Simple lock mechanism (in production use Redis or database locks)
        from utils import booking_locks
        if car_id in booking_locks:
            return jsonify({"error": "Car is being booked by another user. Please try again."}), 409
        
        booking_locks[car_id] = time.time()
        
        try:
            # Get car details
            car = db_service.get_car_by_id(car_id)
            if not car:
                return jsonify({"error": "Car not found"}), 404
            
            # Atomic availability check
            is_available, error_msg = db_service.check_car_availability(
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
                'rental_days': rental_days,
                'client_last_name': validated_data['client_last_name'].strip(),
                'client_first_name': validated_data['client_first_name'].strip(),
                'client_email': validated_data['client_email'].strip().lower(),
                'client_phone': validated_data['client_phone'].strip(),
                'total_price': total_price,
                'status': 'pending',  # Start as pending, confirm after payment
                'payment_method': validated_data.get('payment_method', 'cash'),
                'deposit_amount': car['deposit_amount'],
                'deposit_status': 'pending',
                'ip_address': get_client_ip(),
                'notes': validated_data.get('notes', ''),
                'created_at': datetime.now().isoformat()
            }
            
            # Insert booking
            booking = db_service.create_booking(booking_data)
            
            logger.info(f"Booking created: SOF{booking['id'][:8].upper()} for car {car_id} by {booking['client_email']}")
            
            # Send emails (non-blocking)
            try:
                # Send booking confirmation to client
                email_service.send_booking_confirmation_email(booking, car)
                # Send admin notification
                email_service.send_admin_notification_email(booking, car)
                logger.info(f"Emails sent for booking SOF{booking['id'][:8].upper()}")
            except Exception as e:
                logger.error(f"Failed to send emails for booking SOF{booking['id'][:8].upper()}: {e}")
                # Don't fail the booking if email fails
            
            return jsonify({
                "success": True,
                "booking": booking,
                "message": "Booking created successfully",
                "next_steps": "Please proceed with payment confirmation"
            }), 201
                
        except Exception as e:
            logger.error(f"Error in booking creation: {e}")
            return jsonify({"error": "Failed to create booking", "details": str(e)}), 500
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
        return jsonify({"error": "Failed to create booking", "details": str(e)}), 500

@app.route('/bookings/<int:booking_id>', methods=['GET'])
def get_booking(booking_id):
    """Get booking by ID"""
    try:
        if not db_service:
            return jsonify({"error": "Database not available"}), 503
        
        booking = db_service.get_booking_by_id(booking_id)
        if not booking:
            return jsonify({"error": "Booking not found"}), 404
        
        return jsonify(booking)
    except Exception as e:
        logger.error(f"Error getting booking {booking_id}: {e}")
        return jsonify({"error": "Failed to fetch booking"}), 500

@app.route('/bookings/reference/<booking_reference>', methods=['GET'])
def get_booking_by_reference(booking_reference):
    """Get booking by reference number"""
    try:
        if not db_service:
            return jsonify({"error": "Database not available"}), 503
        
        booking = db_service.get_booking_by_reference(booking_reference)
        if not booking:
            return jsonify({"error": "Booking not found"}), 404
        
        return jsonify(booking)
    except Exception as e:
        logger.error(f"Error getting booking {booking_reference}: {e}")
        return jsonify({"error": "Failed to fetch booking"}), 500

@app.route('/contact/inquiry', methods=['POST'])
def contact_inquiry():
    """Handle contact form submissions"""
    try:
        # Rate limiting check
        check_rate_limit()
        
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        # Validate and clean form data
        form_data = validate_contact_form_data(data)
        
        # Send email
        email_sent = email_service.send_contact_form_email(form_data)
        
        if email_sent:
            logger.info(f"Contact form submitted by {form_data['email']} from IP: {get_client_ip()}")
            return jsonify({
                "success": True,
                "message": "Your message has been sent successfully. We will get back to you soon!"
            }), 200
        else:
            logger.error(f"Failed to send contact form email from {form_data['email']}")
            return jsonify({
                "success": False,
                "message": "Failed to send message. Please try again later."
            }), 500
        
    except TooManyRequests as e:
        return jsonify({"error": str(e)}), 429
    except BadRequest as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Error processing contact form: {e}")
        return jsonify({"error": "Failed to process contact form"}), 500

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
    if db_service:
        try:
            response = db_service.supabase.table('cars').select('id').limit(1).execute()
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
    from utils import rate_limit_storage, booking_locks
    health_data['rate_limiting'] = 'active' if rate_limit_storage is not None else 'inactive'
    health_data['active_booking_locks'] = len(booking_locks)
    health_data['rate_limit_entries'] = len(rate_limit_storage)
    health_data['admin_session'] = 'active' if session.get('admin_logged_in') else 'inactive'
    
    return jsonify(health_data), status_code

@app.route('/usage-overview', methods=['GET'])
def get_usage_overview():
    """Get complete usage overview - database + storage"""
    try:
        if not db_service:
            return jsonify({"error": "Database not available"}), 503
        
        overview = get_usage_statistics()
        return jsonify(overview)
        
    except Exception as e:
        logger.error(f"Error getting usage overview: {e}")
        return jsonify({"error": "Failed to get usage overview"}), 500

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