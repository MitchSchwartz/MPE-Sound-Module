#!/usr/bin/env python3
"""
analyze_fxp.py

Analyzes Surge FXP file format to understand the structure
and find modulation routing XML elements.
"""

import sys
from pathlib import Path

def parse_fxp(fxp_path):
    """
    Parse Surge FXP file and extract XML patch data

    Returns: (header_bytes, xml_string, trailer_bytes)
    """
    with open(fxp_path, 'rb') as f:
        data = f.read()

    # Find XML start/end markers
    xml_start = data.find(b'<?xml')
    xml_end = data.find(b'</patch>') + len(b'</patch>')

    if xml_start == -1 or xml_end == -1:
        raise ValueError(f"No valid XML found in {fxp_path}")

    header = data[:xml_start]
    xml_bytes = data[xml_start:xml_end]
    trailer = data[xml_end:]

    xml_string = xml_bytes.decode('utf-8')

    return header, xml_string, trailer

def analyze_patch(fxp_path):
    """Analyze a single patch file"""
    print(f"=== Analyzing: {fxp_path} ===\n")

    try:
        header, xml, trailer = parse_fxp(fxp_path)

        print(f"File size: {len(header) + len(xml.encode('utf-8')) + len(trailer)} bytes")
        print(f"  Header: {len(header)} bytes")
        print(f"  XML: {len(xml.encode('utf-8'))} bytes")
        print(f"  Trailer: {len(trailer)} bytes")
        print()

        # Check header magic
        if header[:4] == b'CcnK':
            print("✓ Valid FXP header (CcnK magic)")
        else:
            print(f"⚠ Unexpected header magic: {header[:4]}")
        print()

        # Display XML
        print("=" * 80)
        print("XML Content:")
        print("=" * 80)
        print(xml)
        print("=" * 80)
        print()

        # Look for modulation elements
        print("Searching for modulation-related XML elements...")
        for line in xml.split('\n'):
            line_lower = line.lower()
            if any(keyword in line_lower for keyword in ['mod', 'timbre', 'routing']):
                print(f"  {line.strip()}")

        return True

    except Exception as e:
        print(f"❌ Error: {e}")
        return False

if __name__ == '__main__':
    if len(sys.argv) != 2:
        print("Usage: python analyze_fxp.py <path_to_fxp_file>")
        sys.exit(1)

    fxp_path = Path(sys.argv[1])

    if not fxp_path.exists():
        print(f"Error: File not found: {fxp_path}")
        sys.exit(1)

    analyze_patch(fxp_path)
