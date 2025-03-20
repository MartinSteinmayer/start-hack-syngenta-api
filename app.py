from flask import Flask, request, Response, jsonify
import ee
import urllib.request
import os
import math
import json
import logging
from flask_cors import CORS
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# Global flag to track initialization
ee_initialized = False


def initialize_earth_engine():
    """Initialize Earth Engine with service account credentials from environment variables."""
    global ee_initialized

    # Skip initialization if already done
    if ee_initialized:
        logger.info("Earth Engine already initialized")
        return True

    try:
        # Create a service account credentials dictionary from environment variables
        service_account_info = {
            "type": os.getenv("EE_TYPE"),
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

        # Initialize with service account credentials
        credentials = ee.ServiceAccountCredentials(service_account_info["client_email"],
                                                   key_data=json.dumps(service_account_info))
        ee.Initialize(credentials)

        ee_initialized = True
        logger.info("Earth Engine initialized successfully")
        return True
    except Exception as e:
        logger.error(f"Error initializing Earth Engine: {e}")
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
    area_sq_km = hectares * 0.01
    radius_km = math.sqrt(area_sq_km / math.pi)
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
        bytes: The satellite image as binary data
    """
    logger.info(f"Retrieving satellite image for coordinates: {latitude}, {longitude} with buffer: {buffer_km}km")

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
        logger.info("No Sentinel-2 images found. Trying Landsat...")
        landsat_collection = (ee.ImageCollection('LANDSAT/LC08/C02/T1_L2').filterBounds(area_of_interest).filterDate(
            start_date, end_date))
        image = landsat_collection.sort('CLOUD_COVER').first()

        if image is None:
            logger.error("No suitable satellite images found for the specified criteria.")
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

    logger.info(f"Generated image URL (truncated): {map_id[:50]}...")

    # Download and return the image
    response = urllib.request.urlopen(map_id)
    img_data = response.read()

    return img_data


@app.route('/satellite', methods=['GET'])
def satellite_endpoint():
    try:
        # Initialize Earth Engine (if not already done)
        if not initialize_earth_engine():
            return jsonify({"error": "Failed to initialize Earth Engine"}), 500

        # Get parameters from the request
        latitude = request.args.get('latitude')
        longitude = request.args.get('longitude')
        hectares = request.args.get('hectares', '100')  # Default to 100 hectares if not specified
        start_date = request.args.get('start_date', '2023-01-01')
        end_date = request.args.get('end_date', '2025-03-20')

        # Validate parameters
        if not latitude or not longitude:
            logger.error("Missing required parameters: latitude and longitude")
            return jsonify({"error": "Latitude and longitude are required parameters"}), 400

        try:
            latitude = float(latitude)
            longitude = float(longitude)
            hectares = float(hectares)
        except ValueError:
            logger.error("Invalid parameter types")
            return jsonify({"error": "Latitude, longitude, and hectares must be numeric values"}), 400

        logger.info(f"Processing request for satellite image: lat={latitude}, lon={longitude}, hectares={hectares}")

        # Convert hectares to buffer distance with 10% safety margin
        buffer_km = hectares_to_buffer_km(hectares, safety_margin=1.1)

        # Get the satellite image as binary data
        img_data = get_satellite_image(latitude, longitude, buffer_km, start_date, end_date)

        logger.info(f"Successfully generated satellite image ({len(img_data)} bytes)")

        # Return the image directly from memory
        return Response(
            img_data,
            mimetype='image/png',
            headers={'Content-Disposition': f'attachment; filename=satellite_{latitude}_{longitude}_{hectares}ha.png'})

    except Exception as e:
        logger.error(f"Error processing satellite request: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/health', methods=['GET'])
def health_check():
    logger.info("Health check requested")
    return jsonify({
        "status": "ok",
        "earth_engine_initialized": ee_initialized,
        "environment": os.getenv("FLASK_ENV", "production")
    }), 200


@app.route('/', methods=['GET'])
def index():
    return jsonify({
        "service": "Satellite Image API",
        "endpoints": {
            "/satellite": "Get satellite imagery (params: latitude, longitude, hectares, start_date, end_date)",
            "/health": "Health check"
        },
        "version": "1.0.0"
    }), 200


# For local development
if __name__ == '__main__':
    # Initialize Earth Engine
    initialize_earth_engine()

    # Run the Flask app with parameters from environment variables
    host = os.getenv("FLASK_HOST", "0.0.0.0")
    port = int(os.getenv("FLASK_PORT", 5032))
    debug = os.getenv("FLASK_DEBUG", "0") == "1"

    logger.info(f"Starting Flask app on {host}:{port}, debug={debug}")
    app.run(host=host, port=port, debug=debug)
