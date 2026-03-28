#!/usr/bin/env python3
"""
Convert PAGE XML transcription files to TEI XML for the Necturus Viewer Compact.

Usage:
    python convert_page_to_tei.py [--max N] [--collection-name NAME] [--collection-dir DIR]

This script:
  1. Reads PAGE XML files from  page/xml/
  2. Reads matching images from  page/images/
  3. Converts each PAGE XML to TEI XML
  4. Writes results into  files/<collection-dir>/xml/  and  files/<collection-dir>/img/
  5. Creates  files/<collection-dir>/meta.json
  6. Regenerates  files_info.json  (same logic as script.py)
  7. Patches  page/data.json  with viewer URLs for the atlas
"""

import argparse
import csv
import json
import os
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# ---------------------------------------------------------------------------
# Namespace helpers
# ---------------------------------------------------------------------------
PAGE_NS = "http://schema.primaresearch.org/PAGE/gts/pagecontent/2013-07-15"
TEI_NS = "http://www.tei-c.org/ns/1.0"

# Register the TEI namespace so ET uses it without ns0: prefixes
ET.register_namespace("", TEI_NS)


def page_tag(local: str) -> str:
    """Return a fully-qualified PAGE element name."""
    return f"{{{PAGE_NS}}}{local}"


# ---------------------------------------------------------------------------
# PAGE XML parsing
# ---------------------------------------------------------------------------

def parse_page_xml(path: str) -> dict:
    """
    Parse a PAGE XML file and return a dict with:
        image_filename, image_width, image_height,
        text_regions: [ { id, coords, lines: [ { id, coords, baseline, text } ] } ]
    """
    tree = ET.parse(path)
    root = tree.getroot()

    page_el = root.find(page_tag("Page"))
    if page_el is None:
        raise ValueError(f"No <Page> element found in {path}")

    result = {
        "image_filename": page_el.get("imageFilename", ""),
        "image_width": int(page_el.get("imageWidth", "0")),
        "image_height": int(page_el.get("imageHeight", "0")),
        "text_regions": [],
    }

    # Respect ReadingOrder if present
    reading_order = {}
    ro_el = page_el.find(page_tag("ReadingOrder"))
    if ro_el is not None:
        for group in ro_el.iter():
            for ref in group.findall(page_tag("RegionRefIndexed")):
                idx = int(ref.get("index", 0))
                region_ref = ref.get("regionRef", "")
                reading_order[region_ref] = idx

    # Collect TextRegions
    regions = []
    for tr in page_el.findall(page_tag("TextRegion")):
        region_id = tr.get("id", "")
        coords_el = tr.find(page_tag("Coords"))
        region_coords = coords_el.get("points", "") if coords_el is not None else ""

        # Collect TextLines (respect readingOrder custom attribute)
        lines = []
        for tl in tr.findall(page_tag("TextLine")):
            line_id = tl.get("id", "")

            line_coords_el = tl.find(page_tag("Coords"))
            line_coords = line_coords_el.get("points", "") if line_coords_el is not None else ""

            baseline_el = tl.find(page_tag("Baseline"))
            baseline = baseline_el.get("points", "") if baseline_el is not None else ""

            text_equiv = tl.find(page_tag("TextEquiv"))
            unicode_el = text_equiv.find(page_tag("Unicode")) if text_equiv is not None else None
            text = unicode_el.text if unicode_el is not None and unicode_el.text else ""

            # Extract line order from custom attribute
            custom = tl.get("custom", "")
            line_order = 0
            m = re.search(r"readingOrder\s*\{index:(\d+)", custom)
            if m:
                line_order = int(m.group(1))

            lines.append({
                "id": line_id,
                "coords": line_coords,
                "baseline": baseline,
                "text": text,
                "order": line_order,
            })

        # Sort lines by reading order
        lines.sort(key=lambda l: l["order"])

        # Determine region order
        region_order = reading_order.get(region_id, 0)
        custom = tr.get("custom", "")
        m = re.search(r"readingOrder\s*\{index:(\d+)", custom)
        if m:
            region_order = int(m.group(1))

        regions.append({
            "id": region_id,
            "coords": region_coords,
            "lines": lines,
            "order": region_order,
        })

    # Sort regions by reading order
    regions.sort(key=lambda r: r["order"])
    result["text_regions"] = regions

    return result


# ---------------------------------------------------------------------------
# TEI XML generation
# ---------------------------------------------------------------------------

def build_tei_xml(page_data: dict, page_stem: str, metadata: dict | None = None) -> str:
    """
    Build a TEI XML string from parsed PAGE data.
    The output mirrors the format used by the Necturus Viewer samples.

    If metadata is provided, it is rendered as a <fw type="header"> block
    at the top of the page (visible in the viewer). Metadata dict can have
    any keys (e.g. date, shelfmark, notes) — all are displayed.
    """
    surface_id = f"facs_{page_stem}"

    lines = []
    lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    lines.append('<TEI xmlns="http://www.tei-c.org/ns/1.0">')

    # --- teiHeader ---
    lines.append("   <teiHeader>")
    lines.append("      <fileDesc>")
    lines.append("         <titleStmt/>")
    lines.append("         <seriesStmt/>")
    lines.append("         <sourceDesc>")
    lines.append("            <bibl/>")
    lines.append("         </sourceDesc>")
    lines.append("      </fileDesc>")
    lines.append("      <profileDesc>")
    lines.append("      </profileDesc>")
    lines.append("   </teiHeader>")

    # --- facsimile ---
    w = page_data["image_width"]
    h = page_data["image_height"]

    lines.append("   <facsimile>")
    lines.append(
        f'      <surface ulx="0" uly="0" lrx="{w}" lry="{h}" xml:id="{surface_id}">'
    )
    # graphic element — use .jpg since that is what the img folder will contain
    lines.append(
        f'         <graphic url="{page_stem}.jpg" width="{w}px" height="{h}px"/>'
    )

    # zones for each line (no TextRegion wrapper — avoids region polygon on image)
    for region in page_data["text_regions"]:
        for line in region["lines"]:
            line_zone_id = f"{surface_id}_{line['id']}"
            lines.append(
                f'         <zone points="{line["coords"]}" rendition="Line" '
                f'xml:id="{line_zone_id}" subtype="default"/>'
            )

    lines.append("      </surface>")
    lines.append("   </facsimile>")

    # --- text body ---
    lines.append("   <text>")
    lines.append(f'      <body><div><pb facs="#{surface_id}" n="{page_stem}" '
                 f'xml:id="img_{page_stem}"/>')

    # Metadata is displayed via the breadcrumb (injected by index.html script),
    # not in the transcription body.

    for region in page_data["text_regions"]:
        lines.append(f'            <ab>')
        for line in region["lines"]:
            line_zone_id = f"{surface_id}_{line['id']}"
            # Escape XML special characters in text
            text = escape_xml(line["text"])
            lines.append(
                f'               <l facs="#{line_zone_id}">{text}</l>'
            )
        lines.append("            </ab>")

    lines.append("      </div></body>")
    lines.append("   </text>")
    lines.append("</TEI>")

    return "\n".join(lines)


def escape_xml(text: str) -> str:
    """Escape XML special characters."""
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    text = text.replace('"', "&quot;")
    return text


# ---------------------------------------------------------------------------
# files_info.json regeneration (mirrors script.py / generate_files_info.py)
# ---------------------------------------------------------------------------

def _chrono_sort_key(stem: str):
    """
    Sort stems like:
        0001_01_01_1332-01
        0461_22_07_1357-01
        0447_XX_XX_1327-01

    Interpreted as:
        <running-index>_<day>_<month>_<year>-<sequence>

    Unknown day/month ("XX") are pushed to the end within a year/month.
    """
    import re

    m = re.match(r"^(\d{4})_(\d{2}|XX)_(\d{2}|XX)_(\d{4})-(\d{2})$", stem)
    if not m:
        return (9999, 99, 99, 99, stem)

    _, day_s, month_s, year_s, seq_s = m.groups()

    year = int(year_s)
    month = 99 if month_s == "XX" else int(month_s)
    day = 99 if day_s == "XX" else int(day_s)
    seq = int(seq_s)

    return (year, month, day, seq, stem)


def regenerate_files_info(files_dir: str, output_path: str):
    """Regenerate files_info.json by scanning the files directory."""
    try:
        import natsort
        sort_fn = natsort.natsorted
    except ImportError:
        sort_fn = sorted

    result = []
    folders = sort_fn([
        f for f in os.listdir(files_dir)
        if os.path.isdir(os.path.join(files_dir, f))
    ])

    for folder in folders:
        folder_path = os.path.join(files_dir, folder)
        xml_folder = os.path.join(folder_path, "xml")

        if not os.path.isdir(xml_folder):
            continue

        xml_files = sorted(
            [
                os.path.splitext(f)[0]
                for f in os.listdir(xml_folder)
                if f.lower().endswith(".xml")
            ],
            key=_chrono_sort_key
        )

        meta_path = os.path.join(folder_path, "meta.json")
        if os.path.exists(meta_path):
            with open(meta_path) as mf:
                meta = json.load(mf)
            name = meta.get("name", folder)
            pics = meta.get("picturesAvailable", True)
        else:
            name = folder
            pics = True

        result.append({
            "path": folder,
            "name": name,
            "pages": xml_files,
            "picturesAvailable": pics,
        })

    with open(output_path, "w") as f:
        json.dump(result, f, indent=4)
    print(f"  Wrote {output_path}")


# ---------------------------------------------------------------------------
# Atlas URL patching
# ---------------------------------------------------------------------------

def patch_atlas_urls(files_info_path: str, data_json_path: str):
    """Add viewer URLs to the atlas data.json based on files_info.json page ordering."""
    if not os.path.exists(data_json_path):
        return

    with open(files_info_path, encoding="utf-8") as f:
        info = json.load(f)

    stem_to_url = {}
    for col_idx, col in enumerate(info, start=1):
        for page_idx, stem in enumerate(col["pages"], start=1):
            stem_to_url[stem] = f"../#/collection/{col_idx}/page/{page_idx}"

    with open(data_json_path, encoding="utf-8") as f:
        data = json.load(f)

    matched = 0
    for entry in data:
        stem = Path(entry["src"].split("/")[-1]).stem
        if stem in stem_to_url:
            entry["url"] = stem_to_url[stem]
            matched += 1

    with open(data_json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    print(f"  Patched atlas: {matched}/{len(data)} entries linked to viewer")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Convert PAGE XML transcriptions to TEI XML for Necturus Viewer."
    )
    parser.add_argument(
        "--max", type=int, default=10,
        help="Maximum number of PAGE XML files to convert (default: 10)"
    )
    parser.add_argument(
        "--collection-name", default="Deeds of Antwerp",
        help='Display name for the collection (default: "Deeds of Antwerp")'
    )
    parser.add_argument(
        "--collection-dir", default="deeds",
        help='Folder name under files/ (default: "deeds")'
    )
    parser.add_argument(
        "--metadata", default=None,
        help='Path to a CSV with document metadata (must have a "filename" column; '
             'all other columns are displayed as a header). Default: page/metadata.csv if it exists.'
    )
    args = parser.parse_args()

    # Paths relative to repo root (the script lives in the repo root)
    repo_root = Path(__file__).resolve().parent
    page_xml_dir = repo_root / "page" / "xml"
    page_img_dir = repo_root / "page" / "images"
    files_dir = repo_root / "files"
    collection_dir = files_dir / args.collection_dir
    out_xml_dir = collection_dir / "xml"
    out_img_dir = collection_dir / "img"

    # Validate inputs
    if not page_xml_dir.is_dir():
        sys.exit(f"ERROR: PAGE XML directory not found: {page_xml_dir}")
    if not page_img_dir.is_dir():
        sys.exit(f"ERROR: PAGE images directory not found: {page_img_dir}")

    # Discover PAGE XML files
    try:
        import natsort
        sort_fn = natsort.natsorted
    except ImportError:
        sort_fn = sorted

    page_xmls = sort_fn([
        f for f in os.listdir(page_xml_dir)
        if f.lower().endswith(".xml")
    ])

    if not page_xmls:
        sys.exit(f"ERROR: No XML files found in {page_xml_dir}")

    # Load metadata CSV if available
    metadata_map = {}  # stem -> dict of metadata fields
    meta_csv_path = Path(args.metadata) if args.metadata else repo_root / "page" / "metadata.csv"
    if meta_csv_path.is_file():
        with open(meta_csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                fn = row.pop("filename", "").strip()
                if fn:
                    # Strip extension if provided
                    fn = Path(fn).stem if "." in fn else fn
                    # Keep only non-empty fields
                    metadata_map[fn] = {k.strip(): v.strip() for k, v in row.items() if v.strip()}
        print(f"Loaded metadata for {len(metadata_map)} document(s) from {meta_csv_path}")
    else:
        print(f"No metadata CSV found at {meta_csv_path} (skipping header generation)")

    # Limit to --max
    page_xmls = page_xmls[: args.max]
    print(f"Converting {len(page_xmls)} PAGE XML file(s) ...")

    # Create output directories
    out_xml_dir.mkdir(parents=True, exist_ok=True)
    out_img_dir.mkdir(parents=True, exist_ok=True)

    # Build image lookup by date suffix (everything after first underscore)
    # so that 0070_05_02_1351-01.xml matches 0071_05_02_1351-01.jpg
    img_by_suffix = {}
    for img_file in page_img_dir.iterdir():
        if img_file.is_file():
            parts = img_file.stem.split("_", 1)
            if len(parts) == 2:
                img_by_suffix[parts[1]] = img_file

    converted = 0
    skipped_images = 0

    for xml_file in page_xmls:
        xml_path = page_xml_dir / xml_file
        stem = Path(xml_file).stem  # e.g. "0001_01_01_1332-01"

        print(f"  {xml_file} ... ", end="")

        # Parse PAGE XML
        try:
            page_data = parse_page_xml(str(xml_path))
        except Exception as e:
            print(f"SKIP (parse error: {e})")
            continue

        # Build TEI XML
        doc_metadata = metadata_map.get(stem)
        tei_xml = build_tei_xml(page_data, stem, metadata=doc_metadata)

        # Write TEI XML
        tei_path = out_xml_dir / f"{stem}.xml"
        with open(tei_path, "w", encoding="utf-8") as f:
            f.write(tei_xml)

        # Copy corresponding image
        # Try: exact name from PAGE XML, then stem match, then suffix match
        img_found = False
        candidates = [
            page_data["image_filename"],         # exact name from PAGE XML
            f"{stem}.jpg",
            f"{stem}.jpeg",
            f"{stem}.png",
            f"{stem}.tif",
            f"{stem}.tiff",
        ]
        for candidate in candidates:
            src = page_img_dir / candidate
            if src.is_file():
                dst = out_img_dir / f"{stem}.jpg"
                shutil.copy2(str(src), str(dst))
                img_found = True
                break

        # Fallback: match by date suffix (ignoring overall index)
        if not img_found:
            suffix = stem.split("_", 1)[1] if "_" in stem else None
            if suffix and suffix in img_by_suffix:
                src = img_by_suffix[suffix]
                dst = out_img_dir / f"{stem}.jpg"
                shutil.copy2(str(src), str(dst))
                img_found = True

        if img_found:
            print("OK")
        else:
            print("OK (no matching image found)")
            skipped_images += 1

        converted += 1

    # Write meta.json
    meta = {"name": args.collection_name}
    meta_path = collection_dir / "meta.json"
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)
    print(f"  Wrote {meta_path}")

    # Write page_metadata.json (used by index.html to show metadata in breadcrumb)
    if metadata_map:
        page_meta_path = collection_dir / "page_metadata.json"
        with open(page_meta_path, "w", encoding="utf-8") as f:
            json.dump(metadata_map, f, indent=2, ensure_ascii=False)
        print(f"  Wrote {page_meta_path}")

    # Regenerate files_info.json
    files_info_path = repo_root / "files_info.json"
    regenerate_files_info(str(files_dir), str(files_info_path))

    # Patch atlas data.json with viewer URLs
    atlas_data_path = repo_root / "page" / "data.json"
    patch_atlas_urls(str(files_info_path), str(atlas_data_path))

    # Summary
    print()
    print(f"Done! Converted {converted} file(s) to {out_xml_dir}")
    if skipped_images:
        print(f"  Warning: {skipped_images} file(s) had no matching image.")
    print()
    print("Next steps:")
    print("  1. (Optional) Run  node generate_fuse_index.js  to build the search index.")
    print("  2. Start a local server and open index.html, or push to GitHub for Pages deployment.")


if __name__ == "__main__":
    main()
