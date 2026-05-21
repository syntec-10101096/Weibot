# 📊 台股每日收盤自動分析報告

每個交易日下午 3:00 (台灣時間) 自動執行，透過 LINE 推播持股深度分析報告。

## 持股清單

| 代碼 | 說明 |
|------|------|
| 2454 | 聯發科 |
| 2492 | 華新科 |
| 2451 | 創見資訊 |
| 6488 | 環球晶 |
| 2330 | 台積電 |
| 6196 | 帆宣 |
| 2344 | 華邦電 |
| 7750 | 岱煒 |
| 2313 | 華通 |

## 報告內容

- 收盤價 + 漲跌幅 + 成交量
- 技術面：MA20/MA60 位階、MACD、RSI
- 籌碼面：三大法人買賣超、融資融券
- 基本面：營收 YoY、產業鏈定位
- 操作建議：支撐壓力、停損停利、Beta 風險
- 個股吸引力評分 (1-10)

## 設定步驟

### 1. 建立 GitHub 倉庫

```bash
# 將 stock_report/ 資料夾推到新的 GitHub repo
cd stock_report
git init
git add .
git commit -m "init: 台股每日分析報告自動化"
git remote add origin https://github.com/<your-username>/tw-stock-daily-report.git
git push -u origin main
```

### 2. 設定 LINE Messaging API

1. 前往 [LINE Developers Console](https://developers.line.biz/)
2. 建立 Provider → 建立 **Messaging API Channel**
3. 取得：
   - **Channel Access Token**（長效 token）
   - **Your User ID**（在 Basic Settings 頁面底部）
4. 加入你自己建立的 LINE Bot 為好友

### 3. 設定 GitHub Secrets

在 GitHub repo → Settings → Secrets and variables → Actions 中加入：

| Secret Name | 值 |
|------------|---|
| `GEMINI_API_KEY` | Google AI Studio API Key（免費） |
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE Channel Access Token |
| `LINE_USER_ID` | 你的 LINE User ID（U 開頭） |

### 4. 啟用 GitHub Actions

推送後 Actions 會自動啟用。你也可以到 Actions 頁面手動觸發 `workflow_dispatch` 測試。

## 費用估算

| 項目 | 費用 |
|------|------|
| GitHub Actions | 免費（公開 repo）/ 每月 2000 分鐘（私有 repo） |
| Google Gemini 2.5 Pro | **免費**（每分鐘 60 次 / 每日 1500 次） |
| LINE Messaging API | 免費（每月 500 則推播） |

**每月總成本：NT$0（全免費）**

## 修改持股

編輯 `main.py` 第 14 行：

```python
STOCK_LIST = ["2454", "2492", "2451", "6488", "2330", "6196", "2344", "7750", "2313"]
```

## 手動測試

```bash
export GEMINI_API_KEY="AIza..."
export LINE_CHANNEL_ACCESS_TOKEN="..."
export LINE_USER_ID="U..."
python main.py
```
