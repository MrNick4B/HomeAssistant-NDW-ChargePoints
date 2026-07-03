# NDW Charge Points for Home Assistant

<img src="assets/ndw.svg" alt="NDW logo" height="48">

A Home Assistant custom integration for the Dutch [NDW (Nationaal
Dataportaal Wegverkeer)](https://www.ndw.nu/) DAFNE
[`charge-point-data`](https://docs.ndw.nu/data-uitwisseling/interface-beschrijvingen/dafne-api/dafne_api_consumer_pull/)
API. Draw a bounding box, pick a charge point, and monitor its live
availability, connector info and location as native Home Assistant
entities.

## Features

- Search for charge points by drawing a bounding box (e.g. with
  [bboxfinder.com](https://bboxfinder.com/))
- One Home Assistant device per charge point, added and removed
  independently of any others
- Live available/total connectors, connector type and format, power
  rating, address, operator and GPS location
- Icons that match the actual connector (Type 2, CCS, CHAdeMO, Tesla, ...)
- English and Dutch translations
- Automatically respects the NDW API's rate limit and pagination

## Installation

### HACS (custom repository)

1. In HACS, add this repository as a custom repository (category:
   Integration).
2. Install "NDW Charge Points".
3. Restart Home Assistant.

### Manual

1. Copy `custom_components/ndw_charge_points` into your Home Assistant
   `config/custom_components/` folder.
2. Restart Home Assistant.

## Setup

1. Go to **Settings → Devices & services → Add integration** and search for
   **NDW Charge Points**.
2. Draw a bounding box around the area you want to search, e.g. with
   [bboxfinder.com](https://bboxfinder.com/). Copy the coordinates shown
   under **Box** (format `min_lon,min_lat,max_lon,max_lat`) into the form.
3. Home Assistant fetches every charge point inside that box and shows a
   list with address, operator and current availability. Pick the one you
   want to monitor and finish the flow. It's created as its own device.
4. To monitor another charge point (from the same or a different area),
   repeat the flow (**Add integration** again). To stop monitoring one,
   delete its device from **Settings → Devices & services**. This doesn't
   affect any other charge points you've added. Each device's **Configure**
   option only changes its polling interval.

## Entities

Each monitored charge point is its own device with the following
entities:

| Entity | Domain | Description |
| --- | --- | --- |
| Available connectors | `sensor` | Number of available connectors, with a full per-connector breakdown as attributes |
| Total connectors | `sensor` | Total number of connectors |
| Connector type | `sensor` | E.g. "Type 2 (IEC 62196, Mennekes)"; if a station has multiple, the most common one is shown, full list in the `types` attribute |
| Connector format | `sensor` | "Tethered cable" or "Socket (bring your own cable)" |
| Highest/Lowest power rating | `sensor` | Rated power range across connectors, in whole kW (see note below) |
| Power type | `sensor` | E.g. "AC (3-phase)" |
| Address | `sensor` | Street address |
| Operator | `sensor` | Operator name |
| Location | `device_tracker` | GPS coordinates, shown on the Home Assistant map |
| Last updated, Country, Station ID | `sensor` *(diagnostic)* | Freshness timestamp, ISO country code, and the raw NDW feature ID |

Icons dynamically match the actual connector where possible (e.g. a
CHAdeMO station shows the CHAdeMO icon), rather than a generic plug icon.

> **Notes on the data:**
> - Power ratings are each connector's rated maximum, not a live
>   measurement. A car often draws less (e.g. 11 kW on a 22 kW-rated
>   connector) because it's limited by its own onboard charger.
> - There's no "Open" sensor: the API's `open` field is `false` on
>   nearly every charge point we've seen, including active public ones,
>   so it isn't reliable enough to expose.
> - The Operator sensor falls back to the short NDW operator code (e.g.
>   `GFX`, `LMS`, `EFL`) when no friendlier name is available. Look these
>   up in the [Benelux ID-register](https://www.benelux-idro.eu/en/id-register).

## Translations

Available in English and Dutch, using Home Assistant's standard entity
translation format. Home Assistant picks the language automatically based
on each user's profile setting. To add another language, copy
[`translations/en.json`](custom_components/ndw_charge_points/translations/en.json)
to e.g. `translations/de.json` and translate the values; no code changes
needed.

## Notes

- Polls every 120 seconds by default (configurable per device, 30–3600
  seconds). Each device polls independently and fetches its whole
  original bounding box, then picks out just its own charge point.
- Every request this integration makes, across all devices and the setup
  flow, shares one rate limiter capped at ~8 requests/second, comfortably
  under the API's 10/s limit. This holds even when many charge points are
  configured, including right after Home Assistant starts.
- Data is provided by NDW. This integration is not affiliated with or
  endorsed by NDW; the NDW logo above is used for attribution only.

## NDW API limits

The DAFNE API enforces:

- **Max bounding box area: 1.0 square degree.** The setup flow rejects
  larger boxes before calling the API.
- **Max 1000 features per response.** If a bounding box returns 1000
  results, the selection screen warns that the list may be incomplete.
  Use a smaller box to be sure you see every station.
- **Max 10 requests/second.** Handled by the shared rate limiter
  described above.
- **Pagination.** Bounding boxes with many results are paginated
  (`cursor` query param, `Link: rel="next"` header). This integration
  follows those links automatically until it has every feature for the
  bbox, so a dense bounding box won't silently return an incomplete list.
