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

勾選「只檢查，不修改」可以先看有多少檔案需要清除。有檔案失敗（例如正被播放器開著）時，關掉那個程式後按「重試失敗的檔案」。紀錄檔在 `%LOCALAPPDATA%\StripMetadata\logs`。

> 注意：預設會直接覆蓋原檔。想保留原檔，請在「進階選項」勾選「另存到新資料夾」。

### 進階選項

按「進階選項 ▸」展開。勾選會記住，下次開啟沿用。

- **保留照片旋轉方向**（預設開）：避免手機照片清完後轉歪。
- **保留 AI 工作流程**（預設關）：保留 ComfyUI、A1111 等存在 PNG 裡的 prompt／工作流程，其他資料照樣清除。
- **另存到新資料夾**（預設關）：原檔不動，清好的檔案存到旁邊的「資料夾名_已清除」，子資料夾結構不變，已存在的同名檔會被覆蓋。開啟時主畫面會顯示提示。
- **保留檔案日期**（預設開）：修改時間維持原樣；關掉則改成清除的時間。
- **自動檢查新版本**（預設開）：見下方〈隱私〉。
- **加入右鍵選單／移除右鍵選單**：在檔案總管對資料夾、資料夾空白處、相片或影片按右鍵，就有「清除相片資訊」。只寫入目前使用者的登錄，不需要系統管理員權限；一次選多個檔案只會開一個視窗。

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

`--dry-run` 只檢查，`--drop-orientation` 連旋轉方向一起移除，`--keep-workflow` 保留 AI 工作流程，`--copy` 另存到「資料夾名_已清除」，`--new-dates` 不保留檔案日期，`--log <檔案>` 指定紀錄檔。

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
- **Release**（`.github/workflows/release.yml`）：推送 `v*` 標籤時，先跑完整 CI，再檢查標籤和程式版本一致，打包 .exe；設定好 SignPath 後會送出簽章請求、等人工核准，換成簽好的 .exe 再驗證簽章；接著打包含 ffmpeg 的 zip，兩者都做冒煙測試，最後附上 SHA256 建立 GitHub Release。
- **SignPath 設定**：repo 的 Settings → Secrets and variables → Actions，新增 secret `SIGNPATH_API_TOKEN`，以及 variables `SIGNPATH_ORGANIZATION_ID`、`SIGNPATH_PROJECT_SLUG`、`SIGNPATH_SIGNING_POLICY_SLUG`。沒設定時發布照常進行，只是 .exe 沒有簽章。

發新版：

```powershell
# 1. 改 src/strip_metadata/__init__.py 的 __version__
git commit -am "Release v0.2.0"
git tag v0.2.0
git push origin main --tags
```

## 隱私

這個程式不收集、不上傳任何資料；所有檔案都在你的電腦上處理。

唯一的網路連線是「自動檢查新版本」：開啟程式時最多每天一次，向 GitHub（`api.github.com`）讀取本專案最新的版本號，不送出任何檔案、路徑或個人資料。可以在「進階選項」取消勾選「自動檢查新版本」關掉。GitHub 的資料處理方式見 [GitHub 隱私聲明](https://docs.github.com/site-policy/privacy-policies/github-general-privacy-statement)。

## Code signing policy

Free code signing provided by [SignPath.io](https://about.signpath.io), certificate by [SignPath Foundation](https://signpath.org).

- Committers and reviewers: [Tommyweige](https://github.com/Tommyweige)
- Approvers: [Tommyweige](https://github.com/Tommyweige)

只有從本 repo 原始碼、經 GitHub Actions 建置的 Release 檔案會被簽章，每一次簽章都需要人工核准。

## 授權

MIT。含 ffmpeg 的 zip 內附 ffmpeg（GPL），授權和原始碼連結在 `ffmpeg` 資料夾。
