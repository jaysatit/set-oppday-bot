import os
import re
import datetime
import requests
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

# ดึงกุญแจจาก GitHub Secrets
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
OPPDAY_URL = "https://opportunity-day.setgroup.or.th/th/calendar?type=0"

THAI_MONTHS = {
    1: "ม.ค.", 2: "ก.พ.", 3: "มี.ค.", 4: "เม.ย.",
    5: "พ.ค.", 6: "มิ.ย.", 7: "ก.ค.", 8: "ส.ค.",
    9: "ก.ย.", 10: "ต.ค.", 11: "พ.ย.", 12: "ธ.ค."
}

TIME_SLOTS = [
    "09:15 - 10:00", "10:15 - 11:00", "11:15 - 12:00",
    "13:15 - 14:00", "14:15 - 15:00", "15:15 - 16:00", "16:15 - 17:00"
]

def get_target_dates():
    """เตรียมวันที่สำหรับ วันนี้, พรุ่งนี้ (+1) และ มะรืนนี้ (+2)"""
    tz_bkk = datetime.timezone(datetime.timedelta(hours=7))
    today = datetime.datetime.now(tz_bkk).date()
    targets = {}
    for offset, label in [(0, "🔴 วันนี้"), (1, "📌 พรุ่งนี้ (+1 วัน)"), (2, "📌 ล่วงหน้า (+2 วัน)")]:
        d = today + datetime.timedelta(days=offset)
        date_str_th = f"{d.day} {THAI_MONTHS[d.month]}"
        date_iso = d.strftime("%Y-%m-%d")
        targets[date_iso] = {
            "label": label,
            "date_th": date_str_th,
            "items": []
        }
    return targets

def scrape_oppday():
    targets = get_target_dates()
    captured_json = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # ระบบดักจับ API อัตโนมัติ (ไม่ต้องกด F12 เอง)
        def handle_response(response):
            if "json" in response.headers.get("content-type", "").lower():
                try:
                    data = response.json()
                    captured_json.append(data)
                except Exception:
                    pass

        page.on("response", handle_response)
        page.goto(OPPDAY_URL, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(5000)
        html = page.content()
        browser.close()

    # ชั้นที่ 1: ค้นหาจากข้อมูล API ที่ดักจับได้อัตโนมัติ
    for data in captured_json:
        text_dump = str(data)
        for date_iso, info in targets.items():
            if date_iso in text_dump and isinstance(data, (list, dict)):
                items_list = data if isinstance(data, list) else data.get("data", data.get("items", []))
                if isinstance(items_list, list):
                    for entry in items_list:
                        if isinstance(entry, dict) and date_iso in str(entry):
                            symbol = entry.get("symbol") or entry.get("stockSymbol") or entry.get("companyName")
                            time_str = entry.get("time") or entry.get("startTime") or "ดูเวลาในเว็บ"
                            link = entry.get("url") or entry.get("liveUrl") or OPPDAY_URL
                            if symbol:
                                item_text = f"⏰ <b>{time_str}</b> | หุ้น <b>{symbol}</b> 👉 <a href='{link}'>คลิกรับชม</a>"
                                if item_text not in info["items"]:
                                    info["items"].append(item_text)

    # ชั้นที่ 2: กวาดจากตารางหน้าเว็บโดยตรง (กรณี API ไม่ส่งรูปแบบมาตรฐาน)
    soup = BeautifulSoup(html, "html.parser")
    for date_iso, info in targets.items():
        if info["items"]:
            continue  # ถ้าได้ข้อมูลจากชั้นที่ 1 แล้วให้ข้ามเลย
        
        date_th = info["date_th"]
        # หา Element ที่มีวันที่ภาษาไทย เช่น "11 พ.ย."
        matched_nodes = soup.find_all(string=re.compile(rf"\b{re.escape(date_th)}\b"))
        for node in matched_nodes:
            container = node.find_parent("tr") or node.find_parent("div", class_=re.compile(r"row|calendar|day|item", re.I))
            if not container:
                continue

            links = container.find_all("a", href=True)
            for idx, a_tag in enumerate(links):
                href = a_tag["href"]
                if not href.startswith("http"):
                    href = "https://opportunity-day.setgroup.or.th" + href
                
                box_text = a_tag.find_parent().get_text(" ", strip=True)
                box_text = box_text.replace("รับชมออนไลน์", "").strip()
                slot = TIME_SLOTS[idx] if idx < len(TIME_SLOTS) else "ตามตาราง"
                
                if box_text:
                    item_text = f"⏰ <b>{slot}</b> | <b>{box_text}</b> 👉 <a href='{href}'>รับชมออนไลน์</a>"
                    if item_text not in info["items"]:
                        info["items"].append(item_text)

    return targets

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise ValueError("กรุณาตั้งค่า TELEGRAM_BOT_TOKEN และ TELEGRAM_CHAT_ID ใน GitHub Secrets ก่อนครับ")
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    res = requests.post(url, json=payload, timeout=20)
    res.raise_for_status()

def main():
    schedule = scrape_oppday()
    lines = ["📢 <b>อัปเดตตารางหุ้น SET Opportunity Day</b>\n"]
    
    for _, info in schedule.items():
        lines.append(f"{info['label']} <b>({info['date_th']})</b>")
        if info["items"]:
            for item in info["items"]:
                lines.append(f"• {item}")
        else:
            lines.append("• <i>ไม่มีกำหนดการนำเสนอในวันนี้</i>")
        lines.append("")

    lines.append(f"🔗 <a href='{OPPDAY_URL}'>เปิดดูตารางปฏิทิน SET OPPDAY ฉบับเต็ม</a>")
    send_telegram("\n".join(lines))

if __name__ == "__main__":
    main()
