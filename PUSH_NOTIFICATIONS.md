# Admin phone notifications

The dashboard is installable as a phone app and uses Web Push for background
alerts. Push delivery requires HTTPS and VAPID credentials configured on the
application server. Keep the private key in the hosting provider's secret
settings; do not commit it to the project.

Configure these server environment variables:

- `PUSH_VAPID_PUBLIC_KEY`: the VAPID application-server public key.
- `PUSH_VAPID_PRIVATE_KEY`: the matching private PEM key, encoded private key,
  or a path to the private key file available to the app.
- `PUSH_VAPID_SUBJECT`: a contact URI, such as `mailto:admin@your-domain`.

`pywebpush` depends on `py-vapid`, which uses `cryptography` to generate the
VAPID P-256 key pair. On Windows, PowerShell may not find the `vapid` command
if Python's `Scripts` directory is not on `PATH`. Run the module instead from
a directory outside the project so the private key is not accidentally
committed:

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\vapid-keys" | Out-Null
Set-Location "$env:USERPROFILE\vapid-keys"
py -m py_vapid --gen --applicationServerKey
```

If `py` is not available, replace it with the Python executable for the
environment where the project requirements are installed, for example:

```powershell
python -m py_vapid --gen --applicationServerKey
```

The command creates `private_key.pem` and `public_key.pem`, and prints an
`Application Server Key` value. Set `PUSH_VAPID_PRIVATE_KEY` to the contents
of `private_key.pem`, and set `PUSH_VAPID_PUBLIC_KEY` to the printed
`Application Server Key` value (not the contents of `public_key.pem`). Set
`PUSH_VAPID_SUBJECT` to your contact URI. Keep the private key secret, and
keep the same key pair configured across deployments so existing device
subscriptions remain valid.

On each admin phone, open the HTTPS site, install it from the browser menu
(or use Safari's **Add to Home Screen** on supported iPhones), open the
installed app, sign in as an administrator, and tap **Enable phone alerts**.
Allow notifications when prompted. Each admin and each phone must enable
notifications separately. Staff accounts cannot register devices.

Alerts are sent for newly added leads, stock that newly reaches 5 units or less,
and sales reports run by staff. Low-stock alerts are sent when inventory crosses
the threshold; a stock item already at or below the threshold will not produce
another alert until it rises above 5 and crosses down again.
