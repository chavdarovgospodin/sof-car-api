"""
Database service module for SofCar Flask API
Handles all Supabase database operations
"""

import logging
from datetime import datetime
from typing import Optional, Dict, Any, List
from supabase import create_client, Client
from config import Config

logger = logging.getLogger(__name__)


class DatabaseService:
    """Service class for all database operations"""
    
    def __init__(self, url: str, anon_key: str, service_role_key: str = None):
        """Initialize database service with Supabase credentials"""
        self.url = url
        self.anon_key = anon_key
        self.service_role_key = service_role_key
        
        # Initialize anon client
        try:
            self.supabase: Client = create_client(url, anon_key)
            logger.info("Supabase anon client initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Supabase anon client: {e}")
            self.supabase = None
        
        # Admin client will be created on demand
        self._admin_client = None
    
    def get_admin_client(self) -> Client:
        """Get admin client with service role key to bypass RLS"""
        if self._admin_client is not None:
            return self._admin_client
            
        try:
            if not self.service_role_key or self.service_role_key == 'your_service_role_key_here':
                raise Exception("Service role key not configured")
            
            self._admin_client = create_client(self.url, self.service_role_key)
            logger.info("Supabase admin client initialized successfully")
            return self._admin_client
        except Exception as e:
            logger.error(f"Failed to create admin client: {e}")
            raise
    
    def get_cars(self, include_inactive: bool = False, car_class: str = None) -> List[Dict[str, Any]]:
        """Get cars with optional filtering"""
        try:
            query = self.supabase.table('cars').select('*')
            
            if not include_inactive:
                query = query.eq('is_active', True)
            
            if car_class:
                query = query.eq('class', car_class)
            
            response = query.order('brand').execute()
            return response.data
        except Exception as e:
            logger.error(f"Error getting cars: {e}")
            raise
    
    def get_car_by_id(self, car_id: str) -> Optional[Dict[str, Any]]:
        """Get specific car by ID"""
        try:
            response = self.supabase.table('cars').select('*').eq('id', car_id).eq('is_active', True).execute()
            return response.data[0] if response.data else None
        except Exception as e:
            logger.error(f"Error getting car {car_id}: {e}")
            raise
    
    def create_car(self, car_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create new car"""
        try:
            # Set timestamps
            car_data['created_at'] = datetime.now().isoformat()
            car_data['updated_at'] = datetime.now().isoformat()
            
            response = self.get_admin_client().table('cars').insert(car_data).execute()
            if not response.data:
                raise Exception("Failed to create car")
            
            return response.data[0]
        except Exception as e:
            logger.error(f"Error creating car: {e}")
            raise
    
    def update_car(self, car_id: str, update_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update existing car"""
        try:
            update_data['updated_at'] = datetime.now().isoformat()
            
            response = self.supabase.table('cars').update(update_data).eq('id', car_id).execute()
            if not response.data:
                raise Exception("Failed to update car")
            
            return response.data[0]
        except Exception as e:
            logger.error(f"Error updating car {car_id}: {e}")
            raise
    
    def delete_car(self, car_id: str) -> bool:
        """Delete car by ID"""
        try:
            response = self.get_admin_client().table('cars').delete().eq('id', car_id).execute()
            
            # Verify deletion
            verify_response = self.get_admin_client().table('cars').select('id').eq('id', car_id).execute()
            if verify_response.data:
                raise Exception("Car still exists after delete operation")
            
            return True
        except Exception as e:
            logger.error(f"Error deleting car {car_id}: {e}")
            raise
    
    def check_car_availability(self, car_id: str, start_date: str, end_date: str) -> tuple[bool, Optional[str]]:
        """Check if car is available for given date range"""
        try:
            logger.info(f"Checking availability for car {car_id} from {start_date} to {end_date}")
            
            # Check for overlapping confirmed bookings
            query = self.supabase.table("bookings").select("id").eq("car_id", car_id).in_("status", ["confirmed", "pending"]).lte("start_date", end_date).gt("end_date", start_date).execute()
            
            logger.info(f"Overlap query result: {query.data}")
            
            if query.data:
                logger.info(f"Car {car_id} is not available - has overlapping bookings")
                return False, "Car is booked for overlapping dates"
            
            logger.info(f"Car {car_id} is available for the requested dates")
            return True, None
        except Exception as e:
            logger.error(f"Error checking availability: {e}")
            return False, "Error checking availability"
    
    def create_booking(self, booking_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create new booking"""
        try:
            response = self.supabase.table('bookings').insert(booking_data).execute()
            if not response.data:
                raise Exception(f"Failed to create booking: {response.error}")
            
            return response.data[0]
        except Exception as e:
            logger.error(f"Error creating booking: {e}")
            raise
    
    def get_bookings_filtered(self, filters: Dict[str, Any], limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        """Get bookings with filtering and pagination"""
        try:
            query = self.get_admin_client().table('bookings').select('*, cars(brand, model, year, class)')
            
            # Apply filters
            if filters.get('status'):
                query = query.eq('status', filters['status'])
            
            if filters.get('car_id'):
                query = query.eq('car_id', filters['car_id'])
            
            if filters.get('start_date'):
                query = query.gte('start_date', filters['start_date'])
            
            if filters.get('end_date'):
                query = query.lte('end_date', filters['end_date'])
            
            # Apply pagination and ordering
            query = query.order('created_at', desc=True).limit(limit).offset(offset)
            
            response = query.execute()
            return response.data
        except Exception as e:
            logger.error(f"Error getting filtered bookings: {e}")
            raise
    
    def update_booking(self, booking_id: str, update_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update booking"""
        try:
            update_data['updated_at'] = datetime.now().isoformat()
            
            response = self.get_admin_client().table('bookings').update(update_data).eq('id', booking_id).execute()
            if not response.data:
                raise Exception("Failed to update booking")
            
            return response.data[0]
        except Exception as e:
            logger.error(f"Error updating booking {booking_id}: {e}")
            raise
    
    def soft_delete_booking(self, booking_id: str) -> Dict[str, Any]:
        """Soft delete booking by setting status to 'deleted'"""
        try:
            update_data = {
                'status': 'deleted',
                'updated_at': datetime.now().isoformat()
            }
            
            response = self.get_admin_client().table('bookings').update(update_data).eq('id', booking_id).execute()
            if not response.data:
                raise Exception("Failed to delete booking")
            
            return response.data[0]
        except Exception as e:
            logger.error(f"Error soft deleting booking {booking_id}: {e}")
            raise
    
    def get_booking_by_id(self, booking_id: int) -> Optional[Dict[str, Any]]:
        """Get booking by ID"""
        try:
            response = self.supabase.table('bookings').select('*, cars(brand, model, year, class)').eq('id', booking_id).execute()
            return response.data[0] if response.data else None
        except Exception as e:
            logger.error(f"Error getting booking {booking_id}: {e}")
            raise
    
    def get_booking_by_reference(self, booking_reference: str) -> Optional[Dict[str, Any]]:
        """Get booking by reference number"""
        try:
            response = self.supabase.table('bookings').select('*, cars(brand, model, year, class)').eq('booking_reference', booking_reference).execute()
            return response.data[0] if response.data else None
        except Exception as e:
            logger.error(f"Error getting booking {booking_reference}: {e}")
            raise
    
    def get_booking_statistics(self, filters: Dict[str, Any] = None) -> Dict[str, Any]:
        """Get booking statistics"""
        try:
            query = self.get_admin_client().table('bookings').select('status, total_price')
            
            if filters:
                if filters.get('start_date'):
                    query = query.gte('start_date', filters['start_date'])
                if filters.get('end_date'):
                    query = query.lte('end_date', filters['end_date'])
            
            response = query.execute()
            bookings = response.data
            
            # Calculate statistics
            total_bookings = len(bookings)
            pending_bookings = len([b for b in bookings if b['status'] == 'pending'])
            confirmed_bookings = len([b for b in bookings if b['status'] == 'confirmed'])
            cancelled_bookings = len([b for b in bookings if b['status'] == 'cancelled'])
            total_revenue = sum([float(b['total_price'] or 0) for b in bookings if b['status'] == 'confirmed'])
            
            return {
                'total': total_bookings,
                'pending': pending_bookings,
                'confirmed': confirmed_bookings,
                'cancelled': cancelled_bookings,
                'total_revenue': total_revenue
            }
        except Exception as e:
            logger.error(f"Error getting booking statistics: {e}")
            raise
    
    def get_car_statistics(self) -> Dict[str, Any]:
        """Get car statistics"""
        try:
            response = self.get_admin_client().table('cars').select('*').execute()
            cars = response.data
            
            total_cars = len(cars)
            active_cars = len([car for car in cars if car.get('is_active', True)])
            
            return {
                'total': total_cars,
                'active': active_cars,
                'inactive': total_cars - active_cars
            }
        except Exception as e:
            logger.error(f"Error getting car statistics: {e}")
            raise
