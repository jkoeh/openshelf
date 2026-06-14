# Windows Setup Guide

## Prerequisites

### Python 3.13
Use **uv** to manage Python versions (recommended over installing from python.org directly).

Install uv (run in PowerShell):
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

uv installs to `C:\Users\<you>\.local\bin` which may not be in PATH. Add it permanently:
```powershell
[Environment]::SetEnvironmentVariable("PATH", [Environment]::GetEnvironmentVariable("PATH", "User") + ";$env:USERPROFILE\.local\bin", "User")
```

**Restart your terminal**, then install Python 3.13:
```powershell
uv python install 3.13
uv python pin 3.13
```

> **Why not Python 3.14?** Several dependencies (kokoro → thinc → blis) don't have pre-built
> wheels for Python 3.14 yet and require a C compiler to build from source. Python 3.13 has
> full wheel coverage for all dependencies.

### Git
Download from [git-scm.com](https://git-scm.com).

### FFmpeg (required for MP3 encoding)
```powershell
winget install ffmpeg
```

winget installs FFmpeg but does **not** add it to PATH automatically. Add it permanently:
```powershell
# Find where winget put it
Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Recurse -Filter "ffmpeg.exe" | Select-Object FullName

# Then add that bin/ folder to PATH (replace the path below with what you found above)
[Environment]::SetEnvironmentVariable("PATH", [Environment]::GetEnvironmentVariable("PATH", "User") + ";C:\Users\<you>\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.0.1-full_build\bin", "User")
```

> **Why not `setx`?** `setx` silently truncates PATH at 1024 characters.
> `[Environment]::SetEnvironmentVariable` writes directly to the registry with no limit.

Restart your terminal, then verify:
```powershell
where.exe ffmpeg  # should print the path
```

---

## Setup

```powershell
git clone <your-repo-url>
cd openshelf

# Create and activate virtual environment
uv venv
.venv\Scripts\activate
```

### Install dependencies

Install PyTorch with CUDA support first (for NVIDIA GPU acceleration):
```powershell
# NVIDIA GPU (CUDA 12.6 — works with driver CUDA 13.x too)
uv pip install torch --index-url https://download.pytorch.org/whl/cu126

# CPU only (no GPU)
uv pip install torch --index-url https://download.pytorch.org/whl/cpu
```

Verify GPU is detected:
```powershell
python -c "import torch; print(torch.__version__); print('CUDA:', torch.cuda.is_available())"
```

Then install the project and all dependencies:
```powershell
uv pip install -e ".[dev]"
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
python pipeline\scripts\openshelf-pipeline.py books download --dry-run --author "Kafka"
```

Start the worker and client together with the Windows-safe npm script:
```powershell
npm run dev:window
```

This starts Wrangler in local mode, so it does not require `wrangler login`.

To run local Worker code against the real remote R2 bucket instead of local
Miniflare R2, log in to Wrangler once and use the remote-R2 variant:
```powershell
npm --prefix worker exec wrangler login
npm run dev:window:r2
```

---

## Windows-Specific Gotchas

| Issue | Fix |
|---|---|
| `python3` command not found | Use `python` instead of `python3` on Windows |
| Long path errors with git | `git config core.longpaths true` |
| Line ending warnings | `git config core.autocrlf true` |
| `.venv\Scripts\activate` blocked by PowerShell | Run `Set-ExecutionPolicy RemoteSigned -Scope CurrentUser` once |
| `ffmpeg` not found | Use `[Environment]::SetEnvironmentVariable` to add to PATH — not `setx` (has 1024-char limit) |
| `uv` not found after install | Add `%USERPROFILE%\.local\bin` to PATH via `[Environment]::SetEnvironmentVariable` |
| kokoro/blis fails to build | You're on Python 3.14+ — downgrade to 3.13 via `uv python pin 3.13` |
| torch shows `+cpu` / CUDA not available | Reinstall torch with the correct index URL: `uv pip install torch --index-url https://download.pytorch.org/whl/cu126 --reinstall` |
