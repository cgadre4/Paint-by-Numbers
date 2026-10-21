# MISSION BRIEFING: HARDWARE-ACCELERATED PAINT-BY-NUMBERS ARCHITECT

## 🎯 OBJECTIVE & ARCHITECTURE
* **Goal:** Build a zero-friction, hardware-accelerated web application that converts images into printable, downloadable paint-by-numbers templates and color swatch palettes.
* **Backend:** Python (`FastAPI`, `uvicorn`, `opencv-python`, `scikit-learn`, `numpy`, `Pillow`).
* **Frontend:** Vanilla HTML5 `<canvas>`, CSS3, and JavaScript (Zero Node.js/npm dependencies).
* **Launcher & Environment:** A Windows `run.bat` script leveraging `uv run` to dynamically manage the virtual environment, resolve dependencies on the fly, start the server, and automatically launch the browser at `http://127.0.0.1:8000`.

---

## 🎮 GUI & THREE-CANVAS PIPELINE

### 1. Viewport Layout (Three-Canvas Synchronized Display)
The application viewport must feature three side-by-side or tabbed interactive `<canvas>` elements with unified zoom/pan controls:
1. **Canvas 1 (Source Image):** Displays the raw uploaded photo.
2. **Canvas 2 (Quantized Preview):** Displays the image after bilateral edge smoothing and K-Means color quantization.
3. **Canvas 3 (Printable Template):** Displays the final white canvas containing only crisp vector-like outlines (contours) and numeric color labels centered in each paintable cell.

### 2. Control Sidebar & Customization Panel
The UI must include a modern, glassmorphic dark-mode control drawer with real-time feedback sliders:
* **Color Quantization ($K$):** Slider ($3$ to $32$ colors) controlling the number of $K$-Means clusters.
* **Bilateral Smoothing Intensity:** Sliders for `Diameter` ($5$–$15$) and `Sigma Color/Space` ($25$–$150$) to blend gradients without destroying crisp edges.
* **Minimum Region Area ($A_{\text{min}}$):** Slider ($10$ to $500$ px) to filter out tiny, unpaintable color artifacts via morphological operations or contour area thresholding.
* **Outline Stroke Thickness:** Slider ($1$ to $4$ px) for line weight on the final template.
* **Number Font Scale:** Slider for label visibility inside small regions.
* **Actions & Export:**
  * `Process Image` (Trigger backend pipeline)
  * `Download Template (PNG/PDF)` (High-DPI printable outline sheet)
  * `Download Palette Key (PNG/PDF)` (Color swatches with corresponding numbers and RGB/Hex values)
  * `Print Ready View` (Optimized high-contrast black-and-white print styling)

---

## 🧠 MEMORY & STATE MANAGEMENT (CRITICAL)
* **External Memory (`status_log.md`):** Treat `status_log.md` as your strict external memory tracker. Read it before any action, and mark completed phases as `[DONE]`[cite: 1, 2].
* **Micro-Batches:** Execute file generation phase-by-phase to prevent context window saturation and compaction loops[cite: 1, 2].
* **Context Pruning:** Write all code directly to disk. Do not dump massive raw code blocks into the active conversational chat[cite: 1, 2].
* **No Waiting for Permission:** Proceed autonomously across implementation phases once the run is initialized[cite: 1, 2].

---

## 🛠 STEP-BY-STEP IMPLEMENTATION PHASES

### Phase 1: Image Processing Backend (`server.py`)
1. Implement FastAPI server with static directory mounting and automatic browser opening via `webbrowser.open("http://127.0.0.1:8000")`.
2. Implement `/api/process` POST endpoint accepting an image file and tuning parameters:
   * **Smoothing:** Apply `cv2.bilateralFilter()` to reduce high-frequency noise while maintaining strong edge boundaries.
   * **Quantization:** Apply `scikit-learn` `KMeans(n_clusters=k)` on the RGB color space to extract dominant palettes.
   * **Region Cleanup:** Use morphological closing (`cv2.morphologyEx`) or connected-component labeling (`cv2.connectedComponentsWithStats`) to merge regions smaller than $A_{\text{min}}$ into the nearest neighbor color.
   * **Outlines & Contours:** Use `cv2.Canny()` or `cv2.findContours()` on each color mask to extract cell boundaries.
   * **Centroid Labeling:** Calculate the spatial center of each region via image moments (`cv2.moments`) or pole-of-inaccessibility for non-convex polygons, and assign the numeric palette index.
3. Return base64-encoded images for Canvas 2 (processed) and Canvas 3 (template), along with the structured palette JSON (Hex, RGB, Color Index).

### Phase 2: Responsive HTML5 & CSS3 Frontend (`static/index.html`, `static/styles.css`)
1. Build the glassmorphic dark-theme UI with responsive grid containers for the 3 canvases.
2. Build the control sidebar with stylized range sliders, live value badges, and a file drag-and-drop dropzone.
3. Build the color palette preview bar showing clickable swatches and their assigned numbers.

### Phase 3: Hardware-Accelerated Interactive Controller (`static/app.js`)
1. Implement synchronized canvas zooming and panning using CSS hardware acceleration (`transform: translate3d(...)`, `will-change: transform`).
2. Wire file upload, slider event listeners, and API fetch calls.
3. Render SVG/Canvas overlays for interactive color highlighting (e.g., clicking Palette Color #3 highlights all regions labeled #3 on the canvas).
4. Implement client-side export functions for downloading the final template and print-ready swatch sheet.

### Phase 4: Single-Click Launcher (`run.bat`)
1. Write a self-contained `run.bat` using `uv run` to execute `server.py` with all required dependencies in a zero-install ephemeral or cached virtual environment.

---

## 📂 REQUIRED FILE & FOLDER STRUCTURE
Create this exact file hierarchy directly in the root of the project directory:
```text
Color Quantization App/
├── static/
│   ├── index.html       # 3-canvas layout, control sidebar, palette grid
│   ├── styles.css       # Dark-mode glassmorphic theme, print stylesheet
│   └── app.js           # 60-120Hz canvas controller, pan/zoom, API bindings
├── server.py            # FastAPI backend, OpenCV K-Means & contour pipeline
├── run.bat              # Single-click uv launch script
├── status_log.md        # External state tracking and completed milestone log
└── mission_briefing.md  # Master architectural specification
