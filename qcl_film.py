"""Standalone, stats-independent Film Room for the QCL Streamlit Hub."""

import csv
import io
import os
import shutil
import subprocess
import tempfile
import time

import pandas as pd
import streamlit as st


HUD_REGIONS = {
    "Bottom-center scoreboard": (.22, .76, .78, .99),
    "Bottom-right HUD": (.58, .76, .99, .99),
    "Top-right scoreboard": (.55, .01, .99, .25),
    "Top-left scoreboard": (.01, .01, .45, .25),
}


def _crop(frame, region):
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = region
    return frame[int(y1 * height):int(y2 * height),
                 int(x1 * width):int(x2 * width)]


def _read_text(image):
    import cv2
    if image is None or image.size == 0:
        return []
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    encoded, png = cv2.imencode(".png", gray)
    if not encoded:
        return []
    process = subprocess.run(
        ["tesseract", "stdin", "stdout", "--psm", "6", "tsv"],
        input=png.tobytes(), capture_output=True, timeout=30, check=False,
    )
    if process.returncode:
        raise RuntimeError(process.stderr.decode(errors="replace").strip()
                           or "Tesseract could not read the frame.")
    lines = csv.DictReader(io.StringIO(process.stdout.decode(
        "utf-8", errors="replace")), delimiter="\t")
    return [(line["text"].strip(), float(line["conf"]) / 100)
            for line in lines if line.get("text", "").strip()
            and float(line.get("conf") or -1) >= 45]


def _scan(upload, region, interval, max_samples, scan_table, progress):
    import cv2
    if not shutil.which("tesseract"):
        raise RuntimeError("Tesseract is not installed. Copy packages.txt to your Streamlit repository and restart the app.")
    path = None
    capture = None
    try:
        suffix = os.path.splitext(upload.name)[1].lower()
        if suffix not in {".mp4", ".mov", ".webm", ".m4v"}:
            raise ValueError("Please upload an MP4, MOV, WebM, or M4V video.")
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as video:
            path = video.name
            upload.seek(0)
            shutil.copyfileobj(upload, video)
        capture = cv2.VideoCapture(path)
        if not capture.isOpened():
            raise ValueError("Cannot open this video. Try an H.264 MP4.")
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0)
        count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        if fps <= 0 or count <= 0:
            raise ValueError("This video has no readable frames or frame rate. Try an H.264 MP4.")
        step = max(1, round(fps * interval))
        total = min(max_samples, (count + step - 1) // step)
        rows = []
        started = time.monotonic()
        for sample in range(total):
            frame_number = sample * step
            capture.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
            ok, frame = capture.read()
            if not ok:
                break
            for text, confidence in _read_text(_crop(frame, region)):
                rows.append({"seconds": round(frame_number / fps, 1),
                             "source": "Scoreboard", "text": text,
                             "confidence": round(confidence, 2)})
            done = sample + 1
            remaining = (total - done) * (time.monotonic() - started) / done if done >= 2 else None
            progress(done, total, remaining, None)
        if scan_table:
            capture.set(cv2.CAP_PROP_POS_FRAMES, count - 1)
            ok, frame = capture.read()
            if ok:
                for text, confidence in _read_text(
                        _crop(frame, (.03, .08, .97, .96))):
                    rows.append({"seconds": round((count - 1) / fps, 1),
                                 "source": "Final recap frame", "text": text,
                                 "confidence": round(confidence, 2)})
        return {"rows": rows, "samples": done if total else 0,
                "duration": round(count / fps, 1), "resolution": f"{width}×{height}",
                "backend": "tesseract"}
    finally:
        if capture is not None:
            capture.release()
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


def render():
    st.title("🎬 Film Room")
    st.caption("Upload a video, choose the scoreboard area, and scan for text. This page opens without loading league stats.")
    upload = st.file_uploader("Choose a video", type=["mp4", "mov", "webm", "m4v"],
                              key="qcl_film_upload")
    if upload is None:
        st.info("Upload an H.264 MP4 to get started.")
        st.session_state.pop("qcl_film_result", None)
        return
    identity = (upload.name, upload.size)
    if st.session_state.get("qcl_film_file") != identity:
        st.session_state["qcl_film_file"] = identity
        st.session_state.pop("qcl_film_result", None)
    st.caption(f"{upload.name} · {upload.size / (1024 * 1024):.1f} MB")
    if st.checkbox("Preview video (uses extra data on mobile)", value=False):
        st.video(upload)
    region = st.selectbox("Scoreboard location", list(HUD_REGIONS), key="qcl_film_hud")
    with st.expander("Scan settings"):
        interval = st.slider("Sample every (seconds)", .5, 10.0, 2.0, .5)
        max_samples = st.slider("Maximum samples", 10, 600, 60, 10)
        table = st.checkbox("Also scan the final recap frame", value=True)
    if shutil.which("tesseract"):
        st.caption("Fast OCR is available; no model download is needed.")
    else:
        st.warning("Tesseract is not installed. Copy packages.txt to your Streamlit repository before scanning.")
    if st.button("Scan film", type="primary", use_container_width=True):
        bar = st.progress(0)
        message = st.empty()

        def update(done, total, remaining, note):
            bar.progress(min(1., done / total) if total else 0.)
            eta = f" · about {int(remaining // 60)}m {int(remaining % 60)}s left" if remaining is not None else ""
            message.info(note or f"Scanning {done}/{total} samples{eta}")

        try:
            st.session_state["qcl_film_result"] = _scan(
                upload, HUD_REGIONS[region], interval, max_samples, table, update)
        except Exception as exc:
            st.session_state["qcl_film_result"] = {"error": f"{type(exc).__name__}: {exc}"}
        finally:
            bar.empty()
            message.empty()
    result = st.session_state.get("qcl_film_result")
    if not result:
        return
    if result.get("error"):
        st.error(result["error"])
        return
    st.success(f"Scanned {result['samples']} frames using {result['backend']}.")
    st.caption(f"{result['resolution']} · {result['duration']} seconds")
    if not result["rows"]:
        st.warning("No confident text was found. Try another scoreboard location or scan interval.")
        return
    frame = pd.DataFrame(result["rows"])
    st.dataframe(frame, use_container_width=True, hide_index=True)
    st.download_button("Download OCR CSV", frame.to_csv(index=False), "qcl-film-ocr.csv",
                       mime="text/csv", use_container_width=True)