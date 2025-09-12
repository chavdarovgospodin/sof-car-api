"""
Utility functions module for SofCar Flask API
Contains helper functions for various operations
"""

import os
import time
import uuid
import logging
from datetime import datetime
from flask import request
from config import Config

logger = logging.getLogger(__name__)

# Simple rate limiting storage (in-memory for development)
rate_limit_storage = {}

# Concurrency protection
booking_locks = {}  # Simple in-memory locks per car_id


def get_client_ip() -> str:
    """Get client IP address"""
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    return request.remote_addr or 'unknown'


def calculate_total_price(car_price: float, start_date: str, end_date: str) -> float:
    """Calculate total price for booking"""
    start = datetime.strptime(start_date, '%Y-%m-%d').date()
    end = datetime.strptime(end_date, '%Y-%m-%d').date()
    days = (end - start).days
    total_price = car_price * days
    
    return total_price


def check_rate_limit() -> None:
    """Enhanced rate limiting check"""
    from werkzeug.exceptions import TooManyRequests
    
    client_ip = get_client_ip()
    current_time = time.time()
    
    if client_ip not in rate_limit_storage:
        rate_limit_storage[client_ip] = {'count': 0, 'reset_time': current_time + Config.RATE_LIMIT_WINDOW}
    
    # Reset counter if window expired
    if current_time > rate_limit_storage[client_ip]['reset_time']:
        rate_limit_storage[client_ip] = {'count': 0, 'reset_time': current_time + Config.RATE_LIMIT_WINDOW}
    
    # Check if limit exceeded
    if rate_limit_storage[client_ip]['count'] >= Config.RATE_LIMIT_MAX_REQUESTS:
        logger.warning(f"Rate limit exceeded for IP: {client_ip}")
        raise TooManyRequests("Rate limit exceeded. Maximum 5 bookings per hour per IP.")
    
    # Increment counter
    rate_limit_storage[client_ip]['count'] += 1


def upload_image_simple(file, car_id: str) -> str:
    """Upload single image and return URL - Alternative version"""
    from database import DatabaseService
    
    try:
        file_ext = file.filename.rsplit('.', 1)[1].lower()
        timestamp = int(time.time())
        unique_id = uuid.uuid4().hex[:8]
        filename = f"car_{car_id}_{timestamp}_{unique_id}.{file_ext}"
        
        # Read file content
        file_content = file.read()
        file.seek(0)
        
        # Use admin client for upload
        db_service = DatabaseService(Config.SUPABASE_URL, Config.SUPABASE_ANON_KEY, Config.SUPABASE_SERVICE_ROLE_KEY)
        admin_client = db_service.get_admin_client()
        
        # Simple upload without options
        response = admin_client.storage.from_(Config.SUPABASE_BUCKET).upload(
            filename,
            file_content
        )
        
        # Get public URL
        public_url = admin_client.storage.from_(Config.SUPABASE_BUCKET).get_public_url(filename)
        
        logger.info(f"Successfully uploaded image: {filename} -> {public_url}")
        return public_url
        
    except Exception as e:
        logger.error(f"Error uploading image: {e}", exc_info=True)
        raise Exception(f"Failed to upload image: {str(e)}")


def upload_multiple_images(files, car_id: str) -> list:
    """Upload multiple images and return array of URLs"""
    try:
        uploaded_urls = []
        
        # Filter out empty files and validate
        valid_files = []
        for file in files:
            # Check if it's actually a file object with required attributes
            if (file and 
                hasattr(file, 'filename') and 
                hasattr(file, 'read') and 
                hasattr(file, 'seek') and
                file.filename and 
                file.filename.strip()):
                valid_files.append(file)
                logger.debug(f"Valid file found: {file.filename}")
            else:
                logger.debug(f"Skipping invalid file object: {type(file)}")
        
        if not valid_files:
            logger.warning("No valid files provided for upload")
            return []
        
        # Upload each file
        for file in valid_files:
            try:
                logger.info(f"Processing file: {file.filename}")
                from validators import validate_image_file
                validate_image_file(file)
                image_url = upload_image_simple(file, car_id)
                uploaded_urls.append(image_url)
                logger.info(f"Successfully uploaded: {file.filename}")
            except Exception as file_error:
                logger.error(f"Failed to upload {file.filename}: {file_error}")
                # Clean up any successfully uploaded images before failing
                for url in uploaded_urls:
                    try:
                        delete_image_simple(url)
                    except:
                        pass
                raise Exception(f"Failed to upload {file.filename}: {str(file_error)}")
        
        logger.info(f"Successfully uploaded {len(uploaded_urls)} images")
        return uploaded_urls
        
    except Exception as e:
        logger.error(f"Error in upload_multiple_images: {e}")
        raise


def delete_image_simple(image_url: str) -> bool:
    """Delete image from storage by URL - FIXED VERSION"""
    from database import DatabaseService
    
    try:
        if not image_url:
            logger.warning("No image URL provided for deletion")
            return True
        
        # Extract filename from URL
        # Expected format: https://[project].supabase.co/storage/v1/object/public/cars/filename.ext
        # OR: https://[project].supabase.co/storage/v1/object/sign/cars/filename.ext?token=...
        
        # Parse the URL to get the filename
        if '/storage/v1/object/' in image_url:
            # Split by the storage path
            parts = image_url.split('/storage/v1/object/')
            if len(parts) > 1:
                # Get everything after 'public/cars/' or 'sign/cars/'
                path_part = parts[1]
                
                # Remove 'public/' or 'sign/' prefix
                if path_part.startswith('public/'):
                    path_part = path_part[7:]  # Remove 'public/'
                elif path_part.startswith('sign/'):
                    path_part = path_part[5:]  # Remove 'sign/'
                
                # Remove bucket name and get filename
                if path_part.startswith(f'{Config.SUPABASE_BUCKET}/'):
                    filename = path_part[len(Config.SUPABASE_BUCKET) + 1:]
                    
                    # Remove any query parameters (for signed URLs)
                    if '?' in filename:
                        filename = filename.split('?')[0]
                else:
                    # Fallback to simple extraction
                    filename = image_url.split('/')[-1].split('?')[0]
            else:
                filename = image_url.split('/')[-1].split('?')[0]
        else:
            # Fallback: just get the last part of the URL
            filename = image_url.split('/')[-1].split('?')[0]
        
        if not filename:
            logger.error(f"Could not extract filename from URL: {image_url}")
            return False
        
        logger.info(f"Attempting to delete file: {filename} from bucket: {Config.SUPABASE_BUCKET}")
        
        # Use admin client with service role key for deletion
        db_service = DatabaseService(Config.SUPABASE_URL, Config.SUPABASE_ANON_KEY, Config.SUPABASE_SERVICE_ROLE_KEY)
        admin_client = db_service.get_admin_client()
        
        # Delete from storage - note the list format
        try:
            result = admin_client.storage.from_(Config.SUPABASE_BUCKET).remove([filename])
            
            # Log the result
            logger.info(f"Delete operation completed for: {filename}")
            logger.debug(f"Delete result: {result}")
            
            # Verify deletion by checking if file still exists
            try:
                files = admin_client.storage.from_(Config.SUPABASE_BUCKET).list()
                file_exists = any(f['name'] == filename for f in (files or []))
                
                if not file_exists:
                    logger.info(f"Confirmed: File {filename} successfully deleted from storage")
                    return True
                else:
                    logger.warning(f"File {filename} still exists after delete attempt")
                    return False
                    
            except Exception as verify_error:
                logger.warning(f"Could not verify deletion: {verify_error}")
                # Assume success if we can't verify
                return True
                
        except Exception as delete_error:
            logger.error(f"Delete operation failed: {delete_error}")
            return False
        
    except Exception as e:
        logger.error(f"Error in delete_image_simple for URL {image_url}: {e}")
        # Return True to not block other operations
        return True


def get_usage_statistics() -> dict:
    """Get usage statistics for database and storage"""
    from database import DatabaseService
    
    try:
        db_service = DatabaseService(Config.SUPABASE_URL, Config.SUPABASE_ANON_KEY, Config.SUPABASE_SERVICE_ROLE_KEY)
        
        overview = {
            'database': {},
            'storage': {},
            'limits': {
                'database_mb': Config.DATABASE_LIMIT_MB,
                'storage_gb': Config.STORAGE_LIMIT_GB
            }
        }
        
        # Database usage - estimate based on record counts
        try:
            cars_count = db_service.supabase.table('cars').select('id', count='exact').execute()
            bookings_count = db_service.supabase.table('bookings').select('id', count='exact').execute()
            
            # Estimate size (very rough)
            estimated_size_mb = (cars_count.count * 0.1) + (bookings_count.count * 0.05)  # KB per record
            
            overview['database'] = {
                'cars_count': cars_count.count,
                'bookings_count': bookings_count.count,
                'estimated_size_mb': round(estimated_size_mb, 2),
                'usage_percent': round((estimated_size_mb / Config.DATABASE_LIMIT_MB) * 100, 1)
            }
            
        except Exception as e:
            overview['database'] = {'error': str(e)}
        
        # Storage usage
        try:
            logger.info("Checking storage usage...")
            total_files = 0
            total_size_bytes = 0
            bucket_details = {}
            
            # Known buckets to check
            known_buckets = [Config.SUPABASE_BUCKET]
            
            for bucket_name in known_buckets:
                try:
                    logger.info(f"Checking bucket: {bucket_name}")
                    files = db_service.supabase.storage.from_(bucket_name).list()
                    bucket_files = len(files) if files else 0
                    total_files += bucket_files
                    
                    # Calculate total size in bytes
                    bucket_size_bytes = 0
                    if files:
                        for file in files:
                            file_size = file.get('metadata', {}).get('size', 0)
                            if isinstance(file_size, (int, float)):
                                bucket_size_bytes += file_size
                    
                    total_size_bytes += bucket_size_bytes
                    
                    # Convert to MB
                    bucket_size_mb = round(bucket_size_bytes / (1024 * 1024), 2)
                    
                    logger.info(f"Bucket {bucket_name} has {bucket_files} files, total size: {bucket_size_mb} MB")
                    
                    bucket_details[bucket_name] = {
                        'files_count': bucket_files,
                        'size_bytes': bucket_size_bytes,
                        'size_mb': bucket_size_mb,
                        'sample_files': files[:3] if files else []  # First 3 files as sample
                    }
                except Exception as e:
                    logger.error(f"Error accessing bucket {bucket_name}: {e}")
                    bucket_details[bucket_name] = {
                        'files_count': 0,
                        'error': str(e)
                    }
            
            # Calculate total storage size
            total_size_mb = round(total_size_bytes / (1024 * 1024), 2)
            total_size_gb = round(total_size_mb / 1024, 3)
            
            overview['storage'] = {
                'total_files': total_files,
                'total_size_mb': total_size_mb,
                'total_size_gb': total_size_gb,
                'buckets': bucket_details,
                'estimated_usage_note': 'Check Supabase Dashboard for exact storage usage'
            }
            
        except Exception as e:
            logger.error(f"Error getting storage info: {e}")
            overview['storage'] = {'error': str(e)}
        
        return overview
        
    except Exception as e:
        logger.error(f"Error getting usage statistics: {e}")
        raise
