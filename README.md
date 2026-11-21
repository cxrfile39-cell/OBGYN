# 婦產科次專科每週文獻 OB/GYN Weekly Literature

每週一 06:00（台北時間）自動從 PubMed 擷取婦產科 14 個次專科的最新期刊論文、綜論與臨床指引，並發布到 GitHub Pages。

製作者：**JH CHIU MD · Claude 5.5**

## 功能
- 14 個次專科分類，附「全選／全不選」按鈕
- 文章類型篩選：臨床指引、綜論／系統性回顧、期刊論文（也有全選／全不選）
- 「只看重點期刊」、關鍵字搜尋、依次專科或依日期排列
- 每篇提供摘要、PubMed、DOI 全文、PMC 免費全文連結
- 手機、電腦都可看，支援深色模式

## 發布步驟（只需做一次）
1. 在 GitHub 建立一個 **Public** repository（例如 `obgyn-weekly`），勾選 *Add a README file*。
2. 進入 **Settings → Pages → Build and deployment → Source**，選 **GitHub Actions**。
3. 回到 repo 首頁，**Add file → Upload files**，把本資料夾內所有檔案（含 `.github` 資料夾）拖曳上傳後 Commit 到 `main`。
4. 上傳後 Actions 會**自動執行第一次更新**（不需手動啟動），約 2–5 分鐘後網址為：
   `https://<你的帳號>.github.io/<repo名稱>/`

> 提醒：`.github` 是隱藏資料夾，Mac 請在 Finder 按 `Cmd+Shift+.` 顯示後再拖曳；或直接拖曳整個解壓縮後的資料夾內容。

## 選用設定
在 **Settings → Secrets and variables → Actions** 新增：
- `NCBI_API_KEY`：NCBI API key（免費申請，抓取較快較穩）
- `NCBI_EMAIL`：你的 email（NCBI 建議提供）

## 調整
- 搜尋字串（MeSH／關鍵字）：`scripts/fetch_pubmed.py` 中的 `CATEGORIES`
- 重點期刊清單：`CORE_JOURNALS`
- 更新時間：`.github/workflows/update.yml` 中的 `cron`（UTC 時間）
- 收錄天數、每類篇數：環境變數 `DAYS`（預設 7）、`RETMAX`（預設 60）

## 檔案結構
```
.github/workflows/update.yml   每週排程 + 自動部署
scripts/fetch_pubmed.py        PubMed 擷取（僅用 Python 標準函式庫）
docs/index.html                網頁
docs/data.json                 自動產生的文獻資料
```

分類依 MeSH 與關鍵字自動判定，同一篇可能出現在多個次專科；內容僅供參考，臨床決策請以原文為準。
