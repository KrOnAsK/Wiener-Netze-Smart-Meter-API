# Wiener Netze Smart Meter — Home Assistant Integration

A [HACS](https://hacs.xyz/) custom integration that brings your **Wiener Netze**
smart meter data into Home Assistant using the **official** Wiener Netze Smart
Meter API.

It is a thin Home Assistant layer on top of
[tschoerk's `wiener-netze-smart-meter-api`](https://github.com/tschoerk/Wiener-Netze-Smart-Meter-API)
package (installed automatically from PyPI), which talks to the official
endpoint instead of recreating the web login — so it does not break on
captchas, rate limiting, or website changes.

## Table of Contents

- [Getting your API credentials](#getting-your-api-credentials)
- [Installation](#installation)
- [Configuration](#configuration)
- [What you get](#what-you-get)
- [Cost tracking (dynamic tariff)](#cost-tracking-dynamic-tariff)
- [Services](#services)
- [Notes & limitations](#notes--limitations)
- [Credits & License](#credits--license)

## Getting your API credentials

You need three values from Wiener Stadtwerke / Wiener Netze: a **client ID**, a
**client secret**, and an **API key**. These steps mirror the
[upstream API project](https://github.com/tschoerk/Wiener-Netze-Smart-Meter-API#firststeps):

1. Create an account at the
   [Wiener Stadtwerke Developer Portal](https://api-portal.wienerstadtwerke.at/).
2. [Create an application](https://api-portal.wienerstadtwerke.at/portal/applications/create)
   for the **WN_SMART_METER_API**.
3. When the application is approved you will get an e-mail from the API Developer
   Portal. The **API key** is then found in the details of the newly created
   [application](https://api-portal.wienerstadtwerke.at/portal/applications).
4. Write an e-mail to the
   [Smart Meter Portal Support](mailto:support.sm-portal@wienit.at) to connect
   the application with your Smart Meter Portal user. It usually takes **1–2
   weeks** to get a response.
5. Afterwards the **client ID** and **client secret** can be found in the
   [settings](https://smartmeter-business.wienernetze.at/einstellungen) of the
   Smart Meter Business portal.

## Installation

1. In HACS, open the menu (⋮) → **Custom repositories**.
2. Add `https://github.com/KrOnAsK/Wiener-Netze-Smart-Meter-Integration` with category **Integration**.
3. Install **Wiener Netze Smart Meter**, then restart Home Assistant.

## Configuration

1. Go to **Settings → Devices & Services → Add Integration**, search for
   **Wiener Netze Smart Meter**.
2. Enter your **client ID**, **client secret**, and **API key**. They are stored
   encrypted by Home Assistant — no YAML editing required.

## What you get

- A **Latest daily energy** sensor per meter (`zaehlpunkt`) holding the most
  recent available daily consumption value (Wh). This is informational — see
  the `reading_date` attribute for the day it belongs to.
- **Hourly energy long-term statistics** per meter for the **Energy dashboard**.
  Quarter-hour API data is summed into hourly buckets and imported with correct
  historical timestamps. On first run the last `BACKFILL_DAYS` (default 30, see
  `const.py`) of history is backfilled; use the
  [import service](#services) to backfill everything.

Add the hourly statistic on the Energy dashboard under
**Settings → Dashboards → Energy → Add consumption**, picking
`wiener_netze_smart_meter:<meter>_hourly_energy`.

## Cost tracking (dynamic tariff)

If you have a dynamic tariff with an hourly price sensor (e.g. the
[EPEX Spot](https://github.com/mampfes/ha_epex_spot) integration's *total price*
sensor), the integration can compute accurate per-hour cost:

1. Open the integration's **Configure** dialog and select your price sensor.
2. A new statistic `wiener_netze_smart_meter:<meter>_hourly_cost` (in €) is
   produced: for each hour, `cost = energy_kWh × that hour's price`.
3. On the Energy dashboard, set **"Use an entity tracking the total costs"** to
   that statistic.

This matches each hour's energy to that same hour's price, which is more
accurate than Home Assistant's single current-price model. The price per hour is
read from the price sensor's hourly statistics (for backfilled history),
overlaid with its live forecast attribute for recent hours.

To verify it is working, go to **Developer Tools → Statistics** and search for
`hourly_cost`. The `wiener_netze_smart_meter:<meter>_hourly_cost` entry should
appear (unit €, no issue). Add it to a **Statistics Graph** card to see the
per-hour cost, and sanity-check a single hour: `cost ≈ hourly kWh × price/kWh`
for that hour. If it is missing, make sure the price sensor is selected in the
integration's **Configure** dialog and that it has price history covering the
hours you imported.

## Services

### `wiener_netze_smart_meter.import_all_history`

Fetches the full available measurement history (the API default is about the
last 3 years) for all meters and rebuilds the hourly energy (and cost)
statistics from scratch. Run it once to seed history; the regular 12-hour
updates keep it current afterwards. It makes many API requests and can take a
while.

## Notes & limitations

- The official API publishes measurements with a **1–2 day delay**, so the most
  recent values always lag by a day or two.
- Home Assistant long-term statistics are bucketed **hourly**, so sub-hour
  (15-minute) resolution is not preserved on the Energy dashboard.
- Cost backfill only reaches as far back as your price integration retained its
  hourly price statistics.

## Credits & License

- API wrapper and credential instructions by
  [tschoerk](https://github.com/tschoerk/Wiener-Netze-Smart-Meter-API).
- Distributed under the [MIT](https://spdx.org/licenses/MIT.html) license.
