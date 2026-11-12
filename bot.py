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

def extract_right_panel_symbols(page):
    """ดึงเฉพาะ 'ชื่อย่อหุ้น' ที่อยู่ในกล่อง 'รายการกิจกรรม ...' ฝั่งขวามือ และเป็นคิวนำเสนองบการเงินเท่านั้น"""
    try:
        return page.evaluate("""() => {
            // 1. ค้นหาหัวข้อ 'รายการกิจกรรม <วันที่>' ด้านขวา
            const allEls = Array.from(document.querySelectorAll('h1, h2, h3, h4, h5, div, span, p'));
            let headerEl = null;
            let headerText = "";

            for (const el of allEls) {
                const txt = (el.innerText || "").trim();
                if (txt.startsWith("รายการกิจกรรม") && txt.length < 80 && el.children.length === 0) {
                    headerEl = el;
                    headerText = txt;
                    break;
                }
            }

            if (!headerEl) {
                return { header: "", symbols: [] };
            }

            // 2. หาเฉพาะกรอบ Container ของฝั่งขวาที่อยู่คู่กับหัวข้อ 'รายการกิจกรรม' เท่านั้น
            // (ไม่ถอยออกไปถึง document.body เพื่อป้องกันไม่ให้ไปกวาดโดน Weekly Highlights ด้านบน)
            let panel = headerEl.parentElement;
            while (panel && panel !== document.body) {
                const pText = panel.innerText || "";
                // ถ้าถอยไปจนเจอคำว่า 'ปฏิทินกิจกรรม' หรือ 'Weekly Highlights' แปลว่ากว้างเกินไปแล้ว ให้หยุดก่อนหน้านั้น
                if (pText.includes("Weekly Highlights") || pText.includes("ปฏิทินกิจกรรม")) {
                    break;
                }
                panel = panel.parentElement;
            }
            // หากหยุดเพราะกว้างเกิน ให้ใช้ชั้นลูกที่ครอบเฉพาะฝั่งขวา
            let rightContainer = headerEl.parentElement;
            let curr = headerEl.parentElement;
            while (curr && curr !== panel && curr !== document.body) {
                rightContainer = curr;
                curr = curr.parentElement;
            }

            // 3. เลื่อน Scrollbar ในกล่องฝั่งขวาลงทีละสเต็ปเพื่อให้เว็บเรนเดอร์หุ้นครบทุกตัว
            const scrollables = [rightContainer, ...Array.from(rightContainer.querySelectorAll('div'))].filter(
                el => el && el.scrollHeight > el.clientHeight + 10
            );

            // กวาดรายชื่อหุ้นในกล่องฝั่งขวา
            const collectSymbols = () => {
                const found = [];
                // รูปแบบบรรทัดแรกของการ์ดหุ้น: ชื่อย่อหุ้นต้องขึ้นต้นด้วยตัวอักษรภาษาอังกฤษพิมพ์ใหญ่ (ป้องกันไม่ให้จับตัวเลขเวลา 09:15)
                const symbolLineRegex = /^([A-Z][A-Z0-9&*+-]{0,14})\\s*:\\s*(.+)$/;
                const cards = Array.from(rightContainer.querySelectorAll('div, li, article'));

                for (const card of cards) {
                    const text = (card.innerText || "").trim();
                    // กรองเฉพาะคิวกิจกรรมที่มีคำว่า 'การนำเสนอข้อมูลงบการเงิน' หรือ 'ผลการดำเนินงาน' (OppDay)
                    if (!text.includes("การนำเสนอข้อมูลงบการเงิน") && !text.includes("ผลการดำเนินงาน")) {
                        continue;
                    }

                    // เช็กว่าเป็นกล่องของการ์ดใบเดียว (มีคำว่า 'รายละเอียด' แค่ 1 ครั้ง)
                    const detailCount = (text.match(/รายละเอียด/g) || []).length;
                    if (detailCount !== 1) continue;

                    const lines = text.split('\\n').map(l => l.trim()).filter(Boolean);
                    for (const line of lines) {
                        const match = line.match(symbolLineRegex);
                        if (match) {
                            const sym = match[1].trim();
                            found.push(sym);
                            break;
                        }
                    }
                }
                return found;
            };

            let allSymbols = collectSymbols();

            // หากมี Scrollbar ให้ค่อยๆ เลื่อนลงไปเก็บจนสุดตาราง
            if (scrollables.length > 0) {
                const scroller = scrollables[0];
                const maxScroll = scroller.scrollHeight;
                for (let pos = 300; pos <= maxScroll + 300; pos += 350) {
                    scroller.scrollTop = pos;
                    const more = collectSymbols();
                    for (const s of more) {
                        if (!allSymbols.includes(s)) {
                            allSymbols.push(s);
                        }
                    }
                }
                scroller.scrollTop = 0; // เลื่อนกลับขึ้นบนสุด
            }

            // ลบตัวซ้ำโดยรักษาลำดับเดิม
            const uniqueSymbols = [...new Set(allSymbols)];
            return { header: headerText, symbols: uniqueSymbols };
        }""")
    except Exception as e:
        print(f"Error extracting right panel: {e}")
        return {"header": "", "symbols": []}

def click_calendar_day_and_wait(page, day_num):
    """คลิกตัวเลขวันที่ในปฏิทินฝั่งซ้าย และรอจนกว่าหัวข้อ 'รายการกิจกรรม <วันที่>' ฝั่งขวาจะเปลี่ยนตามจริง"""
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

        # รอให้หัวข้อฝั่งขวาเปลี่ยนเป็น 'รายการกิจกรรม <day_str> ...' จริงๆ (ป้องกันการดึงข้อมูลวันเก่าซ้ำ)
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
        page.wait_for_timeout(1000)
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

        # เลื่อนหน้าจอลงมาที่ส่วน 'ปฏิทินกิจกรรม'
        page.evaluate("window.scrollBy(0, 700)")
        page.wait_for_timeout(2500)

        # อ่านหัวข้อวันเริ่มต้นที่หน้าเว็บเลือกให้อัตโนมัติ
        day0_data = extract_right_panel_symbols(page)
        header0 = day0_data.get("header", "")
        print(f"Initial header: {header0}")

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
                # กรณีข้ามเดือน ให้กดลูกศร '>' เปลี่ยนเดือนบนปฏิทินก่อน
                prev_d = base_date + datetime.timedelta(days=offset - 1)
                if target_d.month != prev_d.month:
                    page.evaluate("""() => {
                        const nextBtns = Array.from(document.querySelectorAll('button, svg, span')).filter(el => (el.innerText || '').trim() === '>');
                        if (nextBtns.length > 0) nextBtns[0].click();
                    }""")
                    page.wait_for_timeout(1500)

                click_calendar_day_and_wait(page, target_d.day)

            # ดึงรายชื่อหุ้นหลังจากเลื่อน Scrollbar ในแต่ละสเต็ป
            symbols_list = []
            for scroll_step in range(5):
                data = extract_right_panel_symbols(page)
                for s in data.get("symbols", []):
                    if s not in symbols_list:
                        symbols_list.append(s)

                # เลื่อน Scrollbar ของกล่องรายการฝั่งขวาลงเพื่อโหลดตัวถัดไป
                has_more = page.evaluate("""(step) => {
                    const els = Array.from(document.querySelectorAll('div')).filter(
                        el => el.scrollHeight > el.clientHeight + 20 && (el.innerText || '').includes('รายละเอียด')
                    );
                    if (els.length > 0) {
                        const scroller = els[0];
                        if (scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 5) {
                            return false;
                        }
                        scroller.scrollTop += 400;
                        return true;
                    }
                    return false;
                }""", scroll_step)
                if not has_more:
                    break
                page.wait_for_timeout(800)

            schedule_days.append({
                "label": labels[offset],
                "date_display": date_display,
                "symbols": symbols_list
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
            # แสดงเฉพาะชื่อย่อหุ้นล้วนๆ อ่านง่าย สบายตา
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
