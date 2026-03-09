# Windows Setup Guide

## Prerequisites

### Python 3.11+
Download from [python.org](https://python.org) — check **"Add Python to PATH"** during install.
```powershell
python --version  # verify
```

### Git
Download from [git-scm.com](https://git-scm.com).

### FFmpeg (required for MP3 encoding)
```powershell
winget install ffmpeg
```
Or download from [ffmpeg.org](https://ffmpeg.org) and add the `bin/` folder to your PATH manually.

---

## Setup

```powershell
git clone <your-repo-url>
cd openshelf

# Create and activate virtual environment
python -m venv .venv
.venv\Scripts\activate
```

### Install dependencies

Install PyTorch first (avoids conflicts with kokoro):
```powershell
pip install torch --index-url https://download.pytorch.org/whl/cpu
```

Then install the project and all dependencies:
```powershell
pip install -e ".[dev]"
```

> **Why `-e ".[dev]"` instead of `-r requirements.txt`?**
> `pip install -e .` installs the `openshelf` package itself (from `src/`) into your
> environment so imports like `from openshelf.scrapers import http` work anywhere.
> The `-e` flag makes it "editable" — your code changes take effect immediately without
> reinstalling. `[dev]` adds the linter (`ruff`).
> `requirements.txt` only installs third-party libraries and does not register the package.

---

## Environment Variables

Create a `.env` file in the project root with your Cloudflare R2 credentials:
```
R2_ENDPOINT_URL=https://<account-id>.r2.cloudflarestorage.com
R2_ACCESS_KEY_ID=your-access-key
R2_SECRET_ACCESS_KEY=your-secret-key
R2_BUCKET_NAME=your-bucket-name
```

---

## Verify Setup

```powershell
# Run tests (all should pass, no network calls)
python -m unittest discover -s tests -v

# Test the scraper in dry-run mode
python scripts\download-books.py --dry-run --author "Kafka"
```

---

## Windows-Specific Gotchas

| Issue | Fix |
|---|---|
| `python3` command not found | Use `python` instead of `python3` on Windows |
| Long path errors with git | `git config core.longpaths true` |
| Line ending warnings | `git config core.autocrlf true` |
| `.venv\Scripts\activate` blocked by PowerShell | Run `Set-ExecutionPolicy RemoteSigned -Scope CurrentUser` once |
| `ffmpeg` not found by pydub | Ensure FFmpeg `bin/` is in your system PATH, then reopen terminal |
