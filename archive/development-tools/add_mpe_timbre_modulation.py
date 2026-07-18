#!/usr/bin/env python3
"""
add_mpe_timbre_modulation.py

One-time batch script to add default timbre → waveshaper drive (saturation) modulation
to all Surge XT patches that don't already have timbre modulation.

Usage:
    python3 add_mpe_timbre_modulation.py [--depth 0.5] [--dry-run]
"""

import sys
import shutil
import argparse
import xml.etree.ElementTree as ET
from pathlib import Path
from analyze_fxp import parse_fxp

# Patch directories
SURGE_PATCH_DIRS = [
    Path.home() / "surge" / "resources" / "data" / "patches_factory",
    Path.home() / "surge" / "resources" / "data" / "patches_3rdparty",
    Path.home() / "Documents" / "Surge XT" / "Patches",
]

def has_timbre_modulation(xml_string):
    """
    Check if XML contains any timbre modulation routing (source=10)

    Returns:
        bool: True if timbre modulation exists, False otherwise
    """
    try:
        root = ET.fromstring(xml_string)

        # Search for any modrouting with source="10" (timbre)
        for elem in root.iter():
            for child in elem:
                if child.tag == 'modrouting':
                    source = child.get('source', '')
                    if source == '10':
                        return True

        return False
    except Exception:
        return False

def add_timbre_modulation(xml_string, depth=0.5):
    """
    Add timbre → waveshaper drive modulation to XML

    Args:
        xml_string: Original XML content
        depth: Modulation depth (0.0 to 1.0, default 0.5 = 50%)

    Returns:
        Modified XML string, or None if modification failed
    """
    try:
        # Parse XML
        root = ET.fromstring(xml_string)

        # Find Scene A filter feedback (saturation in filter block)
        # This is the "Feedback" control above Unison in the left column
        target_params = [
            'a_feedback',              # Scene A filter feedback (saturation in filter block)
            'a_filter1_cutoff',        # Alternative: filter cutoff for timbral change
        ]

        modified = False

        for param_name in target_params:
            # Find all elements (Surge uses tag names as parameter names)
            for elem in root.iter():
                if elem.tag == param_name:
                    # Check if timbre modulation already exists on this parameter
                    has_timbre = False
                    for child in elem:
                        if child.tag == 'modrouting':
                            source = child.get('source', '')
                            if source == '10':  # Timbre source ID
                                has_timbre = True
                                break

                    if not has_timbre:
                        # Add timbre modulation
                        mod_elem = ET.Element('modrouting')
                        mod_elem.set('source', '10')  # Timbre
                        mod_elem.set('depth', str(depth))
                        mod_elem.set('muted', '0')
                        mod_elem.set('source_index', '0')
                        mod_elem.set('source_scene', '0')

                        elem.append(mod_elem)
                        modified = True
                        break

            if modified:
                break

        if not modified:
            return None

        # Convert back to string
        # Preserve XML declaration
        xml_declaration = '<?xml version="1.0" encoding="UTF-8"?>\n'
        xml_body = ET.tostring(root, encoding='utf-8').decode('utf-8')

        return xml_declaration + xml_body

    except Exception:
        return None

def backup_patch(fxp_path):
    """Create backup of original patch"""
    backup_path = fxp_path.with_suffix('.fxp.backup')
    shutil.copy2(fxp_path, backup_path)
    return backup_path

def process_patch(fxp_path, depth=0.5, dry_run=True):
    """
    Process a single patch file

    Args:
        fxp_path: Path to .fxp file
        depth: Modulation depth
        dry_run: If True, don't modify files (just report)

    Returns:
        Status dict: {'status': 'modified'|'skipped'|'error', 'reason': str}
    """
    try:
        # Parse FXP
        header, xml, trailer = parse_fxp(fxp_path)

        # Check if timbre mod already exists
        if has_timbre_modulation(xml):
            return {'status': 'skipped', 'reason': 'already has timbre modulation'}

        if dry_run:
            return {'status': 'would_modify', 'reason': 'no timbre modulation found'}

        # Try to add timbre modulation
        modified_xml = add_timbre_modulation(xml, depth)

        if modified_xml is None:
            return {'status': 'error', 'reason': 'could not add modulation (no suitable parameter found)'}

        # Backup original
        backup_patch(fxp_path)

        # Write modified FXP
        xml_bytes = modified_xml.encode('utf-8')
        data = header + xml_bytes + trailer

        with open(fxp_path, 'wb') as f:
            f.write(data)

        return {'status': 'modified', 'reason': 'added timbre → saturation modulation'}

    except Exception as e:
        return {'status': 'error', 'reason': str(e)}

def main():
    """Main batch processing loop"""
    parser = argparse.ArgumentParser(
        description='Add MPE timbre modulation to Surge XT patches'
    )
    parser.add_argument(
        '--depth',
        type=float,
        default=0.5,
        help='Modulation depth (0.0 to 1.0, default: 0.5)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Analyze without modifying files'
    )
    parser.add_argument(
        '--yes',
        action='store_true',
        help='Skip confirmation prompt'
    )

    args = parser.parse_args()

    print("=" * 80)
    print("Surge XT Timbre Modulation Batch Processor")
    print("=" * 80)
    print(f"\nSettings:")
    print(f"  Modulation: Timbre → Waveshaper Drive (Saturation)")
    print(f"  Depth: {args.depth * 100}%")
    print(f"  Mode: {'DRY RUN (no changes)' if args.dry_run else 'LIVE (will modify files)'}")
    print()

    # First pass: analyze all patches
    print("Analyzing patches...\n")

    stats = {
        'total': 0,
        'would_modify': 0,
        'skipped': 0,
        'errors': 0
    }

    patches_to_modify = []

    for patch_dir in SURGE_PATCH_DIRS:
        if not patch_dir.exists():
            print(f"Skipping missing directory: {patch_dir}")
            continue

        print(f"Scanning: {patch_dir}")

        for fxp_path in patch_dir.rglob('*.fxp'):
            stats['total'] += 1
            result = process_patch(fxp_path, args.depth, dry_run=True)

            if result['status'] == 'would_modify':
                stats['would_modify'] += 1
                patches_to_modify.append(fxp_path)
            elif result['status'] == 'skipped':
                stats['skipped'] += 1
            elif result['status'] == 'error':
                stats['errors'] += 1
                if not args.dry_run:  # Only show errors if not in dry-run
                    print(f"  ERROR: {fxp_path.name} - {result['reason']}")

    # Print summary
    print()
    print("=" * 80)
    print("Analysis Complete")
    print("=" * 80)
    print(f"  Total patches: {stats['total']}")
    print(f"  Will modify: {stats['would_modify']}")
    print(f"  Already has timbre mod: {stats['skipped']}")
    print(f"  Errors: {stats['errors']}")
    print()

    if args.dry_run:
        print("This was a dry run. No files were modified.")
        print("Run without --dry-run to apply changes.")
        return 0

    # Confirm before actual modification
    if stats['would_modify'] == 0:
        print("No patches need modification.")
        return 0

    if not args.yes:
        print(f"⚠ This will modify {stats['would_modify']} patches and create .fxp.backup files.")
        response = input(f"\nProceed? (yes/no): ")
        if response.lower() != 'yes':
            print("Cancelled.")
            return 1

    print()
    print("=" * 80)
    print("Modifying Patches")
    print("=" * 80)
    print()

    modified_count = 0
    error_count = 0

    for i, fxp_path in enumerate(patches_to_modify, 1):
        result = process_patch(fxp_path, args.depth, dry_run=False)

        if result['status'] == 'modified':
            modified_count += 1
            if modified_count % 100 == 0:  # Progress update every 100 patches
                print(f"  Progress: {modified_count}/{stats['would_modify']} patches...")
        elif result['status'] == 'error':
            error_count += 1
            print(f"  ERROR: {fxp_path.name} - {result['reason']}")

    print()
    print("=" * 80)
    print("Complete")
    print("=" * 80)
    print(f"  Successfully modified: {modified_count} patches")
    print(f"  Errors: {error_count}")
    print(f"  Backups saved as .fxp.backup files")
    print()

    if error_count > 0:
        print(f"⚠ {error_count} patches had errors during modification")
    else:
        print("✅ All patches processed successfully!")

    return 0 if error_count == 0 else 1

if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\nInterrupted by user. Exiting.")
        sys.exit(1)
