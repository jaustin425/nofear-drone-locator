# TAK Aware team onboarding

QR setup fills connection fields; it does not create users or repair rejected enrollment. The app's generic enrollment error can represent failed HTTP responses, TLS/network errors, or unexpected response data. Confirm one team account can enroll before distributing codes widely.

## Connection fields

Use the exact TAK Server login name, including capitalization. A display name or Device Profile is not proof that a login account was created with an enrollment password. The `.p12` package password protects that file; it is separate from the account password used for enrollment.

| TAK Aware field | This deployment's expected value |
| --- | --- |
| Host Name | `tak.c5solutionsgroup.com` (hostname only) |
| Port | `8089` |
| Cert Enroll Port | `8446` |
| Secure API Port | `8443` |

The ports above match TAK Aware's documented/source defaults and this project's CoT input. TCP ports 8089 and 8446 were reachable from the laptop on October 5, 2026; that does not establish phone reachability, valid account authentication or successful certificate issuance. The public 8443/8446 server certificate matched the hostname and was within its validity dates, but the laptop's default OpenSSL trust store rejected its private C5TAKTestCA chain. Do not bypass validation or change production configuration based on the generic app message.

## Local QR generation

Install the pinned development requirements in the project virtual environment if needed. The local encoder uses `qrcode` and PyPNG; no online QR service is used.

Default codes contain server settings and username only:

```sh
.venv/bin/python scripts/generate_takaware_qr.py --username YOUR_UPPERCASE_LOGIN
```

Repeat `--username` for additional people. An ignored private directory under `local-data/tak-aware-onboarding-*` contains PNG images and an offline printable `index.html`. In TAK Aware select Scan QR code, scan the matching code, enter the account password privately, and Start Enrollment. Preserve exact login capitalization.

For explicitly requested automatic enrollment, run this in your own interactive terminal:

```sh
.venv/bin/python scripts/generate_takaware_qr.py --include-passwords --username YOUR_UPPERCASE_LOGIN
```

The tool privately prompts for the account password and confirmation. Never supply passwords as command arguments, files, chat messages or environment variables. It refuses noninteractive input and echoed-input fallback. Password-bearing codes are saved as separate PNG/HTML pairs per user, without a combined team sheet. New output directories are private and files are created with mode 0600; existing permissions are not altered. Each code contains recoverable credentials, so distribute it only to its intended user and keep it out of Git, agent screenshots and uploads. The assistant does not need to receive the password or open the resulting private QR.

The payload follows TAK Aware's `serverCredentials.connectionString` and `userCredentials` JSON format. Without a password, the app asks for private entry; with nonempty username/password it can auto-submit enrollment. String commas are escaped because the app checks four-part CSV before parsing JSON. Account creation, group assignment and certificate signing remain server responsibilities.

TAK Aware's current source logs scanned QR text at debug level. If password-bearing QR codes are used, treat any resulting app debug logs as potentially containing credentials; do not send those logs to the assistant or upload them.

## Read-only enrollment check

Open `https://tak.c5solutionsgroup.com:8446/Marti/api/tls/config` in a browser that trusts the approved server CA. If prompted, enter one exact login name and its account password privately. Stop and report any certificate warning rather than bypass it. Report only the result/status, not credentials or XML content.

- A repeated login prompt or HTTP 401 is an authentication lead; verify account existence, exact login name and assigned enrollment password.
- HTTP 403 is an authorization lead; inspect the account's permissions using the existing admin UI.
- HTTP 500 is a server-side enrollment configuration/error lead; inspect sanitized server diagnostics read-only before proposing any change.
- XML configuration confirms that configuration retrieval succeeded in that browser; it does not verify the later certificate-signing request or CoT connection.

Current observation: the user confirmed Firefox accepted the account credentials at the enrollment config URL and displayed XML configuration. TAK Aware still reports its generic failure on the same phone that enrolled the original deleted account. Reported app version is 2.0.2 (build 342), with ports 8089/8446/8443. The hostname reported in chat was `tak.c5solutinsgroup.com`, missing the `o` in `solutions`. Confirm whether this spelling is also in the app; if so, correct it to `tak.c5solutionsgroup.com` before another enrollment attempt. A successful configuration request does not establish successful certificate signing. Do not reset the account password solely because of the generic app message.

Do not reset accounts, regenerate/revoke certificates, restart TAK, change TLS policy or modify ports/firewall/router as an automatic troubleshooting step. No production changes were made by this project.

## Sources and verification

- [Official TAK Aware QR parser](https://github.com/flighttactics/TAKAware/blob/main/TAKAware/Parsers/QRCodeParser.swift).
- [Official TAK Aware enrollment implementation](https://github.com/flighttactics/TAKAware/blob/main/TAKAware/Communications/CSRRequestor.swift).
- [Official TAK Aware connection fields](https://github.com/flighttactics/TAKAware/blob/main/TAKAware/Screens/SettingsScreens/ConnectionOptions.swift).
- [Official TAK Server enrollment API](https://github.com/TAK-Product-Center/Server/blob/main/src/takserver-core/takserver-war/src/main/java/com/bbn/tak/tls/CertManagerApi.java).

Local checks independently decode generated images with an existing optional ZBar library, verify exact case and special-character round trips, private file modes, separate credential pages, noninteractive rejection and absence of argument echo. Real phone enrollment remains unverified for the new team accounts. QR creation cannot resolve the current enrollment failure by itself.
