#!/usr/bin/env python3
"""
check_patch_complexity.py

Analyzes Surge FXP patches to determine if they might cause crashes.
Checks for:
- XML size (larger = more complex)
- Number of modulation routings
- Unusual parameter counts
- Malformed XML

Returns exit code 0 if safe, 1 if potentially problematic.
"""

import sys
from pathlib import Path
import xml.etree.ElementTree as ET

def analyze_patch(fxp_path, verbose=False):
    """
    Analyze a patch for complexity metrics

    Returns: (is_safe, metrics_dict)
    """
    try:
        with open(fxp_path, 'rb') as f:
            data = f.read()

        # Find XML boundaries
        xml_start = data.find(b'<?xml')
        xml_end = data.find(b'</patch>') + len(b'</patch>')

        if xml_start == -1 or xml_end == -1:
            return False, {'error': 'No valid XML found'}

        xml_bytes = data[xml_start:xml_end]
        xml_string = xml_bytes.decode('utf-8')

        # Parse XML
        try:
            root = ET.fromstring(xml_string)
        except ET.ParseError as e:
            return False, {'error': f'XML parse error: {e}'}

        # Gather metrics
        metrics = {
            'file_size': len(data),
            'xml_size': len(xml_bytes),
            'modulation_count': xml_string.count('<modulation'),
            'routing_count': xml_string.count('<routing'),
            'param_count': len(root.findall('.//param')),
            'scene_count': len(root.findall('.//scene')),
        }

        if verbose:
            print(f"Patch Analysis: {Path(fxp_path).name}")
            print(f"  File size: {metrics['file_size']:,} bytes")
            print(f"  XML size: {metrics['xml_size']:,} bytes")
            print(f"  Modulations: {metrics['modulation_count']}")
            print(f"  Routings: {metrics['routing_count']}")
            print(f"  Parameters: {metrics['param_count']}")
            print(f"  Scenes: {metrics['scene_count']}")

        # Safety thresholds (conservative)
        is_safe = True
        warnings = []

        if metrics['xml_size'] > 30000:
            is_safe = False
            warnings.append(f"Large XML ({metrics['xml_size']} bytes)")

        if metrics['modulation_count'] > 50:
            is_safe = False
            warnings.append(f"Many modulations ({metrics['modulation_count']})")

        if metrics['param_count'] > 500:
            is_safe = False
            warnings.append(f"Many parameters ({metrics['param_count']})")

        metrics['is_safe'] = is_safe
        metrics['warnings'] = warnings

        if verbose and warnings:
            print(f"  ⚠ WARNINGS: {', '.join(warnings)}")

        return is_safe, metrics

    except Exception as e:
        return False, {'error': str(e)}

def main():
    if len(sys.argv) < 2:
        print("Usage: python check_patch_complexity.py <patch.fxp> [-v|--verbose]")
        sys.exit(2)

    patch_path = Path(sys.argv[1])
    verbose = '-v' in sys.argv or '--verbose' in sys.argv

    if not patch_path.exists():
        print(f"Error: File not found: {patch_path}", file=sys.stderr)
        sys.exit(2)

    is_safe, metrics = analyze_patch(patch_path, verbose=verbose)

    if not is_safe:
        if 'error' in metrics:
            print(f"ERROR: {metrics['error']}", file=sys.stderr)
        elif 'warnings' in metrics:
            print(f"UNSAFE: {', '.join(metrics['warnings'])}", file=sys.stderr)
        sys.exit(1)

    if verbose:
        print("✓ Patch appears safe")

    sys.exit(0)

if __name__ == '__main__':
    main()
