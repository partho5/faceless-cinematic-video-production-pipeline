import json
import os
from pathlib import Path

# Key for simple XOR encryption (must match the key in licensing.py)
ENCRYPTION_KEY = b"nanybot_secret_key_123!"

def main():
    urls = {
        "validate": "https://youtube.nanybot.com/api/license/validate"
    }
    
    # Serialize JSON to bytes
    data = json.dumps(urls).encode("utf-8")
    
    # XOR encryption
    key_len = len(ENCRYPTION_KEY)
    encrypted = bytes(data[i] ^ ENCRYPTION_KEY[i % key_len] for i in range(len(data)))
    
    # Output file path
    root = Path(__file__).resolve().parents[1]
    out_path = root / ".urls.enc"
    
    out_path.write_bytes(encrypted)
    print(f"Encrypted URLs saved to: {out_path.resolve()}")

if __name__ == "__main__":
    main()
