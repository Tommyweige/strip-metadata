# StripMetadata — 清除相片與影片資訊

一個 Windows 小程式：把資料夾（含所有子資料夾）或檔案拖進視窗，就會移除相片和影片裡的 EXIF、GPS、相機型號、拍攝時間、XMP，以及 AI 產圖工具嵌入的 C2PA「內容憑證」。

- **畫質不變**：圖片只拿掉資料區塊，像素資料逐位元組保留；影片用 ffmpeg 直接搬移串流，不重新編碼。
- **不會轉歪**：保留照片旋轉方向、色彩描述檔、影片旋轉方向。
- **保留檔案日期**：修改時間維持原樣。
- 可重複執行，已經乾淨的檔案會跳過。

## 下載

到 [Releases](../../releases/latest) 下載：

| 檔案 | 說明 |
|---|---|
| `StripMetadata-vX.Y.Z-with-ffmpeg.zip` | 解壓後執行 `StripMetadata.exe`，相片和影片都能處理（建議） |
| `StripMetadata-vX.Y.Z.exe` | 單一檔案，只處理相片；電腦已安裝 ffmpeg 時也能處理影片 |

Windows 第一次開啟可能出現「Windows 已保護您的電腦」，按「其他資訊」→「仍要執行」。

## 使用

1. 開啟 `StripMetadata.exe`
2. 把資料夾或檔案拖進視窗，或按「選擇資料夾…」／「選擇檔案…」
3. 確認找到的數量後按「確定」

也可以直接把資料夾拖到 `StripMetadata.exe` 的圖示上。

勾選「只檢查，不修改」可以先看有多少檔案需要清除。紀錄檔在 `%LOCALAPPDATA%\StripMetadata\logs`。

> 注意：檔案會直接被覆蓋。ComfyUI 存在 PNG 裡的工作流程也會一起被清掉。

### 支援格式

| 類型 | 格式 | 移除內容 |
|---|---|---|
| 相片 | JPEG | EXIF、XMP、IPTC、C2PA（APP11）、註解 |
| 相片 | PNG | tEXt / zTXt / iTXt 文字、eXIf、時間、C2PA（caBX） |
| 相片 | WebP | EXIF、XMP、C2PA |
| 影片 | MP4 / MOV / M4V / MKV / WebM / AVI / 3GP | 標題、地點、建立時間等標籤、章節、GPS 等資料串流 |

HEIC、TIFF、GIF、RAW 不處理，完成時會列出數量。

## 命令列

```powershell
StripMetadata.exe --cli "D:\Photos" --yes
```

`--dry-run` 只檢查，`--drop-orientation` 連旋轉方向一起移除，`--log <檔案>` 指定紀錄檔。

## 開發

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev,build,dnd]"
.\.venv\Scripts\python -m pytest
.\.venv\Scripts\ruff check .
.\.venv\Scripts\python scripts\build_exe.py          # 產生 dist\StripMetadata.exe
.\.venv\Scripts\python scripts\smoke_test.py dist\StripMetadata.exe
```

### CI / CD

- **CI**（`.github/workflows/ci.yml`）：每次 push 到 `main` 和每個 PR，都在 Windows 上用 Python 3.10、3.12 跑 lint 和測試，接著打包 .exe、實際執行 .exe 做冒煙測試，並把 .exe 存成 artifact。
- **Release**（`.github/workflows/release.yml`）：推送 `v*` 標籤時，先跑完整 CI，再檢查標籤和程式版本一致，打包 .exe 和含 ffmpeg 的 zip，兩者都做冒煙測試，最後附上 SHA256 建立 GitHub Release。

發新版：

```powershell
# 1. 改 src/strip_metadata/__init__.py 的 __version__
git commit -am "Release v0.2.0"
git tag v0.2.0
git push origin main --tags
```

## 授權

MIT。含 ffmpeg 的 zip 內附 ffmpeg（GPL），授權和原始碼連結在 `ffmpeg` 資料夾。
