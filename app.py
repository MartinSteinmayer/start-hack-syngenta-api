from flask import Flask, request, send_file, jsonify
import ee
import matplotlib.pyplot as plt
import numpy as np
import urllib.request
from PIL import Image
from io import BytesIO
import os
import math
import tempfile
import json
from flask_cors import CORS
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})


def initialize_earth_engine():
    """Initialize Earth Engine with service account credentials from environment variables."""
    try:
        # Get credentials from environment variables
        service_account = os.getenv("EE_SERVICE_ACCOUNT")

        # Two approaches depending on your preference:

        # APPROACH 1: Create temporary JSON credentials file
        credentials_dict = {
            "type": "service_account",
            "project_id": os.getenv("EE_PROJECT_ID"),
            "private_key_id": os.getenv("EE_PRIVATE_KEY_ID"),
            "private_key": os.getenv("EE_PRIVATE_KEY"),
            "client_email": os.getenv("EE_CLIENT_EMAIL"),
            "client_id": os.getenv("EE_CLIENT_ID"),
            "auth_uri": os.getenv("EE_AUTH_URI"),
            "token_uri": os.getenv("EE_TOKEN_URI"),
            "auth_provider_x509_cert_url": os.getenv("EE_AUTH_PROVIDER_X509_CERT_URL"),
            "client_x509_cert_url": os.getenv("EE_CLIENT_X509_CERT_URL"),
            "universe_domain": os.getenv("EE_UNIVERSE_DOMAIN")
        }

        # Create a temporary file for credentials
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as temp_creds_file:
            json.dump(credentials_dict, temp_creds_file)
            temp_creds_path = temp_creds_file.name

        # Initialize Earth Engine with the temporary credentials file
        credentials = ee.ServiceAccountCredentials(service_account, temp_creds_path)
        ee.Initialize(credentials)

        # Clean up the temporary file
        os.unlink(temp_creds_path)

        print("Earth Engine successfully initialized")
        return True
    except Exception as e:
        print(f"Error initializing Earth Engine: {e}")
        return False


def hectares_to_buffer_km(hectares, safety_margin=1.1):
    """
    Convert hectares to a buffer distance in kilometers.
    
    Args:
        hectares (float): Area in hectares
        safety_margin (float): Safety margin multiplier (default: 1.1 for 10% extra)
        
    Returns:
        float: Buffer distance in kilometers
    """
    # 1 hectare = 0.01 square kilometers
    # Area = π * r²
    # Solve for r: r = sqrt(Area / π)
    area_sq_km = hectares * 0.01
    radius_km = math.sqrt(area_sq_km / math.pi)

    # Apply safety margin
    return radius_km * safety_margin


def get_satellite_image(latitude, longitude, buffer_km=1.8, start_date='2023-01-01', end_date='2025-03-20'):
    """
    Retrieve a satellite image centered on the given coordinates.
    
    Args:
        latitude (float): Center latitude in decimal degrees
        longitude (float): Center longitude in decimal degrees
        buffer_km (float): Buffer distance in kilometers
        start_date (str): Start date for image collection (YYYY-MM-DD)
        end_date (str): End date for image collection (YYYY-MM-DD)
        
    Returns:
        PIL.Image: The satellite image
    """
    # Create a point and buffer it
    point = ee.Geometry.Point([longitude, latitude])
    area_of_interest = point.buffer(buffer_km * 1000)  # Buffer in meters

    # Get Sentinel-2 imagery
    sentinel_collection = (ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED').filterBounds(area_of_interest).filterDate(
        start_date, end_date).filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 20)))

    # Sort and get the least cloudy image
    image = sentinel_collection.sort('CLOUDY_PIXEL_PERCENTAGE').first()

    # If no images found, try Landsat as a backup
    if image is None:
        print("No Sentinel-2 images found. Trying Landsat...")
        landsat_collection = (ee.ImageCollection('LANDSAT/LC08/C02/T1_L2').filterBounds(area_of_interest).filterDate(
            start_date, end_date))
        image = landsat_collection.sort('CLOUD_COVER').first()

        if image is None:
            raise Exception("No suitable satellite images found for the specified criteria.")

    # For Sentinel-2, use RGB visualization
    visualization_params = {
        'bands': ['B4', 'B3', 'B2'],  # RGB bands for natural color
        'min': 0,
        'max': 3000,
        'gamma': 1.4
    }

    # Get the map URL
    map_id = image.visualize(**visualization_params).getThumbURL({
        'region': area_of_interest,
        'dimensions': '1024',
        'format': 'png'
    })

    # Download and return the image
    response = urllib.request.urlopen(map_id)
    img_data = response.read()
    img = Image.open(BytesIO(img_data))

    return img


@app.route('/satellite', methods=['GET'])
def satellite_endpoint():
    try:
        # Get parameters from the request
        latitude = float(request.args.get('latitude', None))
        longitude = float(request.args.get('longitude', None))
        hectares = float(request.args.get('hectares', 100))  # Default to 100 hectares if not specified
        start_date = request.args.get('start_date', '2023-01-01')
        end_date = request.args.get('end_date', '2025-03-20')

        # Validate parameters
        if latitude is None or longitude is None:
            return jsonify({"error": "Latitude and longitude are required parameters"}), 400

        # Convert hectares to buffer distance with 10% safety margin
        buffer_km = hectares_to_buffer_km(hectares, safety_margin=1.1)

        # Get the satellite image
        img = get_satellite_image(latitude, longitude, buffer_km, start_date, end_date)

        # Create a temporary file to save the image
        temp_file = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
        img.save(temp_file.name)
        temp_file.close()

        # Return the image file
        return send_file(temp_file.name,
                         mimetype='image/png',
                         as_attachment=True,
                         download_name=f"satellite_{latitude}_{longitude}_{hectares}ha.png")

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "ok"}), 200


if __name__ == '__main__':
    # Initialize Earth Engine
    if initialize_earth_engine():
        # Run the Flask app
        host = os.getenv("FLASK_HOST", "0.0.0.0")
        port = int(os.getenv("FLASK_PORT", 5032))
        debug = os.getenv("FLASK_DEBUG", "1") == "1"

        # Run the Flask app
        app.run(host=host, port=port, debug=debug)
    else:
        print("Failed to initialize Earth Engine. Exiting.")


@app.route("", methods=['GET'])
def index():
    return "Hello, World!"
