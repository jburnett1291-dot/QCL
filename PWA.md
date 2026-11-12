# QCL Mobile PWA

This is a separate, static mobile companion for QCL. It provides an installable home-screen entry, the public announcements in `qcl_news.json`, and an offline app shell. The existing Streamlit app and Discord bot remain unchanged.

## Current scope

- Shows QCL announcements from the repository's `qcl_news.json`.
- Caches only the static PWA shell and that public news file.
- Does not cache league stats, account pages, registration data, or any authenticated Streamlit content.
- Opens the full Streamlit hub only after its real HTTPS URL is added to `qcl-pwa-config.js`.
- Does not yet provide native standings, player search, or offline statistics.

## Configure the full-hub link

Edit `qcl-pwa-config.js` and set `hubUrl` to the real deployed QCL site address. Leave it blank until that address is known. Only an HTTPS URL is accepted by the app.

## Run locally

From the repository root, serve the files over localhost:

```sh
python -m http.server 8000
```

Open `http://localhost:8000/` in a browser. Service workers and installation prompts require HTTPS or localhost; opening `index.html` directly as a `file://` URL will not work.

## Publish

The PWA files are at the repository root so they can be served as a static GitHub Pages site without changing how `streamlit run app.py` works. To publish, first configure the real hub URL, then enable GitHub Pages for the repository with the `main` branch and `/ (root)` as the source. This makes the static site publicly reachable at the Pages URL.

After publication:

- Android and desktop browsers can offer **Install QCL**.
- On iPhone or iPad, open the site in Safari, tap **Share**, then **Add to Home Screen**.
- The PWA needs HTTPS to install and cache its shell.

Publishing is intentionally separate from merging these source files. Review the public site and its URL before enabling Pages.
