"""
prepare.py — Generate data.json and thumbnails for Image Atlas

Usage:
    python prepare.py --csv embeddings.csv --images images/ --thumbs thumbs/ --size 128

CSV format (first row = headers):
    filename,x,y
    img001.jpg,1.23,4.56
    img002.jpg,2.34,5.67

Or with an optional label column:
    filename,x,y,label
    img001.jpg,1.23,4.56,A nice photo

The script will:
  1. Read your CSV
  2. Resize images into a thumbs/ folder (if --thumbs is given)
  3. Write data.json
"""

import argparse
import csv
import json
import os
from pathlib import Path

def main():
    parser = argparse.ArgumentParser(description="Prepare data.json for Image Atlas")
    parser.add_argument("--csv", required=True, help="CSV with columns: filename, x, y [, label]")
    parser.add_argument("--images", required=True, help="Directory containing source images")
    parser.add_argument("--thumbs", default=None, help="Output directory for resized thumbnails (optional)")
    parser.add_argument("--size", type=int, default=128, help="Thumbnail size in px (default: 128)")
    parser.add_argument("--out", default="data.json", help="Output JSON file (default: data.json)")
    args = parser.parse_args()

    # Optionally make thumbnails
    make_thumbs = args.thumbs is not None
    if make_thumbs:
        from PIL import Image
        os.makedirs(args.thumbs, exist_ok=True)

    entries = []
    with open(args.csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            filename = row["filename"].strip()
            x = float(row["x"])
            y = float(row["y"])
            label = row.get("label", "").strip()

            if make_thumbs:
                src_path = Path(args.images) / filename
                dst_path = Path(args.thumbs) / filename
                if src_path.exists():
                    img = Image.open(src_path)
                    img.thumbnail((args.size, args.size), Image.LANCZOS)
                    img.save(dst_path, quality=85)
                else:
                    print(f"  warning: {src_path} not found, skipping thumbnail")
                src_field = f"{args.thumbs}/{filename}"
                full_field = f"{args.images}/{filename}"
            else:
                src_field = f"{args.images}/{filename}"
                full_field = None

            entry = {"x": x, "y": y, "src": src_field}
            if full_field:
                entry["full"] = full_field
            if label:
                entry["label"] = label
            entries.append(entry)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)

    print(f"Wrote {len(entries)} entries to {args.out}")

if __name__ == "__main__":
    main()
