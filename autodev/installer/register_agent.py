#!/usr/bin/env python3
"""Register the roadmap-converter agent in openclaw.json.

This module is called by install.sh (step 9) to register the roadmap-converter
agent entry. It is also importable for testing.

CLI usage:
    python register_agent.py <openclaw_json_path> <autodev_root> [--dry-run|--apply]

Return codes (printed to stdout, last line):
    already_registered  — roadmap-converter already in agents.list, no action taken
    registered          — entry successfully added (--apply only)
    dry_run             — dry-run complete, JSON block printed above (--dry-run only)
    missing_prd_creator — prd-creator not found, cannot copy model config
    error:<message>     — unexpected failure (file missing, invalid JSON, etc.)

The openclaw.json agent entry structure (confirmed from live file):
    {
      "id": "roadmap-converter",
      "workspace": "<autodev_root>/workspace-roadmap-converter",
      "model": { "primary": "...", "fallbacks": [] },
      "tools": { "allow": [...], "deny": [...] }
    }

The model and tools config is copied from the prd-creator entry.
ALL other top-level keys in openclaw.json are preserved exactly.
Writes are atomic (mkstemp + os.replace).
"""

import json
import os
import sys
import tempfile


def register_roadmap_converter(
    openclaw_json_path: str,
    autodev_root: str,
    dry_run: bool = False,
) -> str:
    """Register roadmap-converter agent in openclaw.json.

    Args:
        openclaw_json_path: Absolute path to ~/.openclaw/openclaw.json
        autodev_root: Absolute path to the OpenClaw root directory
        dry_run: If True, print the entry that would be added but do not write

    Returns:
        One of: "already_registered", "registered", "dry_run",
                "missing_prd_creator", "error:<message>"
    """
    # Load and parse
    try:
        with open(openclaw_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError as e:
        return f"error:file not found: {openclaw_json_path}"
    except json.JSONDecodeError as e:
        return f"error:invalid JSON in {openclaw_json_path}: {e}"
    except Exception as e:
        return f"error:{e}"

    # Validate structure
    try:
        agents_list = data["agents"]["list"]
    except (KeyError, TypeError) as e:
        return f"error:unexpected openclaw.json structure — missing agents.list: {e}"

    # Check if already registered
    for entry in agents_list:
        if entry.get("id") == "roadmap-converter":
            return "already_registered"

    # Find prd-creator to copy model/tools config
    prd_creator = None
    for entry in agents_list:
        if entry.get("id") == "prd-creator":
            prd_creator = entry
            break

    if prd_creator is None:
        return "missing_prd_creator"

    # Build new entry
    new_entry = {
        "id": "roadmap-converter",
        "workspace": os.path.join(autodev_root, "workspace-roadmap-converter"),
    }
    if "model" in prd_creator:
        new_entry["model"] = prd_creator["model"]
    if "tools" in prd_creator:
        new_entry["tools"] = prd_creator["tools"]

    if dry_run:
        print("  Would add to agents.list in openclaw.json:")
        print(json.dumps(new_entry, indent=4))
        return "dry_run"

    # Atomic write: append to agents list, preserve all other keys
    data["agents"]["list"] = agents_list + [new_entry]
    json_dir = os.path.dirname(os.path.abspath(openclaw_json_path))
    fd, tmp_path = tempfile.mkstemp(dir=json_dir, prefix="openclaw_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, openclaw_json_path)
    except Exception as e:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return f"error:write failed: {e}"

    return "registered"


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Register roadmap-converter in openclaw.json")
    parser.add_argument("openclaw_json", help="Path to openclaw.json")
    parser.add_argument("autodev_root", help="OpenClaw root directory")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, do not write")
    parser.add_argument("--apply", action="store_true", help="Perform the write")
    args = parser.parse_args()

    if not args.dry_run and not args.apply:
        parser.error("Specify --dry-run or --apply")

    result = register_roadmap_converter(
        args.openclaw_json,
        args.autodev_root,
        dry_run=args.dry_run,
    )
    print(result)
    if result.startswith("error:"):
        sys.exit(1)
