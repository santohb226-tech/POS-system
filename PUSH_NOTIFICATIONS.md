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

The `pywebpush` dependency supplies the `vapid --gen` command for creating a
key pair. Keep the same key pair configured across deployments so existing
device subscriptions remain valid.

On each admin phone, open the HTTPS site, install it from the browser menu
(or use Safari's **Add to Home Screen** on supported iPhones), open the
installed app, sign in as an administrator, and tap **Enable phone alerts**.
Allow notifications when prompted. Each admin and each phone must enable
notifications separately. Staff accounts cannot register devices.

Alerts are sent for newly added leads, stock that newly reaches 5 units or less,
and sales reports run by staff. Low-stock alerts are sent when inventory crosses
the threshold; a stock item already at or below the threshold will not produce
another alert until it rises above 5 and crosses down again.
