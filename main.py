"""
台股持股每日收盤分析報告
排程：每個交易日下午 3:00 (UTC+8) 自動執行
分析報告：發佈至 Notion
推播通知：LINE Messaging API（摘要 + Notion 連結）
LLM：Google Gemini 2.5 Flash
"""

import os
import json
import datetime
import requests
from google import genai

# ====== 設定 ======
STOCK_LIST = ["2454", "2492", "2451", "6488", "2330", "6196", "2344", "7750", "2313"]
GEMINI_MODEL = "gemini-2.5-flash"

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_USER_ID = os.environ.get("LINE_USER_ID", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
NOTION_API_KEY = os.environ.get("NOTION_API_KEY", "")
NOTION_PARENT_PAGE_ID = "32e4a231-c75f-8064-b4bf-e6fd300da9d3"


def fetch_twse_closing(date_str: str) -> dict:
    """從 TWSE 取得當日收盤行情"""
    url = f"https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&date={date_str}&type=ALLBUT0999"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        print(f"TWSE 回應狀態: {resp.status_code}")
        resp.raise_for_status()
        data = resp.json()
        print(f"TWSE stat: {data.get('stat')}, has data9: {'data9' in data}")
    except Exception as e:
        print(f"TWSE 請求失敗: {e}")
        return {}

    stock_data = {}
    if data.get("stat") == "OK" and "data9" in data:
        for row in data["data9"]:
            code = row[0].strip()
            if code in STOCK_LIST:
                stock_data[code] = {
                    "name": row[1].strip(),
                    "volume": row[2].replace(",", ""),
                    "open": row[5].replace(",", ""),
                    "high": row[6].replace(",", ""),
                    "low": row[7].replace(",", ""),
                    "close": row[8].replace(",", ""),
                    "change": row[9].replace(",", ""),
                    "change_pct": row[10].replace(",", "") if len(row) > 10 else "N/A",
                }

    # 若 TWSE 無資料，改用備用 API
    if not stock_data:
        print("TWSE 無資料，嘗試備用來源 (Yahoo Finance)...")
        stock_data = fetch_yahoo_backup()

    return stock_data


def fetch_yahoo_backup() -> dict:
    """備用：透過 Yahoo Finance 取得收盤資料"""
    stock_data = {}
    tw_stocks = {code: f"{code}.TW" for code in STOCK_LIST}

    for code, ticker in tw_stocks.items():
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=1d"
            headers = {"User-Agent": "Mozilla/5.0"}
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code != 200:
                continue
            data = resp.json()
            result = data.get("chart", {}).get("result", [])
            if not result:
                continue
            meta = result[0].get("meta", {})
            quote = result[0].get("indicators", {}).get("quote", [{}])[0]
            prev_close = meta.get("chartPreviousClose", 0)
            close = meta.get("regularMarketPrice", 0)
            stock_data[code] = {
                "name": meta.get("shortName", code),
                "volume": str(quote.get("volume", [0])[-1] if quote.get("volume") else 0),
                "open": str(quote.get("open", [0])[-1] if quote.get("open") else 0),
                "high": str(quote.get("high", [0])[-1] if quote.get("high") else 0),
                "low": str(quote.get("low", [0])[-1] if quote.get("low") else 0),
                "close": str(close),
                "change": f"{close - prev_close:.2f}" if prev_close else "N/A",
                "change_pct": f"{((close - prev_close) / prev_close * 100):.2f}%" if prev_close else "N/A",
            }
        except Exception as e:
            print(f"Yahoo {code} 失敗: {e}")
            continue

    print(f"Yahoo 取得 {len(stock_data)} 檔股票資料")
    return stock_data


def fetch_institutional_trading() -> dict:
    """取得三大法人買賣超"""
    today = datetime.date.today().strftime("%Y%m%d")
    url = f"https://www.twse.com.tw/fund/T86?response=json&date={today}&selectType=ALLBUT0999"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        result = {}
        if data.get("stat") == "OK" and "data" in data:
            for row in data["data"]:
                code = row[0].strip()
                if code in STOCK_LIST:
                    result[code] = {
                        "foreign_buy_sell": row[4].replace(",", "").strip(),
                        "investment_trust_buy_sell": row[10].replace(",", "").strip(),
                        "dealer_buy_sell": row[11].replace(",", "").strip() if len(row) > 11 else "0",
                    }
        return result
    except Exception:
        return {}


def build_analysis_prompt(stock_data: dict, institutional: dict) -> str:
    """建構 LLM 分析提示"""
    today = datetime.date.today().strftime("%Y/%m/%d")
    
    data_section = f"## 今日收盤數據 ({today})\n\n"
    for code, info in stock_data.items():
        inst_info = institutional.get(code, {})
        data_section += f"### {code} {info['name']}\n"
        data_section += f"- 收盤價: {info['close']} | 漲跌: {info['change']} | 成交量: {info['volume']}\n"
        data_section += f"- 開: {info['open']} / 高: {info['high']} / 低: {info['low']}\n"
        if inst_info:
            data_section += f"- 外資買賣超: {inst_info.get('foreign_buy_sell', 'N/A')} 張\n"
            data_section += f"- 投信買賣超: {inst_info.get('investment_trust_buy_sell', 'N/A')} 張\n"
        data_section += "\n"

    prompt = f"""你是一位專精於台股個股挖掘的資深資產管理經理（Portfolio Manager）。
請針對以下持股進行深度剖析報告。

{data_section}

請針對每檔股票提供：

一、即時數據與位階診斷：
- 分析目前相對於 20MA（月線）與 60MA（季線）的位階
- MACD 是否出現黃金/死亡交叉，RSI 是否進入超買或超賣區

二、籌碼面深度掃描：
- 法人動向：外資與投信的買賣超變化趨勢
- 散戶情緒：融資餘額與融券變化判斷

三、基本面與產業催化劑：
- 最近一季營收達成率，相較去年同期成長性（YoY）
- 在目前產業鏈中的角色定位

四、操作建議與情境規劃：
- 關鍵支撐價位與建議分批進場時機
- 技術性停損點與合理獲利目標價
- Beta 係數表現與風險預警

五、市場新聞與事件：
- 近期重大法說會、產品發布或簽約新聞

輸出要求：
- 純文字格式，不要使用 Markdown（不要 **粗體**、不要 # 標題、不要 - 列表符號）
- 用「▶」「◆」「→」「│」等符號區分段落層級
- 數據用【】框起來強調
- 每檔股票結論提供「個股吸引力評分 (1-10分)」
- 最後提供整體持股組合建議
- 段落間用空行分隔，方便閱讀
- 開頭不要有問候語、不要有免責聲明、不要有「尊敬的投資人」等客套話
- 直接從第一檔股票的分析開始
"""
    return prompt


def call_llm_analysis(prompt: str) -> str:
    """呼叫 Gemini 進行深度分析"""
    client = genai.Client(api_key=GEMINI_API_KEY)
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=genai.types.GenerateContentConfig(
            system_instruction="你是專精台股的資深資產管理經理，請以繁體中文回覆。輸出純文字，不使用任何 Markdown 語法。數據須基於事實，如無法確認請明確標註。",
            max_output_tokens=8000,
            temperature=0.3,
        ),
    )
    return response.text


def publish_to_notion(title: str, content: str) -> str:
    """發佈報告至 Notion，回傳頁面 URL"""
    url = "https://api.notion.com/v1/pages"
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }

    # 將內容切成段落 blocks（Notion 單一 rich_text 限 2000 字元）
    paragraphs = content.split("\n")
    blocks = []
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        # 切成 2000 字元以內的 chunks
        chunks = [para[i:i+2000] for i in range(0, len(para), 2000)]
        for chunk in chunks:
            blocks.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": chunk}}]
                }
            })

    # Notion API 一次最多 100 blocks
    blocks = blocks[:100]

    payload = {
        "parent": {"page_id": NOTION_PARENT_PAGE_ID},
        "properties": {
            "title": {
                "title": [{"text": {"content": title}}]
            }
        },
        "children": blocks,
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    if resp.status_code != 200:
        print(f"Notion 發佈失敗: {resp.status_code} {resp.text}")
        raise RuntimeError(f"Notion publish failed: {resp.status_code}")

    page_data = resp.json()
    page_url = page_data.get("url", "")
    print(f"Notion 發佈成功: {page_url}")
    return page_url


def build_line_summary(stock_data: dict, notion_url: str) -> str:
    """建構 LINE 摘要訊息（漲跌一覽 + Notion 連結）"""
    today = datetime.date.today().strftime("%Y/%m/%d")
    lines = [f"📊 持股收盤速報 {today}", ""]

    for code, info in stock_data.items():
        change = info.get("change", "N/A")
        close = info.get("close", "N/A")
        name = info.get("name", code)
        # 漲跌符號
        try:
            change_val = float(change)
            arrow = "🔺" if change_val > 0 else "🔻" if change_val < 0 else "➖"
            change_str = f"+{change}" if change_val > 0 else str(change)
        except (ValueError, TypeError):
            arrow = "➖"
            change_str = change

        pct = info.get("change_pct", "")
        lines.append(f"{arrow} {code} {name} │ {close} ({change_str}) {pct}")

    lines.append("")
    lines.append("📋 完整分析報告：")
    lines.append(notion_url)

    return "\n".join(lines)


def send_line_message(text: str):
    """透過 LINE Messaging API 推播訊息"""
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
    }

    # LINE 單則訊息上限 5000 字，超過則分段
    max_len = 4900
    messages = []
    while text:
        chunk = text[:max_len]
        messages.append({"type": "text", "text": chunk})
        text = text[max_len:]

    payload = {
        "to": LINE_USER_ID,
        "messages": messages[:5],  # LINE 一次最多 5 則
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    if resp.status_code != 200:
        print(f"LINE 推播失敗: {resp.status_code} {resp.text}")
        raise RuntimeError(f"LINE push failed: {resp.status_code}")
    print("LINE 推播成功")


def is_trading_day() -> bool:
    """檢查今天是否為交易日（排除週末）"""
    today = datetime.date.today()
    # 週六=5, 週日=6
    if today.weekday() >= 5:
        return False
    return True


def main():
    print(f"=== 台股收盤分析報告 {datetime.date.today()} ===")

    if not is_trading_day():
        print("今日非交易日，跳過。")
        return

    # 1. 取得收盤數據
    target_date = datetime.date.today()
    date_str = target_date.strftime("%Y%m%d")
    print(f"正在取得 {date_str} 收盤數據...")
    stock_data = fetch_twse_closing(date_str)

    if not stock_data:
        print("無法取得收盤數據（可能尚未結算或非交易日）")
        return

    # 2. 取得三大法人
    print("正在取得三大法人數據...")
    institutional = fetch_institutional_trading()

    # 3. LLM 深度分析
    print("正在進行 AI 深度分析...")
    prompt = build_analysis_prompt(stock_data, institutional)
    analysis = call_llm_analysis(prompt)

    # 4. 發佈至 Notion
    print("正在發佈至 Notion...")
    today_str = datetime.date.today().strftime("%Y/%m/%d")
    notion_title = f"📊 持股收盤報告 {today_str}"
    notion_url = publish_to_notion(notion_title, analysis)

    # 5. LINE 推播摘要 + Notion 連結
    print("正在推播至 LINE...")
    line_msg = build_line_summary(stock_data, notion_url)
    send_line_message(line_msg)

    print("=== 完成 ===")


if __name__ == "__main__":
    main()
