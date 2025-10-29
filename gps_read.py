#!/usr/bin/env python3
import argparse, sys, os, re, time, json, math, datetime
import serial

NMEA_RE = re.compile(r'^\$([A-Z0-9]{2,3}[A-Z]{3}),(.*)\*([0-9A-F]{2})\r?\n?$')

def nmea_checksum(s):
    c = 0
    for ch in s:
        c ^= ord(ch)
    return f"{c:02X}"

def dm_to_deg(dm, hemi):
    # NMEA lat: ddmm.mmmm, lon: dddmm.mmmm
    if not dm or dm == '':
        return None
    try:
        val = float(dm)
    except ValueError:
        return None
    deg = int(val // 100)
    minutes = val - deg * 100
    dec = deg + minutes / 60.0
    if hemi in ('S', 'W'):
        dec = -dec
    return dec

def knots_to_mps(kn):
    try:
        return float(kn) * 0.514444
    except:
        return None

def safe_float(x):
    try:
        return float(x)
    except:
        return None

def parse_time_date(utc_hms, ddmmyy):
    # utc_hms like 123519.00, ddmmyy like 230394
    if not utc_hms:
        return None
    hh = int(utc_hms[0:2])
    mm = int(utc_hms[2:4])
    ss = int(float(utc_hms[4:]))  # handles fractional seconds
    if ddmmyy and len(ddmmyy) == 6:
        d = int(ddmmyy[0:2])
        mo = int(ddmmyy[2:4])
        y = 2000 + int(ddmmyy[4:6]) if int(ddmmyy[4:6]) < 80 else 1900 + int(ddmmyy[4:6])
        return datetime.datetime(y, mo, d, hh, mm, ss, tzinfo=datetime.timezone.utc)
    # If no date, use today with UTC
    now = datetime.datetime.now(datetime.timezone.utc)
    return now.replace(hour=hh, minute=mm, second=ss, microsecond=0)

def parse_sentence(talker_sen, fields):
    """Return updates dict extracted from a sentence."""
    tp = talker_sen[-3:]  # e.g., RMC, GGA...
    talker = talker_sen[:-3]  # GN, GP, GA, etc.
    u = {}
    if tp == 'RMC':
        # 0 time,1 status,2 lat,3 N/S,4 lon,5 E/W,6 sog,7 cog,8 date,9 mv,10 mvE/W, 11 mode (NMEA 2.3+)
        if len(fields) >= 11:
            t = parse_time_date(fields[0], fields[8])
            if t: u['utc_time'] = t.isoformat().replace('+00:00','Z')
            u['fix_ok'] = (fields[1] == 'A')
            lat = dm_to_deg(fields[2], fields[3]); lon = dm_to_deg(fields[4], fields[5])
            if lat is not None: u['lat'] = lat
            if lon is not None: u['lon'] = lon
            sog_mps = knots_to_mps(fields[6]) if fields[6] else None
            if sog_mps is not None:
                u['speed_mps'] = sog_mps
                u['speed_kmh'] = sog_mps * 3.6
            u['course_deg'] = safe_float(fields[7])
            # mode indicator may hint GNSS quality: N,A,D,E,R,F
            if len(fields) >= 12 and fields[11]:
                u['mode'] = fields[11]
    elif tp == 'GGA':
        # 0 time,1 lat,2 N/S,3 lon,4 E/W,5 fixq,6 numsats,7 hdop,8 alt(m),9 M,10 geoid,11 M,12 age,13 dgpsid
        if len(fields) >= 12:
            t = parse_time_date(fields[0], None)
            if t: u['utc_time'] = t.isoformat().replace('+00:00','Z')
            lat = dm_to_deg(fields[1], fields[2]); lon = dm_to_deg(fields[3], fields[4])
            if lat is not None: u['lat'] = lat
            if lon is not None: u['lon'] = lon
            u['fix_quality'] = int(fields[5]) if fields[5].isdigit() else None  # 0 no fix,1 GPS,2 DGPS,4 RTK fix,5 RTK float
            u['num_sats'] = int(fields[6]) if fields[6].isdigit() else None
            u['hdop'] = safe_float(fields[7])
            u['alt_m'] = safe_float(fields[8])
            u['geoid_sep_m'] = safe_float(fields[10]) if len(fields) > 10 else None
            u['age_corrections_s'] = safe_float(fields[12]) if len(fields) > 12 else None
            u['dgps_id'] = fields[13] if len(fields) > 13 else None
    elif tp == 'GNS':
        # 0 time,1 lat,2 N/S,3 lon,4 E/W,5 mode chars,6 numsats,7 hdop,8 alt,9 sep,10 age,11 stn
        if len(fields) >= 9:
            t = parse_time_date(fields[0], None)
            if t: u['utc_time'] = t.isoformat().replace('+00:00','Z')
            lat = dm_to_deg(fields[1], fields[2]); lon = dm_to_deg(fields[3], fields[4])
            if lat is not None: u['lat'] = lat
            if lon is not None: u['lon'] = lon
            u['mode'] = fields[5]
            u['num_sats'] = int(fields[6]) if fields[6].isdigit() else None
            u['hdop'] = safe_float(fields[7])
            u['alt_m'] = safe_float(fields[8])
            if len(fields) > 9: u['geoid_sep_m'] = safe_float(fields[9])
    elif tp == 'GSA':
        # 0 op mode,1 fix type,2-13 sv ids,14 pdop,15 hdop,16 vdop,17 sysid (v4.10)
        if len(fields) >= 17:
            u['fix_type'] = int(fields[1]) if fields[1].isdigit() else None  # 1 no fix,2 2D,3 3D
            u['pdop'] = safe_float(fields[14])
            u['hdop'] = safe_float(fields[15])
            u['vdop'] = safe_float(fields[16])
    elif tp == 'GSV':
        # satellites in view; we will tally per-talkers
        # 0 total_msgs,1 msg_num,2 total_sats, then per-sat: id, elev, az, snr
        if len(fields) >= 3:
            total_sats = int(fields[2]) if fields[2].isdigit() else None
            if total_sats is not None:
                u.setdefault('gsv', {})
                # Normalize talker GN/GP/GA/GQ etc
                u['gsv'][talker] = {'in_view': total_sats}
    elif tp == 'VTG':
        # 0 course true,1 T,2 course mag,3 M,4 spd knots,5 N,6 spd kmh,7 K, 8 mode
        if len(fields) >= 7:
            u['course_deg'] = safe_float(fields[0])
            if fields[4]:
                mps = knots_to_mps(fields[4])
                if mps is not None:
                    u['speed_mps'] = mps
                    u['speed_kmh'] = mps * 3.6
            elif fields[6]:
                kmh = safe_float(fields[6])
                if kmh is not None:
                    u['speed_kmh'] = kmh
                    u['speed_mps'] = kmh / 3.6
            if len(fields) >= 9 and fields[8]:
                u['mode'] = fields[8]
    elif tp == 'GST':
        # pseudorange noise stats: we surface rms and lat/lon/alt std dev if present
        # 0 time,1 rms,2-4 std lat lon alt,5-7 corr coef
        if len(fields) >= 5:
            if fields[0]:
                t = parse_time_date(fields[0], None)
                if t: u['utc_time'] = t.isoformat().replace('+00:00','Z')
            u['rms_range_err_m'] = safe_float(fields[1])
            u['sd_lat_m'] = safe_float(fields[2])
            u['sd_lon_m'] = safe_float(fields[3])
            u['sd_alt_m'] = safe_float(fields[4])
    return u

def merge_updates(state, updates):
    for k, v in updates.items():
        if k == 'gsv':
            # merge per-talker dict
            gsv = state.setdefault('gsv', {})
            for sys, obj in v.items():
                gsv[sys] = obj
        else:
            state[k] = v
    state['last_update'] = time.time()
    return state

def human_status(state):
    def fmt(v, nd=6):
        return f"{v:.{nd}f}" if isinstance(v, float) and not math.isnan(v) else str(v)
    parts = []
    t = state.get('utc_time')
    parts.append(f"UTC {t}" if t else "UTC ?")
    fxq = state.get('fix_quality')
    fxt = state.get('fix_type')
    fix_txt = None
    if fxq is not None:
        qmap = {0:'no-fix',1:'GPS',2:'DGPS',4:'RTK-fix',5:'RTK-float',6:'est'}
        fix_txt = qmap.get(fxq, str(fxq))
    elif fxt is not None:
        fix_txt = {1:'no-fix',2:'2D',3:'3D'}.get(fxt, str(fxt))
    if fix_txt:
        parts.append(f"fix {fix_txt}")
    if state.get('lat') is not None and state.get('lon') is not None:
        parts.append(f"lat {fmt(state['lat'],6)} lon {fmt(state['lon'],6)}")
    if state.get('alt_m') is not None:
        parts.append(f"alt {fmt(state['alt_m'],1)} m")
    if state.get('num_sats') is not None:
        parts.append(f"sats {state['num_sats']}")
    if state.get('hdop') is not None:
        parts.append(f"HDOP {fmt(state['hdop'],1)}")
    if state.get('pdop') is not None:
        parts.append(f"PDOP {fmt(state['pdop'],1)}")
    if state.get('vdop') is not None:
        parts.append(f"VDOP {fmt(state['vdop'],1)}")
    if state.get('speed_kmh') is not None:
        parts.append(f"spd {fmt(state['speed_kmh'],1)} kmh")
    if state.get('course_deg') is not None:
        parts.append(f"cog {fmt(state['course_deg'],1)} deg")
    # GSV systems summary
    gsv = state.get('gsv') or {}
    if gsv:
        sys_s = ",".join(f"{sys}:{gsv[sys].get('in_view','?')}" for sys in sorted(gsv.keys()))
        parts.append(f"in_view[{sys_s}]")
    return " | ".join(parts)

class SerialReader:
    def __init__(self, port, baud):
        self.port = port
        self.baud = baud
        self.ser = None

    def open(self):
        self.ser = serial.Serial(self.port, self.baud, timeout=0)

    def readlines(self):
        while True:
            line = self.ser.readline()  #type: ignore
            if not line:
                time.sleep(0.02)
                continue
            yield line

def main():
    ap = argparse.ArgumentParser(description="Read and interpret NMEA from a Navisys GR-M02 or similar GNSS")
    ap.add_argument("-p", "--port", default="/dev/ttyUSB0")
    ap.add_argument("-b", "--baud", type=int, default=115200)
    ap.add_argument("-j", "--json", action="store_true", help="emit compact JSON status lines once per second")
    ap.add_argument("-r", "--raw", action="store_true", help="echo valid NMEA sentences")
    ap.add_argument("-l", "--log", default=None, help="path to append raw NMEA")
    ap.add_argument("-o", "--once", action="store_true", help="exit after first valid fix summary")
    ap.add_argument("-f", "--format", default=None, help="custom format string using %% formatting (%%(utc_time)s|lat: %%(lat).6f')")
    ap.add_argument("-P", "--partial", action="store_true", help="allow partial outputs without lat/lon/utc_time")
    ap.add_argument("--help-format", action="store_true", help="show available format keys and exit")
    args = ap.parse_args()

    if args.help_format:
        print("Available format keys for use with --format:")
        print("\nTime and Position:")
        print("  %(utc_time)s      - ISO-8601 UTC timestamp (e.g., '2025-10-29T12:34:56Z')")
        print("  %(lat).6f         - Latitude in decimal degrees")
        print("  %(lon).6f         - Longitude in decimal degrees")
        print("  %(alt_m).1f       - Altitude in meters above MSL")
        print("\nFix Quality and Status:")
        print("  %(fix_ok)s        - Boolean indicating valid position fix")
        print("  %(fix_quality)d   - GGA fix quality (0=no fix, 1=GPS, 2=DGPS, 4=RTK-fix, 5=RTK-float)")
        print("  %(fix_type)d      - GSA fix dimension (1=no fix, 2=2D, 3=3D)")
        print("  %(mode)s          - NMEA positioning mode (N/A/D/E/R/F)")
        print("\nSatellite Information:")
        print("  %(num_sats)d      - Number of satellites used in solution")
        print("  %(gsv)s           - Satellites in view per constellation (dict)")
        print("\nDilution of Precision:")
        print("  %(hdop).2f        - Horizontal dilution of precision")
        print("  %(vdop).2f        - Vertical dilution of precision")
        print("  %(pdop).2f        - Position dilution of precision")
        print("\nMotion:")
        print("  %(speed_mps).2f   - Ground speed in meters per second")
        print("  %(speed_kmh).2f   - Ground speed in kilometers per hour")
        print("  %(course_deg).1f  - Course over ground in degrees (0-360)")
        print("\nDifferential Corrections:")
        print("  %(geoid_sep_m).1f - Geoid separation in meters")
        print("  %(age_corrections_s)s - Age of differential corrections in seconds")
        print("  %(dgps_id)s       - Differential reference station ID")
        print("\nError Estimates:")
        print("  %(rms_range_err_m).2f - RMS of pseudorange residuals in meters")
        print("  %(sd_lat_m).2f    - Standard deviation of latitude error in meters")
        print("  %(sd_lon_m).2f    - Standard deviation of longitude error in meters")
        print("  %(sd_alt_m).2f    - Standard deviation of altitude error in meters")
        print("\nMetadata:")
        print("  %(last_update).3f - Local timestamp when record was emitted")
        print("\nExample format strings:")
        print("  --format 'Lat: %(lat).6f, Lon: %(lon).6f'")
        print("  --format '%(utc_time)s | %(lat).6f,%(lon).6f | Alt: %(alt_m).1fm | Sats: %(num_sats)d'")
        print("  --format 'Speed: %(speed_kmh).1f km/h | Course: %(course_deg).0f°'")
        sys.exit(0)

    rdr = SerialReader(args.port, args.baud)
    try:
        rdr.open()
    except Exception as e:
        print(f"Failed to open {args.port} at {args.baud}: {e}", file=sys.stderr)
        sys.exit(1)

    state = {}
    last_emit = 0
    logf = None
    if args.log:
        logf = open(args.log, "a", buffering=1)

    # Special handling for --once: collect two outputs and print the longer one (avoids partial data)
    if args.once:
        outputs = []
        for raw in rdr.readlines():
            try:
                s = raw.decode('ascii', errors='ignore').strip()
            except:
                continue
            if not s.startswith('$'):
                continue
            m = NMEA_RE.match(s)
            if not m:
                continue
            body = m.group(1) + ',' + m.group(2)
            cs = m.group(3).upper()
            calc = nmea_checksum(body)
            if cs != calc:
                continue  # bad checksum
            talker_sen = m.group(1)  # e.g., GNRMC
            fields = m.group(2).split(',')

            if args.raw:
                print(s)
            if logf:
                logf.write(s + "\n")

            updates = parse_sentence(talker_sen, fields)
            if updates:
                merge_updates(state, updates)

            now = time.time()
            if now - last_emit >= 1.0:
                last_emit = now
                if state.get('lat') is not None and state.get('lon') is not None and state.get('utc_time'):
                    # Generate output
                    if args.format:
                        try:
                            format_dict = {}
                            for k, v in state.items():
                                if k == 'gsv':
                                    format_dict[k] = str(v)
                                elif isinstance(v, float):
                                    format_dict[k] = v
                                else:
                                    format_dict[k] = v
                            output = args.format % format_dict
                            outputs.append(output)
                        except (KeyError, ValueError, TypeError) as e:
                            print(f"Format error: {e}", file=sys.stderr)
                    elif args.json:
                        out = dict(state)  # shallow copy
                        # reduce float verbosity and format gsv for readability
                        for k in list(out.keys()):
                            v = out[k]
                            if k == 'gsv':
                                # Flatten gsv to just {"GP": 7, "GL": 8} format
                                out[k] = {sys: data.get('in_view', 0) for sys, data in v.items()}
                            elif isinstance(v, float):
                                out[k] = round(v, 6 if k in ('lat','lon') else 3)
                        output = json.dumps(out, separators=(',',':'), ensure_ascii=False)
                        outputs.append(output)
                    else:
                        output = human_status(state)
                        outputs.append(output)

                    # Exit after collecting two outputs
                    if len(outputs) >= 2:
                        break

        # Print the longer of the two outputs
        if outputs:
            longer = max(outputs, key=len)
            print(longer, flush=True)
        if logf:
            logf.close()
        return

    try:
        for raw in rdr.readlines():
            try:
                s = raw.decode('ascii', errors='ignore').strip()
            except:
                continue
            if not s.startswith('$'):
                continue
            m = NMEA_RE.match(s)
            if not m:
                continue
            body = m.group(1) + ',' + m.group(2)
            cs = m.group(3).upper()
            calc = nmea_checksum(body)
            if cs != calc:
                continue  # bad checksum
            talker_sen = m.group(1)  # e.g., GNRMC
            fields = m.group(2).split(',')

            if args.raw:
                print(s)
            if logf:
                logf.write(s + "\n")

            updates = parse_sentence(talker_sen, fields)
            if updates:
                merge_updates(state, updates)

            now = time.time()
            if now - last_emit >= 1.0:
                last_emit = now
                if args.partial or (state.get('lat') is not None and state.get('lon') is not None and state.get('utc_time')):
                    if args.format:
                        # Custom format string output
                        try:
                            # Create a dict with safe defaults for missing values
                            format_dict = {}
                            for k, v in state.items():
                                if k == 'gsv':
                                    # Convert GSV dict to string representation
                                    format_dict[k] = str(v)
                                elif isinstance(v, float):
                                    format_dict[k] = v
                                else:
                                    format_dict[k] = v
                            # Use % formatting with the state dict
                            print(args.format % format_dict, flush=True)
                        except (KeyError, ValueError, TypeError) as e:
                            print(f"Format error: {e}", file=sys.stderr)
                    elif args.json:
                        out = dict(state)  # shallow copy
                        # reduce float verbosity and format gsv for readability
                        for k in list(out.keys()):
                            v = out[k]
                            if k == 'gsv':
                                # Flatten gsv to just {"GP": 7, "GL": 8} format
                                out[k] = {sys: data.get('in_view', 0) for sys, data in v.items()}
                            elif isinstance(v, float):
                                out[k] = round(v, 6 if k in ('lat','lon') else 3)
                        print(json.dumps(out, separators=(',',':'), ensure_ascii=False), flush=True)
                    else:
                        line = human_status(state)
                        print(line, flush=True)
    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        if logf:
            logf.close()

if __name__ == "__main__":
    main()
