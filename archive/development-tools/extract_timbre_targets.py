#!/usr/bin/env python3
"""
extract_timbre_targets.py

Extracts all parameters that have timbre modulation in a patch.
This helps us understand what parameters are typically modulated.
"""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from analyze_fxp import parse_fxp

def extract_timbre_modulations(fxp_path):
    """Find all parameters with timbre modulation (source=10)"""
    print(f"=== Timbre Modulations in: {fxp_path.name} ===\n")

    try:
        header, xml, trailer = parse_fxp(fxp_path)

        # Parse XML
        root = ET.fromstring(xml)

        timbre_mods = []

        # Iterate through all elements
        for elem in root.iter():
            # Look for elements with modrouting children
            for child in elem:
                if child.tag == 'modrouting':
                    source = child.get('source', '')
                    if source == '10':  # Timbre
                        depth = child.get('depth', '')
                        muted = child.get('muted', '')

                        # Get parameter value
                        param_value = elem.get('value', 'N/A')
                        param_type = elem.get('type', 'N/A')

                        timbre_mods.append({
                            'parameter': elem.tag,
                            'value': param_value,
                            'type': param_type,
                            'depth': depth,
                            'muted': muted
                        })

        if timbre_mods:
            print(f"Found {len(timbre_mods)} timbre modulation(s):\n")
            for i, mod in enumerate(timbre_mods, 1):
                print(f"{i}. Parameter: {mod['parameter']}")
                print(f"   Base value: {mod['value']}")
                print(f"   Type: {mod['type']}")
                print(f"   Depth: {mod['depth']}")
                print(f"   Muted: {mod['muted']}")
                print()

            # Look for filter-related parameters
            filter_mods = [m for m in timbre_mods if 'filter' in m['parameter'].lower() or 'sat' in m['parameter'].lower()]
            if filter_mods:
                print("Filter/Saturation parameters with timbre modulation:")
                for mod in filter_mods:
                    print(f"  → {mod['parameter']} (depth: {mod['depth']})")
            else:
                print("No filter/saturation parameters found with timbre modulation")

        else:
            print("No timbre modulations found in this patch")

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python extract_timbre_targets.py <patch1.fxp> [patch2.fxp ...]")
        sys.exit(1)

    for arg in sys.argv[1:]:
        fxp_path = Path(arg)
        if fxp_path.exists():
            extract_timbre_modulations(fxp_path)
            print("\n" + "=" * 80 + "\n")
        else:
            print(f"Error: File not found: {fxp_path}\n")
