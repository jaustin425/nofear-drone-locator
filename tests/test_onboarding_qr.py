"""Local QR interoperability and privacy checks using synthetic credentials only."""
import ctypes
import ctypes.util
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import png

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / 'scripts/generate_takaware_qr.py'
spec = importlib.util.spec_from_file_location('onboarding', SCRIPT)
onboarding = importlib.util.module_from_spec(spec)
spec.loader.exec_module(onboarding)
EXAMPLE = 'EXAMPLE-ONLY,NOT-A-REAL-CREDENTIAL:"\\☃'


def decode(path):
    library = ctypes.util.find_library('zbar')
    if not library:
        raise unittest.SkipTest('Optional local ZBar decoder unavailable')
    lib = ctypes.CDLL(library)
    pointer = ctypes.c_void_p
    signatures = {
        'zbar_image_scanner_create': ([], pointer),
        'zbar_image_scanner_destroy': ([pointer], None),
        'zbar_image_scanner_set_config': ([pointer, ctypes.c_int, ctypes.c_int, ctypes.c_int], ctypes.c_int),
        'zbar_image_create': ([], pointer), 'zbar_image_destroy': ([pointer], None),
        'zbar_image_set_format': ([pointer, ctypes.c_ulong], None),
        'zbar_image_set_size': ([pointer, ctypes.c_uint, ctypes.c_uint], None),
        'zbar_image_set_data': ([pointer, pointer, ctypes.c_ulong, pointer], None),
        'zbar_scan_image': ([pointer, pointer], ctypes.c_int),
        'zbar_image_first_symbol': ([pointer], pointer),
        'zbar_symbol_get_type': ([pointer], ctypes.c_int),
        'zbar_symbol_get_data': ([pointer], ctypes.c_char_p),
        'zbar_symbol_next': ([pointer], pointer),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(lib, name)
        function.argtypes, function.restype = arguments, result
    scanner, image = lib.zbar_image_scanner_create(), lib.zbar_image_create()
    try:
        lib.zbar_image_scanner_set_config(scanner, 0, 0, 0)
        lib.zbar_image_scanner_set_config(scanner, 64, 0, 1)
        with path.open('rb') as source:
            width, height, rows, _ = png.Reader(file=source).asRGBA8()
            grayscale = bytes(value for row in rows for value in row[::4])
        buffer = ctypes.create_string_buffer(grayscale)
        lib.zbar_image_set_format(image, int.from_bytes(b'Y800', 'little'))
        lib.zbar_image_set_size(image, width, height)
        lib.zbar_image_set_data(image, buffer, len(grayscale), None)
        if lib.zbar_scan_image(scanner, image) <= 0:
            raise AssertionError('Generated QR could not be independently decoded')
        symbol = lib.zbar_image_first_symbol(image)
        if not symbol or lib.zbar_symbol_get_type(symbol) != 64 or lib.zbar_symbol_next(symbol):
            raise AssertionError('Expected one QR symbol')
        return lib.zbar_symbol_get_data(symbol).decode('utf-8')
    finally:
        lib.zbar_image_destroy(image)
        lib.zbar_image_scanner_destroy(scanner)


class OnboardingTests(unittest.TestCase):
    def test_default_codes_keep_exact_case_and_contain_no_password(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(onboarding, 'LOCAL_DATA', Path(directory)):
            folder = onboarding.generate(['AETHERLINK-SCOUT-EXAMPLE', 'AETHERLINK-SCOUT-EXAMPLE'])
            images = list(folder.glob('*.png'))
            self.assertEqual(len(images), 1)
            value = json.loads(decode(images[0]))
            self.assertEqual(value['userCredentials'], {'username': 'AETHERLINK-SCOUT-EXAMPLE'})
            self.assertEqual(value['serverCredentials']['connectionString'], 'tak.c5solutionsgroup.com:8089:ssl')
            self.assertTrue((folder / 'index.html').is_file())

    def test_private_codes_round_trip_special_characters_and_have_separate_pages(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(onboarding, 'LOCAL_DATA', Path(directory)):
            folder = onboarding.generate(['AETHERLINK-SCOUT-TEST-A', 'AETHERLINK-SCOUT-TEST-B'], lambda _: EXAMPLE)
            self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
            self.assertFalse((folder / 'index.html').exists())
            self.assertEqual(len(list(folder.glob('*.html'))), 2)
            for image in folder.glob('*.png'):
                encoded = decode(image)
                # TAK Aware routes four comma-separated pieces to its CSV
                # parser first, even when the contents are otherwise JSON.
                self.assertNotEqual(len(encoded.split(',')), 4)
                value = json.loads(encoded)
                self.assertEqual(value['userCredentials']['password'], EXAMPLE)
                page = image.with_suffix('.html')
                text = page.read_text()
                self.assertEqual(text.count('<img '), 1)
                self.assertNotIn(EXAMPLE, text)
                self.assertIn('PRIVATE', text)
                self.assertEqual(image.stat().st_mode & 0o777, 0o600)
                self.assertEqual(page.stat().st_mode & 0o777, 0o600)

    def test_cli_refuses_noninteractive_passwords_and_never_echoes_unknown_values(self):
        result = subprocess.run([sys.executable, str(SCRIPT), '--username', 'AETHERLINK-SCOUT-EXAMPLE', '--include-passwords'],
                                stdin=subprocess.DEVNULL, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('noninteractive', result.stderr)
        result = subprocess.run([sys.executable, str(SCRIPT), '--username', 'AETHERLINK-SCOUT-EXAMPLE', '--password', EXAMPLE],
                                stdin=subprocess.DEVNULL, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(EXAMPLE, result.stdout + result.stderr)

    def test_private_entry_confirms_and_refuses_echo_fallback(self):
        with patch.object(sys.stdin, 'isatty', return_value=True), patch.object(sys.stderr, 'isatty', return_value=True):
            with patch.object(onboarding.getpass, 'getpass', side_effect=[EXAMPLE, EXAMPLE]):
                self.assertEqual(onboarding.read_password('AETHERLINK-SCOUT-EXAMPLE'), EXAMPLE)
            with patch.object(onboarding.getpass, 'getpass', side_effect=[EXAMPLE, 'EXAMPLE-MISMATCH']):
                with self.assertRaises(ValueError): onboarding.read_password('AETHERLINK-SCOUT-EXAMPLE')
            def fallback(_):
                import warnings
                warnings.warn('Synthetic echo fallback', onboarding.getpass.GetPassWarning)
            with patch.object(onboarding.getpass, 'getpass', side_effect=fallback):
                with self.assertRaises(onboarding.getpass.GetPassWarning): onboarding.read_password('AETHERLINK-SCOUT-EXAMPLE')
        with self.assertRaises(ValueError): onboarding.payload('INVALID,USERNAME')


if __name__ == '__main__':
    unittest.main()
