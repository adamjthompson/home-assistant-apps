const express = require("express");
const MazdaClient = require("node-mymazda");
const fs = require("fs");

const app = express();
app.use(express.json());

// ---------------------------------------------------------------------------
// Config — read from HA add-on options file
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
// Mazda client + cached state
// ---------------------------------------------------------------------------
let client = null;
let cachedVehicles = [];   // [{id, nickname, vin, ...}]
let cachedStatus = {};     // { [vehicleId]: statusObj }
let lastUpdated = null;
let updateInProgress = false;

async function getClient() {
  if (!client) {
    client = new MazdaClient(email, password, region);
  }
  return client;
}

async function refreshData() {
  if (updateInProgress) return;
  updateInProgress = true;
  try {
    const c = await getClient();
    const vehicles = await c.getVehicles();
    cachedVehicles = vehicles;

    for (const v of vehicles) {
      try {
        const status = await c.getVehicleStatus(v.id);
        cachedStatus[v.id] = status;
      } catch (err) {
        console.error(`Failed to get status for vehicle ${v.id}:`, err.message);
      }
    }

    lastUpdated = new Date().toISOString();
    console.log(`Data refreshed at ${lastUpdated} for ${vehicles.length} vehicle(s)`);
  } catch (err) {
    console.error("Failed to refresh data:", err.message);
    // Reset client so it re-authenticates next time
    client = null;
  } finally {
    updateInProgress = false;
  }
}

// Initial fetch + polling
refreshData();
setInterval(refreshData, POLL_MS);

// ---------------------------------------------------------------------------
// Helper — find a vehicle or return 404
// ---------------------------------------------------------------------------
function resolveVehicle(req, res) {
  const vehicles = cachedVehicles;
  if (!vehicles.length) {
    res.status(503).json({ error: "No vehicle data available yet. Try again shortly." });
    return null;
  }
  // Support ?id=... or default to first vehicle
  const id = req.query.id ? parseInt(req.query.id) : vehicles[0].id;
  const vehicle = vehicles.find((v) => v.id === id);
  if (!vehicle) {
    res.status(404).json({ error: `Vehicle id ${id} not found` });
    return null;
  }
  return vehicle;
}

// ---------------------------------------------------------------------------
// Routes — status & sensors
// ---------------------------------------------------------------------------

// GET /health
app.get("/health", (req, res) => {
  res.json({
    status: "ok",
    last_updated: lastUpdated,
    vehicle_count: cachedVehicles.length,
  });
});

// GET /vehicles  — list all vehicles
app.get("/vehicles", (req, res) => {
  res.json(cachedVehicles);
});

// GET /status?id=<vehicleId>  — full raw status for one vehicle
app.get("/status", (req, res) => {
  const vehicle = resolveVehicle(req, res);
  if (!vehicle) return;
  const status = cachedStatus[vehicle.id];
  if (!status) {
    return res.status(503).json({ error: "Status not yet available" });
  }
  res.json({ vehicle_id: vehicle.id, nickname: vehicle.nickname, status });
});

// GET /sensors?id=<vehicleId>  — curated sensor payload for HA
app.get("/sensors", (req, res) => {
  const vehicle = resolveVehicle(req, res);
  if (!vehicle) return;
  const s = cachedStatus[vehicle.id];
  if (!s) {
    return res.status(503).json({ error: "Status not yet available" });
  }

  // node-mymazda status shape (may vary slightly by model year):
  //   s.fuelRemainingPercent, s.fuelDistanceRemainingKm
  //   s.odometerKm
  //   s.doors.driverDoorOpen, passengerDoorOpen, rearLeftDoorOpen, rearRightDoorOpen
  //   s.doors.trunkOpen, hoodOpen
  //   s.isLocked
  //   s.tirePressure.frontLeftTirePressurePsi, frontRightTirePressurePsi,
  //                  rearLeftTirePressurePsi,  rearRightTirePressurePsi
  //   s.latitude, s.longitude

  const km2mi = (km) => (km != null ? Math.round(km * 0.621371) : null);

  res.json({
    vehicle_id: vehicle.id,
    nickname: vehicle.nickname,
    vin: vehicle.vin,
    last_updated: lastUpdated,

    // Fuel
    fuel_level_percent: s.fuelRemainingPercent ?? null,
    fuel_range_miles: km2mi(s.fuelDistanceRemainingKm),
    fuel_range_km: s.fuelDistanceRemainingKm ?? null,

    // Odometer
    odometer_miles: km2mi(s.odometerKm),
    odometer_km: s.odometerKm ?? null,

    // Doors & locks
    is_locked: s.isLocked ?? null,
    driver_door_open: s.doors?.driverDoorOpen ?? null,
    passenger_door_open: s.doors?.passengerDoorOpen ?? null,
    rear_left_door_open: s.doors?.rearLeftDoorOpen ?? null,
    rear_right_door_open: s.doors?.rearRightDoorOpen ?? null,
    trunk_open: s.doors?.trunkOpen ?? null,
    hood_open: s.doors?.hoodOpen ?? null,

    // Tire pressures (PSI)
    tire_fl_psi: s.tirePressure?.frontLeftTirePressurePsi ?? null,
    tire_fr_psi: s.tirePressure?.frontRightTirePressurePsi ?? null,
    tire_rl_psi: s.tirePressure?.rearLeftTirePressurePsi ?? null,
    tire_rr_psi: s.tirePressure?.rearRightTirePressurePsi ?? null,

    // Location
    latitude: s.latitude ?? null,
    longitude: s.longitude ?? null,
  });
});

// GET /refresh  — force an immediate data refresh
app.get("/refresh", async (req, res) => {
  await refreshData();
  res.json({ status: "refreshed", last_updated: lastUpdated });
});

// ---------------------------------------------------------------------------
// Routes — commands
// ---------------------------------------------------------------------------

// POST /lock?id=<vehicleId>
app.post("/lock", async (req, res) => {
  const vehicle = resolveVehicle(req, res);
  if (!vehicle) return;
  try {
    const c = await getClient();
    await c.lockDoors(vehicle.id);
    await refreshData();
    res.json({ success: true, action: "lock", vehicle_id: vehicle.id });
  } catch (err) {
    console.error("Lock failed:", err.message);
    res.status(500).json({ error: err.message });
  }
});

// POST /unlock?id=<vehicleId>
app.post("/unlock", async (req, res) => {
  const vehicle = resolveVehicle(req, res);
  if (!vehicle) return;
  try {
    const c = await getClient();
    await c.unlockDoors(vehicle.id);
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
