import os
import re
import datetime
import requests
from playwright.sync_api import sync_playwright

# ดึงกุญแจจาก GitHub Secrets
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ใช้ URL หน้าแรกตามไอเดียใหม่ของคุณ
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

def extract_right_panel_events(page):
    """ดึงหัวข้อวันที่และรายชื่อหุ้นจากกล่องฝั่งขวามือของ 'ปฏิทินกิจกรรม' รวมถึงเลื่อน Scrollbar ให้ครบทุกตัว"""
    return page.evaluate("""() => {
        // 1. หาหัวข้อ 'รายการกิจกรรม ...' เช่น 'รายการกิจกรรม 11 พฤศจิกายน 2026'
        const allElements = Array.from(document.querySelectorAll('h1, h2, h3, h4, h5, div, span, p'));
        let headerText = "";
        let headerEl = null;

        for (const el of allElements) {
            const txt = (el.innerText || "").trim();
            if (txt.startsWith("รายการกิจกรรม") && txt.length < 60 && el.children.length === 0) {
                headerText = txt;
                headerEl = el;
                break;
            }
        }

        // 2. เลื่อน Scrollbar ของกล่องรายการฝั่งขวาเผื่อกรณีใช้ Virtual Scroll
        let container = headerEl ? headerEl.parentElement : document.body;
        while (container && container !== document.body) {
            if (container.innerText && (container.innerText.includes("รับชมออนไลน์") || container.innerText.includes("รายละเอียด") || container.innerText.includes("ไม่มีกิจกรรม"))) {
                break;
            }
            container = container.parentElement;
        }
        if (!container) container = document.body;

        // ค้นหาการ์ดหุ้นแต่ละบริษัท (สังเกตจากรูปแบบ SYMBOL: COMPANY NAME และเวลา HH:MM - HH:MM)
        const timeRegex = /(\\d{2}:\\d{2}\\s*[-–]\\s*\\d{2}:\\d{2})/;
        const companyRegex = /^([A-Z0-9&*+-]+)\\s*:\\s*(.+)$/;

        const cards = Array.from(container.querySelectorAll('div, li, article'));
        const items = [];
        const seenSymbols = new Set();

        for (const card of cards) {
            const text = (card.innerText || "").trim();
            if (!timeRegex.test(text)) continue;

            // เอาเฉพาะกล่องย่อยสุดของแต่ละบริษัท (ไม่เอากล่องใหญ่ที่รวมหลายบริษัท)
            const timeMatches = text.match(new RegExp(timeRegex, 'g')) || [];
            if (timeMatches.length !== 1) continue;

            const lines = text.split('\\n').map(l => l.trim()).filter(l => l && l !== '⋮' && l !== '...');
            let titleLine = "";
            let sectorLine = "";
            let timeStr = timeMatches[0];
            let detailLine = "";

            for (let i = 0; i < lines.length; i++) {
                if (companyRegex.test(lines[i])) {
                    titleLine = lines[i];
                    if (i + 1 < lines.length && !timeRegex.test(lines[i + 1]) && !lines[i + 1].includes("รับชม")) {
                        sectorLine = lines[i + 1];
                    }
                }
                if (lines[i].startsWith("การนำเสนอข้อมูล") || lines[i].includes("Q1/") || lines[i].includes("Q2/") || lines[i].includes("Q3/") || lines[i].includes("Q4/") || lines[i].includes("ประจำปี")) {
                    detailLine = lines[i];
                }
            }

            if (titleLine && !seenSymbols.has(titleLine + "_" + timeStr)) {
                seenSymbols.add(titleLine + "_" + timeStr);
                const linkEl = card.querySelector('a[href]');
                const linkUrl = linkEl ? linkEl.href : "";
                items.push({
                    title: titleLine,
                    sector: sectorLine,
                    time: timeStr,
                    detail: detailLine,
                    link: linkUrl
                });
            }
        }

        return {
            header: headerText,
            items: items
        };
    }""")

def extract_weekly_highlights(page):
    """ดึงข้อมูลจากส่วน Weekly Highlights ด้านบนเป็นข้อมูลเสริม"""
    return page.evaluate("""() => {
        const results = [];
        const timeRegex = /(\\d{2}:\\d{2}\\s*[-–]\\s*\\d{2}:\\d{2})/;
        const dateRegex = /(\\d{1,2}\\s*(?:ม\\.ค\\.|ก\\.พ\\.|มี\\.ค\\.|เม\\.ย\\.|พ\\.ค\\.|มิ\\.ย\\.|ก\\.ค\\.|ส\\.ค\\.|ก\\.ย\\.|ต\\.ค\\.|พ\\.ย\\.|ธ\\.ค\\.)\\s*\\d{4})/;
        const companyRegex = /^([A-Z0-9&*+-]+)\\s*:\\s*(.+)$/;

        const boxes = Array.from(document.querySelectorAll('div, li'));
        const seen = new Set();

        for (const box of boxes) {
            const txt = (box.innerText || "").trim();
            if (!timeRegex.test(txt) || !dateRegex.test(txt)) continue;

            const tMatches = txt.match(new RegExp(timeRegex, 'g')) || [];
            if (tMatches.length !== 1) continue;

            const dMatch = txt.match(dateRegex);
            const lines = txt.split('\\n').map(l => l.trim()).filter(Boolean);
            const compLine = lines.find(l => companyRegex.test(l));

            if (compLine && dMatch) {
                const key = compLine + "_" + dMatch[1] + "_" + tMatches[0];
                if (!seen.has(key)) {
                    seen.add(key);
                    const compIdx = lines.indexOf(compLine);
                    const sector = (compIdx >= 0 && compIdx + 1 < lines.length && !dateRegex.test(lines[compIdx + 1])) ? lines[compIdx + 1] : "";
                    const linkEl = box.querySelector('a[href]');
                    results.push({
                        date_str: dMatch[1],
                        title: compLine,
                        sector: sector,
                        time: tMatches[0],
                        link: linkEl ? linkEl.href : ""
                    });
                }
            }
        }
        return results;
    }""")

def click_calendar_day(page, day_num):
    """คลิกที่ตัวเลขวันที่ในปฏิทินกิจกรรมฝั่งซ้ายมือ"""
    day_str = str(day_num)
    clicked = page.evaluate("""(dayStr) => {
        // หาบริเวณปฏิทินกิจกรรมฝั่งซ้าย
        const calHeaders = Array.from(document.querySelectorAll('div, span, h2, h3, p')).filter(
            el => (el.innerText || '').trim() === 'ปฏิทินกิจกรรม'
        );
        let calRoot = document.body;
        if (calHeaders.length > 0) {
            calRoot = calHeaders[0].parentElement.parentElement || document.body;
        }

        // ค้นหาปุ่มหรือช่องตัวเลขวันที่ตรงกับ dayStr (และไม่ใช่ตัวเลขสีเทาของเดือนอื่น)
        const candidates = Array.from(calRoot.querySelectorAll('button, div, span, td'));
        for (const el of candidates) {
            const txt = (el.innerText || '').replace(/\\n.*/g, '').trim();
            if (txt !== dayStr) continue;
            if (el.offsetParent === null) continue; // ข้ามตัวที่ซ่อนอยู่

            const cls = (typeof el.className === 'string') ? el.className.toLowerCase() : '';
            if (cls.includes('disabled') || cls.includes('outside') || cls.includes('gray') || cls.includes('muted') || cls.includes('other')) {
                continue;
            }

            // ตรวจสอบสีตัวอักษรเพื่อข้ามวันของเดือนถัดไปที่เป็นสีเทาอ่อน
            const style = window.getComputedStyle(el);
            if (style.opacity && parseFloat(style.opacity) < 0.5) continue;

            el.click();
            return true;
        }
        return false;
    }""", day_str)
    page.wait_for_timeout(2500)
    return clicked

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
        page.goto(HOME_URL, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(4000)

        # เลื่อนหน้าจอลงมาที่ส่วน "ปฏิทินกิจกรรม" เพื่อให้หน้าเว็บเรนเดอร์ข้อมูลครบ
        page.evaluate("window.scrollBy(0, 700)")
        page.wait_for_timeout(2000)

        # 1. อ่านข้อมูลของ "วันนี้" ที่หน้าเว็บเลือกให้อัตโนมัติอยู่แล้ว (เช่น รายการกิจกรรม 11 พฤศจิกายน 2026)
        day0_data = extract_right_panel_events(page)
        header0 = day0_data.get("header", "")

        # ตรวจหาเลขวันที่ตั้งต้นจากหัวข้อ 'รายการกิจกรรม 11 พฤศจิกายน 2026'
        m = re.search(r"(\d{1,2})\s+([ก-๙]+)\s+(\d{4})", header0)
        if m:
            base_day_num = int(m.group(1))
            base_month_name = m.group(2)
            base_year_num = int(m.group(3))
            # หาเลขเดือนจากชื่อเดือนภาษาไทย
            month_num = next((k for k, v in THAI_MONTHS_FULL.items() if v == base_month_name), today.month)
            base_date = datetime.date(base_year_num, month_num, base_day_num)
        else:
            base_date = today

        # ดึงข้อมูล 3 วัน: วันนี้ (Offset 0), พรุ่งนี้ (Offset +1), มะรืนนี้ (Offset +2)
        labels = ["🔴 วันนี้", "📌 พรุ่งนี้ (+1 วัน)", "📌 ล่วงหน้า (+2 วัน)"]
        for offset in range(3):
            target_d = base_date + datetime.timedelta(days=offset)
            date_display = f"{target_d.day} {THAI_MONTHS_FULL[target_d.month]} {target_d.year}"

            if offset == 0 and day0_data.get("items"):
                current_data = day0_data
            else:
                # ถ้าเปลี่ยนเดือน (เช่น สิ้นเดือนไปวันที่ 1 เดือนถัดไป) ให้กดปุ่มลูกศร '>' บนปฏิทิน
                if target_d.month != base_date.month and offset > 0 and (base_date + datetime.timedelta(days=offset - 1)).month == base_date.month:
                    page.evaluate("""() => {
                        const nextBtns = Array.from(document.querySelectorAll('button, svg, span')).filter(el => (el.innerText || '').trim() === '>');
                        if (nextBtns.length > 0) nextBtns[0].click();
                    }""")
                    page.wait_for_timeout(1500)

                click_calendar_day(page, target_d.day)
                current_data = extract_right_panel_events(page)

            # เลื่อนกล่องฝั่งขวาลงเพื่อเก็บหุ้นตัวท้ายๆ ที่อาจซ่อนอยู่ด้านล่างของ Scrollbar
            extra_items = page.evaluate("""() => {
                const scrollables = Array.from(document.querySelectorAll('div')).filter(
                    el => el.scrollHeight > el.clientHeight + 20 && (el.innerText || '').includes('รายละเอียด')
                );
                if (scrollables.length > 0) {
                    scrollables[0].scrollTop = scrollables[0].scrollHeight;
                    return true;
                }
                return false;
            }""")
            if extra_items:
                page.wait_for_timeout(1000)
                more_data = extract_right_panel_events(page)
                existing_keys = {f"{x['title']}_{x['time']}" for x in current_data["items"]}
                for item in more_data["items"]:
                    if f"{item['title']}_{item['time']}" not in existing_keys:
                        current_data["items"].append(item)

            schedule_days.append({
                "label": labels[offset],
                "date_display": date_display,
                "date_short": f"{target_d.day} {THAI_MONTHS_SHORT[target_d.month]} {target_d.year}",
                "items": current_data.get("items", [])
            })

        # 2. ดึงข้อมูลจาก Weekly Highlights มาช่วยเสริมเผื่อมีหุ้นตัวไหนตกหล่น
        highlights = extract_weekly_highlights(page)
        browser.close()

    # นำข้อมูลจาก Weekly Highlights มาเติมหากวันไหนยังดึงมาไม่ครบ
    for day_info in schedule_days:
        existing_titles = {x["title"].split(":")[0].strip() for x in day_info["items"]}
        for hl in highlights:
            if hl["date_str"] == day_info["date_short"]:
                sym = hl["title"].split(":")[0].strip()
                if sym not in existing_titles:
                    day_info["items"].append({
                        "title": hl["title"],
                        "sector": hl["sector"],
                        "time": hl["time"],
                        "detail": "",
                        "link": hl["link"]
                    })

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
    res.raise_for_status()

def main():
    schedule_days = scrape_from_homepage()
    lines = ["📢 <b>แจ้งเตือนตารางหุ้น SET Opportunity Day</b>\n"]

    for day_info in schedule_days:
        lines.append(f"{day_info['label']} <b>({day_info['date_display']})</b>")
        items = day_info["items"]
        if items:
            # เรียงลำดับตามเวลา
            items.sort(key=lambda x: x["time"])
            for it in items:
                title = it["title"]
                sector = f" <i>({it['sector']})</i>" if it["sector"] else ""
                link = it["link"] if it["link"].startswith("http") else HOME_URL
                lines.append(f"• ⏰ <b>{it['time']}</b> | <b>{title}</b>{sector} 👉 <a href='{link}'>รับชมออนไลน์</a>")
        else:
            lines.append("• <i>ไม่มีกำหนดการนำเสนอในวันนี้</i>")
        lines.append("")

    lines.append(f"🔗 <a href='{HOME_URL}'>เปิดหน้าแรก SET OPPDAY</a> | <a href='{CALENDAR_URL}'>ดูตารางปฏิทินเต็ม</a>")
    send_telegram("\n".join(lines))

if __name__ == "__main__":
    main()
