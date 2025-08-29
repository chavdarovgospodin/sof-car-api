# Sof Car API

Python Flask API backend for Sof Car rental service.

## Setup

1. **Install dependencies:**

   ```bash
   pip install -r requirements.txt
   ```

2. **Create .env file:**

   ```bash
   cp .env.example .env
   ```

   Then edit `.env` with your Supabase credentials:

   ```
   SUPABASE_URL=https://your-project.supabase.co
   SUPABASE_ANON_KEY=your_anon_key_here
   ```

3. **Run locally:**

   ```bash
   python app.py
   ```

   The API will be available at `http://localhost:5001`

## API Endpoints

- `GET /health` - Health check
- `GET /api/cars` - Get all cars
- `GET /api/cars/available?start_date=...&end_date=...` - Get available cars
- `POST /api/bookings` - Create a new booking

## Deployment

For cPanel deployment, the `passenger_wsgi.py` file is already configured.
