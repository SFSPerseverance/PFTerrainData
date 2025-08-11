#!/usr/bin/env python3
"""
Sentinel-2 Cloudless API to Colors Converter
Converts satellite imagery to color data for the entire world
Output: JSON format for Roblox integration
"""

import requests
import json
import numpy as np
from PIL import Image
import io
import time
import os
from datetime import datetime
import base64

class Sentinel2ColorConverter:
    def __init__(self, client_id, client_secret):
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token = None
        self.base_url = "https://sh.dataspace.copernicus.eu"
        self.process_url = f"{base_url}/api/v1/process"
        
    def authenticate(self):
        """Authenticate with Copernicus Data Space"""
        auth_url = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
        
        data = {
            'grant_type': 'client_credentials',
            'client_id': self.client_id,
            'client_secret': self.client_secret
        }
        
        response = requests.post(auth_url, data=data)
        if response.status_code == 200:
            self.access_token = response.json()['access_token']
            print("Authentication successful")
        else:
            raise Exception(f"Authentication failed: {response.text}")
    
    def create_evalscript(self):
        """Create evalscript for Sentinel-2 true color with cloudless"""
        return """
        //VERSION=3
        function setup() {
            return {
                input: [{
                    bands: ["B02", "B03", "B04", "CLM"],
                    units: "DN"
                }],
                output: {
                    bands: 3,
                    sampleType: "AUTO"
                }
            };
        }
        
        function evaluatePixel(sample) {
            // True color RGB
            let r = sample.B04 / 10000;
            let g = sample.B03 / 10000;
            let b = sample.B02 / 10000;
            
            // Apply gamma correction for better visual appearance
            r = Math.pow(r * 3.5, 0.8);
            g = Math.pow(g * 3.5, 0.8);
            b = Math.pow(b * 3.5, 0.8);
            
            // Clamp values
            r = Math.min(1, Math.max(0, r));
            g = Math.min(1, Math.max(0, g));
            b = Math.min(1, Math.max(0, b));
            
            return [r, g, b];
        }
        """
    
    def get_world_tiles(self, resolution=5000):
        """Generate world coordinate tiles for processing"""
        tiles = []
        
        # World bounds in Web Mercator (EPSG:3857)
        world_bounds = {
            'west': -20037508.34,
            'east': 20037508.34,
            'south': -20037508.34,
            'north': 20037508.34
        }
        
        # Calculate tile size based on resolution
        tile_width = tile_height = resolution * 1000  # Convert km to meters
        
        # Generate tiles
        x = world_bounds['west']
        tile_id = 0
        
        while x < world_bounds['east']:
            y = world_bounds['south']
            while y < world_bounds['north']:
                # Skip polar regions where there's no land/limited data
                if abs(y) > 15000000:  # Roughly 85 degrees latitude
                    y += tile_height
                    continue
                    
                tiles.append({
                    'id': tile_id,
                    'bounds': {
                        'west': x,
                        'east': min(x + tile_width, world_bounds['east']),
                        'south': y,
                        'north': min(y + tile_height, world_bounds['north'])
                    }
                })
                tile_id += 1
                y += tile_height
            x += tile_width
        
        return tiles
    
    def create_request_payload(self, bounds, width=512, height=512):
        """Create request payload for Sentinel Hub Process API"""
        return {
            "input": {
                "bounds": {
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[
                            [bounds['west'], bounds['south']],
                            [bounds['east'], bounds['south']],
                            [bounds['east'], bounds['north']],
                            [bounds['west'], bounds['north']],
                            [bounds['west'], bounds['south']]
                        ]]
                    },
                    "properties": {
                        "crs": "http://www.opengis.net/def/crs/EPSG/0/3857"
                    }
                },
                "data": [{
                    "type": "sentinel-2-l2a",
                    "dataFilter": {
                        "timeRange": {
                            "from": "2023-01-01T00:00:00Z",
                            "to": "2023-12-31T23:59:59Z"
                        },
                        "maxCloudCoverage": 10
                    },
                    "processing": {
                        "atmosphericCorrection": "SURFACE_REFLECTANCE"
                    }
                }]
            },
            "output": {
                "width": width,
                "height": height,
                "responses": [{
                    "identifier": "default",
                    "format": {
                        "type": "image/png"
                    }
                }]
            },
            "evalscript": self.create_evalscript()
        }
    
    def fetch_tile_image(self, tile_bounds, retries=3):
        """Fetch satellite image for a tile"""
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json'
        }
        
        payload = self.create_request_payload(tile_bounds)
        
        for attempt in range(retries):
            try:
                response = requests.post(self.process_url, 
                                       headers=headers, 
                                       json=payload,
                                       timeout=60)
                
                if response.status_code == 200:
                    return Image.open(io.BytesIO(response.content))
                elif response.status_code == 429:  # Rate limit
                    wait_time = 2 ** attempt
                    print(f"Rate limited, waiting {wait_time} seconds...")
                    time.sleep(wait_time)
                else:
                    print(f"Error fetching tile: {response.status_code} - {response.text}")
                    
            except Exception as e:
                print(f"Request failed (attempt {attempt + 1}): {e}")
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
        
        return None
    
    def image_to_dominant_color(self, image, grid_size=4):
        """Convert image to dominant colors in a grid"""
        if image is None:
            return None
            
        # Convert to RGB if needed
        if image.mode != 'RGB':
            image = image.convert('RGB')
        
        # Resize to manageable size for color analysis
        image = image.resize((grid_size * 16, grid_size * 16), Image.Resampling.LANCZOS)
        img_array = np.array(image)
        
        colors = []
        cell_height = img_array.shape[0] // grid_size
        cell_width = img_array.shape[1] // grid_size
        
        for i in range(grid_size):
            row = []
            for j in range(grid_size):
                # Extract cell
                y_start = i * cell_height
                y_end = (i + 1) * cell_height
                x_start = j * cell_width
                x_end = (j + 1) * cell_width
                
                cell = img_array[y_start:y_end, x_start:x_end]
                
                # Calculate mean color
                mean_color = np.mean(cell.reshape(-1, 3), axis=0)
                
                # Convert to Roblox Color3 format (0-1 range)
                roblox_color = [
                    round(mean_color[0] / 255, 3),
                    round(mean_color[1] / 255, 3),
                    round(mean_color[2] / 255, 3)
                ]
                row.append(roblox_color)
            colors.append(row)
        
        return colors
    
    def process_world_colors(self, output_file="world_colors.json", resolution=5000):
        """Process the entire world and generate color data"""
        if not self.access_token:
            self.authenticate()
        
        world_data = {
            "metadata": {
                "generated": datetime.now().isoformat(),
                "resolution_km": resolution,
                "format": "roblox_color3",
                "coordinate_system": "EPSG:3857",
                "description": "World satellite imagery colors from Sentinel-2 cloudless"
            },
            "tiles": []
        }
        
        tiles = self.get_world_tiles(resolution)
        print(f"Processing {len(tiles)} tiles...")
        
        for i, tile in enumerate(tiles):
            print(f"Processing tile {i + 1}/{len(tiles)} (ID: {tile['id']})")
            
            # Fetch satellite image
            image = self.fetch_tile_image(tile['bounds'])
            
            # Convert to colors
            colors = self.image_to_dominant_color(image)
            
            tile_data = {
                "id": tile['id'],
                "bounds": tile['bounds'],
                "colors": colors,
                "has_data": colors is not None
            }
            
            world_data["tiles"].append(tile_data)
            
            # Save progress every 10 tiles
            if (i + 1) % 10 == 0:
                with open(f"temp_{output_file}", 'w') as f:
                    json.dump(world_data, f, indent=2)
                print(f"Progress saved: {i + 1}/{len(tiles)} tiles completed")
            
            # Rate limiting
            time.sleep(1)
        
        # Final save
        with open(output_file, 'w') as f:
            json.dump(world_data, f, indent=2)
        
        print(f"World color data saved to {output_file}")
        print(f"Total tiles processed: {len(tiles)}")
        
        # Clean up temp file
        if os.path.exists(f"temp_{output_file}"):
            os.remove(f"temp_{output_file}")
        
        return world_data

def main():
    """Main execution function"""
    # Set your Copernicus Data Space credentials
    # Get these from: https://dataspace.copernicus.eu/
    CLIENT_ID = "your_client_id_here"
    CLIENT_SECRET = "your_client_secret_here"
    
    if CLIENT_ID == "your_client_id_here":
        print("ERROR: Please set your Copernicus Data Space credentials!")
        print("1. Register at https://dataspace.copernicus.eu/")
        print("2. Create OAuth client credentials")
        print("3. Replace CLIENT_ID and CLIENT_SECRET in this script")
        return
    
    # Initialize converter
    converter = Sentinel2ColorConverter(CLIENT_ID, CLIENT_SECRET)
    
    try:
        # Process world colors
        # Lower resolution for faster processing, increase for more detail
        world_data = converter.process_world_colors(
            output_file="world_satellite_colors.json",
            resolution=1000  # 1000km tiles (adjust as needed)
        )
        
        print("Conversion completed successfully!")
        print("Upload the JSON file to GitHub and access it from Roblox using:")
        print("https://raw.githubusercontent.com/your-username/your-repo/main/world_satellite_colors.json")
        
    except Exception as e:
        print(f"Error during processing: {e}")

if __name__ == "__main__":
    main()
