# Archive And Viewer

## Local Archive

Archive recent DanXi content into SQLite:

```powershell
python scripts\archive_danxi.py --hours 24 --base-urls "https://forum.fduhole.com/api"
```

Default paths:

- Database: `data/danxi.sqlite`
- Images: `data/images/`

Run continuously every 10 minutes:

```powershell
python scripts\run_archive_loop.py --interval-minutes 10 --hours 24 --base-urls "https://forum.fduhole.com/api"
```

The archive is incremental. It upserts holes, floors, tags, and image records.
It does not attempt full historical backfill; it preserves content seen during
scheduled runs.

If you are off campus, prefer:

```powershell
python scripts\archive_danxi.py --hours 24 --base-urls "https://forum.fduhole.com/api" --webvpn-mode force
```

This does not require your computer to be on the campus network, but it still
depends on Fudan WebVPN and valid Fudan credentials.

## Local Viewer

Start the read-only API:

```powershell
python scripts\serve_archive.py --db data\danxi.sqlite --image-root data\images --port 8787
```

Start the web UI:

```powershell
cd web
npm install
npm run dev
```

Open:

```text
http://127.0.0.1:5173
```

## GitHub Pages Mode

GitHub Pages should only host the static frontend. Do not commit local archive
data, images, `.env`, or exported zip files.

Export a browser-importable data package:

```powershell
python scripts\export_pages_data.py --db data\danxi.sqlite --out exports\danxi-export.zip
```

Open the Pages site and import the zip package. The data is stored in the
browser's IndexedDB for local viewing.

Pages mode is intentionally static:

- It does not expose your local SQLite archive
- It does not publish your image cache by default
- It is meant to load private export packages that you import in the browser

## Independent Runtime With GitHub Actions

To run the archive independently of your own machine:

1. Add repository secrets:
   - `DANXI_WEBVPN_USERNAME`
   - `DANXI_WEBVPN_PASSWORD`
   - `DANXI_API_TOKEN` (optional)
2. Enable `.github/workflows/archive-export.yml`
3. Let GitHub Actions run the archive on schedule
4. Download `danxi-watch-archive-export` from workflow artifacts
5. Import `exports/danxi-export.zip` into the static frontend

This removes the dependency on your own computer and local campus network, but
it still relies on WebVPN being reachable from GitHub Actions.

## Security

- Keep `DANXI_API_TOKEN` only in `.env` or local environment variables.
- `data/`, `exports/`, and frontend build outputs are ignored by Git.
- The local API is intended for `127.0.0.1`; do not expose it publicly.
