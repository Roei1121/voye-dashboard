"""
Voye eSIM Competitor Price Scraper — Fixed Version
===================================================
Fixes: correct URL slugs for all competitors + proper GB/days extraction
"""
import asyncio, json, os, re, sys, time, argparse, logging, shutil
from datetime import datetime
from pathlib import Path
from playwright.async_api import async_playwright

BASE_DIR    = Path(__file__).parent
DATA_DIR    = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
PRICES_FILE = DATA_DIR / "competitor_prices.json"
PREV_FILE   = DATA_DIR / "competitor_prices_prev.json"
DIFF_FILE   = DATA_DIR / "diff_report.json"
LOG_FILE    = DATA_DIR / "scraper.log"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)])
log = logging.getLogger("voye")

TARGETS = [
    "Albania","Algeria","Andorra","Anguilla","Antigua and Barbuda","Argentina",
    "Armenia","Aruba","Asia","Australia","Austria","Azerbaijan","Bahamas","Bahrain",
    "Bangladesh","Barbados","Belarus","Belgium","Belize","Bermuda","Bolivia",
    "Bosnia and Herzegovina","Brazil","British Virgin Islands","Brunei","Bulgaria",
    "Cambodia","Cameroon","Canada","Caribbean","Cayman Islands","Chile","China",
    "Colombia","Congo","Costa Rica","Croatia","Cyprus","Czech Republic","Denmark",
    "Dominica","Dominican Republic","Ecuador","Egypt","El Salvador","Estonia","Europe",
    "Faroe Islands","Fiji","Finland","France","French Polynesia","Georgia","Germany",
    "Ghana","Gibraltar","Global","Greece","Grenada","Guam","Guatemala","Guyana",
    "Haiti","Honduras","Hong Kong","Hungary","Iceland","India","Indonesia","Ireland",
    "Israel","Italy","Jamaica","Japan","Jordan","Kazakhstan","Kenya","Kuwait",
    "Laos","Latin America","Latvia","Liechtenstein","Lithuania","Luxembourg",
    "Macau","Madagascar","Malawi","Malaysia","Maldives","Malta","Martinique",
    "Mauritius","Mexico","Middle East","Moldova","Monaco","Montenegro","Morocco",
    "Nepal","Netherlands","New Zealand","Nigeria","North America","North Macedonia",
    "Norway","Oman","Pakistan","Panama","Paraguay","Peru","Philippines","Poland",
    "Portugal","Puerto Rico","Qatar","Romania","Rwanda","Saint Kitts and Nevis",
    "Saint Lucia","Saint Martin","Saudi Arabia","Senegal","Serbia","Seychelles",
    "Singapore","Slovakia","Slovenia","South Africa","South Korea","Spain",
    "Sri Lanka","Suriname","Sweden","Switzerland","Taiwan","Tanzania","Thailand",
    "Trinidad and Tobago","Tunisia","Turkey","Turks and Caicos Islands","Uganda",
    "Ukraine","United Arab Emirates","United Kingdom","United States",
    "Uzbekistan","Vietnam","Zambia",
]

# ── Slug mappings ────────────────────────────────────────────────────────────
AIRALO_SLUGS = {
    "United States":"united-states-esim","United Kingdom":"united-kingdom-esim",
    "United Arab Emirates":"united-arab-emirates-esim","South Korea":"south-korea-esim",
    "North Macedonia":"north-macedonia-esim","Costa Rica":"costa-rica-esim",
    "El Salvador":"el-salvador-esim","Trinidad and Tobago":"trinidad-and-tobago-esim",
    "Saudi Arabia":"saudi-arabia-esim","Sri Lanka":"sri-lanka-esim",
    "New Zealand":"new-zealand-esim","Hong Kong":"hong-kong-esim",
    "Dominican Republic":"dominican-republic-esim","North America":"north-america-esim",
    "Latin America":"latin-america-esim","Middle East":"middle-east-esim",
    "Cayman Islands":"cayman-islands-esim","Faroe Islands":"faroe-islands-esim",
    "French Polynesia":"french-polynesia-esim","South Africa":"south-africa-esim",
    "Bosnia and Herzegovina":"bosnia-and-herzegovina-esim",
    "Saint Kitts and Nevis":"saint-kitts-and-nevis-esim",
    "Saint Lucia":"saint-lucia-esim","Saint Martin":"saint-martin-esim",
}

HOLAFLY_SLUGS = {
    "United States":"usa","United Kingdom":"uk","United Arab Emirates":"dubai",
    "South Korea":"south-korea","Costa Rica":"costa-rica","El Salvador":"el-salvador",
    "Trinidad and Tobago":"trinidad-and-tobago","Saudi Arabia":"saudi-arabia",
    "Sri Lanka":"sri-lanka","New Zealand":"new-zealand","Hong Kong":"hong-kong",
    "Dominican Republic":"dominican-republic","North Macedonia":"north-macedonia",
    "South Africa":"south-africa","Bosnia and Herzegovina":"bosnia-and-herzegovina",
}

NOMAD_SLUGS = {
    "United States":"usa","United Kingdom":"united-kingdom",
    "United Arab Emirates":"uae","South Korea":"south-korea",
}

ESIMO_SLUGS = {
    "United States":"usa","United Kingdom":"uk","United Arab Emirates":"uae",
    "South Korea":"south-korea","Hong Kong":"hong-kong",
}

SAILY_SLUGS = {
    "United States":"united-states","United Kingdom":"united-kingdom",
    "United Arab Emirates":"united-arab-emirates","South Korea":"south-korea",
    "Hong Kong":"hong-kong","Saudi Arabia":"saudi-arabia","New Zealand":"new-zealand",
}

def generic_slug(name):
    return re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')

def get_slug(name, mapping):
    return mapping.get(name, generic_slug(name))

# ── Browser setup ─────────────────────────────────────────────────────────────
async def make_context(p, headless=True):
    browser = await p.chromium.launch(headless=headless, args=[
        "--no-sandbox","--disable-setuid-sandbox","--disable-dev-shm-usage",
        "--disable-blink-features=AutomationControlled",
    ])
    ctx = await browser.new_context(
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        viewport={"width":1280,"height":900}, locale="en-US",
        extra_http_headers={"Accept-Language":"en-US,en;q=0.9"}
    )
    await ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});window.chrome={runtime:{}};")
    return browser, ctx

# ── Price extraction ──────────────────────────────────────────────────────────
async def extract_plans(page, wait_ms=3000):
    await page.wait_for_timeout(wait_ms)
    content = await page.content()
    plans = []
    
    # Try JS evaluation first
    try:
        raw = await page.evaluate("""() => {
            const results = [];
            const cards = document.querySelectorAll(
                '[class*="package"], [class*="Package"], [class*="plan"], [class*="Plan"],
                [class*="card"], [class*="Card"], [class*="eSIM"], [class*="esim"],
                [class*="product"], [class*="Product"]'
            );
            cards.forEach(card => {
                const text = card.innerText || '';
                if (!text.includes('$') && !text.match(/\\d+[.,]\\d+/)) return;
                results.push(text.slice(0, 300));
            });
            return results;
        }""")
        
        for block in raw:
            price_m = re.search(r'\$\s*(\d+(?:[.,]\d+)?)', block)
            gb_m    = re.search(r'(\d+(?:\.\d+)?)\s*GB', block, re.IGNORECASE)
            unl_m   = re.search(r'[Uu]nlimited', block)
            day_m   = re.search(r'(\d+)\s*[Dd]ay', block)
            if price_m:
                price = float(price_m.group(1).replace(',',''))
                if 0.5 < price < 300:
                    data = f"{gb_m.group(1)}GB" if gb_m else ("Unlimited" if unl_m else None)
                    days = int(day_m.group(1)) if day_m else None
                    plans.append({"price":price,"data":data,"days":days})
    except:
        pass
    
    # Fallback: parse HTML
    if not plans:
        price_pattern = re.compile(r'\$\s*(\d+(?:[.,]\d+)?)')
        gb_pattern    = re.compile(r'(\d+(?:\.\d+)?)\s*GB', re.IGNORECASE)
        unl_pattern   = re.compile(r'[Uu]nlimited')
        day_pattern   = re.compile(r'(\d+)\s*[Dd]ay', re.IGNORECASE)
        
        # Split around price mentions
        chunks = re.split(r'(?=\$\d)', content)
        for chunk in chunks[:80]:
            c = chunk[:400]
            pm = price_pattern.search(c)
            if not pm: continue
            price = float(pm.group(1).replace(',',''))
            if not (0.5 < price < 300): continue
            gm = gb_pattern.search(c)
            um = unl_pattern.search(c)
            dm = day_pattern.search(c)
            data = f"{gm.group(1)}GB" if gm else ("Unlimited" if um else None)
            days = int(dm.group(1)) if dm else None
            plans.append({"price":price,"data":data,"days":days})
    
    # Deduplicate
    seen, unique = set(), []
    for p in plans:
        key = (round(p["price"],2), p.get("data"), p.get("days"))
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique

# ── Scrapers ──────────────────────────────────────────────────────────────────
async def _extract_airalo_plans(page):
    """Parse Airalo's plan grid for both Standard and Unlimited tabs.

    Airalo shows two tabs — 'Standard' (default) and 'Unlimited'. Each tab
    must be active for its plans to appear in the DOM. We click each tab in
    turn and extract plans from both.

    Each day-bucket is a div.flex.flex-col.gap-2 whose innerText is:
        {N} days
        {GB or "Unlimited"}
        GB
        ${price}
        USD
    """
    plans, seen = [], set()

    async def _extract_active_tab():
        blocks = await page.evaluate("""() => {
            const out = [];
            document.querySelectorAll('div.flex.flex-col.gap-2').forEach(el => {
                const t = (el.innerText || '').trim();
                if (/\\d+\\s*days?/i.test(t) && t.includes('$') && /GB/i.test(t))
                    out.push(t.slice(0, 1000));
            });
            return out;
        }""")
        for block in blocks:
            day_m = re.search(r'^(\d+)\s*days?', block, re.IGNORECASE | re.MULTILINE)
            if not day_m:
                continue
            days = int(day_m.group(1))
            # Matches both "{number}\nGB\n$price\nUSD" and "Unlimited\nGB\n$price\nUSD"
            for m in re.finditer(
                r'((?:\d+(?:\.\d+)?)|Unlimited)\n+GB\n+\$(\d+(?:\.\d+)?)\n+USD',
                block, re.IGNORECASE
            ):
                gb_val = m.group(1)
                price  = float(m.group(2))
                if not (0.5 < price < 500):
                    continue
                data = "Unlimited" if gb_val.lower() == "unlimited" else f"{gb_val}GB"
                key  = (price, data, days)
                if key not in seen:
                    seen.add(key)
                    plans.append({"price": price, "data": data, "days": days})

    # Airalo defaults to the Unlimited tab for most countries — click Standard first
    std_tab = page.locator('button.p-tab:has-text("Standard")')
    if await std_tab.count() > 0:
        await std_tab.first.click()
        await page.wait_for_timeout(1500)
    await _extract_active_tab()

    # Then switch to Unlimited tab if present
    unl_tab = page.locator('button.p-tab:has-text("Unlimited")')
    if await unl_tab.count() > 0:
        await unl_tab.first.click()
        await page.wait_for_timeout(1500)
        await _extract_active_tab()

    return plans

async def scrape_airalo(ctx, countries, concurrency=8):
    results = {}
    sem  = asyncio.Semaphore(concurrency)
    lock = asyncio.Lock()

    async def _one(country):
        async with sem:
            slug = AIRALO_SLUGS.get(country, generic_slug(country) + "-esim")
            url  = f"https://www.airalo.com/{slug}"
            page = await ctx.new_page()
            try:
                log.info(f"  Airalo → {country}")
                await page.goto(url, timeout=30000, wait_until="domcontentloaded")
                await page.wait_for_timeout(4000)
                plans = await _extract_airalo_plans(page)
                async with lock:
                    if plans:
                        results[country] = plans
                        log.info(f"    ✓ {len(plans)} plans  [{country}]")
                    else:
                        log.warning(f"    ✗ No plans for {country}")
            except Exception as e:
                log.error(f"    ✗ {country}: {e}")
            finally:
                await page.close()

    await asyncio.gather(*[_one(c) for c in countries])
    return results

async def _holafly_get_plan_for_days(page, n_days):
    """Select n_days in the Holafly dual-month calendar. Returns (price, actual_days) or (None,None).
    The calendar shows 2 months at once; past dates are disabled so end-date buttons are
    mostly unique within the visible window (no navigation needed for ≤56-day spans)."""
    from datetime import date, timedelta
    today = date.today()
    end_date = today + timedelta(days=n_days - 1)
    try:
        await page.locator('[data-qa="numberOfDaysInput"]').click(timeout=3000)
        await page.wait_for_timeout(800)
        modal = page.locator('.modal--active')
        if await modal.count() == 0:
            return None, None
        # Click today as start — .first picks the earlier-month occurrence (today's month)
        start_btns = modal.locator(f'button.h-7:not([disabled]):text-is("{today.day}")')
        if await start_btns.count() == 0:
            return None, None
        await start_btns.first.click()
        await page.wait_for_timeout(400)
        # Click end date — navigate forward only if the date isn't visible yet
        end_str = str(end_date.day)
        end_btns = modal.locator(f'button.h-7:not([disabled]):text-is("{end_str}")')
        if await end_btns.count() == 0:
            for _ in range(6):
                await modal.locator('button.absolute.cursor-pointer.right-0').click()
                await page.wait_for_timeout(400)
                end_btns = modal.locator(f'button.h-7:not([disabled]):text-is("{end_str}")')
                if await end_btns.count() > 0:
                    break
        if await end_btns.count() == 0:
            return None, None
        # Use .last if end is in a future month to avoid ambiguity with same-day in current month
        if end_date.month != today.month:
            await end_btns.last.click()
        else:
            await end_btns.first.click()
        await page.wait_for_timeout(400)
        # Apply
        await page.locator('button#apply').click()
        await page.wait_for_timeout(700)
        # Read price and actual days shown by widget
        price_text = await page.locator('[data-qa="priceLabel"]').first.inner_text(timeout=2000)
        pm = re.search(r'[\$€£]\s*(\d+(?:[.,]\d+)?)', price_text)
        days_text = await page.locator('[data-qa="numberOfDaysInput"]').first.inner_text(timeout=1000)
        dm = re.search(r'(\d+)', days_text)
        if pm:
            return float(pm.group(1).replace(',', '.')), int(dm.group(1)) if dm else n_days
    except:
        try:
            await page.keyboard.press('Escape')
        except:
            pass
    return None, None


async def scrape_holafly(ctx, countries, concurrency=5):
    results = {}
    sem  = asyncio.Semaphore(concurrency)
    lock = asyncio.Lock()

    async def _one(country):
        async with sem:
            slug = get_slug(country, HOLAFLY_SLUGS)
            url  = f"https://holafly.com/esim-{slug}"
            page = await ctx.new_page()
            try:
                log.info(f"  Holafly → {country}")
                await page.goto(url, timeout=30000, wait_until="domcontentloaded")
                await page.wait_for_timeout(3000)
                if not await page.locator('[data-qa="currencySelect"]').count():
                    log.warning(f"    ✗ No pricing widget for {country}")
                    return
                try:
                    await page.locator('[data-qa="currencySelect"]').first.click(timeout=2000)
                    await page.wait_for_timeout(700)
                    usd = page.locator('.product-pricing__currency-item:has-text("USD")')
                    if await usd.count() > 0:
                        await usd.first.click()
                        await page.wait_for_timeout(800)
                except:
                    pass
                plans = []
                for n_days in [7, 14, 30]:
                    price, actual_days = await _holafly_get_plan_for_days(page, n_days)
                    if price:
                        plans.append({"price": price, "data": "Unlimited", "days": actual_days})
                if not plans:
                    try:
                        pt = await page.locator('[data-qa="priceLabel"]').first.inner_text(timeout=3000)
                        pm = re.search(r'[\$€£]\s*(\d+(?:[.,]\d+)?)', pt)
                        if pm:
                            plans.append({"price": float(pm.group(1).replace(',', '.')),
                                          "data": "Unlimited", "days": 1})
                    except:
                        pass
                async with lock:
                    if plans:
                        results[country] = plans
                        log.info(f"    ✓ {len(plans)} plans  [{country}]")
                    else:
                        log.warning(f"    ✗ No plans for {country}")
            except Exception as e:
                log.error(f"    ✗ {country}: {e}")
            finally:
                await page.close()

    await asyncio.gather(*[_one(c) for c in countries])
    return results

async def scrape_nomad(ctx, countries, concurrency=5):
    results = {}
    sem  = asyncio.Semaphore(concurrency)
    lock = asyncio.Lock()

    async def _one(country):
        async with sem:
            slug = get_slug(country, NOMAD_SLUGS)
            url  = f"https://www.getnomad.app/en/esim/{slug}"
            page = await ctx.new_page()
            try:
                log.info(f"  Nomad → {country}")
                await page.goto(url, timeout=30000, wait_until="domcontentloaded")
                plans = await extract_plans(page, 3000)
                async with lock:
                    if plans:
                        results[country] = plans
                        log.info(f"    ✓ {len(plans)} plans  [{country}]")
                    else:
                        log.warning(f"    ✗ No plans for {country}")
            except Exception as e:
                log.error(f"    ✗ {country}: {e}")
            finally:
                await page.close()

    await asyncio.gather(*[_one(c) for c in countries])
    return results

async def scrape_esimo(ctx, countries):
    # Esimo has global plans (no per-country pages) — scrape homepage once and apply to all countries
    results = {}
    page = await ctx.new_page()
    log.info("  Esimo → (homepage, global plans)")
    global_plans = []
    try:
        await page.goto("https://esimo.com/", timeout=30000, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        # Parse plan cards: class contains "border-2 border-primary-700 rounded-2xl"
        cards = await page.evaluate("""() => {
            const results = [];
            document.querySelectorAll('[class*="border-primary-700"]').forEach(el => {
                const t = (el.innerText || '').trim();
                if (t.match(/GB/) && t.match(/\\$/)) results.push(t.slice(0, 200));
            });
            return results;
        }""")
        seen = set()
        for card in cards:
            gb_m  = re.search(r'(\d+(?:\.\d+)?)\s*GB', card)
            pr_m  = re.search(r'\$\s*(\d+(?:[.,]\d+)?)', card)
            if gb_m and pr_m:
                key = (gb_m.group(1), pr_m.group(1))
                if key not in seen:
                    seen.add(key)
                    global_plans.append({
                        "price": float(pr_m.group(1).replace(',', '.')),
                        "data": f"{gb_m.group(1)}GB",
                        "days": None,  # recurring subscription, no fixed validity
                    })
        log.info(f"    ✓ {len(global_plans)} global plans")
    except Exception as e:
        log.error(f"    ✗ Esimo homepage: {e}")

    # Apply the same global plans to every requested country
    for country in countries:
        if global_plans:
            results[country] = list(global_plans)

    await page.close()
    return results

async def scrape_saily(ctx, countries, concurrency=8):
    results = {}
    sem  = asyncio.Semaphore(concurrency)
    lock = asyncio.Lock()

    async def _one(country):
        async with sem:
            slug = get_slug(country, SAILY_SLUGS)
            url  = f"https://saily.com/esim-{slug}/"
            page = await ctx.new_page()
            try:
                log.info(f"  Saily → {country}")
                await page.goto(url, timeout=30000, wait_until="domcontentloaded")
                for _ in range(3):
                    await page.evaluate("window.scrollBy(0, 500)")
                    await page.wait_for_timeout(500)
                await page.wait_for_timeout(2000)

                plans = []
                seen  = set()
                n_cards = await page.locator('li.flex-1').count()

                for i in range(n_cards):
                    card = page.locator('li.flex-1').nth(i)
                    card_text = await card.inner_text()
                    if not re.search(r'US\$', card_text):
                        continue

                    gb_m = re.search(r'([\d.]+|Unlimited)\s*GB', card_text, re.IGNORECASE)
                    data = ("Unlimited" if (not gb_m or gb_m.group(1).lower() == 'unlimited')
                            else f"{gb_m.group(1)}GB") if gb_m else None

                    has_select = await card.locator('select').count() > 0
                    if has_select:
                        select = card.locator('select').first
                        option_labels = await select.evaluate(
                            'el => Array.from(el.options).map(o => o.text.trim())'
                        )
                        for opt_label in option_labels:
                            day_m = re.search(r'(\d+)', opt_label)
                            if not day_m:
                                continue
                            await select.select_option(label=opt_label)
                            await page.wait_for_timeout(400)
                            updated = await card.inner_text()
                            pr_m = re.search(r'US\$\s*(\d+(?:[.,]\d+)?)', updated)
                            if pr_m:
                                price = float(pr_m.group(1).replace(',', '.'))
                                days  = int(day_m.group(1))
                                key   = (price, data, days)
                                if key not in seen and 0.5 < price < 500:
                                    seen.add(key)
                                    plans.append({"price": price, "data": data, "days": days})
                    else:
                        day_m = re.search(r'(\d+)\s*days?', card_text, re.IGNORECASE)
                        pr_m  = re.search(r'US\$\s*(\d+(?:[.,]\d+)?)', card_text)
                        if pr_m:
                            price = float(pr_m.group(1).replace(',', '.'))
                            days  = int(day_m.group(1)) if day_m else None
                            key   = (price, data, days)
                            if key not in seen and 0.5 < price < 500:
                                seen.add(key)
                                plans.append({"price": price, "data": data, "days": days})

                async with lock:
                    if plans:
                        results[country] = plans
                        log.info(f"    ✓ {len(plans)} plans  [{country}]")
                    else:
                        log.warning(f"    ✗ No plans for {country}")
            except Exception as e:
                log.error(f"    ✗ {country}: {e}")
            finally:
                await page.close()

    await asyncio.gather(*[_one(c) for c in countries])
    return results

# ── Diff ──────────────────────────────────────────────────────────────────────
def compute_diff(prev, curr):
    changes = []
    for comp in curr:
        for country in curr[comp]:
            cp = {(p.get("data"),p.get("days")):p["price"] for p in curr[comp].get(country,[])}
            pp = {(p.get("data"),p.get("days")):p["price"] for p in prev.get(comp,{}).get(country,[])}
            for key, price in cp.items():
                old = pp.get(key)
                if old and abs(price-old)/old > 0.05:
                    changes.append({"competitor":comp,"country":country,"data":key[0],"days":key[1],
                        "prev_price":old,"curr_price":price,"change_pct":round((price-old)/old*100,1),
                        "direction":"up" if price>old else "down"})
                elif not old:
                    changes.append({"competitor":comp,"country":country,"data":key[0],"days":key[1],
                        "prev_price":None,"curr_price":price,"change_pct":None,"direction":"new"})
    return {"timestamp":datetime.now().isoformat(),"total_changes":len(changes),
            "changes":sorted(changes,key=lambda x:abs(x["change_pct"] or 0),reverse=True)}

# ── Slack ─────────────────────────────────────────────────────────────────────
def send_slack(wh, diff, results):
    import urllib.request, urllib.error
    if not wh or not wh.startswith("https://hooks.slack.com"): return
    changes = [c for c in diff.get("changes",[]) if c["direction"]!="new"]
    if not changes:
        text = f"✅ *Voye Scan Complete* — No significant competitor price changes.\n_{datetime.now().strftime('%d %b %Y %H:%M')}_"
    else:
        lines = [f"*📡 Voye Competitor Price Alert — {datetime.now().strftime('%d %b %Y')}*",
                 f"_{len(changes)} price changes detected_\n"]
        for c in changes[:8]:
            arr = "↑" if c["direction"]=="up" else "↓"
            lines.append(f"• {c['competitor']} | {c['country']} {c['data']} {c['days']}d: ${c['prev_price']} → ${c['curr_price']} *{arr}{c['change_pct']:+.0f}%*")
        text = "\n".join(lines)
    try:
        req = urllib.request.Request(wh, json.dumps({"text":text}).encode(),
              headers={"Content-Type":"application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=10)
        log.info("Slack ✓")
    except Exception as e:
        log.error(f"Slack failed: {e}")

# ── Main ──────────────────────────────────────────────────────────────────────
SCRAPERS = {"airalo":scrape_airalo,"holafly":scrape_holafly,
            "nomad":scrape_nomad,"esimo":scrape_esimo,"saily":scrape_saily}

async def run(headless=True, competitors=None, countries=None, slack_wh=None):
    if competitors is None: competitors = list(SCRAPERS.keys())
    if countries is None:   countries  = TARGETS
    log.info(f"Starting — {len(competitors)} competitors × {len(countries)} countries")
    start = time.time()
    if PRICES_FILE.exists(): shutil.copy(PRICES_FILE, PREV_FILE)
    
    results = {}
    async with async_playwright() as p:
        browser, ctx = await make_context(p, headless)
        try:
            active = [c for c in competitors if c in SCRAPERS]
            log.info(f"Running {len(active)} competitors concurrently")

            async def _run_comp(comp):
                log.info(f"\n── {comp.upper()} ──")
                return comp, await SCRAPERS[comp](ctx, countries)

            gathered = await asyncio.gather(
                *[_run_comp(c) for c in active],
                return_exceptions=True
            )
            for item in gathered:
                if isinstance(item, Exception):
                    log.error(f"Competitor crashed: {item}")
                else:
                    comp, data = item
                    results[comp] = data
        finally:
            await ctx.close(); await browser.close()
    
    output = {"scraped_at":datetime.now().isoformat(),"competitors":results,
              "countries_scraped":countries,
              "summary":{c:{k:len(v) for k,v in d.items()} for c,d in results.items()}}
    PRICES_FILE.write_text(json.dumps(output,indent=2))
    log.info(f"✓ Saved → {PRICES_FILE}")
    
    prev = {}
    if PREV_FILE.exists():
        try: prev = json.loads(PREV_FILE.read_text()).get("competitors",{})
        except: pass
    diff = compute_diff(prev, results)
    DIFF_FILE.write_text(json.dumps(diff,indent=2))
    log.info(f"✓ Diff → {DIFF_FILE} ({diff['total_changes']} changes)")
    
    wh = slack_wh or os.environ.get("SLACK_WEBHOOK_URL","")
    if wh: send_slack(wh, diff, results)
    
    elapsed = round(time.time()-start,1)
    print(f"\n{'='*50}\nSCRAPE SUMMARY\n{'='*50}")
    for comp, data in results.items():
        total = sum(len(v) for v in data.values())
        print(f"  {comp:12} → {len(data):3d} countries, {total:4d} plans")
    print(f"\n  Changes: {diff['total_changes']}")
    for c in diff["changes"][:5]:
        print(f"    {c['competitor']} {c['country']} {c['data']} {c['days']}d: {c['change_pct']:+.1f}%" if c['change_pct'] else f"    {c['competitor']} {c['country']}: new")
    print("="*50)
    return output, diff

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--competitor", nargs="+", default=None)
    p.add_argument("--country", nargs="+", default=None)
    p.add_argument("--headless", default="true")
    p.add_argument("--slack", default=None)
    args = p.parse_args()
    asyncio.run(run(headless=args.headless.lower()!="false",
                    competitors=args.competitor, countries=args.country,
                    slack_wh=args.slack))

if __name__ == "__main__":
    main()
