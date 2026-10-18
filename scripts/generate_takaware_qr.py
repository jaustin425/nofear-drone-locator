"""Local TAK Aware QR codes; optional passwords are entered privately, never as arguments."""
import argparse
import getpass
import hashlib
import html
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import warnings

import qrcode
from qrcode.image.pure import PyPNGImage

SERVER = 'tak.c5solutionsgroup.com'
PORT = 8089
LOCAL_DATA = Path(__file__).resolve().parent.parent / 'local-data'


class PrivateArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # Never echo arbitrary unknown arguments: someone might accidentally
        # supply a password. Credential values are never accepted as arguments.
        self.exit(2, 'Invalid arguments. Use --help; passwords must never be supplied as arguments.\n')


def payload(username, password=None):
    if (not username or username != username.strip() or len(username) > 128
            or ',' in username or any(ord(char) < 32 or ord(char) == 127 for char in username)):
        raise ValueError('Use an exact, nonempty login username without commas or control characters.')
    credentials = {'username': username}
    if password is not None:
        if not isinstance(password, str) or not password:
            raise ValueError('A nonempty enrollment password is required.')
        credentials['password'] = password
    encoded = json.dumps({
        'serverCredentials': {'connectionString': f'{SERVER}:{PORT}:ssl'},
        'userCredentials': credentials,
    }, ensure_ascii=False, separators=(',', ':'))
    # TAK Aware checks for four comma-separated parts before parsing JSON.
    # Escape commas inside string values so a password comma cannot make the
    # JSON appear to be a four-part CSV code. JSON restores the original value.
    return re.sub(r'"(?:\\.|[^"\\])*"', lambda match: match.group().replace(',', r'\u002c'), encoded)


def read_password(username):
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise ValueError('Private password entry requires an interactive terminal.')
    with warnings.catch_warnings():
        # Refuse getpass fallback rather than permit echoed input.
        warnings.simplefilter('error', getpass.GetPassWarning)
        password = getpass.getpass(f'TAK Server enrollment password for {username}: ')
        confirmation = getpass.getpass(f'Confirm enrollment password for {username}: ')
    if not password or password != confirmation:
        raise ValueError('Password entries did not match or were empty.')
    return password


def private_file(path, content):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as output:
        output.write(content)


def generate(usernames, password_provider=None):
    usernames = list(dict.fromkeys(usernames))
    encoded = []
    for username in usernames:
        payload(username)  # Validate before prompting or writing anything.
        password = password_provider(username) if password_provider else None
        code = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=4)
        code.add_data(payload(username, password))
        code.make(fit=True)
        encoded.append((username, code))
    private = password_provider is not None
    LOCAL_DATA.mkdir(exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix='tak-aware-onboarding-', dir=LOCAL_DATA))
    cards = []
    for username, code in encoded:
        slug = re.sub(r'[^A-Za-z0-9_-]', '-', username)[:48] or 'user'
        suffix = hashlib.sha256(username.encode()).hexdigest()[:8]
        filename = f'{slug}-{suffix}.png'
        fd = os.open(folder / filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as output:
            code.make_image(image_factory=PyPNGImage).save(output)
        instructions = ('PRIVATE: this QR contains recoverable enrollment credentials. Share only with this user.'
                        if private else 'Server and username only. Enter your own password privately in TAK Aware.')
        card = (f'<section><h2>{html.escape(username)}</h2><img src="{filename}" '
                f'alt="TAK Aware setup QR for {html.escape(username, quote=True)}">'
                f'<p>{instructions}</p></section>')
        cards.append(card)
        if private:
            private_file(folder / filename.replace('.png', '.html'), page(card, private=True))
    if not private:
        private_file(folder / 'index.html', page(''.join(cards)))
    return folder


def page(cards, private=False):
    instructions = ('In TAK Aware, choose Scan QR code and scan only your own private code. '
                    'The QR supplies your enrollment credentials and may start enrollment automatically.'
                    if private else 'In TAK Aware, choose Scan QR code. Scan your own code, enter your TAK Server account password, then Start Enrollment.')
    notice = ('Keep this page and its QR private. Anyone with the image can recover the password. '
              'Do not share a combined team sheet or upload it to GitHub.' if private else 'These codes contain no password or certificate.')
    return '''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AetherLink Scout · TAK Aware team onboarding</title>
<style>body{font:16px system-ui,sans-serif;margin:24px;color:#111;background:white}
main{display:flex;flex-wrap:wrap;gap:24px}section{border:1px solid #aaa;padding:16px;width:360px;break-inside:avoid}
img{width:320px;max-width:100%;height:auto;image-rendering:pixelated}h2{overflow-wrap:anywhere}
@media print{body{margin:0}section{width:320px}img{width:280px}}</style>
<h1>AetherLink Scout · TAK Aware onboarding</h1>
<p>''' + instructions + '''</p>
<p>Host: tak.c5solutionsgroup.com · TLS stream: 8089 · Cert Enroll Port: 8446 · Secure API Port: 8443.</p>
<p>''' + notice + ''' They do not create accounts or fix enrollment errors; each account must already be authorized to enroll.</p>
<main>''' + cards + '</main></html>\n'


def main():
    parser = PrivateArgumentParser(description='Local TAK Aware QR images; no server or network actions. Default codes contain no passwords.')
    parser.add_argument('--username', action='append', required=True, help='Exact TAK Server login name; repeat for each person.')
    parser.add_argument('--include-passwords', action='store_true', help='Prompt privately in an interactive terminal; create separate credential-bearing QR pages, never a combined sheet.')
    args = parser.parse_args()
    try:
        folder = generate(args.username, read_password if args.include_passwords else None)
    except (EOFError, KeyboardInterrupt, getpass.GetPassWarning, ValueError):
        parser.exit(2, 'No codes generated: invalid username, noninteractive input, cancelled entry, empty password or confirmation mismatch.\n')
    except Exception:
        parser.exit(2, 'QR generation failed; no credential values printed. Check the local environment and output access.\n')
    destination = folder if args.include_passwords else folder / 'index.html'
    kind = 'private credential QR images with separate pages' if args.include_passwords else 'password-free QR images with an offline printable page'
    print(f'Created {len(set(args.username))} {kind}: {destination}')


if __name__ == '__main__':
    main()
