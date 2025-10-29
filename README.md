# gps-read

Python scripts for reading GNSS usb devices on linux based OSes.

## Installation
_Running `./install.sh` as a sudo capable regular user (not root) will automate the following actions._

1. Install pip and the virtual environment module from the package repository.
```
sudo apt update
sudo apt install -y python3-venv python3-pip
```

2. Create virtual environment and install required packages.
```
project_dir="$(dirname "$0")/.venv"
python3 -m venv "${project_dir}/.venv"
source "${project_dir}/.venv/bin/activate"
pip install --upgrade pip
pip install -r "${project_dir}/requirements.txt"
```

3. Add user to  dialout group.
_(You may need to re-login or run `newgrp dialout` to update group membership)_
```
sudo usermod -aG dialout "$USER"
```

## Usage
```
gps_read.py [-h] [--port PORT] [--baud BAUD] [--json] [--raw] [--log LOG] [--once]

Read and interpret NMEA from a USB GNSS on Linux.

options:
  -h, --help   show this help message and exit
  --port PORT
  --baud BAUD
  --json       emit compact JSON status lines once per second
  --raw        echo valid NMEA sentences
  --log LOG    path to append raw NMEA
  --once       exit after first valid fix summary
  --format     custom format string using %% formatting %(utc_time)s | lat: %(lat).6f
```

## Fields

### Time and Position
- `utc_time`: ISO-8601 UTC timestamp from the receiver plus date if available (Z suffix indicates UTC).
- `lat`: geodetic latitude in decimal degrees (positive is north, negative is south).
- `lon`: geodetic longitude in decimal degrees (positive is east, negative is west).
- `alt_m`: altitude in meters above mean sea level (MSL).

### Fix Quality and Status
- `fix_ok`: boolean indicating whether the receiver has a valid position fix (from RMC sentence, `true` = Active, `false` = Void).
- `fix_quality`: GGA fix quality indicator:
  - `0` = no fix / invalid
  - `1` = autonomous GNSS fix (GPS/GLONASS/Galileo/BeiDou)
  - `2` = DGPS (differential GPS with corrections)
  - `4` = RTK fixed (centimeter-level accuracy)
  - `5` = RTK float (decimeter-level accuracy)
  - `6` = estimated / dead reckoning
  - `7` = manual input mode
  - `8` = simulator mode
- `fix_type`: GSA fix dimension indicator:
  - `1` = no fix
  - `2` = 2D fix (latitude/longitude only)
  - `3` = 3D fix (latitude/longitude/altitude)
- `mode`: NMEA 2.3+ positioning mode indicator (from RMC/GNS/VTG):
  - `N` = no fix
  - `A` = autonomous
  - `D` = differential (DGPS)
  - `E` = estimated / dead reckoning
  - `R` = RTK fixed
  - `F` = RTK float
  - For GNS sentences, this can be a string of mode characters (one per constellation).

### Satellite Information
- `num_sats`: number of satellites actively used in the position solution (not just visible).
- `gsv`: object containing satellites in view per constellation (e.g., `{"GN": {"in_view": 24}, "GP": {"in_view": 12}}`).
  - Keys are NMEA talker IDs: `GN` = multi-GNSS, `GP` = GPS, `GA` = Galileo, `GB` = BeiDou, `GL` = GLONASS, `GQ` = QZSS.

### Dilution of Precision (DOP) - Accuracy Indicators
- `hdop`: horizontal dilution of precision (lower is better):
  - < 1 = excellent
  - 1–2 = good
  - 2–5 = moderate
  - 5–10 = fair
  - \> 10 = poor
- `vdop`: vertical dilution of precision (same scale as HDOP, applies to altitude accuracy).
- `pdop`: position dilution of precision (3D combination of HDOP and VDOP).

### Motion
- `speed_mps`: ground speed in meters per second.
- `speed_kmh`: ground speed in kilometers per hour.
- `course_deg`: course over ground (true heading) in degrees (0–360°, where 0° = north, 90° = east).

### Differential Corrections
- `geoid_sep_m`: geoid separation in meters (difference between WGS-84 ellipsoid and mean sea level at your location).
- `age_corrections_s`: age of differential corrections in seconds (only present when using DGPS/SBAS).
- `dgps_id`: differential reference station ID (only present when DGPS is active).

### Error Estimates (from GST sentence)
- `rms_range_err_m`: RMS (root mean square) of pseudorange residuals in meters.
- `sd_lat_m`: standard deviation of latitude error in meters.
- `sd_lon_m`: standard deviation of longitude error in meters.
- `sd_alt_m`: standard deviation of altitude error in meters.

### Metadata
- `last_update`: local monotonic timestamp (from `time.time()`) when this record was emitted by the script.


## Examples
### Simple format with UTC time
`python gps_read.py --once --format "utc_time: %(utc_time)s"`
### Multiple fields with custom formatting
`python gps_read.py --once --format "UTC: %(utc_time)s | Lat: %(lat).6f | Lon: %(lon).6f | Alt: %(alt_m).1f m"`
### Speed and course
`python gps_read.py --once --format "Speed: %(speed_kmh).1f km/h | Course: %(course_deg).1f°"`
### All important fields
`python gps_read.py --once --format "%(utc_time)s | %(lat).6f,%(lon).6f | Alt: %(alt_m)sm | Sats: %(num_sats)d | HDOP: %(hdop).1f"`
### Custom JSON format
`python gps_read.py --once --format '{"timestamp":"%(utc_time)s","latitude":%(lat).6f,"longitude":%(lon).6f,"altitude":%(alt_m).1f,"satellites":%(num_sats)d,"hdop":%(hdop).2f}'`
