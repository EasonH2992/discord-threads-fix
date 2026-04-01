# Threads Preview Fix Bot 🧵

這是一個專為 Discord 設計的 Threads 預覽修正機器人。它可以自動偵測訊息中的 Threads 連結，並產生美觀、資訊完整的預覽卡片，解決 Discord 原生預覽不穩定或資訊缺失的問題。

## ✨ 主要功能

*   **自動解析連結**：支援 `threads.net/t/...` 與 `threads.net/@user/post/...` 各種格式。
*   **智慧排版**：
    *   **多媒體貼文**：若貼文包含圖片或影片，會以**滿版大圖**形式呈顯（支援多圖輪播的首張）。
    *   **純文字貼文**：若僅有文字，會將作者頭照縮小為 **Thumbnail** 側邊欄，保持頻道簡潔。
*   **文字長度限制**：自動將過長的描述截斷至 150 字，避免洗版。
*   **同步刪除**：若使用者刪除含有連結的原始訊息，機器人也會**自動撤回**對應的預覽。
*   **暴雷偵測**：自動忽略包在 `|| ||` 內部的連結，不主動暴雷。
*   **隱私保護**：機器人回覆預覽時使用 `silent=True`，不會觸發 Ping 提示音。

## 🚀 快速開始 (Docker 部署)

### 1. 準備工作
*   在 [Discord Developer Portal](https://discord.com/developers/applications) 建立機器人。
*   開啟 **Message Content Intent** (重要！否則機器人看不見網址)。
*   將機器人邀請至伺服器。

### 2. 設定環境變數
在專案根目錄建立 `.env` 檔案：
```env
DISCORD_TOKEN=您的_DISCORD_機器人_TOKEN
```

### 3. 使用 Docker Compose 啟動
```bash
docker compose up -d --build
```

## 🛠️ GCP 部署建議 (Debian/Ubuntu)

1. **安裝 Docker & Compose**
2. **上傳程式碼**：建議透過 `git clone` 或是壓縮成 `.7z` 上傳。
3. **啟動**：進入資料夾後執行 `docker compose up -d --build`。
4. **查看 Log**：`docker compose logs -f`。

---

## 👨‍💻 開發資訊
*   **語言**: Python 3.11+
*   **框架**: discord.py, httpx, BeautifulSoup4
*   **模擬身分**: 使用 GoogleBot (UA) 以獲取最精準的 OpenGraph 數據。
