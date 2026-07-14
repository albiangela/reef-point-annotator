# Reef Point Annotator

A local tool for clicking through reef transect point-crop images and
assigning a benthic class to each one. Only images named like
`PT1080_GPAB1439_x2701_y2881.png` are loaded — the `...3DModel...`
context images and the `combined_images/` folder are skipped automatically.

Annotations are saved to `annotations.csv`, written inside the image
folder itself, and updated after every click — safe to close and resume
anytime.

## Running it on Linux/Mac

```
cd annotation_tool
pip install -r requirements.txt
python3 app.py
```

A folder-picker window opens — browse to the folder containing the
point images and select it (or pass the path directly:
`python3 app.py "/path/to/image/folder"`). A browser tab then opens
automatically at `http://127.0.0.1:5057`.

## Running it on Windows

1. **Install Python** (skip if already installed): go to
   https://www.python.org/downloads/, download the installer, and run
   it. On the very first screen, **check the box "Add Python to PATH"**
   before clicking Install — easy to miss but important.
2. **Download this tool**: go to
   https://github.com/albiangela/reef-point-annotator, click the green
   **Code** button, then **Download ZIP**. Unzip it anywhere (e.g. your
   Desktop).
3. **Double-click `run.bat`** inside the unzipped `reef-point-annotator`
   folder.
   - A black window opens briefly and installs the one required
     package (Flask) automatically.
   - A **folder-picker window** then pops up — browse to the folder
     containing the point images you need to annotate (the one with
     files named like `PT1080_GPAB1439_x2701_y2881.png`) and select it.
4. A browser tab opens automatically. If it doesn't, open Chrome or
   Edge and go to `http://127.0.0.1:5057`.
5. Click a class button (or press its keyboard shortcut, shown on each
   button) for each image. It saves and auto-advances to the next one.
6. To stop, close the browser tab and close the black command window
   (or press Ctrl+C in it). Progress is saved in `annotations.csv`
   inside the image folder — reopening later resumes where you left off.

If a class you need isn't in the list, use the "+ Add class" box in
the sidebar to add your own (see below).

## Adding a class that isn't in the list

If you hit a point that doesn't fit any of the built-in classes, type
its name into the "New class name" box in the sidebar and click
**+ Add class**. It's saved to `custom_classes.json` in the image
folder and becomes available (with its own button, and a keyboard
shortcut if one is free) for the rest of the session and any future
one on that same folder.

## Editing the built-in class list

The 32 built-in classes (and their colors/keyboard shortcuts) live in
[`classes.json`](classes.json), not in the code, so you can add, rename,
recolor, or reorder them without touching `app.py`. Structure:

```json
[
  {
    "group": "Acropora",
    "classes": [
      {"label": "Acropora alive", "key": "1", "color": "#2ecc71"}
    ]
  }
]
```

- `key` is the single-character keyboard shortcut (optional — leave it
  `""` for mouse-only classes).
- Labels and keys must each be unique across the whole file; the app
  will refuse to start with a clear error message if they collide.
