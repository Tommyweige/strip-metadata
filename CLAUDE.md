# StripMetadata

Windows 小程式：無損移除相片與影片的附加資料（EXIF、GPS、C2PA 等）。用語以 `GLOSSARY.md` 為準。

## 開發

- 環境：`.venv`（`python -m pip install -e ".[dev,build,dnd]"`）
- 測試：`.\.venv\Scripts\python -m pytest`；lint：`.\.venv\Scripts\ruff check .`
- 打包：`python scripts\build_exe.py`，再用 `python scripts\smoke_test.py dist\StripMetadata.exe` 實際執行 .exe
- 發版：改 `src/strip_metadata/__init__.py` 的 `__version__`，打 `v*` 標籤推上去，GitHub Actions 會測試、打包、發布

## Agent skills

### Issue tracker

Issues live in this repo's GitHub Issues (`gh` CLI). See `docs/agents/issue-tracker.md`.

### Triage labels

Default five labels: needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
