import os
import sys
import platform
import subprocess
import json
import time
import datetime
import hashlib
import urllib.request
import urllib.parse
from pathlib import Path

# Paths
ROOT = Path(__file__).resolve().parent
URLS_ENC_FILE = ROOT / ".urls.enc"
LICENSE_KEY_FILE = ROOT / ".lic_key"

# Key for XOR decryption of URLs
ENCRYPTION_KEY = b"nanybot_secret_key_123!"

# Global license metadata (filled on successful validation)
LICENSED_TO = "Unknown"
EXPIRE_TIME = "Unknown"
EXPIRE_TIME_READABLE = "Unknown"

# Free quota handling
USAGE_TRACKING_FILE = ROOT / ".usage_quota"
FREE_QUOTA_LIMIT = 10

MACHINE_ID_CACHE_FILE = ROOT / ".machine_id"


def _detect_machine_id() -> str | None:
    """Best-effort lookup of a stable hardware identifier for Windows and macOS."""
    os_name = platform.system().lower()

    if "windows" in os_name:
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography")
            guid, _ = winreg.QueryValueEx(key, "MachineGuid")
            if guid and guid.strip():
                return guid.strip()
        except Exception:
            pass
        try:
            output = subprocess.check_output(
                ["wmic", "csproduct", "get", "uuid"], text=True,
                stderr=subprocess.DEVNULL,
            )
            lines = [line.strip() for line in output.splitlines() if line.strip()]
            if len(lines) > 1 and lines[1].upper() != "UUID":
                return lines[1]
        except Exception:
            pass
        try:
            output = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_ComputerSystemProduct).UUID"],
                text=True, stderr=subprocess.DEVNULL,
            )
            uuid_str = output.strip()
            if uuid_str:
                return uuid_str
        except Exception:
            pass

    elif "darwin" in os_name:  # macOS
        try:
            output = subprocess.check_output(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"], text=True,
            )
            for line in output.splitlines():
                if "IOPlatformUUID" in line:
                    parts = line.split("=")
                    if len(parts) > 1:
                        return parts[1].strip().strip('"')
        except Exception:
            pass

    return None


def get_machine_id() -> str:
    """Returns a persistent hardware identifier for this machine.

    Hardware lookups above can fail or return non-deterministic values
    (e.g. uuid.getnode() falls back to a random ID that changes per
    process). To guarantee the SAME id is sent on every run, whatever
    id is resolved on first use is cached to disk and reused thereafter.
    """
    if MACHINE_ID_CACHE_FILE.exists():
        try:
            cached = MACHINE_ID_CACHE_FILE.read_text(encoding="utf-8").strip()
            if cached:
                return cached
        except Exception:
            pass

    machine_id = _detect_machine_id()
    if not machine_id:
        import uuid
        machine_id = f"fallback-{uuid.uuid4()}"

    try:
        MACHINE_ID_CACHE_FILE.write_text(machine_id, encoding="utf-8")
    except Exception:
        pass

    return machine_id


def decrypt_urls() -> dict:
    """Decrypts and returns the API URLs config dictionary."""
    if not URLS_ENC_FILE.exists():
        raise FileNotFoundError("License configuration file (.urls.enc) is missing.")
    
    encrypted = URLS_ENC_FILE.read_bytes()
    key_len = len(ENCRYPTION_KEY)
    decrypted = bytes(encrypted[i] ^ ENCRYPTION_KEY[i % key_len] for i in range(len(encrypted)))
    return json.loads(decrypted.decode("utf-8"))


def load_saved_key() -> str | None:
    """Reads the saved license key if it exists."""
    if LICENSE_KEY_FILE.exists():
        try:
            return LICENSE_KEY_FILE.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    return None


def save_key(key: str) -> None:
    """Saves the license key to disk."""
    try:
        LICENSE_KEY_FILE.write_text(key.strip(), encoding="utf-8")
    except Exception:
        pass


def prompt_license_key() -> str:
    """Prompts the user to enter a license key using Tkinter dialog or CLI fallback."""
    # Try Tkinter GUI popup prompt first
    try:
        import tkinter as tk
        from tkinter import simpledialog
        
        root = tk.Tk()
        root.withdraw()
        
        # Style prompt window briefly
        key = simpledialog.askstring(
            "Product Activation",
            "Please enter your license key to activate the software:"
        )
        root.destroy()
        if key:
            return key.strip()
    except Exception:
        pass
        
    # Fallback to terminal input
    try:
        print("\n=== LICENSE ACTIVATION ===")
        key = input("Enter your license key: ")
        return key.strip()
    except Exception:
        return ""


def show_error(title: str, message: str) -> None:
    """Displays an error dialog to the user and prints it to stderr."""
    print(f"\n[Licensing Error] {title}: {message}\n", file=sys.stderr)
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(title, message)
        root.destroy()
    except Exception:
        pass


def check_api(validate_url: str, key: str, machine_id: str) -> dict | None:
    """Sends a POST request to check the license validity."""
    try:
        payload = {
            "license_key": key,
            "machine_id": machine_id
        }
        data = json.dumps(payload).encode("utf-8")
        
        req = urllib.request.Request(
            validate_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        
        # 10-second timeout to avoid hanging indefinitely if there's connection lag
        with urllib.request.urlopen(req, timeout=10) as response:
            res_body = response.read().decode("utf-8")
            return json.loads(res_body)
    except Exception as e:
        print(f"[Licensing] API connection failed: {e}", file=sys.stderr)
        return None


def is_expired(expire_time_str: str) -> bool:
    """Compares the current UTC time with expire_time_str (lexicographically friendly ISO-8601)."""
    try:
        # Get current UTC time formatted standardly
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        # Normalize timezone suffix
        exp_normalized = expire_time_str.strip().replace("+00:00", "Z")
        return now_str > exp_normalized
    except Exception:
        return True


def check_and_use_free_quota() -> bool:
    """Tracks monthly usage locally. Returns True if within free quota."""
    current_month = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m")
    
    usage_data = {"month": current_month, "count": 0}
    
    # Load existing tracking data if available
    if USAGE_TRACKING_FILE.exists():
        try:
            data = json.loads(USAGE_TRACKING_FILE.read_text(encoding="utf-8"))
            if data.get("month") == current_month:
                usage_data = data
        except Exception:
            pass # Reset on corruption
            
    # Check if quota is available
    if usage_data["count"] < FREE_QUOTA_LIMIT:
        usage_data["count"] += 1
        try:
            USAGE_TRACKING_FILE.write_text(json.dumps(usage_data), encoding="utf-8")
        except Exception:
            pass
        print(f"[Licensing] Using free quota. ({usage_data['count']}/{FREE_QUOTA_LIMIT} this month)")
        return True
        
    return False


def enforce() -> None:
    """Enforces license validity on startup. Exits if verification fails or is unreachable."""

    global LICENSED_TO, EXPIRE_TIME, EXPIRE_TIME_READABLE
    
    """ Allows free quota bypass."""
    # Check if the user can use a free slot this month
    if check_and_use_free_quota():
        LICENSED_TO = "Free Tier User"
        EXPIRE_TIME_READABLE = "N/A (Free Quota)"
        return

    try:
        urls = decrypt_urls()
        validate_url = urls.get("validate")
        if not validate_url:
            raise ValueError("Validation URL not configured in encrypted source.")
    except Exception as e:
        show_error("Configuration Error", f"Failed to decrypt license configuration: {e}")
        sys.exit(1)
        
    machine_id = get_machine_id()
    key = load_saved_key()
    
    # Prompt if no key found
    if not key:
        key = prompt_license_key()
        if not key:
            show_error("Activation Failed", "A license key is required to run this software.")
            sys.exit(1)
            
    # Always query API to validate
    result = check_api(validate_url, key, machine_id)
    
    if result is None:
        show_error(
            "Connection Error",
            "Could not connect to the license validation server.\n"
            "An active internet connection is required to start this app."
        )
        sys.exit(1)
        
    licensed_to = result.get("licensedTo")
    expire_time = result.get("expireTime")
    
    # Validation checks
    if not licensed_to:
        show_error("Invalid License", "The provided license key is invalid or not registered.")
        sys.exit(1)
        
    if not expire_time or is_expired(expire_time):
        show_error("License Expired", f"Your license expired on: {expire_time}\nPlease renew to continue.")
        sys.exit(1)
        
    # Successfully validated
    save_key(key)
    
    LICENSED_TO = licensed_to
    EXPIRE_TIME = expire_time
    
    # Format readable date (e.g. Dec 31, 2028)
    readable_date = expire_time
    try:
        cleaned = expire_time.strip().replace("Z", "").split(".")[0]
        dt = datetime.datetime.strptime(cleaned, "%Y-%m-%dT%H:%M:%S")
        readable_date = dt.strftime("%b %d, %Y")
    except Exception:
        pass
    EXPIRE_TIME_READABLE = readable_date
    
    print(f"[Licensing] Valid license. Registered to: {licensed_to} (Expires: {expire_time})")
