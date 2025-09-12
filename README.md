# SofCar API

Modular Python Flask REST API backend for SofCar car rental service.

## 🚀 Quick Start

1. **Install dependencies:**

   ```bash
   pip install -r requirements.txt
   ```

2. **Configure environment:**
   Create `.env` file with Supabase and EmailJS credentials:

   ```env
   SUPABASE_URL=https://your-project.supabase.co
   SUPABASE_ANON_KEY=your_anon_key_here
   SUPABASE_SERVICE_ROLE_KEY=your_service_role_key_here
   SECRET_KEY=your_secret_key_here
   ADMIN_USERNAME=admin
   ADMIN_PASSWORD=your_secure_password
   EMAILJS_SERVICE_ID=your_service_id
   EMAILJS_PUBLIC_KEY=your_public_key
   EMAILJS_PRIVATE_KEY=your_private_key
   ```

3. **Run:**
   ```bash
   python app.py
   ```
   API available at `http://localhost:5002/api`

## 📁 Modular Structure

```
/api/
├── passenger_wsgi.py          # cPanel entry point
├── app.py                     # Main Flask app (~800 lines)
├── config.py                  # Centralized configuration
├── database.py                # Supabase operations
├── validators.py              # Input validation
├── email_service.py           # EmailJS integration
├── auth.py                    # Admin authentication
└── utils.py                   # Helper functions
```

## 📚 API Endpoints

### Public Endpoints

- `GET /health` - Health check
- `GET /api/cars/all` - Get all cars (homepage)
- `GET /api/cars?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD` - Available cars
- `POST /api/bookings` - Create booking
- `POST /api/contact/inquiry` - Contact form

### Admin Endpoints (Authentication Required)

- `GET /api/admin/bookings` - All bookings with filtering
- `POST /api/admin/cars` - Create car
- `PUT /api/admin/cars/{id}` - Update car
- `DELETE /api/admin/cars/{id}` - Delete car
- `PUT /api/admin/bookings/{id}` - Update booking
- `PATCH /api/admin/bookings/{id}/delete` - Soft delete booking

## 🔒 Security Features

- **Rate Limiting:** 100 requests/hour per IP
- **Authentication:** Basic auth for admin endpoints
- **Input Validation:** Joi schemas for all inputs
- **CORS Protection:** Configured for specific origins
- **Honeypot:** Spam protection on contact forms
- **Security Headers:** X-Frame-Options, X-Content-Type-Options, X-XSS-Protection

## 📧 Email Integration

- **EmailJS:** Contact form and booking confirmations
- **Templates:** Client confirmation + admin notification
- **Templates:** Contact form notifications

## 🛠️ Features

- **Modular Architecture:** Clean separation of concerns
- **Database:** Supabase PostgreSQL with RLS
- **Validation:** 5-30 day rental period, email/phone validation
- **Booking Conflicts:** Automatic conflict detection
- **Soft Delete:** Bookings marked as deleted, cars become available
- **Logging:** Comprehensive logging for monitoring
- **Currency:** BGN storage with EUR display conversion

## 📊 Response Format

**Success:**

```json
{
  "cars": [...],
  "bookings": [...]
}
```

**Error:**

```json
{
  "error": "Error message",
  "details": "Additional details"
}
```

## 🚀 Deployment

**cPanel:**

1. Upload all files to cPanel
2. Install Python dependencies
3. Configure environment variables
4. `passenger_wsgi.py` remains the entry point
5. All modules import automatically

## 📝 Environment Variables

Required variables:

- `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`
- `SECRET_KEY`, `ADMIN_USERNAME`, `ADMIN_PASSWORD`
- `EMAILJS_SERVICE_ID`, `EMAILJS_PUBLIC_KEY`, `EMAILJS_PRIVATE_KEY`
- `RATE_LIMIT_MAX_REQUESTS`, `RATE_LIMIT_WINDOW_HOURS`

## 🔧 Validation Rules

**Bookings:**

- Minimum 5 days, maximum 30 days
- Start date from tomorrow onwards
- Maximum 3 months advance booking
- Valid email and phone required

**Cars:**

- Required fields: brand, model, year, class, fuel_type, transmission
- Valid enum values for class, fuel_type, transmission
- Price and deposit must be positive numbers

## 📞 Support

Check logs for error details, verify environment configuration, test with proper authentication.
