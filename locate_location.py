import os
import time
import logging
import json
import yaml
import requests
import pandas as pd
from datetime import datetime
from typing import List, Dict, Optional
from geopy.geocoders import Nominatim

# Setup logger
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("GooglePlacesScraper")


class GooglePlacesScraper:
    BASE_URL_SEARCH = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
    BASE_URL_DETAILS = "https://maps.googleapis.com/maps/api/place/details/json"
    STATS_FILE = "request_stats.csv"

    def __init__(self, api_key: str, daily_limit: int = 150):
        self.api_key = api_key
        self.daily_limit = daily_limit
        self.request_count = self.load_request_count()

    def load_request_count(self) -> int:
        if os.path.exists(self.STATS_FILE):
            df = pd.read_csv(self.STATS_FILE)
            count = df['total_count'].iloc[-1]
            logger.info(f"Loaded previous request count: {count}")
            return int(count)
        return 0

    def save_request_stat(self, endpoint: str):
        cost = (self.request_count * 17) / 1000
        row = {
            "timestamp": datetime.utcnow().isoformat(),
            "endpoint": endpoint,
            "request_count": 1,
            "total_count": self.request_count,
            "estimated_cost_usd": round(cost, 4)
        }

        df_new = pd.DataFrame([row])
        if os.path.exists(self.STATS_FILE):
            df_existing = pd.read_csv(self.STATS_FILE)
            df_combined = pd.concat([df_existing, df_new], ignore_index=True)
        else:
            df_combined = df_new
        df_combined.to_csv(self.STATS_FILE, index=False)
        logger.info(f"Logged API usage: {row}")

    def _check_quota(self) -> bool:
        return self.request_count < self.daily_limit

    def _make_request(self, url: str, params: Dict) -> Optional[Dict]:
        if not self._check_quota():
            logger.warning("Daily request limit reached.")
            return None

        try:
            params['key'] = self.api_key
            response = requests.get(url, params=params, timeout=10)
            self.request_count += 1
            self.save_request_stat(url.split("/")[-1])

            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"Request failed: {response.status_code} - {response.text}")
                return None
        except requests.RequestException as e:
            logger.exception(f"Network error: {e}")
            return None

    def search_places(self, location: str, radius: int, type_: str = "school") -> List[Dict]:
        params = {"location": location, "radius": radius, "type": type_}
        all_results, next_page_token = [], None

        while True:
            if next_page_token:
                params["pagetoken"] = next_page_token
                time.sleep(2)

            data = self._make_request(self.BASE_URL_SEARCH, params)
            if not data:
                break

            all_results.extend(data.get("results", []))
            next_page_token = data.get("next_page_token")
            if not next_page_token:
                break
        return all_results

    def get_place_details(self, place_id: str) -> Optional[Dict]:
        params = {"place_id": place_id, "fields": "name,formatted_address,formatted_phone_number,website"}
        data = self._make_request(self.BASE_URL_DETAILS, params)
        return data.get("result") if data else None

    def export_data(self, data: List[Dict], filename: str, format_: str = "csv"):
        if format_ == "csv":
            df = pd.DataFrame(data)
            df.to_csv(filename, index=False)
        elif format_ == "json":
            with open(filename, "w") as f:
                json.dump(data, f, indent=2)
        elif format_ == "yaml":
            with open(filename, "w") as f:
                yaml.dump(data, f, allow_unicode=True)
        else:
            raise ValueError(f"Unsupported format: {format_}")
        logger.info(f"Exported {len(data)} records to {filename}")

    @staticmethod
    def resolve_location_input(user_input: str) -> Optional[Dict]:
        try:
            if ',' in user_input:
                lat, lon = map(str.strip, user_input.split(","))
                return {"latitude": lat, "longitude": lon, "display_name": f"{lat},{lon}"}
            else:
                geolocator = Nominatim(user_agent="lms_lead_scraper")
                location = geolocator.geocode(user_input)
                if location:
                    return {"latitude": location.latitude, "longitude": location.longitude, "display_name": location.address}
        except Exception as e:
            logger.exception(f"Failed to resolve location: {e}")
        return None

    def run(self, user_location_input: str, radius: int, export_format: str = "csv"):
        resolved = self.resolve_location_input(user_location_input)
        if not resolved:
            logger.error("Invalid location input.")
            return

        logger.info(f"Resolved Location: {resolved['display_name']}")
        location_coords = f"{resolved['latitude']},{resolved['longitude']}"
        places = self.search_places(location_coords, radius)

        detailed_places = []
        for place in places:
            place_id = place.get("place_id")
            if place_id:
                details = self.get_place_details(place_id)
                if details:
                    detailed_places.append(details)

        filename = f"leads.{export_format}"
        self.export_data(detailed_places, filename, format_=export_format)

        self.show_usage_summary()

    def show_usage_summary(self):
        cost_nearby = (self.request_count * 17) / 1000
        cost_details = (self.request_count * 20) / 1000
        logger.info(f"Total API Requests Used: {self.request_count}")
        logger.info(f"Estimated Nearby Search Cost: ${cost_nearby:.2f}")
        logger.info(f"Estimated Place Details Cost: ${cost_details:.2f}")


if __name__ == "__main__":
    api_key = input("Enter your Google Places API Key: ")
    location_input = input("Enter location name or 'latitude,longitude': ")
    radius = int(input("Enter radius in meters: "))
    format_ = input("Export format (csv/json/yaml): ").strip().lower()
    scraper = GooglePlacesScraper(api_key)
    scraper.run(location_input, radius, export_format=format_)
