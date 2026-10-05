import asyncio
import unittest
import httpx
from fastapi.testclient import TestClient
from aetherlink_scout.app import create_app
from aetherlink_scout.geocoding import AddressLookup, GeoapifyLookup, validate_endpoint
from aetherlink_scout.replay import demo_dataset


class GeocodingTests(unittest.TestCase):
    def test_endpoints_exclude_public_and_secret_urls(self):
        for url in ['https://nominatim.openstreetmap.org/reverse', 'http://example.com/reverse',
                    'https://user:password@example.com/reverse', 'https://example.com/reverse?key=test']:
            with self.assertRaises(ValueError):
                validate_endpoint(url)
        self.assertEqual(validate_endpoint('http://127.0.0.1:8088/reverse'), 'http://127.0.0.1:8088/reverse')

    def test_geoapify_format_cache_and_key_is_server_only(self):
        calls = []
        def handler(request):
            calls.append(request)
            self.assertEqual(request.url.params['format'], 'json')
            self.assertEqual(request.url.params['apiKey'], 'SYNTHETIC-TEST-KEY')
            return httpx.Response(200, json={'results': [{'formatted': 'Synthetic address'}]})
        lookup = GeoapifyLookup('SYNTHETIC-TEST-KEY', httpx.MockTransport(handler))
        async def check():
            first = await lookup.reverse(35.0, -115.0)
            second = await lookup.reverse(35.0, -115.0)
            self.assertEqual(first, second)
            self.assertEqual(first['address'], 'Synthetic address')
            self.assertNotIn('SYNTHETIC-TEST-KEY', str(first))
        asyncio.run(check())
        self.assertEqual(len(calls), 1)

    def test_disabled_invalid_origin_and_provider_error(self):
        app = create_app(demo_dataset())
        client = TestClient(app, base_url='http://127.0.0.2')
        point = {'latitude': 35, 'longitude': -115}
        self.assertEqual(client.post('/api/address', json=point).status_code, 503)
        self.assertEqual(client.post('/api/address', json={'latitude': 95, 'longitude': -115}).status_code, 422)
        self.assertEqual(client.post('/api/address', json=point, headers={'Origin': 'http://outside.example'}).status_code, 403)
        app.state.geocoder = GeoapifyLookup('SYNTHETIC-TEST-KEY', httpx.MockTransport(lambda r: httpx.Response(401)))
        response = client.post('/api/address', json=point)
        self.assertEqual(response.status_code, 502)
        self.assertNotIn('SYNTHETIC-TEST-KEY', response.text)
        self.assertNotIn('SYNTHETIC-TEST-KEY', client.get('/api/health').text)
        self.assertNotIn('SYNTHETIC-TEST-KEY', client.get('/api/replay').text)

    def test_local_compatible_geocoder_and_no_result(self):
        lookup = AddressLookup('http://127.0.0.1:8088/reverse', httpx.MockTransport(lambda r: httpx.Response(200, json={})))
        self.assertIsNone(asyncio.run(lookup.reverse(35, -115))['address'])


if __name__ == '__main__':
    unittest.main()
