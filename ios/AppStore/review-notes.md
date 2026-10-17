# Notes for App Review

*(Paste into App Store Connect → App Review Information → Notes.)*

MinePower is the companion app for MinePower, an open-source (MIT) energy
manager that people run on their own hardware at home (Docker on a NAS,
Unraid or a mini PC). The app connects only to the user's own server on the
home network or via the user's VPN/HTTPS. There is no MinePower cloud service
and no account with us.

**How to review without a server**

1. Launch the app. On the first screen ("Mit MinePower verbinden" / "Connect
   to MinePower"), tap **"Demo ansehen" / "Try the Demo"**.
2. The app opens a built-in demo system with simulated solar, battery, car and
   hot water. No sign-in, no network and no hardware are needed. All sections
   (Overview, Car, Hot water, Battery, Tariff, Statistics, Diagnostics,
   Settings, Changelog, Wall display) work with simulated values, including
   commands such as "Start Boost" or "Fast charge", which only change the
   simulation.
3. To leave the demo: Settings → Account & Server → End Demo.

No demo account or credentials are required.

**Permissions**

- *Local Network*: used only to find and reach the user's own MinePower
  server on the home network (Bonjour service `_minepower._tcp` and the local
  subnet). Shown when the user opens the connect screen.
- *Face ID*: optional app lock, off by default (Settings → Account & Server).

**Other**

- No in-app purchases, no ads, no tracking, no data collection.
- Widgets, a Live Activity (boost / fast charging) and App Shortcuts use the
  same data as the app; the demo system feeds them as well.
- Encryption: only the system's HTTPS/TLS (`ITSAppUsesNonExemptEncryption` =
  NO).
- Source code: https://github.com/Marwinrentz/MinePower-Controller

**Contact**

*(Name, phone and email of the person App Review can reach – to be filled in
by the account holder.)*
