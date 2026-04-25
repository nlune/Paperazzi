import json
from pathlib import Path

import httpx

# Local URL for testing
URL = "http://localhost:8000/concepts"

def test_extract_concepts(pdf_path: str):
    path = Path(pdf_path)
    if not path.exists():
        print(f"File not found: {pdf_path}")
        return

    print(f"Uploading {pdf_path} to {URL}...")
    
    with open(path, "rb") as f:
        files = {"file": (path.name, f, "application/pdf")}
        response = httpx.post(URL, files=files, timeout=60.0)

    if response.status_code == 200:
        print("Success! Concepts extracted:")
        print(json.dumps(response.json(), indent=2))
    else:
        print(f"Failed with status {response.status_code}")
        print(response.text)

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python test.py <path_to_pdf>")
    else:
        test_extract_concepts(sys.argv[1])
