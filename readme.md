# Home Assistant App Repository

Custom Home Assistant repository for my custom apps.

## Installation

1. In Home Assistant, go to **Settings → Apps → Install App**
2. Click the **⋮** menu → **Repositories**
3. Add this repository URL: https://github.com/adamjthompson/home-assistant-apps
4. Find the desired app in the store and click **Install**

## Apps included

| App | Description |
|--------|-------------|
| [Litter Robot Proxy](litter-robot-proxy/) | Local MQTT proxy for Litter Robot 3 Connect devices |
| [AES Ohio Energy Usage](ohio-aes/) | Logs into AES Ohio and publishes daily electricity usage to MQTT |

## Requirements

- Home Assistant with Supervisor (HAOS or Supervised)
- Mosquitto broker app
- AdGuard Home or another local DNS server
