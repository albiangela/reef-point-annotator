#!/usr/bin/env python3
"""
Local point-annotation tool for reef transect point-crop images.

Usage:
    python3 app.py [DIRECTORY]

If DIRECTORY is omitted, a folder-picker window opens so you can browse
to the image folder (falls back to a plain text prompt in the terminal
if a GUI isn't available).

Only images matching PT<marker>_<camera>_x<x>_y<y>.png are loaded
(this naturally excludes the "...3DModel..." context images and the
combined_images/ subfolder, which don't match that naming pattern).

Annotations are saved to <DIRECTORY>/annotations.csv after every click,
so the tool is safe to stop and resume at any time.
"""

import csv
import json
import re
import sys
import threading
import webbrowser
from pathlib import Path

from flask import Flask, jsonify, request, send_file, abort

FILENAME_RE = re.compile(r"^(PT\d+)_([A-Za-z0-9]+)_x(\d+)_y(\d+)\.png$", re.IGNORECASE)


def ask_directory_via_gui():
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox
    except ImportError:
        return None
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    messagebox.showinfo(
        "Reef Point Annotator",
        "Select the folder that contains the point images to annotate.",
        parent=root,
    )
    path = filedialog.askdirectory(title="Select image folder", parent=root)
    root.destroy()
    return path or None


def load_images(directory):
    images = []
    skipped = 0
    for p in sorted(directory.iterdir()):
        if not p.is_file():
            continue
        m = FILENAME_RE.match(p.name)
        if not m:
            skipped += 1
            continue
        marker, camera, x, y = m.groups()
        images.append({
            "filename": p.name,
            "marker": marker,
            "camera": camera,
            "x": int(x),
            "y": int(y),
        })
    images.sort(key=lambda im: int(im["marker"][2:]))
    return images, skipped


def resolve_target_dir():
    for attempt in range(5):
        if attempt == 0 and len(sys.argv) > 1:
            path = sys.argv[1]
        else:
            path = ask_directory_via_gui()
            if not path:
                path = input(
                    "Enter the full path to the folder of point images: "
                ).strip()
        if not path:
            continue
        directory = Path(path)
        if not directory.is_dir():
            print(f"Not a folder: {directory}")
            continue
        images, skipped = load_images(directory)
        if images:
            return directory, images, skipped
        print(f"No matching PT####_..._x###_y###.png images found in {directory}")
    print("Could not find a folder with matching images after several tries.")
    sys.exit(1)


TARGET_DIR, IMAGES, SKIPPED_COUNT = resolve_target_dir()
CSV_PATH = TARGET_DIR / "annotations.csv"

# The built-in class list lives in classes.json (next to this script) so it
# can be edited without touching code. Two kinds of entries:
#   Flat:       {"group": name, "classes": [{"label", "key", "color"}, ...]}
#   Morphology: {"morphology": name, "key": selector_key,
#                "genera": [{"genus": name, "classes": [...]}, ...]}
# Morphology entries drive the two-step UI: pick a morphology (step 1),
# then a genus within it (step 2). Genus-level shortcut keys only need to be
# unique within their own morphology, since only one morphology's genus
# panel is visible at a time.
CLASSES_CONFIG_PATH = Path(__file__).resolve().parent / "classes.json"


def _parse_classes(raw_classes, seen_labels, seen_keys):
    items = []
    for c in raw_classes:
        label = c["label"]
        key = (c.get("key") or "").lower()
        color = c.get("color", "#7f8c8d")
        if label.lower() in seen_labels:
            raise ValueError(f"Duplicate class label in classes.json: {label!r}")
        if key and key in seen_keys:
            raise ValueError(f"Duplicate shortcut key in classes.json: {key!r} ({label})")
        seen_labels.add(label.lower())
        if key:
            seen_keys.add(key)
        items.append((label, key, color))
    return items


def load_class_groups():
    with open(CLASSES_CONFIG_PATH) as f:
        data = json.load(f)
    groups = []
    seen_labels = set()
    seen_global_keys = set()
    for entry in data:
        if "morphology" in entry:
            name = entry["morphology"]
            selector_key = (entry.get("key") or "").lower()
            if selector_key and selector_key in seen_global_keys:
                raise ValueError(f"Duplicate shortcut key in classes.json: {selector_key!r} ({name})")
            if selector_key:
                seen_global_keys.add(selector_key)
            genera = []
            seen_local_keys = set()
            for g in entry["genera"]:
                items = _parse_classes(g["classes"], seen_labels, seen_local_keys)
                genera.append((g["genus"], items))
            groups.append({"type": "morphology", "name": name, "key": selector_key, "genera": genera})
        else:
            items = _parse_classes(entry["classes"], seen_labels, seen_global_keys)
            groups.append({"type": "flat", "name": entry["group"], "items": items})
    return groups


CLASS_GROUPS = load_class_groups()
ALL_CLASSES = [
    label
    for group in CLASS_GROUPS
    for label in (
        [label for _, items in group["genera"] for label, _, _ in items]
        if group["type"] == "morphology"
        else [label for label, _, _ in group["items"]]
    )
]

# Single-key shortcuts still free after classes.json claims its own, used to
# auto-assign a shortcut to any class added at runtime via "+ Add class".
ALL_SHORTCUT_KEYS = list("1234567890qwertyuiopasdfghjklzxcvbnm")

# User-added custom classes (label, shortcut_key, color), persisted alongside the images.
CUSTOM_CLASSES_PATH = TARGET_DIR / "custom_classes.json"
CUSTOM_COLOR_CYCLE = ["#34495e", "#8e44ad", "#2c3e50", "#c0392b", "#117864", "#7d6608"]


def load_custom_classes():
    if not CUSTOM_CLASSES_PATH.exists():
        return []
    with open(CUSTOM_CLASSES_PATH) as f:
        return [tuple(item) for item in json.load(f)]


def save_custom_classes():
    with open(CUSTOM_CLASSES_PATH, "w") as f:
        json.dump(CUSTOM_CLASSES, f)


CUSTOM_CLASSES = load_custom_classes()


def all_class_labels():
    return ALL_CLASSES + [label for label, _, _ in CUSTOM_CLASSES]


def used_shortcut_keys():
    keys = set()
    for group in CLASS_GROUPS:
        if group["type"] == "morphology":
            if group["key"]:
                keys.add(group["key"].lower())
            for _, items in group["genera"]:
                keys |= {key.lower() for _, key, _ in items if key}
        else:
            keys |= {key.lower() for _, key, _ in group["items"] if key}
    keys |= {key.lower() for _, key, _ in CUSTOM_CLASSES if key}
    return keys


ANNOTATIONS = {}  # filename -> class label


def load_annotations():
    if not CSV_PATH.exists():
        return
    with open(CSV_PATH, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("class"):
                ANNOTATIONS[row["filename"]] = row["class"]


def save_annotations():
    with open(CSV_PATH, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["filename", "marker", "camera", "x", "y", "class"])
        for im in IMAGES:
            cls = ANNOTATIONS.get(im["filename"], "")
            writer.writerow([im["filename"], im["marker"], im["camera"], im["x"], im["y"], cls])


load_annotations()

app = Flask(__name__)


@app.route("/")
def index():
    return INDEX_HTML


@app.route("/api/list")
def api_list():
    morphologies = [
        {"name": g["name"], "key": g["key"], "genera": g["genera"]}
        for g in CLASS_GROUPS if g["type"] == "morphology"
    ]
    flat_groups = [(g["name"], g["items"]) for g in CLASS_GROUPS if g["type"] == "flat"]
    if CUSTOM_CLASSES:
        flat_groups.append(("Custom", CUSTOM_CLASSES))
    return jsonify({
        "images": [
            {**im, "class": ANNOTATIONS.get(im["filename"])}
            for im in IMAGES
        ],
        "morphologies": morphologies,
        "flatGroups": flat_groups,
        "dir": str(TARGET_DIR),
        "skipped": SKIPPED_COUNT,
    })


@app.route("/api/add_class", methods=["POST"])
def api_add_class():
    data = request.get_json()
    label = (data.get("label") or "").strip()
    if not label:
        abort(400, "Class name cannot be empty")
    if label.lower() in {c.lower() for c in all_class_labels()}:
        abort(400, "That class already exists")
    used_keys = used_shortcut_keys()
    key = next((k for k in ALL_SHORTCUT_KEYS if k not in used_keys), "")
    color = CUSTOM_COLOR_CYCLE[len(CUSTOM_CLASSES) % len(CUSTOM_COLOR_CYCLE)]
    CUSTOM_CLASSES.append((label, key, color))
    save_custom_classes()
    return jsonify({"ok": True, "label": label, "key": key, "color": color})


@app.route("/image/<int:idx>")
def image(idx):
    if idx < 0 or idx >= len(IMAGES):
        abort(404)
    return send_file(TARGET_DIR / IMAGES[idx]["filename"])


@app.route("/api/annotate", methods=["POST"])
def api_annotate():
    data = request.get_json()
    idx = data["index"]
    cls = data.get("class")
    if idx < 0 or idx >= len(IMAGES):
        abort(400)
    filename = IMAGES[idx]["filename"]
    if cls is None:
        ANNOTATIONS.pop(filename, None)
    else:
        if cls not in all_class_labels():
            abort(400)
        ANNOTATIONS[filename] = cls
    save_annotations()
    return jsonify({"ok": True})


INDEX_HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Reef Point Annotator</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body {
    margin: 0; font-family: -apple-system, Segoe UI, Roboto, sans-serif;
    background: #14181c; color: #e8e8e8; height: 100vh; overflow: hidden;
  }
  #layout { display: flex; height: 100vh; }
  #main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
  #topbar {
    padding: 10px 16px; display: flex; align-items: center; gap: 16px;
    background: #1c2126; border-bottom: 1px solid #2b3238; flex-wrap: wrap;
  }
  #topbar h1 { font-size: 15px; margin: 0; font-weight: 600; color: #9fd3c7; }
  #progressWrap { flex: 1; min-width: 160px; }
  #progressBar { height: 8px; background: #2b3238; border-radius: 4px; overflow: hidden; }
  #progressFill { height: 100%; background: #2ecc71; width: 0%; transition: width .15s; }
  #progressText { font-size: 12px; color: #9aa5ab; margin-top: 4px; }
  #imgWrap {
    flex: 1; display: flex; align-items: center; justify-content: center;
    position: relative; background: #0b0d0f; overflow: hidden;
  }
  #imgWrap img { max-width: 100%; max-height: 100%; object-fit: contain; }
  #crosshair {
    position: absolute; top: 50%; left: 50%; width: 28px; height: 28px;
    transform: translate(-50%, -50%); pointer-events: none;
  }
  #crosshair::before, #crosshair::after {
    content: ""; position: absolute; background: rgba(255,60,60,0.85);
  }
  #crosshair::before { top: 50%; left: 0; width: 100%; height: 2px; margin-top: -1px; }
  #crosshair::after { left: 50%; top: 0; height: 100%; width: 2px; margin-left: -1px; }
  #annotatedBadge {
    position: absolute; top: 12px; left: 12px; background: rgba(46, 204, 113, 0.92);
    color: #0e1a12; font-size: 13px; font-weight: 700; padding: 5px 12px;
    border-radius: 999px; display: none;
  }
  #bottombar {
    display: flex; align-items: center; justify-content: center; gap: 10px;
    padding: 10px; background: #1c2126; border-top: 1px solid #2b3238;
  }
  #bottombar button {
    background: #2b3238; color: #e8e8e8; border: none; padding: 8px 16px;
    border-radius: 6px; cursor: pointer; font-size: 13px;
  }
  #bottombar button:hover { background: #384049; }
  #jumpBox { width: 70px; text-align: center; border-radius: 6px; border: 1px solid #3a424a;
    background: #14181c; color: #e8e8e8; padding: 7px; font-size: 13px; }
  #sidebar {
    width: 340px; overflow-y: auto; background: #1c2126; border-left: 1px solid #2b3238;
    padding: 12px;
  }
  .group { margin-bottom: 14px; }
  .group h2 {
    font-size: 11px; text-transform: uppercase; letter-spacing: .05em;
    color: #7f8c8d; margin: 0 0 6px 2px;
  }
  .grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; }
  .grid.narrow { grid-template-columns: repeat(2, 1fr); }
  .clsBtn {
    position: relative; border: 2px solid transparent; border-radius: 6px;
    padding: 8px 6px 6px; font-size: 12px; cursor: pointer;
    text-align: left; line-height: 1.25; min-height: 46px;
  }
  .clsBtn .key {
    position: absolute; top: 2px; right: 4px; font-size: 10px; font-weight: 700;
    background: rgba(0,0,0,0.25); color: #fff; border-radius: 3px; padding: 0 4px;
  }
  .clsBtn.selected { border-color: #fff; box-shadow: 0 0 0 2px #14181c inset; }
  #currentLabel {
    font-size: 13px; padding: 8px 10px; background: #14181c; border-radius: 6px;
    margin-bottom: 10px; min-height: 18px;
  }
  #currentLabel.set { color: #2ecc71; font-weight: 600; }
  #filenameLabel { font-size: 11px; color: #7f8c8d; margin-top: 2px; word-break: break-all; }
  kbd { background: #384049; padding: 1px 5px; border-radius: 3px; font-size: 11px; }
  #addClassBox { display: flex; gap: 6px; margin-bottom: 14px; }
  #newClassInput {
    flex: 1; border-radius: 6px; border: 1px solid #3a424a; background: #14181c;
    color: #e8e8e8; padding: 7px 8px; font-size: 12px; min-width: 0;
  }
  #addClassBtn { background: #2b3238; color: #e8e8e8; border: none; border-radius: 6px;
    padding: 7px 10px; font-size: 12px; cursor: pointer; white-space: nowrap; }
  #addClassBtn:hover { background: #384049; }
  .morphGrid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; margin-bottom: 16px; }
  .morphBtn {
    position: relative; border: 2px solid transparent; border-radius: 8px;
    padding: 14px 8px; font-size: 13px; font-weight: 600; cursor: pointer;
    text-align: center; background: #2b3238; color: #e8e8e8;
  }
  .morphBtn:hover { background: #384049; }
  .morphBtn .key {
    position: absolute; top: 4px; right: 6px; font-size: 10px; font-weight: 700;
    background: rgba(0,0,0,0.25); color: #fff; border-radius: 3px; padding: 0 4px;
  }
  #backBtn {
    display: flex; align-items: center; gap: 6px; background: #2b3238; color: #e8e8e8;
    border: none; border-radius: 6px; padding: 7px 10px; font-size: 12px; cursor: pointer;
    margin-bottom: 12px; width: 100%;
  }
  #backBtn:hover { background: #384049; }
  #step1Label {
    font-size: 11px; text-transform: uppercase; letter-spacing: .05em;
    color: #7f8c8d; margin: 0 0 6px 2px;
  }
</style>
</head>
<body>
<div id="layout">
  <div id="main">
    <div id="topbar">
      <h1>Reef Point Annotator</h1>
      <div id="progressWrap">
        <div id="progressBar"><div id="progressFill"></div></div>
        <div id="progressText"></div>
      </div>
    </div>
    <div id="imgWrap">
      <img id="mainImg" src="">
      <div id="crosshair"></div>
      <div id="annotatedBadge"></div>
    </div>
    <div id="bottombar">
      <button id="prevBtn">&larr; Prev</button>
      <button id="clearBtn">Clear label</button>
      <input id="jumpBox" type="number" min="1">
      <button id="jumpBtn">Go</button>
      <button id="nextUnBtn">Next unlabeled &raquo;</button>
      <button id="nextBtn">Next &rarr;</button>
    </div>
  </div>
  <div id="sidebar">
    <div id="currentLabel">No label</div>
    <div id="addClassBox">
      <input id="newClassInput" placeholder="New class name (if needed)">
      <button id="addClassBtn">+ Add class</button>
    </div>
    <div id="groups"></div>
  </div>
</div>
<script>
let IMAGES = [];
let MORPHOLOGIES = [];
let FLAT_GROUPS = [];
let idx = 0;
let activeMorph = null; // name of the morphology whose genus panel (step 2) is open, or null for step 1

function morphOfLabel(label) {
  for (const m of MORPHOLOGIES) {
    for (const [, items] of m.genera) {
      if (items.some(([l]) => l === label)) return m.name;
    }
  }
  return null;
}

async function load() {
  const res = await fetch('/api/list');
  const data = await res.json();
  IMAGES = data.images;
  MORPHOLOGIES = data.morphologies;
  FLAT_GROUPS = data.flatGroups;
  const firstUnlabeled = IMAGES.findIndex(im => !im.class);
  idx = firstUnlabeled === -1 ? 0 : firstUnlabeled;
  render();
}

async function refreshGroups() {
  const res = await fetch('/api/list');
  const data = await res.json();
  MORPHOLOGIES = data.morphologies;
  FLAT_GROUPS = data.flatGroups;
  render();
}

async function addClass() {
  const input = document.getElementById('newClassInput');
  const label = input.value.trim();
  if (!label) return;
  const res = await fetch('/api/add_class', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({label})
  });
  if (!res.ok) {
    alert('Could not add that class (it may already exist).');
    return;
  }
  input.value = '';
  await refreshGroups();
}

function readableTextColor(hex) {
  const c = hex.replace('#', '');
  const r = parseInt(c.substring(0, 2), 16);
  const g = parseInt(c.substring(2, 4), 16);
  const b = parseInt(c.substring(4, 6), 16);
  const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
  return luminance > 0.55 ? '#14181c' : '#f5f5f5';
}

function addClassGroup(container, groupName, items) {
  const g = document.createElement('div');
  g.className = 'group';
  const h = document.createElement('h2');
  h.textContent = groupName;
  g.appendChild(h);
  const grid = document.createElement('div');
  grid.className = 'grid' + (items.length <= 2 ? ' narrow' : '');
  for (const [label, key, color] of items) {
    const btn = document.createElement('button');
    btn.className = 'clsBtn';
    btn.dataset.label = label;
    btn.style.background = color;
    btn.style.color = readableTextColor(color);
    btn.innerHTML = label + (key ? '<span class="key">' + key.toUpperCase() + '</span>' : '');
    btn.onclick = () => annotate(label);
    grid.appendChild(btn);
  }
  g.appendChild(grid);
  container.appendChild(g);
}

function renderGroups() {
  const container = document.getElementById('groups');
  container.innerHTML = '';

  if (activeMorph) {
    const morph = MORPHOLOGIES.find(m => m.name === activeMorph);
    const back = document.createElement('button');
    back.id = 'backBtn';
    back.innerHTML = '&larr; Back to categories <span class="key">ESC</span>';
    back.onclick = () => { activeMorph = null; renderGroups(); };
    container.appendChild(back);
    for (const [genusName, items] of morph.genera) {
      addClassGroup(container, morph.name + ' – ' + genusName, items);
    }
    return;
  }

  const step1 = document.createElement('div');
  step1.id = 'step1Label';
  step1.textContent = 'Morphology';
  container.appendChild(step1);
  const morphGrid = document.createElement('div');
  morphGrid.className = 'morphGrid';
  for (const m of MORPHOLOGIES) {
    const btn = document.createElement('button');
    btn.className = 'morphBtn';
    btn.innerHTML = m.name + (m.key ? '<span class="key">' + m.key.toUpperCase() + '</span>' : '');
    btn.onclick = () => { activeMorph = m.name; renderGroups(); };
    morphGrid.appendChild(btn);
  }
  container.appendChild(morphGrid);

  for (const [groupName, items] of FLAT_GROUPS) {
    addClassGroup(container, groupName, items);
  }
}

function render() {
  const im = IMAGES[idx];
  document.getElementById('mainImg').src = '/image/' + idx;
  const labeled = IMAGES.filter(i => i.class).length;
  document.getElementById('progressFill').style.width = (100 * labeled / IMAGES.length) + '%';
  document.getElementById('progressText').textContent =
    `Image ${idx + 1} / ${IMAGES.length}  —  ${labeled} labeled (${(100*labeled/IMAGES.length).toFixed(1)}%)`;
  document.getElementById('jumpBox').value = idx + 1;

  const curLabel = document.getElementById('currentLabel');
  const badge = document.getElementById('annotatedBadge');
  if (im.class) {
    curLabel.textContent = im.class;
    curLabel.classList.add('set');
    badge.textContent = '✓ ' + im.class;
    badge.style.display = 'block';
    // Jump the sidebar to whichever step (morphology panel or step 1) actually
    // contains this image's existing label, so it's visible and highlighted.
    activeMorph = morphOfLabel(im.class);
  } else {
    curLabel.textContent = 'No label';
    curLabel.classList.remove('set');
    badge.style.display = 'none';
  }

  renderGroups();
  document.querySelectorAll('.clsBtn').forEach(btn => {
    btn.classList.toggle('selected', btn.dataset.label === im.class);
  });
}

async function annotate(label) {
  const im = IMAGES[idx];
  if (im.class && im.class !== label) {
    const ok = confirm(`This image is already labeled "${im.class}". Change it to "${label}"?`);
    if (!ok) return;
  }
  im.class = label;
  await fetch('/api/annotate', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({index: idx, class: label})
  });
  if (idx < IMAGES.length - 1) idx++;
  render();
}

async function clearLabel() {
  const im = IMAGES[idx];
  im.class = null;
  await fetch('/api/annotate', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({index: idx, class: null})
  });
  render();
}

function goto(n) {
  idx = Math.max(0, Math.min(IMAGES.length - 1, n));
  render();
}

document.getElementById('addClassBtn').onclick = addClass;
document.getElementById('newClassInput').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') { e.stopPropagation(); addClass(); }
});
document.getElementById('prevBtn').onclick = () => goto(idx - 1);
document.getElementById('nextBtn').onclick = () => goto(idx + 1);
document.getElementById('clearBtn').onclick = clearLabel;
document.getElementById('jumpBtn').onclick = () => goto(parseInt(document.getElementById('jumpBox').value, 10) - 1);
document.getElementById('nextUnBtn').onclick = () => {
  let n = IMAGES.findIndex((im, i) => i > idx && !im.class);
  if (n === -1) n = IMAGES.findIndex(im => !im.class);
  if (n !== -1) goto(n);
};

window.addEventListener('keydown', (e) => {
  if (document.activeElement && document.activeElement.tagName === 'INPUT') {
    if (e.key === 'Enter') document.getElementById('jumpBtn').click();
    return;
  }
  if (e.key === 'ArrowRight') { goto(idx + 1); return; }
  if (e.key === 'ArrowLeft') { goto(idx - 1); return; }
  if (e.key === 'Backspace' || e.key === 'Delete') { clearLabel(); return; }
  if (e.key === 'Escape' && activeMorph) { activeMorph = null; renderGroups(); return; }

  if (activeMorph) {
    const morph = MORPHOLOGIES.find(m => m.name === activeMorph);
    const KEY_MAP = {};
    for (const [, items] of morph.genera) for (const [label, key] of items) if (key) KEY_MAP[key.toLowerCase()] = label;
    const label = KEY_MAP[e.key.toLowerCase()];
    if (label) { annotate(label); return; }
    return;
  }

  for (const m of MORPHOLOGIES) {
    if (m.key && m.key.toLowerCase() === e.key.toLowerCase()) { activeMorph = m.name; renderGroups(); return; }
  }
  const KEY_MAP = {};
  for (const [, items] of FLAT_GROUPS) for (const [label, key] of items) if (key) KEY_MAP[key.toLowerCase()] = label;
  const label = KEY_MAP[e.key.toLowerCase()];
  if (label) annotate(label);
});

load();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    URL = "http://127.0.0.1:5057"
    print(f"Loaded {len(IMAGES)} point images from {TARGET_DIR}")
    if SKIPPED_COUNT:
        print(f"Skipped {SKIPPED_COUNT} non-matching files (3D models, etc.)")
    print(f"Annotations will be saved to {CSV_PATH}")
    print(f"Opening {URL} in your browser...")
    threading.Timer(1.0, lambda: webbrowser.open(URL)).start()
    app.run(host="127.0.0.1", port=5057, debug=False)
