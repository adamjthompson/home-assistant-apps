# Mazda Connected Services — Home Assistant Add-on

A local Node.js API server that wraps node-mymazda and exposes your Mazda's
data as REST endpoints for Home Assistant to consume. Bypasses the TLS
fingerprinting issue that affects pymazda on HAOS.

## File Structure

```
mazda-connected/
├── config.yaml            # HA add-on manifest
├── build.yaml             # Docker base image config
├── Dockerfile             # Container build instructions
├── package.json           # Node.js dependencies
├── server.js              # Express API server
├── ha_configuration.yaml  # Paste into your HA config
└── node-mymazda/          # Vendored local copy (NOT from npm — no longer available)
    ├── package.json
    ├── index.js
    └── ...
```

> **Important:** node-mymazda is no longer available on the npm registry due to
> the Mazda DMCA situation. Your local copy of node-mymazda must be placed in
> the `mazda-connected/node-mymazda/` folder before committing to GitHub.

## Installation

### Step 1 — Place your node-mymazda copy in the repo

Copy your local node-mymazda folder into `mazda_connected/node-mymazda/`.
The final structure should look like the tree above.

### Step 2 — Host the add-on folder in a Git repository

HA requires custom add-ons to be in a Git repo (GitHub public or private works).

1. Create a repo on GitHub (e.g. `ha-addons`)
2. Inside it, create a folder called `mazda_connected`
3. Copy all files from this add-on into that folder, including `node-mymazda/`
4. Commit and push

### Step 3 — Add the repository to Home Assistant

1. In HA, go to **Settings → Add-ons → Add-on Store**
2. Click the **⋮ menu** (top right) → **Repositories**
3. Paste your repo URL (e.g. `https://github.com/yourname/ha-addons`)
4. Click **Add**

### Step 4 — Install the add-on

1. Scroll down in the Add-on Store — "Mazda Connected Services" should appear
2. Click it → **Install**
3. Go to the **Configuration** tab and fill in:
   - `email`: your MyMazda account email
   - `password`: your MyMazda account password
   - `region`: `MNAO` (North America)
   - `poll_interval_minutes`: 5 (or your preference)
4. Go to the **Info** tab → **Start**
5. Check the **Log** tab — you should see:
   ```
   Starting Mazda API server for you@email.com (region: MNAO)
   Mazda API server listening on port 3000
   Data refreshed at <timestamp> for 1 vehicle(s)
   ```

### Step 5 — Get your vehicle ID

Open a browser or curl:
```
http://<your-ha-ip>:3000/vehicles
```
Note the `id` field — you'll need it if you have multiple vehicles.

Test the sensor endpoint:
```
http://<your-ha-ip>:3000/sensors
```

### Step 6 — Configure Home Assistant

Copy the contents of `ha_configuration.yaml` into your `configuration.yaml`
(or use `!include` files). Then:

```
Developer Tools → Check Configuration → Restart HA
```

Your Mazda entities will appear under **Settings → Devices & Services → Entities**.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Server health + last update time |
| GET | `/vehicles` | List all vehicles on your account |
| GET | `/status` | Full raw status object |
| GET | `/sensors` | Curated sensor payload for HA |
| GET | `/refresh` | Force immediate data refresh |
| POST | `/lock` | Lock doors |
| POST | `/unlock` | Unlock doors |

All endpoints accept an optional `?id=<vehicleId>` query param.
Without it, the first vehicle on your account is used.

## Dashboard Card Example

```yaml
type: entities
title: Mazda
entities:
  - entity: sensor.mazda_fuel_level
  - entity: sensor.mazda_fuel_range
  - entity: sensor.mazda_odometer
  - entity: binary_sensor.mazda_locked
  - entity: binary_sensor.mazda_trunk
  - entity: sensor.mazda_tire_fl
  - entity: sensor.mazda_tire_fr
  - entity: sensor.mazda_tire_rl
  - entity: sensor.mazda_tire_rr
  - entity: sensor.mazda_last_updated
```

## Map Card for Location

Use a Picture Entity card or a Map card pointed at the lat/lon sensors.
Build a template sensor with a Google Maps static URL using the lat/lon values.

## Troubleshooting

- **"No vehicle data available yet"** — wait 30 seconds after starting, then retry
- **Auth errors in logs** — double-check email/password in add-on config; re-save and restart
- **Port 3000 conflict** — change the port in `config.yaml` and update all HA URLs accordingly
- **Null sensor values** — check `/status` raw output to see the actual field names your car returns; some model years use slightly different keys
- **Build fails with module not found** — make sure `node-mymazda/` folder is present inside `mazda_connected/` before pushing to GitHub
