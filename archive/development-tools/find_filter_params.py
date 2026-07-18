#!/usr/bin/env python3
"""
find_filter_params.py

Searches Surge patch XML for filter-related parameters to identify
the filter saturation parameter name.
"""

import sys
from pathlib import Path
from analyze_fxp import parse_fxp

def find_filter_parameters(fxp_path):
    """Extract all filter-related parameters from a patch"""
    print(f"=== Analyzing: {fxp_path.name} ===\n")

    try:
        header, xml, trailer = parse_fxp(fxp_path)

        print("Filter-related parameters:")
        print("-" * 80)

        for line in xml.split('\n'):
            line_lower = line.lower()
            # Look for filter parameters
            if any(keyword in line_lower for keyword in ['filter', 'filt_', 'a_filter', 'b_filter']):
                print(line.strip())

        print("-" * 80)
        print()

        # Specifically look for saturation/drive parameters
        print("Saturation/Drive-related parameters:")
        print("-" * 80)

        for line in xml.split('\n'):
            line_lower = line.lower()
            if any(keyword in line_lower for keyword in ['sat', 'drive', 'resonance', 'cutoff']):
                print(line.strip())

        print("-" * 80)

    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python find_filter_params.py <path_to_fxp_file> [path_to_fxp_file2 ...]")
        sys.exit(1)

    for arg in sys.argv[1:]:
        fxp_path = Path(arg)
        if fxp_path.exists():
            find_filter_parameters(fxp_path)
            print("\n")
        else:
            print(f"Error: File not found: {fxp_path}\n")
