#!/usr/bin/python3
"""
Passenger WSGI configuration for cPanel deployment
"""
import sys
import os

# Add the current directory to the Python path
sys.path.insert(0, os.path.dirname(__file__))

# Import the Flask application
from app import app as application

# Ensure we're using the right Python version
if __name__ == '__main__':
    print(f"Python version: {sys.version}")
    print(f"Python executable: {sys.executable}")
    print(f"Current working directory: {os.getcwd()}")
    print(f"Python path: {sys.path[:3]}...")  # Show first 3 paths
    
    # Test import
    try:
        import flask
        print(f"Flask version: {flask.__version__}")
    except ImportError:
        print("Flask not installed!")
    
    try:
        import supabase
        print("Supabase client available")
    except ImportError:
        print("Supabase not installed!")
    
    # Test app creation
    try:
        print(f"Application: {application}")
        print("WSGI application ready!")
    except Exception as e:
        print(f"Error creating application: {e}")