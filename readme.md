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
| [AES Ohio Energy Usage](ohio-aes/) | Logs into AES Ohio and publishes publishes daily usage to Home Assistant's long-term statistics |
| [CenterPoint Energy Gas Usage](centerpoint-gas/) | Logs into  CenterPoint Energy and publishes each meter-read cycle's usage to Home Assistant's long-term statistics |
| [Litter Robot Proxy](litter-robot-proxy/) | Local MQTT proxy for Litter Robot 3 Connect devices |
| [Ooma Call Logs](ooma-call-logs/) | Logs into Ooma web site and grabs call logs |

## Requirements

- Home Assistant with Supervisor (HAOS or Supervised)
- Mosquitto broker app
- AdGuard Home or another local DNS server
