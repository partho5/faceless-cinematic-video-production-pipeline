import base64
import zlib
import sys
from pathlib import Path

def obfuscate_file(file_path: Path):
    if not file_path.exists():
        print(f"Error: {file_path} does not exist.")
        return
        
    print(f"Obfuscating {file_path}...")
    source = file_path.read_text(encoding="utf-8")
    
    # Compress and encode
    compressed = zlib.compress(source.encode("utf-8"))
    encoded = base64.b64encode(compressed).decode("utf-8")
    
    # Generate wrapper
    obfuscated_content = (
        f"# Obfuscated with custom packager\n"
        f"import base64, zlib\n"
        f"exec(zlib.decompress(base64.b64decode(b'{encoded}')))\n"
    )
    
    # Write to a new .obf.py file first for safety, then replace original if desired
    obf_path = file_path.with_suffix(".obf.py")
    obf_path.write_text(obfuscated_content, encoding="utf-8")
    print(f"Saved obfuscated script to: {obf_path.resolve()}")
    print("You can rename this file to overwrite the original once verified.")

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/obfuscator.py <path_to_python_file>")
        print("Example: python3 scripts/obfuscator.py licensing.py")
        sys.exit(1)
        
    file_path = Path(sys.argv[1]).resolve()
    obfuscate_file(file_path)

if __name__ == "__main__":
    main()
