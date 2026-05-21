"""
一次性腳本：在 Notion 建立「持股清單」資料庫並寫入初始資料
執行後會印出 DATABASE_ID，需將其更新至 main.py
"""
import os
import requests

NOTION_API_KEY = os.environ.get("NOTION_API_KEY", "")
NOTION_PARENT_PAGE_ID = "32e4a231-c75f-8064-b4bf-e6fd300da9d3"

HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}

# 初始持股資料
INITIAL_HOLDINGS = [
    {"code": "2454", "name": "聯發科", "cost": 2190.78},
    {"code": "2492", "name": "華新科", "cost": 143.87},
    {"code": "2451", "name": "創見", "cost": 235.33},
    {"code": "6488", "name": "環球晶", "cost": 540.77},
    {"code": "2330", "name": "台積電", "cost": 2110.20},
    {"code": "6196", "name": "帆宣", "cost": 393.96},
    {"code": "2344", "name": "華邦電", "cost": 123.68},
    {"code": "7750", "name": "鑫宇辰", "cost": 0},
    {"code": "2313", "name": "華通", "cost": 255.36},
]


def create_database():
    """建立持股清單資料庫"""
    url = "https://api.notion.com/v1/databases"
    payload = {
        "parent": {"type": "page_id", "page_id": NOTION_PARENT_PAGE_ID},
        "icon": {"type": "emoji", "emoji": "💼"},
        "title": [{"type": "text", "text": {"content": "持股清單"}}],
        "properties": {
            "代號": {"title": {}},
            "名稱": {"rich_text": {}},
            "持有成本": {"number": {"format": "number"}},
            "啟用": {"checkbox": {}},
        },
    }

    resp = requests.post(url, headers=HEADERS, json=payload, timeout=30)
    if resp.status_code != 200:
        print(f"建立資料庫失敗: {resp.status_code} {resp.text}")
        return None

    db = resp.json()
    db_id = db["id"]
    print(f"✅ 資料庫建立成功！")
    print(f"   Database ID: {db_id}")
    print(f"   URL: {db.get('url', '')}")
    return db_id


def add_holding(db_id: str, code: str, name: str, cost: float):
    """新增一筆持股"""
    url = "https://api.notion.com/v1/pages"
    payload = {
        "parent": {"database_id": db_id},
        "properties": {
            "代號": {"title": [{"text": {"content": code}}]},
            "名稱": {"rich_text": [{"text": {"content": name}}]},
            "持有成本": {"number": cost if cost > 0 else None},
            "啟用": {"checkbox": True},
        },
    }

    resp = requests.post(url, headers=HEADERS, json=payload, timeout=30)
    if resp.status_code != 200:
        print(f"  ❌ {code} {name} 寫入失敗: {resp.status_code}")
        return False
    print(f"  ✅ {code} {name} (成本: {cost})")
    return True


def main():
    if not NOTION_API_KEY:
        print("錯誤：請設定 NOTION_API_KEY 環境變數")
        return

    print("=== 建立 Notion 持股清單資料庫 ===\n")

    # 1. 建立資料庫
    db_id = create_database()
    if not db_id:
        return

    # 2. 寫入初始持股
    print("\n正在寫入持股資料...")
    for h in INITIAL_HOLDINGS:
        add_holding(db_id, h["code"], h["name"], h["cost"])

    print(f"\n=== 完成！===")
    print(f"\n請將以下 ID 更新至 main.py：")
    print(f'NOTION_HOLDINGS_DB_ID = "{db_id}"')


if __name__ == "__main__":
    main()
