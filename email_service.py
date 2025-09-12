"""
Email service module for SofCar Flask API
Handles all email operations using EmailJS
"""

import logging
import requests
from datetime import datetime
from config import Config

logger = logging.getLogger(__name__)


class EmailService:
    """Service class for all email operations"""
    
    def __init__(self):
        """Initialize email service with EmailJS configuration"""
        self.service_id = Config.EMAILJS_SERVICE_ID
        self.public_key = Config.EMAILJS_PUBLIC_KEY
        self.private_key = Config.EMAILJS_PRIVATE_KEY
        self.contact_template_id = Config.EMAILJS_CONTACT_TEMPLATE_ID
        self.booking_template_id = Config.EMAILJS_BOOKING_TEMPLATE_ID
    
    def send_emailjs_email(self, service_id: str, template_id: str, template_params: dict, public_key: str, private_key: str = None) -> bool:
        """Send email using EmailJS API"""
        try:
            url = "https://api.emailjs.com/api/v1.0/email/send"
            
            data = {
                "service_id": service_id,
                "template_id": template_id,
                "user_id": public_key,
                "template_params": template_params
            }
            
            # Add access token for server-side API calls if available
            if private_key:
                data["accessToken"] = private_key
            
            response = requests.post(url, json=data, timeout=30)
            
            if response.status_code == 200:
                logger.info(f"Email sent successfully via EmailJS")
                return True
            else:
                logger.error(f"EmailJS API error: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Error sending email via EmailJS: {e}")
            return False
    
    def send_booking_confirmation_email(self, booking_data: dict, car_data: dict) -> bool:
        """Send booking confirmation email to client"""
        if not all([self.service_id, self.booking_template_id, self.public_key]):
            logger.warning("EmailJS not configured for booking confirmations")
            return False
        
        # Calculate rental days
        start_date = datetime.strptime(booking_data['start_date'], '%Y-%m-%d').date()
        end_date = datetime.strptime(booking_data['end_date'], '%Y-%m-%d').date()
        rental_days = (end_date - start_date).days
        
        # Calculate BGN values (assuming prices are stored in BGN)
        total_price_bgn = booking_data['total_price']
        deposit_amount_bgn = booking_data['deposit_amount']
        
        # Calculate EUR equivalents (approximate conversion rate 1.96)
        total_price_eur = round(total_price_bgn / 1.96, 2)
        deposit_amount_eur = round(deposit_amount_bgn / 1.96, 2)
        
        template_params = {
            "name": f"{booking_data['client_first_name']} {booking_data['client_last_name']}",
            "email": booking_data['client_email'],
            "phone": booking_data['client_phone'],
            "client_name": f"{booking_data['client_first_name']} {booking_data['client_last_name']}",
            "client_email": booking_data['client_email'],
            "booking_reference": f"SOF{booking_data['id'][:8].upper()}",  # Use booking ID as reference
            "car_brand": car_data['brand'],
            "car_model": car_data['model'],
            "car_year": car_data['year'],
            "start_date": booking_data['start_date'],
            "end_date": booking_data['end_date'],
            "rental_days": rental_days,
            "total_price": f"{total_price_bgn:.2f} лв / ≈{total_price_eur:.2f} €",
            "deposit_amount": f"{deposit_amount_bgn:.2f} лв / ≈{deposit_amount_eur:.2f} €",
            "payment_method": booking_data['payment_method']
        }
        
        return self.send_emailjs_email(
            self.service_id,
            self.booking_template_id,
            template_params,
            self.public_key,
            self.private_key
        )
    
    def send_admin_notification_email(self, booking_data: dict, car_data: dict) -> bool:
        """Send admin notification email for new booking using contact template"""
        if not all([self.service_id, self.contact_template_id, self.public_key]):
            logger.warning("EmailJS not configured for admin notifications")
            return False
        
        # Calculate rental days
        start_date = datetime.strptime(booking_data['start_date'], '%Y-%m-%d').date()
        end_date = datetime.strptime(booking_data['end_date'], '%Y-%m-%d').date()
        rental_days = (end_date - start_date).days
        
        # Format the message for admin notification
        admin_message = f"""🚗 НОВА РЕЗЕРВАЦИЯ!

Резервация #: SOF{booking_data['id'][:8].upper()}
ID: {booking_data['id']}

Автомобил: {car_data['brand']} {car_data['model']} ({car_data['year']})
Период: {booking_data['start_date']} - {booking_data['end_date']}
Дни: {rental_days}
Обща сума: {booking_data['total_price']} лв
Депозит: {booking_data['deposit_amount']} лв
Метод на плащане: {booking_data['payment_method']}

Моля, свържете се с клиента за потвърждение."""
        
        template_params = {
            "name": f"Booking System - {booking_data['client_first_name']} {booking_data['client_last_name']}",
            "email": booking_data['client_email'],
            "phone": booking_data['client_phone'],
            "message": admin_message
        }
        
        return self.send_emailjs_email(
            self.service_id,
            self.contact_template_id,  # Use contact template
            template_params,
            self.public_key,
            self.private_key
        )
    
    def send_contact_form_email(self, form_data: dict) -> bool:
        """Send contact form email"""
        if not all([self.service_id, self.contact_template_id, self.public_key]):
            logger.warning("EmailJS not configured for contact form")
            return False
        
        template_params = {
            "from_name": form_data['name'],
            "from_email": form_data['email'],
            "from_phone": form_data.get('phone', ''),
            "message": form_data['message'],
            "to_name": "SofCar Team"
        }
        
        return self.send_emailjs_email(
            self.service_id,
            self.contact_template_id,
            template_params,
            self.public_key,
            self.private_key
        )
