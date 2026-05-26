const express = require("express");
const fs = require("fs");
const dns = require("dns");
const MazdaAuthClient = require("./MazdaAuthClient");

dns.setServers(["8.8.8.8", "1.1.1.1"]);
dns.setDefaultResultOrder("ipv4first");

const app = express();
app.use(express.json());

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------
function loadConfig() {
  try {
    const raw = fs.readFileSync("/data/options.json", "utf8");
    return JSON.parse(raw);
  } catch (e) {
    console.error("Failed to read /data/options.json:", e.message);
    process.exit(1);
  }
}

const config = loadConfig();
const { email, password, region, poll_interval_minutes } = config;
const POLL_MS = (poll_interval_minutes || 5) * 60 * 1000;

console.log(`Starting Mazda API server for ${email} (region: ${region})`);

// ---------------------------------------------------------------------------
// Auth client + Mazda API base URLs
// ---------------------------------------------------------------------------
const authClient = new MazdaAuthClient(email, password);

const BASE_URL  = "https://0cxo7m58.mazda.com/prod";
const HGS_URL   = "https://hgs2ivna.mazda.com";

const APP_CODE = {
  MNAO: "202007270941270111799",
  MME:  "202008100250281064816",
  MJO:  "202009170613074283422",
}[region] || "202007270941270111799";

// ---------------------------------------------------------------------------
// Mazda API helpers
// ---------------------------------------------------------------------------
const https = require("https");
const crypto = require("crypto");

function encryptPayload(payload) {
  const IV  = Buffer.from("0102030405060708", "utf8");
  const KEY_SOURCE = "C383D8C4D279B78130AD52DC71D95CAA";
  const key = Buffer.from(KEY_SOURCE, "hex");
  const cipher = crypto.createCipheriv("aes-128-cbc", key.slice(0, 16), IV);
  let encrypted = cipher.update(JSON.stringify(payload), "utf8", "base64");
  encrypted += cipher.final("base64");
  return encrypted;
}

async function mazdaApiRequest(method, endpoint, body = null) {
  const accessToken = await authClient.getAccessToken();
  const timestamp   = new Date().toISOString().replace(/[-:.TZ]/g, "").slice(0, 14);

  const headers = {
    "Authorization": `Bearer ${accessToken}`,
    "Content-Type":  "application/json; charset=utf-8",
    "User-Agent":    "MyMazda-Android/9.1.0",
    "appCode":       APP_CODE,
    "app-code":      APP_CODE,
    "appOs":         "Android",
    "appVersion":    "9.1.0",
    "region":        region,
    "locale":        "en-US",
    "timestamp":     timestamp,
    "Accept":        "application/json, text/plain, */*",
    "Accept-Encoding": "gzip",
    "Connection":    "keep-alive",
  };

  return new Promise((resolve, reject) => {
    const reqUrl  = new URL(`${BASE_URL}${endpoint}`);
    const options = {
      hostname: reqUrl.hostname,
      path:     reqUrl.pathname + reqUrl.search,
      method,
      headers,
    };

    const req = https.request(options, (res) => {
      const chunks = [];
      res.on("data", c => chunks.push(c));
      res.on("end", () => {
        try {
          const raw  = Buffer.concat(chunks).toString("utf8");
          const json = JSON.parse(raw);
          if (json.resultCode && json.resultCode !== "200S") {
            reject(new Error(`Mazda API error: ${json.resultCode} — ${JSON.stringify(json)}`));
          } else {
            resolve(json);
          }
        } catch (e) {
          reject(new Error(`Failed to parse Mazda API response: ${e.message}`));
        }
      });
    });

    req.on("error", reject);
    if (body) req.write(JSON.stringify(body));
    req.end();
  });
}

// ---------------------------------------------------------------------------
// Cached state
// ---------------------------------------------------------------------------
let cachedVehicles = [];
let cachedStatus   = {};
let lastUpdated    = null;
let updateInProgress = false;

async function getMazdaClient() {
  const MazdaClient = require("./node-mymazda").default;
  const client = new MazdaClient(email, password, region);

  client.controller.connection.login = async () => {
    const tokens = await authClient.getAccessToken();
    client.controller.connection.accessToken = tokens;
    client.controller.connection.accessTokenExpirationTs = 
      Math.floor(Date.now() / 1000) + 6600;
  };

  return client;
}

async function refreshData() {
  if (updateInProgress) return;
  updateInProgress = true;
  try {
    const client   = await getMazdaClient();
    const vehicles = await client.getVehicles();
    cachedVehicles = vehicles;

    for (const v of vehicles) {
      try {
        let status = await client.getVehicleStatus(v.id);
        
        // Ensure we fetch EV-specific data (battery, charging stats) for PHEVs
        if (v.isElectric) {
            try {
                const evStatus = await client.getEVVehicleStatus(v.id);
                status.chargeInfo = evStatus.chargeInfo;
                status.hvacInfo = evStatus.hvacInfo;
            } catch (evErr) {
                console.error(`Failed to get EV status for vehicle ${v.id}:`, evErr.message);
            }
        }
        
        cachedStatus[v.id] = status;
      } catch (err) {
        console.error(`Failed to get status for vehicle ${v.id}:`, err.message);
      }
    }

    lastUpdated = new Date().toISOString();
    console.log(`Data refreshed at ${lastUpdated} for ${vehicles.length} vehicle(s)`);
  } catch (err) {
    console.error("Failed to refresh data:", err.message);
  } finally {
    updateInProgress = false;
  }
}

refreshData();
setInterval(refreshData, POLL_MS);

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function resolveVehicle(req, res) {
  if (!cachedVehicles.length) {
    res.status(503).json({ error: "No vehicle data available yet. Try again shortly." });
    return null;
  }
  const id = req.query.id ? parseInt(req.query.id) : cachedVehicles[0].id;
  const vehicle = cachedVehicles.find(v => v.id === id);
  if (!vehicle) {
    res.status(404).json({ error: `Vehicle id ${id} not found` });
    return null;
  }
  return vehicle;
}

const km2mi = km => km != null ? Math.round(km * 0.621371) : null;

// ---------------------------------------------------------------------------
// Routes
// ---------------------------------------------------------------------------
app.get("/health", (req, res) => {
  res.json({ status: "ok", last_updated: lastUpdated, vehicle_count: cachedVehicles.length });
});

app.get("/vehicles", (req, res) => res.json(cachedVehicles));

app.get("/status", (req, res) => {
  const vehicle = resolveVehicle(req, res);
  if (!vehicle) return;
  const status = cachedStatus[vehicle.id];
  if (!status) return res.status(503).json({ error: "Status not yet available" });
  res.json({ vehicle_id: vehicle.id, nickname: vehicle.nickname, status });
});

app.get("/sensors", (req, res) => {
  const vehicle = resolveVehicle(req, res);
  if (!vehicle) return;
  const s = cachedStatus[vehicle.id];
  if (!s) return res.status(503).json({ error: "Status not yet available" });

  res.json({
    vehicle_id:           vehicle.id,
    nickname:             vehicle.nickname,
    vin:                  vehicle.vin,
    last_updated:         lastUpdated,
    fuel_level_percent:   s.fuelRemainingPercent ?? null,
    fuel_range_miles:     km2mi(s.fuelDistanceRemainingKm),
    fuel_range_km:        s.fuelDistanceRemainingKm ?? null,
    odometer_miles:       km2mi(s.odometerKm),
    odometer_km:          s.odometerKm ?? null,
    is_locked:            s.doorLocks ? (!s.doorLocks.driverDoorUnlocked && !s.doorLocks.passengerDoorUnlocked) : null,
    driver_door_open:     s.doors?.driverDoorOpen ?? null,
    passenger_door_open:  s.doors?.passengerDoorOpen ?? null,
    rear_left_door_open:  s.doors?.rearLeftDoorOpen ?? null,
    rear_right_door_open: s.doors?.rearRightDoorOpen ?? null,
    trunk_open:           s.doors?.trunkOpen ?? null,
    hood_open:            s.doors?.hoodOpen ?? null,
    tire_fl_psi:          s.tirePressure?.frontLeftTirePressurePsi ?? null,
    tire_fr_psi:          s.tirePressure?.frontRightTirePressurePsi ?? null,
    tire_rl_psi:          s.tirePressure?.rearLeftTirePressurePsi ?? null,
    tire_rr_psi:          s.tirePressure?.rearRightTirePressurePsi ?? null,
    latitude:             s.latitude ?? null,
    longitude:            s.longitude ?? null,
    // EV / PHEV stats
    battery_level_percent: s.chargeInfo?.batteryLevelPercentage ?? null,
    battery_range_miles:   km2mi(s.chargeInfo?.drivingRangeKm),
    battery_range_km:      s.chargeInfo?.drivingRangeKm ?? null,
    plugged_in:            s.chargeInfo?.pluggedIn ?? null,
    charging:              s.chargeInfo?.charging ?? null,
  });
});

app.get("/refresh", async (req, res) => {
  await refreshData();
  res.json({ status: "refreshed", last_updated: lastUpdated });
});

app.post("/lock", async (req, res) => {
  const vehicle = resolveVehicle(req, res);
  if (!vehicle) return;
  try {
    const client = await getMazdaClient();
    await client.lockDoors(vehicle.id);
    await refreshData();
    res.json({ success: true, action: "lock", vehicle_id: vehicle.id });
  } catch (err) {
    console.error("Lock failed:", err.message);
    res.status(500).json({ error: err.message });
  }
});

app.post("/unlock", async (req, res) => {
  const vehicle = resolveVehicle(req, res);
  if (!vehicle) return;
  try {
    const client = await getMazdaClient();
    await client.unlockDoors(vehicle.id);
    await refreshData();
    res.json({ success: true, action: "unlock", vehicle_id: vehicle.id });
  } catch (err) {
    console.error("Unlock failed:", err.message);
    res.status(500).json({ error: err.message });
  }
});

// ---------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------
const PORT = 3001;
app.listen(PORT, "0.0.0.0", () => {
  console.log(`Mazda API server listening on port ${PORT}`);
  console.log(`Polling every ${poll_interval_minutes} minute(s)`);
});