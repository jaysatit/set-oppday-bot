import os
import re
import html
import datetime
import requests
from playwright.sync_api import sync_playwright

# ดึงกุญแจจาก GitHub Secrets
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

HOME_URL = "https://opportunity-day.setgroup.or.th/th/"

THAI_MONTHS_FULL = {
    1: "มกราคม", 2: "กุมภาพันธ์", 3: "มีนาคม", 4: "เมษายน",
    5: "พฤษภาคม", 6: "มิถุนายน", 7: "กรกฎาคม", 8: "สิงหาคม",
    9: "กันยายน", 10: "ตุลาคม", 11: "พฤศจิกายน", 12: "ธันวาคม"
}

def extract_right_panel_events(page):
    """ดึงหัวข้อวันที่และรายชื่อหุ้นเฉพาะที่อยู่ใต้หัวข้อ 'รายการกิจกรรม ...' (ไม่เอา Weekly Highlights ด้านบน)"""
    try:
        return page.evaluate("""() => {
            // 1. ค้นหาตำแหน่งของหัวข้อ 'รายการกิจกรรม ...' บนหน้าจอ
            const allElements = Array.from(document.querySelectorAll('h1, h2, h3, h4, h5, div, span, p'));
            let headerText = "";
            let headerTop = 0;

            for (const el of allElements) {
                const txt = (el.innerText || "").trim();
                if (txt.startsWith("รายการกิจกรรม") && txt.length < 80 && el.children.length === 0) {
                    headerText = txt;
                    headerTop = el.getBoundingClientRect().top;
                    break;
                }
            }

            const timeRegex = /(\\d{2}:\\d{2}\\s*[-–]\\s*\\d{2}:\\d{2})/;
            // บังคับว่าชื่อย่อหุ้นต้องขึ้นต้นด้วยตัวอักษรภาษาอังกฤษ A-Z เท่านั้น (เพื่อไม่ให้จับตัวเลขเวลา 09:15)
            const companyRegex = /^([A-Z][A-Z0-9&*+-]{0,14})\\s*:\\s*(.+)$/;

            const cards = Array.from(document.querySelectorAll('div, li, article'));
            const symbols = [];
            const seenSymbols = new Set();

            for (const card of cards) {
                // ข้ามการ์ดที่อยู่สูงกว่าหัวข้อ 'รายการกิจกรรม' (เช่น การ์ด GGC ใน Weekly Highlights ด้านบน)
                const rect = card.getBoundingClientRect();
                if (headerTop > 0 && rect.top < headerTop - 10) {
                    continue;
                }

                const text = (card.innerText || "").trim();
                if (!timeRegex.test(text)) continue;

                // เอาเฉพาะการ์ดของหุ้นที่มีคิว 'การนำเสนอข้อมูลงบการเงิน' หรือ 'ผลการดำเนินงาน' (OppDay)
                if (!text.includes("การนำเสนอข้อมูลงบการเงิน") && !text.includes("ผลการดำเนินงาน") && !text.includes("รายละเอียด")) {
                    continue;
                }

                // เช็กว่าเป็นกล่องของการ์ดใบเดียว (มีเวลาแค่ 1 ช่วง)
                const timeMatches = text.match(new RegExp(timeRegex, 'g')) || [];
                if (timeMatches.length !== 1) continue;

                const lines = text.split('\\n').map(l => l.trim()).filter(Boolean);
                for (const line of lines) {
                    const match = line.match(companyRegex);
                    if (match) {
                        const sym = match[1].trim(); // ดึงเฉพาะชื่อย่อหน้าเครื่องหมาย : เช่น CRC, AAV
                        if (!seenSymbols.has(sym)) {
                            seenSymbols.add(sym);
                            symbols.push(sym);
                        }
                        break;
                    }
                }
            }

            return { header: headerText, symbols: symbols };
        }""")
    except Exception as e:
        print(f"Error extracting right panel: {e}")
        return {"header": "", "symbols": []}

def click_calendar_day(page, day_num):
    """คลิกตัวเลขวันที่ในปฏิทินกิจกรรมฝั่งซ้าย และรอจนหัวข้อฝั่งขวาเปลี่ยนวันที่จริง"""
    day_str = str(day_num)
    try:
        page.evaluate("""(dayStr) => {
            const calHeaders = Array.from(document.querySelectorAll('div, span, h2, h3, p')).filter(
                el => (el.innerText || '').trim() === 'ปฏิทินกิจกรรม'
            );
            let calRoot = document.body;
            if (calHeaders.length > 0 && calHeaders[0].parentElement && calHeaders[0].parentElement.parentElement) {
                calRoot = calHeaders[0].parentElement.parentElement;
            }

            const candidates = Array.from(calRoot.querySelectorAll('button, div, span, td'));
            for (const el of candidates) {
                const txt = (el.innerText || '').replace(/\\n.*/g, '').trim();
                if (txt !== dayStr) continue;
                if (el.offsetParent === null) continue;

                const cls = (typeof el.className === 'string') ? el.className.toLowerCase() : '';
                if (cls.includes('disabled') || cls.includes('outside') || cls.includes('gray') || cls.includes('muted') || cls.includes('other')) {
                    continue;
                }

                const style = window.getComputedStyle(el);
                if (style.opacity && parseFloat(style.opacity) < 0.5) continue;

                el.click();
                return true;
            }
            return false;
        }""", day_str)

        # รอจนกว่าหัวข้อ 'รายการกิจกรรม <วันที่>' จะเปลี่ยนเป็นเลขวันใหม่จริงๆ
        expected_prefix = f"รายการกิจกรรม {day_str} "
        for _ in range(10):
            page.wait_for_timeout(500)
            current_header = page.evaluate("""() => {
                const els = Array.from(document.querySelectorAll('h1, h2, h3, h4, h5, div, span, p'));
                const h = els.find(el => (el.innerText || '').trim().startsWith('รายการกิจกรรม') && el.children.length === 0);
                return h ? h.innerText.trim() : '';
            }""")
            if current_header.startswith(expected_prefix):
                break
        page.wait_for_timeout(1500)
    except Exception as e:
        print(f"Error clicking calendar day {day_num}: {e}")

def scrape_from_homepage():
    tz_bkk = datetime.timezone(datetime.timedelta(hours=7))
    today = datetime.datetime.now(tz_bkk).date()
    schedule_days = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="th-TH"
        )
        page = context.new_page()

        page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)

        # เลื่อนหน้าจอลงมาให้ส่วน 'ปฏิทินกิจกรรม' อยู่ตรงกลางหน้าจอ
        page.evaluate("window.scrollBy(0, 750)")
        page.wait_for_timeout(2500)

        day0_data = extract_right_panel_events(page)
        header0 = day0_data.get("header", "")
        print(f"Detected initial header: {header0}")

        m = re.search(r"(\d{1,2})\s+([ก-๙]+)\s+(\d{4})", header0)
        if m:
            base_day_num = int(m.group(1))
            base_month_name = m.group(2)
            base_year_num = int(m.group(3))
            month_num = next((k for k, v in THAI_MONTHS_FULL.items() if v == base_month_name), today.month)
            base_date = datetime.date(base_year_num, month_num, base_day_num)
        else:
            base_date = today

        labels = ["🔴 วันนี้", "📌 พรุ่งนี้ (+1 วัน)", "📌 ล่วงหน้า (+2 วัน)"]
        for offset in range(3):
            target_d = base_date + datetime.timedelta(days=offset)
            date_display = f"{target_d.day} {THAI_MONTHS_FULL[target_d.month]} {target_d.year}"

            if offset > 0:
                prev_d = base_date + datetime.timedelta(days=offset - 1)
                if target_d.month != prev_d.month:
                    page.evaluate("""() => {
                        const nextBtns = Array.from(document.querySelectorAll('button, svg, span')).filter(el => (el.innerText || '').trim() === '>');
                        if (nextBtns.length > 0) nextBtns[0].click();
                    }""")
                    page.wait_for_timeout(1500)

                click_calendar_day(page, target_d.day)

            # กวาดรายชื่อหุ้น และเลื่อน Scrollbar ของกล่องฝั่งขวาลงทีละนิดเพื่อเก็บหุ้นที่ซ่อนอยู่ด้านล่างให้ครบทุกตัว
            collected_symbols = []
            # เลื่อนกลับไปบนสุดของกล่องรายการก่อนเริ่มกวาด
            page.evaluate("""() => {
                const scrollables = Array.from(document.querySelectorAll('div')).filter(
                    el => el.scrollHeight > el.clientHeight + 20 && (el.innerText || '').includes('รายละเอียด')
                );
                if (scrollables.length > 0) scrollables[0].scrollTop = 0;
            }""")
            page.wait_for_timeout(500)

            for _ in range(8):
                current_data = extract_right_panel_events(page)
                for sym in current_data.get("symbols", []):
                    if sym not in collected_symbols:
                        collected_symbols.append(sym)

                # เลื่อน Scrollbar ลงครั้งละ 450px
                scrolled_more = page.evaluate("""() => {
                    const scrollables = Array.from(document.querySelectorAll('div')).filter(
                        el => el.scrollHeight > el.clientHeight + 20 && (el.innerText || '').includes('รายละเอียด')
                    );
                    if (scrollables.length > 0) {
                        const sc = scrollables[0];
                        if (sc.scrollTop + sc.clientHeight >= sc.scrollHeight - 5) return false;
                        sc.scrollTop += 450;
                        return true;
                    }
                    return false;
                }""")
                if not scrolled_more:
                    break
                page.wait_for_timeout(800)

            schedule_days.append({
                "label": labels[offset],
                "date_display": date_display,
                "symbols": collected_symbols
            })

        browser.close()

    return schedule_days

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise ValueError("กรุณาตั้งค่า TELEGRAM_BOT_TOKEN และ TELEGRAM_CHAT_ID ใน GitHub Secrets")
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    res = requests.post(url, json=payload, timeout=20)
    if not res.ok:
        clean_text = re.sub(r"<[^>]+>", "", html.unescape(message))
        payload["text"] = clean_text
        payload.pop("parse_mode", None)
        res = requests.post(url, json=payload, timeout=20)
    res.raise_for_status()

def main():
    schedule_days = scrape_from_homepage()
    lines = ["📊 <b>รายชื่อหุ้นนำเสนอผลการดำเนินงาน (OppDay)</b>\n"]

    for day_info in schedule_days:
        lines.append(f"{day_info['label']} <b>({html.escape(day_info['date_display'])})</b>")
        symbols = day_info["symbols"]
        if symbols:
            safe_syms = [f"<b>{html.escape(s)}</b>" for s in symbols]
            lines.append(f"👉 {', '.join(safe_syms)}")
            lines.append(f"<i>(รวม {len(symbols)} บริษัท)</i>")
        else:
            lines.append("• <i>ไม่มีกำหนดการนำเสนอในวันนี้</i>")
        lines.append("")

    lines.append(f"🔗 <a href='{HOME_URL}'>คลิกดูตารางและรับชมออนไลน์บนเว็บ SET</a>")
    send_telegram("\n".join(lines))

if __name__ == "__main__":
    main()
