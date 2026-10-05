"""Explicit, cached reverse lookup against a configured Nominatim-compatible service."""
import asyncio
from collections import OrderedDict
import ipaddress
import math
import time
from urllib.parse import urlparse

import httpx


def validate_endpoint(endpoint):
    parsed = urlparse(endpoint)
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Use a plain geocoder endpoint without credentials, query or fragment.")
    if parsed.hostname.lower().rstrip(".") == "nominatim.openstreetmap.org":
        raise ValueError("Use a local or approved provider; public Nominatim is not supported for tracking.")
    try:
        local = ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        local = parsed.hostname == "localhost"
    if parsed.scheme != "https" and not (parsed.scheme == "http" and local):
        raise ValueError("Geocoder must use HTTPS or local loopback HTTP.")
    return endpoint


class AddressLookup:
    def __init__(self, endpoint, transport=None):
        self.endpoint = validate_endpoint(endpoint) if endpoint else None
        self.transport = transport
        self.cache = OrderedDict()
        self.lock = asyncio.Lock()
        self.last_request = 0
        self.provider = "Configured geocoder"

    async def fetch_address(self, lat, lon):
        async with httpx.AsyncClient(timeout=8, follow_redirects=False, trust_env=False, transport=self.transport) as client:
            response = await client.get(self.endpoint, params={"lat": lat, "lon": lon,
                "format": "jsonv2", "addressdetails": 1, "layer": "address", "zoom": 18},
                headers={"User-Agent": "AetherLink-Scout/local-prototype"})
            response.raise_for_status()
            data = response.json()
        return data.get("display_name"), data.get("licence", "Address data from configured provider")

    async def reverse(self, lat, lon):
        if not self.endpoint:
            raise ValueError("Address lookup needs a configured local or approved geocoder.")
        if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError("Invalid coordinates.")
        key = (lat, lon)
        async with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
            delay = 1.1 - (time.monotonic() - self.last_request)
            if delay > 0:
                await asyncio.sleep(delay)
            self.last_request = time.monotonic()
            address, attribution = await self.fetch_address(lat, lon)
            result = {"address": str(address)[:1000] if address else None,
                      "attribution": str(attribution)[:500], "provider": self.provider,
                      "latitude": lat, "longitude": lon,
                      "note": "Nearest mapped address; not a verified operator residence or exact position."}
            self.cache[key] = result
            if len(self.cache) > 128:
                self.cache.popitem(last=False)
            return result


class GeoapifyLookup(AddressLookup):
    def __init__(self, api_key, transport=None):
        if not api_key:
            raise ValueError("Set AETHERLINK_SCOUT_GEOAPIFY_KEY privately in the launch environment.")
        super().__init__("https://api.geoapify.com/v1/geocode/reverse", transport)
        self.api_key = api_key
        self.provider = "Geoapify"

    async def fetch_address(self, lat, lon):
        async with httpx.AsyncClient(timeout=8, follow_redirects=False, trust_env=False, transport=self.transport) as client:
            response = await client.get(self.endpoint, params={"lat": lat, "lon": lon,
                "format": "json", "limit": 1, "apiKey": self.api_key})
            response.raise_for_status()
            data = response.json()
        results = data.get("results", [])
        return (results[0].get("formatted") if results else None), "Powered by Geoapify · © OpenStreetMap contributors"
