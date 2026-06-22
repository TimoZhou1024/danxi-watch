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

## Security

- Keep `DANXI_API_TOKEN` only in `.env` or local environment variables.
- `data/`, `exports/`, and frontend build outputs are ignored by Git.
- The local API is intended for `127.0.0.1`; do not expose it publicly.
