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
STOCK_NAMES = {
    "2454": "聯發科", "2492": "華新科", "2451": "創見",
    "6488": "環球晶", "2330": "台積電", "6196": "帆宣",
    "2344": "華邦電", "7750": "鑫宇辰", "2313": "華通",
}
STOCK_COSTS = {
    "2454": 2190.78, "2492": 143.87, "2451": 235.33,
    "6488": 540.77, "2330": 2110.20, "6196": 393.96,
    "2344": 123.68, "7750": 0, "2313": 255.36,
}
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
                "name": STOCK_NAMES.get(code, meta.get("shortName", code)),
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
        cost = STOCK_COSTS.get(code, 0)
        try:
            close_val = float(info['close'])
            pnl_pct = ((close_val - cost) / cost * 100) if cost > 0 else 0
            pnl_str = f"{pnl_pct:+.2f}%"
        except (ValueError, TypeError):
            pnl_str = "N/A"
        data_section += f"### {code} {info['name']}\n"
        data_section += f"- 收盤價: {info['close']} | 漲跌: {info['change']} | 成交量: {info['volume']}\n"
        data_section += f"- 開: {info['open']} / 高: {info['high']} / 低: {info['low']}\n"
        data_section += f"- 持有成本: {cost:.2f} | 目前損益: {pnl_str}\n"
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

四、持有成本與操作建議：
- 根據我的持有成本，分析目前是獲利或套牢狀態
- 若獲利中：是否到達合理停利點？建議部分獲利了結或繼續持有？
- 若套牢中：建議攤平策略（加碼價位）或停損出場？
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


def publish_to_notion(title: str, content: str, stock_data: dict, institutional: dict) -> str:
    """發佈報告至 Notion，使用豐富格式，回傳頁面 URL"""
    url = "https://api.notion.com/v1/pages"
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }

    blocks = []

    # === 收盤數據總覽 ===
    blocks.append(_heading2("📈 收盤數據總覽"))

    # 表格：代號 | 名稱 | 收盤價 | 漲跌 | 漲跌% | 成本 | 損益% | 成交量
    table_rows = []
    # Header row
    table_rows.append({
        "type": "table_row",
        "table_row": {"cells": [
            [{"type": "text", "text": {"content": "代號"}}],
            [{"type": "text", "text": {"content": "名稱"}}],
            [{"type": "text", "text": {"content": "收盤價"}}],
            [{"type": "text", "text": {"content": "漲跌"}}],
            [{"type": "text", "text": {"content": "漲跌%"}}],
            [{"type": "text", "text": {"content": "持有成本"}}],
            [{"type": "text", "text": {"content": "損益%"}}],
            [{"type": "text", "text": {"content": "成交量"}}],
        ]}
    })
    for code, info in stock_data.items():
        # 漲跌顏色：紅漲綠跌
        change_str = info.get("change", "")
        change_pct_str = info.get("change_pct", "")
        try:
            change_val = float(change_str)
            change_color = "red" if change_val > 0 else "green" if change_val < 0 else "default"
        except (ValueError, TypeError):
            change_color = "default"

        # 持有成本與損益
        cost = STOCK_COSTS.get(code, 0)
        cost_str = f"{cost:.2f}" if cost > 0 else "N/A"
        try:
            close_val = float(info.get("close", "0"))
            pnl_pct = ((close_val - cost) / cost * 100) if cost > 0 else 0
            pnl_str = f"{pnl_pct:+.2f}%"
            pnl_color = "red" if pnl_pct > 0 else "green" if pnl_pct < 0 else "default"
        except (ValueError, TypeError):
            pnl_str = "N/A"
            pnl_color = "default"

        table_rows.append({
            "type": "table_row",
            "table_row": {"cells": [
                [{"type": "text", "text": {"content": code}}],
                [{"type": "text", "text": {"content": info.get("name", "")}}],
                [{"type": "text", "text": {"content": info.get("close", "")}}],
                [{"type": "text", "text": {"content": change_str}, "annotations": {"color": change_color}}],
                [{"type": "text", "text": {"content": change_pct_str}, "annotations": {"color": change_color}}],
                [{"type": "text", "text": {"content": cost_str}}],
                [{"type": "text", "text": {"content": pnl_str}, "annotations": {"color": pnl_color}}],
                [{"type": "text", "text": {"content": info.get("volume", "")}}],
            ]}
        })

    blocks.append({
        "type": "table",
        "table": {
            "table_width": 8,
            "has_column_header": True,
            "has_row_header": False,
            "children": table_rows,
        }
    })

    # === 三大法人動向 ===
    if institutional:
        blocks.append(_divider())
        blocks.append(_heading2("🏦 三大法人買賣超"))
        inst_rows = [{
            "type": "table_row",
            "table_row": {"cells": [
                [{"type": "text", "text": {"content": "代號"}}],
                [{"type": "text", "text": {"content": "外資"}}],
                [{"type": "text", "text": {"content": "投信"}}],
                [{"type": "text", "text": {"content": "自營商"}}],
            ]}
        }]
        for code, inst in institutional.items():
            name = stock_data.get(code, {}).get("name", code)
            inst_rows.append({
                "type": "table_row",
                "table_row": {"cells": [
                    [{"type": "text", "text": {"content": f"{code} {name}"}}],
                    [{"type": "text", "text": {"content": inst.get("foreign_buy_sell", "N/A")}}],
                    [{"type": "text", "text": {"content": inst.get("investment_trust_buy_sell", "N/A")}}],
                    [{"type": "text", "text": {"content": inst.get("dealer_buy_sell", "N/A")}}],
                ]}
            })
        blocks.append({
            "type": "table",
            "table": {
                "table_width": 4,
                "has_column_header": True,
                "has_row_header": False,
                "children": inst_rows,
            }
        })

    # === AI 深度分析 ===
    blocks.append(_divider())
    blocks.append(_heading2("🤖 AI 深度分析"))

    # 將分析內容轉為 Notion blocks
    analysis_blocks = _parse_analysis_to_blocks(content)
    blocks.extend(analysis_blocks)

    # Notion API 一次最多 100 blocks，超過則分批追加
    first_batch = blocks[:100]
    remaining = blocks[100:]

    payload = {
        "parent": {"page_id": NOTION_PARENT_PAGE_ID},
        "icon": {"type": "emoji", "emoji": "📊"},
        "properties": {
            "title": {
                "title": [{"text": {"content": title}}]
            }
        },
        "children": first_batch,
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    if resp.status_code != 200:
        print(f"Notion 發佈失敗: {resp.status_code} {resp.text}")
        raise RuntimeError(f"Notion publish failed: {resp.status_code}")

    page_data = resp.json()
    page_id = page_data.get("id", "")
    page_url = page_data.get("url", "")

    # 分批追加剩餘 blocks
    while remaining:
        batch = remaining[:100]
        remaining = remaining[100:]
        append_url = f"https://api.notion.com/v1/blocks/{page_id}/children"
        append_resp = requests.patch(
            append_url, headers=headers,
            json={"children": batch}, timeout=30
        )
        if append_resp.status_code != 200:
            print(f"Notion 追加 blocks 失敗: {append_resp.status_code} {append_resp.text}")
            break
        print(f"Notion 追加 {len(batch)} blocks 成功")

    print(f"Notion 發佈成功 (共 {len(blocks)} blocks): {page_url}")
    return page_url


def _heading2(text: str) -> dict:
    return {
        "type": "heading_2",
        "heading_2": {"rich_text": [{"type": "text", "text": {"content": text}}]}
    }


def _heading3(text: str) -> dict:
    return {
        "type": "heading_3",
        "heading_3": {"rich_text": [{"type": "text", "text": {"content": text}}]}
    }


def _divider() -> dict:
    return {"type": "divider", "divider": {}}


def _paragraph(text: str) -> dict:
    return {
        "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": text}}]}
    }


def _callout(text: str, emoji: str = "💡") -> dict:
    return {
        "type": "callout",
        "callout": {
            "icon": {"type": "emoji", "emoji": emoji},
            "rich_text": [{"type": "text", "text": {"content": text}}],
        }
    }


def _parse_analysis_to_blocks(content: str) -> list:
    """智慧解析 LLM 分析內容為 Notion blocks，平衡可讀性與 block 數量"""
    import re
    blocks = []
    lines = content.split("\n")
    pending_lines = []  # 累積一般段落文字

    def flush_pending():
        """將累積的一般段落合併為 paragraph blocks（每段 ≤ 2000 字）"""
        if pending_lines:
            text = "\n".join(pending_lines)
            while text:
                chunk = text[:2000]
                blocks.append(_paragraph(chunk))
                text = text[2000:]
            pending_lines.clear()

    # 辨識段落標題模式
    section_pattern = re.compile(r'^[一二三四五六七八九十]+[、．.]')
    stock_code_pattern = re.compile(r'^\d{4}\s')

    for line in lines:
        line = line.strip()
        if not line:
            flush_pending()
            continue

        # ▶ 開頭 → heading_3（個股標題）
        if line.startswith("▶"):
            flush_pending()
            blocks.append(_heading3(line.lstrip("▶").strip()))
        # ◆ 開頭 → callout
        elif line.startswith("◆"):
            flush_pending()
            blocks.append(_callout(line.lstrip("◆").strip(), "💡"))
        # 中文數字段落標題（一、即時數據... 二、籌碼面...）
        elif section_pattern.match(line):
            flush_pending()
            blocks.append(_heading3(line))
        # 純股票代號 + 名稱（短行，作為個股分隔標題）
        elif stock_code_pattern.match(line) and len(line) < 30:
            flush_pending()
            blocks.append(_heading3(line))
        # 含「評分」或「吸引力」→ 星星 callout
        elif "評分" in line or "吸引力" in line:
            flush_pending()
            blocks.append(_callout(line, "⭐"))
        # 含「組合建議」或「整體」→ 摘要 callout
        elif "組合建議" in line or "整體建議" in line:
            flush_pending()
            blocks.append(_callout(line, "📋"))
        # 一般段落：累積（但超過 1500 字就先 flush）
        else:
            pending_lines.append(line)
            if sum(len(l) for l in pending_lines) > 1500:
                flush_pending()

    flush_pending()
    return blocks


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
            arrow = "⬆️" if change_val > 0 else "⬇️" if change_val < 0 else "➖"
            change_str = f"+{change}" if change_val > 0 else str(change)
        except (ValueError, TypeError):
            arrow = "➖"
            change_str = change

        # 持有損益
        cost = STOCK_COSTS.get(code, 0)
        try:
            close_val = float(close)
            pnl_pct = ((close_val - cost) / cost * 100) if cost > 0 else 0
            pnl_icon = "📈" if pnl_pct > 0 else "📉" if pnl_pct < 0 else ""
            pnl_str = f" {pnl_icon}{pnl_pct:+.1f}%" if cost > 0 else ""
        except (ValueError, TypeError):
            pnl_str = ""

        pct = info.get("change_pct", "")
        lines.append(f"{arrow} {code} {name} │ {close} ({change_str}) {pct}{pnl_str}")

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
    notion_url = publish_to_notion(notion_title, analysis, stock_data, institutional)

    # 5. LINE 推播摘要 + Notion 連結
    print("正在推播至 LINE...")
    line_msg = build_line_summary(stock_data, notion_url)
    send_line_message(line_msg)

    print("=== 完成 ===")


if __name__ == "__main__":
    main()
