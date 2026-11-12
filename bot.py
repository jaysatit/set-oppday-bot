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
CALENDAR_URL = "https://opportunity-day.setgroup.or.th/th/calendar?type=0"

THAI_MONTHS_FULL = {
    1: "มกราคม", 2: "กุมภาพันธ์", 3: "มีนาคม", 4: "เมษายน",
    5: "พฤษภาคม", 6: "มิถุนายน", 7: "กรกฎาคม", 8: "สิงหาคม",
    9: "กันยายน", 10: "ตุลาคม", 11: "พฤศจิกายน", 12: "ธันวาคม"
}

THAI_MONTHS_SHORT = {
    1: "ม.ค.", 2: "ก.พ.", 3: "มี.ค.", 4: "เม.ย.",
    5: "พ.ค.", 6: "มิ.ย.", 7: "ก.ค.", 8: "ส.ค.",
    9: "ก.ย.", 10: "ต.ค.", 11: "พ.ย.", 12: "ธ.ค."
}

def extract_weekly_highlights_with_scroll(page):
    """
    ดึงรายชื่อหุ้นจากกล่อง Weekly Highlights (ซึ่งมีวันที่กำกับชัดเจนทุกใบ เช่น '12 พ.ย. 2026')
    พร้อมเลื่อน Scrollbar ในกล่อง Weekly Highlights จนครบทุกบริษัท
    """
    collected = {}  # key: "12 พ.ย. 2026" -> list of symbols ["GGC", "BTG", ...]

    for _ in range(12):
        batch = page.evaluate("""() => {
            const results = [];
            const timeRegex = /(\\d{2}:\\d{2}\\s*[-–]\\s*\\d{2}:\\d{2})/;
            const dateRegex = /(\\d{1,2}\\s*(?:ม\\.ค\\.|ก\\.พ\\.|มี\\.ค\\.|เม\\.ย\\.|พ\\.ค\\.|มิ\\.ย\\.|ก\\.ค\\.|ส\\.ค\\.|ก\\.ย\\.|ต\\.ค\\.|พ\\.ย\\.|ธ\\.ค\\.)\\s*\\d{4})/;
            // ชื่อย่อหุ้นต้องขึ้นต้นด้วย A-Z เท่านั้น (ป้องกันการจับตัวเลขเวลา 09:15)
            const companyRegex = /^([A-Z][A-Z0-9&*+-]{0,14})\\s*:\\s*(.+)$/;

            const boxes = Array.from(document.querySelectorAll('div, li, article'));
            for (const box of boxes) {
                const txt = (box.innerText || "").trim();
                if (!timeRegex.test(txt) || !dateRegex.test(txt)) continue;

                // ต้องเป็นการ์ดของหุ้นใบเดียว (มีเวลาแค่ 1 ช่วง)
                const tMatches = txt.match(new RegExp(timeRegex, 'g')) || [];
                if (tMatches.length !== 1) continue;

                const dMatch = txt.match(dateRegex);
                if (!dMatch) continue;

                const lines = txt.split('\\n').map(l => l.trim()).filter(Boolean);
                for (const line of lines) {
                    const cMatch = line.match(companyRegex);
                    if (cMatch) {
                        results.push({
                            date_str: dMatch[1].replace(/\\s+/g, ' ').trim(),
                            symbol: cMatch[1].trim()
                        });
                        break;
                    }
                }
            }
            return results;
        }""")

        for item in batch:
            d_str = item["date_str"]
            sym = item["symbol"]
            if d_str not in collected:
                collected[d_str] = []
            if sym not in collected[d_str]:
                collected[d_str].append(sym)

        # เลื่อน Scrollbar ของกล่อง Weekly Highlights ลงเพื่อโหลดหุ้นตัวถัดไป
        scrolled = page.evaluate("""() => {
            const dateRegex = /(\\d{1,2}\\s*(?:ม\\.ค\\.|ก\\.พ\\.|มี\\.ค\\.|เม\\.ย\\.|พ\\.ค\\.|มิ\\.ย\\.|ก\\.ค\\.|ส\\.ค\\.|ก\\.ย\\.|ต\\.ค\\.|พ\\.ย\\.|ธ\\.ค\\.)\\s*\\d{4})/;
            const scrollables = Array.from(document.querySelectorAll('div')).filter(
                el => el.scrollHeight > el.clientHeight + 20 && dateRegex.test(el.innerText || '')
            );
            if (scrollables.length > 0) {
                // เลือกตัวที่เป็น Scroll container ของ Weekly Highlights
                const target = scrollables[scrollables.length - 1];
                if (target.scrollTop + target.clientHeight >= target.scrollHeight - 5) {
                    return false;
                }
                target.scrollTop += 400;
                return true;
            }
            return false;
        }""")
        if not scrolled:
            break
        page.wait_for_timeout(600)

    return collected

def extract_calendar_right_panel(page):
    """
    ดึงหัวข้อวันที่ 'รายการกิจกรรม <วันที่>' และรายชื่อหุ้นที่อยู่ในกล่องด้านขวามือเท่านั้น
    (ข้ามการ์ดที่มีวันที่ระดับ '12 พ.ย. 2026' ของ Weekly Highlights เพื่อไม่ให้ GGC ปนข้ามวัน)
    """
    return page.evaluate("""() => {
        const allElements = Array.from(document.querySelectorAll('h1, h2, h3, h4, h5, div, span, p'));
        let headerText = "";

        for (const el of allElements) {
            const txt = (el.innerText || "").trim();
            if (txt.startsWith("รายการกิจกรรม") && txt.length < 80 && el.children.length === 0) {
                headerText = txt;
                break;
            }
        }

        const timeRegex = /(\\d{2}:\\d{2}\\s*[-–]\\s*\\d{2}:\\d{2})/;
        // การ์ดใน Weekly Highlights จะมีวันที่ย่อ เช่น '12 พ.ย. 2026' แต่การ์ดในปฏิทินกิจกรรมจะไม่มีบรรทัดวันที่นี้
        const shortDateRegex = /(\\d{1,2}\\s*(?:ม\\.ค\\.|ก\\.พ\\.|มี\\.ค\\.|เม\\.ย\\.|พ\\.ค\\.|มิ\\.ย\\.|ก\\.ค\\.|ส\\.ค\\.|ก\\.ย\\.|ต\\.ค\\.|พ\\.ย\\.|ธ\\.ค\\.)\\s*\\d{4})/;
        const companyRegex = /^([A-Z][A-Z0-9&*+-]{0,14})\\s*:\\s*(.+)$/;

        const cards = Array.from(document.querySelectorAll('div, li, article'));
        const symbols = [];

        for (const card of cards) {
            const text = (card.innerText || "").trim();
            if (!timeRegex.test(text)) continue;

            // 1. กรองการ์ดจาก Weekly Highlights ทิ้ง 100% (ถ้ามีวันที่ย่อ '12 พ.ย. 2026' แปลว่ามาจาก Weekly Highlights ด้านบน)
            if (shortDateRegex.test(text)) continue;

            // 2. เอาเฉพาะคิวที่มีคำว่า 'การนำเสนอข้อมูลงบการเงิน' หรือ 'ผลการดำเนินงาน' (OppDay)
            if (!text.includes("การนำเสนอข้อมูลงบการเงิน") && !text.includes("ผลการดำเนินงาน")) continue;

            // 3. ต้องเป็นการ์ดของบริษัทเดียว (มีช่วงเวลาแค่ 1 จุด)
            const timeMatches = text.match(new RegExp(timeRegex, 'g')) || [];
            if (timeMatches.length !== 1) continue;

            const lines = text.split('\\n').map(l => l.trim()).filter(Boolean);
            for (const line of lines) {
                const match = line.match(companyRegex);
                if (match) {
                    const sym = match[1].trim();
                    if (!symbols.includes(sym)) {
                        symbols.push(sym);
                    }
                    break;
                }
            }
        }

        return { header: headerText, symbols: symbols };
    }""")

def click_calendar_day_real_mouse(page, day_num):
    """ใช้เมาส์จริงของ Playwright คลิกที่พิกัดตัวเลขวันที่บนปฏิทิน และเช็กว่าหัวข้อเปลี่ยนวันจริงไหม"""
    day_str = str(day_num)
    try:
        # หาพิกัด (x, y) ของปุ่มตัวเลขวันที่ในส่วนปฏิทินกิจกรรม
        coords = page.evaluate("""(dayStr) => {
            const calHeaders = Array.from(document.querySelectorAll('div, span, h2, h3, p')).filter(
                el => (el.innerText || '').trim() === 'ปฏิทินกิจกรรม' && el.children.length === 0
            );
            let calTop = 0;
            if (calHeaders.length > 0) {
                calTop = calHeaders[0].getBoundingClientRect().top;
            }

            const candidates = Array.from(document.querySelectorAll('button, div, span, td'));
            for (const el of candidates) {
                const rect = el.getBoundingClientRect();
                if (calTop > 0 && rect.top < calTop) continue; // ต้องอยู่ใต้หัวข้อ 'ปฏิทินกิจกรรม'
                if (rect.width === 0 || rect.height === 0 || rect.width > 120 || rect.height > 120) continue;

                const txt = (el.innerText || '').replace(/\\n.*/g, '').trim();
                if (txt !== dayStr) continue;

                const cls = (typeof el.className === 'string') ? el.className.toLowerCase() : '';
                if (cls.includes('disabled') || cls.includes('outside') || cls.includes('gray') || cls.includes('muted') || cls.includes('other')) {
                    continue;
                }

                const style = window.getComputedStyle(el);
                if (style.opacity && parseFloat(style.opacity) < 0.5) continue;

                return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
            }
            return null;
        }""", day_str)

        if coords:
            page.mouse.click(coords["x"], coords["y"])
            # รอให้หัวข้อ 'รายการกิจกรรม <วันที่>' เปลี่ยนตามวันที่คลิก
            expected_prefix = f"รายการกิจกรรม {day_str} "
            for _ in range(10):
                page.wait_for_timeout(400)
                info = extract_calendar_right_panel(page)
                if info.get("header", "").startswith(expected_prefix):
                    return True
        return False
    except Exception as e:
        print(f"Mouse click error on day {day_num}: {e}")
        return False

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

        # ขั้นที่ 1: กวาดรายชื่อหุ้นจากกล่อง Weekly Highlights ด้านบนทั้งหมด (มีวันที่กำกับชัดเจนทุกตัว แม่นยำ 100%)
        highlights_by_date = extract_weekly_highlights_with_scroll(page)
        print(f"Highlights by date: {highlights_by_date}")

        # ขั้นที่ 2: เลื่อนลงมาที่ส่วน 'ปฏิทินกิจกรรม' ด้านล่าง
        page.evaluate("window.scrollBy(0, 750)")
        page.wait_for_timeout(2500)

        initial_panel = extract_calendar_right_panel(page)
        header0 = initial_panel.get("header", "")
        print(f"Initial calendar header: {header0}")

        # กำหนดปีและเดือนตามหน้าเว็บ SET (เช่น พ.ย. 2026) แต่ใช้วันที่ปัจจุบันตามเวลาประเทศไทย
        m = re.search(r"(\d{1,2})\s+([ก-๙]+)\s+(\d{4})", header0)
        if m:
            web_year = int(m.group(3))
            base_date = datetime.date(web_year, today.month, today.day)
        else:
            base_date = today

        labels = ["🔴 วันนี้", "📌 พรุ่งนี้ (+1 วัน)", "📌 ล่วงหน้า (+2 วัน)"]
        for offset in range(3):
            target_d = base_date + datetime.timedelta(days=offset)
            date_display = f"{target_d.day} {THAI_MONTHS_FULL[target_d.month]} {target_d.year}"
            date_short = f"{target_d.day} {THAI_MONTHS_SHORT[target_d.month]} {target_d.year}"
            expected_header_prefix = f"รายการกิจกรรม {target_d.day} "

            # เริ่มต้นด้วยรายชื่อหุ้นที่ได้จาก Weekly Highlights ของวันนั้นโดยตรง (ไม่ปนวันอื่นแน่นอน)
            day_symbols = list(highlights_by_date.get(date_short, []))

            # คลิกเลือกวันที่บนปฏิทินกิจกรรมด้วยเมาส์จริงเพื่อดึงเพิ่ม
            click_calendar_day_real_mouse(page, target_d.day)
            panel_data = extract_calendar_right_panel(page)

            # เอาข้อมูลจากกล่องปฏิทินกิจกรรมมาเติม *เฉพาะเมื่อหัวข้อวันที่ตรงกับวันที่ต้องการจริงๆ เท่านั้น!*
            # (ถ้าหัวข้อไม่ตรง เช่น คลิกวันที่ 14 แล้วหน้าเว็บไม่เปลี่ยนหัวข้อเป็นวันที่ 14 จะไม่ดึงมาซ้ำเด็ดขาด!)
            if panel_data.get("header", "").startswith(expected_header_prefix):
                for _ in range(8):
                    curr_panel = extract_calendar_right_panel(page)
                    for sym in curr_panel.get("symbols", []):
                        if sym not in day_symbols:
                            day_symbols.append(sym)

                    # เลื่อน Scrollbar ของกล่องรายการกิจกรรมฝั่งขวา
                    scrolled_more = page.evaluate("""() => {
                        const scrollables = Array.from(document.querySelectorAll('div')).filter(
                            el => el.scrollHeight > el.clientHeight + 20 && (el.innerText || '').includes('รายละเอียด')
                        );
                        if (scrollables.length > 0) {
                            const sc = scrollables[0];
                            if (sc.scrollTop + sc.clientHeight >= sc.scrollHeight - 5) return false;
                            sc.scrollTop += 400;
                            return true;
                        }
                        return false;
                    }""")
                    if not scrolled_more:
                        break
                    page.wait_for_timeout(700)

            schedule_days.append({
                "label": labels[offset],
                "date_display": date_display,
                "symbols": day_symbols
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
