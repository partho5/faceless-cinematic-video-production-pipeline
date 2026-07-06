# Developer Guide: Licensing & Activation System

This document explains how the machine-locked license verification system works and how you, as the developer, can manage, configure, or disable it.

---

## 1. File Structure Overview

*   **`licensing.py`**: The core licensing module. Handles hardware ID retrieval, URL decryption, validation requests, and UI/CLI block prompts.
*   **`.urls.enc`**: Binary encrypted configuration file holding the production licensing API URLs.
*   **`scripts/encrypt_urls.py`**: Developer script to configure and encrypt API URLs.
*   **`scripts/obfuscator.py`**: Developer utility to obfuscate python code.
*   **`.lic_key`**: A local plain-text file created on the user's computer storing their activated license key (to avoid re-entering on every startup).

---

## 2. Enabling or Disabling Licensing

The licensing system is integrated into both the GUI launcher and the CLI orchestrator. It can be toggled on or off by commenting out a single line of code.

### GUI Launcher (`run.py`)
Open `run.py`, navigate to the `main()` function, and comment/uncomment the line:

```python
def main() -> int:
    import licensing; licensing.enforce()  # <--- COMMENT THIS LINE TO DISABLE
    if os.name == "nt":
        # ...
```

### CLI Orchestrator (`src/vp/run.py`)
Open `src/vp/run.py`, navigate to the `main()` function, and comment/uncomment the line:

```python
def main(argv=None) -> int:
    import licensing; licensing.enforce()  # <--- COMMENT THIS LINE TO DISABLE
    ap = argparse.ArgumentParser(prog="vp.run")
    # ...
```

---

## 3. Modifying the Licensing API URLs

To prevent simple text inspection of the binary package, the API endpoints are encrypted in `.urls.enc`. 

Follow these steps to change the licensing URLs:

1. Open `scripts/encrypt_urls.py`.
2. Update the dictionary with your new endpoints:
   ```python
   urls = {
       "validate": "https://youtube.nanybot.com/api/license/validate"
   }
   ```
3. Run the script to generate a new encrypted `.urls.enc` file:
   ```bash
   python3 scripts/encrypt_urls.py
   ```
4. Commit the new `.urls.enc` to git.

> [!NOTE]
> The encryption/decryption key is defined as `ENCRYPTION_KEY = b"nanybot_secret_key_123!"` inside both `scripts/encrypt_urls.py` and `licensing.py`. You can change this key to make it unique to your setup.

---

## 4. Expected API Request and Response

Your server endpoint must handle a `POST` request with a JSON payload and return license metadata.

### Request Payload
Sent by `licensing.py`:
```json
{
  "license_key": "USER_ENTERED_KEY_STRING",
  "machine_id": "HARDWARE_UUID_STRING"
}
```

### Expected Response Payload (JSON)
*   **Active License Response:**
    ```json
    {
      "licensedTo": "User Name or Company",
      "expireTime": "2026-12-31T23:59:59Z"
    }
    ```
*   **Expired or Invalid License Response:**
    ```json
    {
      "licensedTo": null,
      "expireTime": "2026-01-01T00:00:00Z"
    }
    ```

### Expired Check Logic
The validation logic lexicographically compares the ISO-8601 string of the current UTC time against the returned `expireTime` value:
`now_utc_string > expireTime` indicates an expired license.

---

## 5. Machine ID Bindings

To prevent license keys from being shared, the software binds the key to a single device. The constant machine ID is generated as follows:
*   **Windows**: Queries the Registry path `HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Cryptography\MachineGuid`. Falls back to running command line `wmic csproduct get uuid`.
*   **macOS**: Runs the system shell utility command `ioreg -rd1 -c IOPlatformExpertDevice` and extracts `IOPlatformUUID`.
*   **Fallback**: Uses the device network card MAC address (`uuid.getnode()`).

---

## 6. How to Obfuscate Files

### Using the Built-In Obfuscator
We provided a lightweight packer script to convert code into compressed, Base64-encoded executable strings.

1. Run the script against the code module:
   ```bash
   python3 scripts/obfuscator.py licensing.py
   ```
   This generates `licensing.obf.py`.
2. Inspect `licensing.obf.py` to ensure it works correctly:
   ```bash
   python3 licensing.obf.py
   ```
3. Overwrite the original source file:
   ```bash
   mv licensing.obf.py licensing.py
   ```

### Using PyArmor (Recommended for Production)
For robust bytecode-level encryption that prevents code extraction and debugging:
1. Install PyArmor:
   ```bash
   pip install pyarmor
   ```
2. Encrypt the entrypoints:
   ```bash
   pyarmor obfuscate run.py
   pyarmor obfuscate licensing.py
   ```
