#!/usr/bin/env python3
"""
Voye Dashboard Builder
======================
Reads competitor_prices.json + Voye master data and builds index.html
Then pushes to GitHub automatically.

Usage:
    python3 build_dashboard.py                    # build + push
    python3 build_dashboard.py --build-only       # just build, no push
"""

import json
import sys
import os
import subprocess
import argparse
from pathlib import Path
from datetime import datetime

BASE_DIR   = Path(__file__).parent
DATA_DIR   = BASE_DIR / "data"
PRICES_FILE = DATA_DIR / "competitor_prices.json"
VOYE_FILE   = DATA_DIR / "voye_master.json"
OUT_FILE    = BASE_DIR / "index.html"

COMP_COLORS = {
    "airalo":  "#F97316",
    "holafly": "#8B5CF6",
    "esimo":   "#EC4899",
    "saily":   "#22C55E",
}

def load_competitor_data():
    if not PRICES_FILE.exists():
        print(f"ERROR: {PRICES_FILE} not found. Run scraper.py first.")
        sys.exit(1)
    with open(PRICES_FILE) as f:
        data = json.load(f)
    competitors = {k: v for k, v in data.get("competitors", {}).items() if k != "nomad"}
    return competitors, data.get("scraped_at", "")

def load_voye_data():
    """Load Voye pricing — from voye_master.json if exists, else use embedded data."""
    if VOYE_FILE.exists():
        with open(VOYE_FILE) as f:
            return json.load(f)
    print("WARNING: voye_master.json not found — using last known Voye prices.")
    return {}

def build_country_stats(voye_data):
    stats = []
    for country, plans in voye_data.items():
        changed = [p for p in plans if p.get('changed')]
        prices_old = [p['price'] for p in plans if p.get('price')]
        prices_new = [p.get('new_price', p['price']) for p in plans if p.get('price')]
        if not prices_old:
            continue
        avg_old = sum(prices_old) / len(prices_old)
        avg_new = sum(prices_new) / len(prices_new)
        pct = round((avg_new - avg_old) / avg_old * 100) if avg_old else 0
        stats.append({'country': country, 'total': len(plans), 'changed': len(changed), 'pct': pct})
    return sorted(stats, key=lambda x: x['country'])

def build_changes(voye_data):
    changes = []
    for country, plans in voye_data.items():
        for p in plans:
            if p.get('changed') and p.get('price') and p.get('new_price'):
                pct = round((p['new_price'] - p['price']) / p['price'] * 100)
                changes.append({
                    'country': country,
                    'data': p.get('data', '?'),
                    'days': p.get('days'),
                    'old': p['price'],
                    'new': p['new_price'],
                    'pct': pct
                })
    return sorted(changes, key=lambda x: abs(x['pct']), reverse=True)

def _match_comp_plan(plan, cplans):
    """Find best matching competitor plan by data type then by days."""
    m = next((cp for cp in cplans if cp.get('data') == plan.get('data')), None)
    if not m:
        m = next((cp for cp in cplans if cp.get('days') == plan.get('days')), None)
    return m

def build_competitive_analysis(voye_data, comp_data):
    n_cheaper = n_pricier = n_needs_update = 0
    country_advantage = []
    for country, plans in voye_data.items():
        plan_adv = []
        for plan in plans:
            voye_p = plan.get('new_price') or plan.get('price')
            if not voye_p:
                continue
            comp_prices = []
            for cdata in comp_data.values():
                m = _match_comp_plan(plan, (cdata or {}).get(country, []))
                if m and m.get('price'):
                    comp_prices.append(m['price'])
            if not comp_prices:
                continue
            avg = sum(comp_prices) / len(comp_prices)
            if voye_p < avg:
                n_cheaper += 1
                plan_adv.append(round((avg - voye_p) / avg * 100))
            else:
                n_pricier += 1
                gap = round((voye_p - avg) / avg * 100)
                if gap > 10:
                    n_needs_update += 1
                plan_adv.append(-gap)
        if plan_adv:
            country_advantage.append({
                'country': country,
                'adv': round(sum(plan_adv) / len(plan_adv)),
                'plans': len(plan_adv),
            })
    strong = sorted([m for m in country_advantage if m['adv'] > 0], key=lambda x: x['adv'], reverse=True)[:12]
    weak   = sorted([m for m in country_advantage if m['adv'] < 0], key=lambda x: x['adv'])[:12]
    return n_cheaper, n_pricier, n_needs_update, strong, weak

def build_competitor_breakdown(voye_data, comp_data):
    result = []
    for comp_name, cdata in comp_data.items():
        cheaper = equal = pricier = 0
        for country, plans in voye_data.items():
            cplans = (cdata or {}).get(country, [])
            for plan in plans:
                voye_p = plan.get('new_price') or plan.get('price')
                if not voye_p:
                    continue
                m = _match_comp_plan(plan, cplans)
                if not (m and m.get('price')):
                    continue
                diff = (voye_p - m['price']) / m['price'] * 100
                if abs(diff) <= 1:
                    equal += 1
                elif voye_p < m['price']:
                    cheaper += 1
                else:
                    pricier += 1
        result.append({'name': comp_name, 'cheaper': cheaper, 'equal': equal,
                       'pricier': pricier, 'total': cheaper + equal + pricier})
    return result

def build_alerts(voye_data, comp_data):
    overpriced = []
    price_matches = []
    for country, plans in voye_data.items():
        for plan in plans:
            voye_p = plan.get('new_price') or plan.get('price')
            if not voye_p:
                continue
            all_comp_prices, per_comp = [], {}
            for comp_name, cdata in comp_data.items():
                m = _match_comp_plan(plan, (cdata or {}).get(country, []))
                if m and m.get('price'):
                    all_comp_prices.append(m['price'])
                    per_comp[comp_name] = m['price']
            if not all_comp_prices:
                continue
            avg = sum(all_comp_prices) / len(all_comp_prices)
            gap_pct = round((voye_p - avg) / avg * 100)
            if gap_pct > 20:
                overpriced.append({'country': country, 'data': plan.get('data', '?'),
                                   'days': plan.get('days'), 'voye': voye_p,
                                   'avg_comp': round(avg, 2), 'gap': gap_pct})
            for comp_name, cp in per_comp.items():
                if abs((voye_p - cp) / cp * 100) <= 1:
                    price_matches.append({'country': country, 'data': plan.get('data', '?'),
                                          'days': plan.get('days'), 'voye': voye_p,
                                          'comp': comp_name, 'comp_price': cp})
                    break
    stale = sorted([c for c, plans in voye_data.items()
                    if not any(p.get('changed') for p in plans)])
    return (sorted(overpriced, key=lambda x: x['gap'], reverse=True)[:8],
            price_matches[:8], stale)

def build_html(voye_data, comp_data, scraped_at, update_date="22 May 2026"):
    stats = build_country_stats(voye_data)
    changes = build_changes(voye_data)
    n_up = len([c for c in changes if c['pct'] > 0])
    n_dn = len([c for c in changes if c['pct'] < 0])
    n_countries = len(voye_data)
    n_plans = sum(len(v) for v in voye_data.values())
    competitors = list(comp_data.keys())
    scan_date = scraped_at[:10] if scraped_at else "N/A"

    n_cheaper, n_pricier, n_needs_update, strong_markets, weak_markets = build_competitive_analysis(voye_data, comp_data)
    comp_breakdown = build_competitor_breakdown(voye_data, comp_data)
    alerts_overpriced, alerts_matches, alerts_stale = build_alerts(voye_data, comp_data)
    max_strong_adv = strong_markets[0]['adv'] if strong_markets else 1
    max_weak_adv   = abs(weak_markets[0]['adv']) if weak_markets else 1
    recent_updates = sorted(
        [{'country': c, 'changed': len([p for p in plans if p.get('changed')]),
          'total': len(plans),
          'pct': round(len([p for p in plans if p.get('changed')]) / len(plans) * 100)}
         for c, plans in voye_data.items() if any(p.get('changed') for p in plans)],
        key=lambda x: x['changed'], reverse=True
    )[:12]

    total_compared = n_cheaper + n_pricier
    cheaper_pct = round(n_cheaper / total_compared * 100) if total_compared > 0 else 0
    pricier_pct = round(n_pricier / total_compared * 100) if total_compared > 0 else 0

    diff_file = DATA_DIR / "diff_report.json"
    diff_data = {}
    if diff_file.exists():
        with open(diff_file) as f:
            diff_data = json.load(f)
    diff_date = (diff_data.get('timestamp') or scraped_at or '')[:10] or 'N/A'
    diff_json = json.dumps(diff_data, separators=(',',':'))

    voye_json = json.dumps(voye_data, separators=(',',':'))
    changes_json = json.dumps(changes, separators=(',',':'))
    stats_json = json.dumps(stats, separators=(',',':'))
    comp_json = json.dumps(comp_data, separators=(',',':'))
    colors_json = json.dumps(COMP_COLORS, separators=(',',':'))
    competitors_json = json.dumps(competitors, separators=(',',':'))
    strong_markets_json    = json.dumps(strong_markets,    separators=(',',':'))
    weak_markets_json      = json.dumps(weak_markets,      separators=(',',':'))
    comp_breakdown_json    = json.dumps(comp_breakdown,    separators=(',',':'))
    alerts_overpriced_json = json.dumps(alerts_overpriced, separators=(',',':'))
    alerts_matches_json    = json.dumps(alerts_matches,    separators=(',',':'))
    alerts_stale_json      = json.dumps(alerts_stale,      separators=(',',':'))
    recent_updates_json    = json.dumps(recent_updates,    separators=(',',':'))

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Voye Price Intelligence</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/react/18.2.0/umd/react.production.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/react-dom/18.2.0/umd/react-dom.production.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/babel-standalone/7.23.2/babel.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/xlsx/0.18.5/xlsx.full.min.js"></script>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=Roboto+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Inter',sans-serif;background:#F8F9FC;color:#1A1D2E;min-height:100vh}}
::-webkit-scrollbar{{width:5px;height:5px}}
::-webkit-scrollbar-track{{background:#F1F5F9}}
::-webkit-scrollbar-thumb{{background:#CBD5E1;border-radius:3px}}
</style>
</head>
<body>
<div id="root"></div>
<script>
const VOYE_DATA={voye_json};
const ALL_CHANGES={changes_json};
const STATS={stats_json};
const COMP_DATA={comp_json};
const COMP_COLORS={colors_json};
const COMPETITORS={competitors_json};
const N_UP={n_up};
const N_DN={n_dn};
const N_CHANGES={len(changes)};
const N_COUNTRIES={n_countries};
const N_PLANS={n_plans};
const N_CHEAPER={n_cheaper};
const N_PRICIER={n_pricier};
const N_NEEDS_UPDATE={n_needs_update};
const CHEAPER_PCT={cheaper_pct};
const PRICIER_PCT={pricier_pct};
const STRONG_MARKETS={strong_markets_json};
const WEAK_MARKETS={weak_markets_json};
const MAX_STRONG_ADV={max_strong_adv};
const MAX_WEAK_ADV={max_weak_adv};
const COMP_BREAKDOWN={comp_breakdown_json};
const ALERTS_OVERPRICED={alerts_overpriced_json};
const ALERTS_MATCHES={alerts_matches_json};
const ALERTS_STALE={alerts_stale_json};
const RECENT_UPDATES={recent_updates_json};
const UPDATE_DATE="{update_date}";
const SCAN_DATE="{scan_date}";
const DIFF_DATA={diff_json};
const DIFF_DATE="{diff_date}";
</script>
<script type="text/babel">
const {{ useState, useMemo, useCallback }} = React;

function Pill({{cls, text}}) {{
  const s = {{
    info:   {{background:'#DBEAFE',color:'#1E40AF'}},
    up:     {{background:'#D1FAE5',color:'#065F46'}},
    dn:     {{background:'#FEE2E2',color:'#991B1B'}},
    na:     {{background:'#F1F5F9',color:'#475569'}},
    amber:  {{background:'#FEF3C7',color:'#92400E'}},
    purple: {{background:'#EDE9FE',color:'#5B21B6'}},
  }};
  return <span style={{{{display:'inline-flex',alignItems:'center',padding:'3px 10px',borderRadius:20,fontSize:11,fontWeight:600,...s[cls]}}}}>{{text}}</span>;
}}

function App() {{
  const [tab, setTab] = useState('overview');
  const [filterMode, setFilterMode] = useState('all');
  const [searchQ, setSearchQ] = useState('');
  const [selCountry, setSelCountry] = useState(Object.keys(VOYE_DATA).sort()[0]);
  const [aiText, setAiText] = useState('');
  const [aiLoading, setAiLoading] = useState(false);
  const [voyeState, setVoyeState] = useState(() => {{
    try {{ return JSON.parse(localStorage.getItem('voye_state')||'null') || VOYE_DATA; }} catch(e) {{ return VOYE_DATA; }}
  }});
  const [manualEdits, setManualEdits] = useState(() => {{
    try {{ return JSON.parse(localStorage.getItem('manual_edits')||'{{}}'); }} catch(e) {{ return {{}}; }}
  }});
  const [editMode, setEditMode] = useState(false);
  const [uploadSummary, setUploadSummary] = useState(null);
  const [saveStatus, setSaveStatus] = useState('');
  const [reportType, setReportType] = useState('comp');
  const [compFilter, setCompFilter] = useState('all');
  const [dateFrom, setDateFrom] = useState(DIFF_DATE);
  const [dateTo, setDateTo] = useState(DIFF_DATE);
  const [previewRows, setPreviewRows] = useState([]);
  const [previewHeaders, setPreviewHeaders] = useState([]);
  const [previewTotal, setPreviewTotal] = useState(0);

  const filteredChanges = useMemo(() => {{
    let list = ALL_CHANGES;
    if (filterMode === 'up') list = list.filter(c => c.pct > 0);
    if (filterMode === 'down') list = list.filter(c => c.pct < 0);
    if (searchQ) list = list.filter(c => c.country.toLowerCase().includes(searchQ.toLowerCase()) || (c.data||'').toLowerCase().includes(searchQ.toLowerCase()));
    return list;
  }}, [filterMode, searchQ]);

  const goCompare = useCallback((country) => {{ setSelCountry(country); setTab('compare'); }}, []);

  const runAI = useCallback(async () => {{
    setAiLoading(true); setAiText('');
    const top = ALL_CHANGES.slice(0,10).map(c => c.country+' '+c.data+' '+c.days+'d: $'+c.old+'→$'+c.new+' ('+(c.pct>0?'+':'')+c.pct+'%)').join('\\n');
    try {{
      const res = await fetch('https://api.anthropic.com/v1/messages', {{
        method:'POST', headers:{{'Content-Type':'application/json'}},
        body: JSON.stringify({{ model:'claude-sonnet-4-20250514', max_tokens:1000,
          messages:[{{role:'user',content:'You are a pricing strategist for Voye Global, an eSIM provider.\\n\\nPricing update ('+UPDATE_DATE+'):\\n- '+N_CHANGES+' price changes across '+N_COUNTRIES+' destinations\\n- '+N_UP+' increases, '+N_DN+' decreases\\n\\nBiggest changes:\\n'+top+'\\n\\nGive 5 sharp actionable strategic insights. English bullet points.'}}]
        }})
      }});
      const data = await res.json();
      setAiText(data.content?.filter(b=>b.type==='text').map(b=>b.text).join('') || 'No response');
    }} catch(e) {{ setAiText('Error — try again.'); }}
    setAiLoading(false);
  }}, []);

  const handleUpload = useCallback(async (e) => {{
    const file = e.target.files[0];
    if (!file) return;
    const ab = await file.arrayBuffer();
    const wb = XLSX.read(ab, {{type:'array'}});
    const ws = wb.Sheets[wb.SheetNames[0]];
    const rows = XLSX.utils.sheet_to_json(ws);
    const newVoye = JSON.parse(JSON.stringify(voyeState));
    let updated = 0, skipped = [];
    rows.forEach(row => {{
      const country = (row['Country']||row['country']||'').trim();
      const data = (row['Data']||row['Package']||row['package']||row['data']||'').trim();
      const days = parseInt(row['Days']||row['days']);
      const price = parseFloat(row['Price']||row['price']);
      if (!country||!data||isNaN(days)||isNaN(price)) {{ skipped.push('invalid row'); return; }}
      if (!newVoye[country]) {{ skipped.push('Unknown: '+country); return; }}
      const plan = newVoye[country].find(p => p.data===data && p.days===days);
      if (plan) {{ plan.new_price=price; plan.changed=true; updated++; }}
      else skipped.push(country+' '+data+' '+days+'d');
    }});
    setVoyeState(newVoye);
    try {{ localStorage.setItem('voye_state', JSON.stringify(newVoye)); }} catch(ex) {{}}
    setUploadSummary({{updated, skipped: skipped.slice(0,5), total: rows.length}});
    e.target.value='';
  }}, [voyeState]);

  const saveEdits = useCallback(() => {{
    try {{
      localStorage.setItem('manual_edits', JSON.stringify(manualEdits));
      localStorage.setItem('voye_state', JSON.stringify(voyeState));
    }} catch(ex) {{}}
    setSaveStatus('Saved ✓');
    setTimeout(() => setSaveStatus(''), 2000);
  }}, [manualEdits, voyeState]);

  const resetEdits = useCallback(() => {{
    if (!confirm('Reset all manual edits and uploaded prices to original data?')) return;
    localStorage.removeItem('manual_edits');
    localStorage.removeItem('voye_state');
    setVoyeState(VOYE_DATA);
    setManualEdits({{}});
    setEditMode(false);
    setUploadSummary(null);
  }}, []);

  const buildReportRows = useCallback((type, filter) => {{
    if (type === 'comp') {{
      const headers = ['Destination','Package','Days','Competitor','Old Price','New Price','Change %','Direction'];
      const rows = (DIFF_DATA.changes||[])
        .filter(c => filter === 'all' || c.competitor === filter)
        .map(c => [c.country, c.data||'—', c.days+'d', c.competitor.charAt(0).toUpperCase()+c.competitor.slice(1), '$'+c.prev_price.toFixed(2), '$'+c.curr_price.toFixed(2), (c.change_pct>0?'+':'')+c.change_pct+'%', c.direction==='up'?'▲ Up':'▼ Down']);
      const rawRows = (DIFF_DATA.changes||[])
        .filter(c => filter === 'all' || c.competitor === filter)
        .map(c => [c.country, c.data||'—', c.days, c.competitor.charAt(0).toUpperCase()+c.competitor.slice(1), c.prev_price, c.curr_price, c.change_pct/100, c.direction==='up'?'Up':'Down']);
      return {{headers, rows, rawRows}};
    }} else {{
      const headers = ['Destination','Package','Days','Old Price','New Price','Change $','Change %','Date'];
      const rows = [], rawRows = [];
      Object.entries(VOYE_DATA).forEach(([country, plans]) => {{
        plans.filter(p => p.changed && p.price && p.new_price).forEach(p => {{
          const pct = Math.round((p.new_price - p.price) / p.price * 100);
          const diff = (p.new_price - p.price).toFixed(2);
          rows.push([country, p.data||'—', (p.days||'?')+'d', '$'+p.price.toFixed(2), '$'+p.new_price.toFixed(2), (p.new_price>p.price?'+':'')+diff, (pct>0?'+':'')+pct+'%', UPDATE_DATE]);
          rawRows.push([country, p.data||'—', p.days, p.price, p.new_price, p.new_price-p.price, (p.new_price-p.price)/p.price, UPDATE_DATE]);
        }});
      }});
      return {{headers, rows, rawRows}};
    }}
  }}, []);

  const generatePreview = useCallback(() => {{
    const {{headers, rows}} = buildReportRows(reportType, compFilter);
    setPreviewHeaders(headers);
    setPreviewRows(rows.slice(0,10));
    setPreviewTotal(rows.length);
  }}, [reportType, compFilter, buildReportRows]);

  const downloadReport = useCallback(() => {{
    const {{headers, rawRows}} = buildReportRows(reportType, compFilter);
    const wb = XLSX.utils.book_new();
    const ws = XLSX.utils.aoa_to_sheet([headers, ...rawRows]);
    ws['!cols'] = headers.map((_,i) => ({{wch:i===0?22:13}}));
    if (reportType === 'comp') {{
      const numCols = [4,5,6];
      rawRows.forEach((_,ri) => numCols.forEach(ci => {{
        const addr = XLSX.utils.encode_cell({{r:ri+1,c:ci}});
        const cell = ws[addr];
        if (cell && typeof cell.v === 'number') cell.z = ci===6?'0.0%':'$#,##0.00';
      }}));
    }} else {{
      rawRows.forEach((_,ri) => [3,4,5,6].forEach(ci => {{
        const addr = XLSX.utils.encode_cell({{r:ri+1,c:ci}});
        const cell = ws[addr];
        if (cell && typeof cell.v === 'number') cell.z = ci===6?'0.0%':'$#,##0.00';
      }}));
    }}
    XLSX.utils.book_append_sheet(wb, ws, 'Report');
    const part = reportType==='comp'
      ? (compFilter==='all'?'All_Competitors':compFilter.charAt(0).toUpperCase()+compFilter.slice(1))
      : 'Voye';
    const d1 = dateFrom.replace(/-/g,'');
    const d2 = dateTo && dateTo!==dateFrom ? '_to_'+dateTo.replace(/-/g,'') : '';
    XLSX.writeFile(wb, part+'_Price_Changes_'+d1+d2+'.xlsx');
  }}, [reportType, compFilter, dateFrom, dateTo, buildReportRows]);

  const tabLabels = {{overview:'Overview',changes:'Price Changes',compare:'vs Competitors',reports:'Reports',ai:'AI Analysis'}};

  return (
    <div style={{{{display:'flex',minHeight:'100vh',fontFamily:"'Inter',sans-serif",background:'#F8F9FC'}}}}>
      <style>{{`
        button{{font-family:'Inter',sans-serif;cursor:pointer}}
        .nav-item{{display:flex;align-items:center;gap:10px;padding:9px 12px;border-radius:10px;font-size:13px;font-weight:500;color:#64748B;transition:all .15s;border:none;background:transparent;width:100%;text-align:left;margin-bottom:2px}}
        .nav-item:hover{{background:#F8F9FC;color:#1A1D2E}}
        .nav-item.active{{background:#EEF2FF;color:#6366F1;font-weight:600}}
        .card{{background:#fff;border-radius:16px;box-shadow:0 1px 3px rgba(0,0,0,.08),0 4px 16px rgba(0,0,0,.04);border:1px solid #E8EBF0}}
        .fbtn{{background:#fff;border:1px solid #E2E8F0;border-radius:8px;padding:6px 14px;font-size:13px;font-weight:500;color:#64748B;transition:all .15s}}
        .fbtn:hover{{border-color:#CBD5E1;color:#1A1D2E}}
        .fbtn.on{{background:#EEF2FF;border-color:#C7D2FE;color:#6366F1}}
        .dbtn{{background:#fff;border:1px solid #E2E8F0;border-radius:7px;padding:4px 10px;font-size:12px;font-weight:500;color:#64748B;transition:all .15s}}
        .dbtn:hover{{border-color:#CBD5E1;color:#1A1D2E;background:#F8F9FC}}
        .dbtn.on{{background:#EEF2FF;border-color:#C7D2FE;color:#6366F1}}
        tr:hover td{{background:#F0F4FF!important}}
        .tbl-row:nth-child(even) td{{background:#FAFBFD}}
        @keyframes fadeIn{{from{{opacity:0;transform:translateY(4px)}}to{{opacity:1;transform:translateY(0)}}}}
        .page{{animation:fadeIn .2s ease}}
        @keyframes blink{{0%,100%{{opacity:1}}50%{{opacity:.3}}}}
        .blink{{animation:blink 1.2s infinite}}
      `}}</style>

      {{/* ── SIDEBAR ── */}}
      <div style={{{{width:220,background:'#fff',borderRight:'1px solid #E8EBF0',display:'flex',flexDirection:'column',position:'fixed',left:0,top:0,bottom:0,zIndex:100}}}}>
        <div style={{{{padding:'18px 16px',borderBottom:'1px solid #E8EBF0'}}}}>
          <div style={{{{display:'flex',alignItems:'center',gap:10}}}}>
            <div style={{{{width:34,height:34,borderRadius:10,background:'linear-gradient(135deg,#6366F1,#8B5CF6)',display:'flex',alignItems:'center',justifyContent:'center',fontSize:16}}}}>📡</div>
            <div>
              <div style={{{{fontSize:14,fontWeight:700,color:'#1A1D2E'}}}}>Voye</div>
              <div style={{{{fontSize:10,color:'#94A3B8',letterSpacing:'.4px',fontWeight:600}}}}>PRICE INTEL</div>
            </div>
          </div>
        </div>
        <nav style={{{{padding:'12px 10px',flex:1}}}}>
          {{[['overview','📊','Overview'],['changes','🔄','Price Changes'],['compare','🔍','vs Competitors'],['reports','📥','Reports'],['ai','🤖','AI Analysis']].map(([id,icon,label]) => (
            <button key={{id}} className={{'nav-item'+(tab===id?' active':'')}} onClick={{() => setTab(id)}}>
              <span style={{{{fontSize:15}}}}>{{icon}}</span>{{label}}
            </button>
          ))}}
        </nav>
        <div style={{{{padding:'14px 16px',borderTop:'1px solid #E8EBF0'}}}}>
          <div style={{{{fontSize:10,color:'#94A3B8',fontWeight:600,letterSpacing:'.5px',marginBottom:4}}}}>LAST SCAN</div>
          <div style={{{{fontSize:12,fontWeight:600,color:'#475569',marginBottom:8}}}}>{{SCAN_DATE}}</div>
          <div style={{{{display:'flex',gap:5}}}}>
            <span style={{{{background:'#D1FAE5',color:'#065F46',padding:'2px 8px',borderRadius:10,fontSize:11,fontWeight:600}}}}>↑ {{N_UP}}</span>
            <span style={{{{background:'#FEE2E2',color:'#991B1B',padding:'2px 8px',borderRadius:10,fontSize:11,fontWeight:600}}}}>↓ {{N_DN}}</span>
          </div>
        </div>
      </div>

      {{/* ── MAIN ── */}}
      <div style={{{{marginLeft:220,flex:1,background:'#F8F9FC',minHeight:'100vh'}}}}>

        {{/* TOP BAR */}}
        <div style={{{{background:'#fff',borderBottom:'1px solid #E8EBF0',padding:'0 28px',height:60,display:'flex',alignItems:'center',justifyContent:'space-between',position:'sticky',top:0,zIndex:50,boxShadow:'0 1px 4px rgba(0,0,0,.04)'}}}}>
          <div>
            <div style={{{{fontSize:17,fontWeight:700,color:'#1A1D2E'}}}}>{{tabLabels[tab]}}</div>
            <div style={{{{fontSize:11,color:'#94A3B8'}}}}>Updated {{UPDATE_DATE}}</div>
          </div>
          <div style={{{{display:'flex',gap:8,alignItems:'center'}}}}>
            <label style={{{{cursor:'pointer'}}}}>
              <input type="file" accept=".xlsx" style={{{{display:'none'}}}} onChange={{handleUpload}} />
              <span style={{{{background:'#EEF2FF',border:'1px solid #C7D2FE',color:'#6366F1',padding:'6px 14px',borderRadius:8,fontSize:13,fontWeight:600,cursor:'pointer',userSelect:'none'}}}}>↑ Upload Prices</span>
            </label>
            <Pill cls="info" text={{N_COUNTRIES+' markets'}} />
            <Pill cls="na" text={{N_PLANS+' packages'}} />
          </div>
        </div>

        {{/* Upload banner */}}
        {{uploadSummary && (
          <div style={{{{background:'#F0FDF4',borderBottom:'1px solid #BBF7D0',padding:'10px 28px',display:'flex',gap:20,alignItems:'center',fontSize:13}}}}>
            <span style={{{{color:'#15803D',fontWeight:700}}}}>✓ Upload complete</span>
            <span style={{{{color:'#16A34A'}}}}>{{uploadSummary.updated}} / {{uploadSummary.total}} prices updated</span>
            {{uploadSummary.skipped.length > 0 && <span style={{{{color:'#D97706'}}}}>Issues: {{uploadSummary.skipped.join(', ')}}</span>}}
            <button onClick={{() => setUploadSummary(null)}} style={{{{marginLeft:'auto',background:'transparent',border:'none',color:'#9CA3AF',fontSize:20,lineHeight:1,cursor:'pointer'}}}}>×</button>
          </div>
        )}}

        {{/* PAGE CONTENT */}}
        <div style={{{{padding:'24px 28px'}}}} className="page" key={{tab}}>

          {{/* ── OVERVIEW ── */}}
          {{tab === 'overview' && <>

            {{/* KPI Row */}}
            <div style={{{{display:'grid',gridTemplateColumns:'repeat(5,1fr)',gap:16,marginBottom:24}}}}>
              {{[
                {{icon:'🌍',val:N_COUNTRIES,label:'Total Destinations',trend:N_CHANGES+' price changes',tc:'#5B21B6',tbg:'#EDE9FE'}},
                {{icon:'📦',val:N_PLANS,label:'Total Packages',trend:COMPETITORS.length+' competitors tracked',tc:'#1E40AF',tbg:'#DBEAFE'}},
                {{icon:'✅',val:N_CHEAPER,label:'Packages Cheaper',trend:'↑ '+CHEAPER_PCT+'% of compared',tc:'#065F46',tbg:'#D1FAE5'}},
                {{icon:'⚠️',val:N_PRICIER,label:'Packages Pricier',trend:PRICIER_PCT+'% of compared',tc:'#991B1B',tbg:'#FEE2E2'}},
                {{icon:'🎯',val:N_NEEDS_UPDATE,label:'Need Repricing',trend:'>10% above market avg',tc:'#92400E',tbg:'#FEF3C7'}},
              ].map((k,i) => (
                <div key={{i}} className="card" style={{{{padding:20}}}}>
                  <div style={{{{width:42,height:42,borderRadius:12,background:k.tbg,display:'flex',alignItems:'center',justifyContent:'center',fontSize:20,marginBottom:14}}}}>{{k.icon}}</div>
                  <div style={{{{fontSize:30,fontWeight:800,color:'#1A1D2E',letterSpacing:'-1px',lineHeight:1,marginBottom:6}}}}>{{k.val}}</div>
                  <div style={{{{fontSize:13,color:'#64748B',fontWeight:500,marginBottom:12}}}}>{{k.label}}</div>
                  <span style={{{{background:k.tbg,color:k.tc,padding:'3px 10px',borderRadius:20,fontSize:11,fontWeight:600}}}}>{{k.trend}}</span>
                </div>
              ))}}
            </div>

            {{/* Advantage + Weak Markets */}}
            <div style={{{{display:'grid',gridTemplateColumns:'1fr 1fr',gap:20,marginBottom:20}}}}>
              <div className="card" style={{{{padding:20}}}}>
                <div style={{{{display:'flex',alignItems:'center',gap:8,marginBottom:4}}}}>
                  <div style={{{{width:30,height:30,borderRadius:8,background:'#D1FAE5',display:'flex',alignItems:'center',justifyContent:'center',fontSize:15}}}}>💪</div>
                  <div style={{{{fontSize:14,fontWeight:700,color:'#1A1D2E'}}}}>Competitive Advantage Markets</div>
                </div>
                <div style={{{{fontSize:12,color:'#94A3B8',marginBottom:18}}}}>Voye's biggest price advantages vs competitor average</div>
                {{STRONG_MARKETS.map((m,i) => (
                  <div key={{i}} style={{{{marginBottom:11}}}}>
                    <div style={{{{display:'flex',justifyContent:'space-between',marginBottom:4}}}}>
                      <span style={{{{fontSize:13,fontWeight:600,color:'#1A1D2E'}}}}>{{m.country}}</span>
                      <span style={{{{fontSize:12,color:'#059669',fontWeight:700}}}}>{{m.adv}}% cheaper · {{m.plans}} pkgs</span>
                    </div>
                    <div style={{{{background:'#ECFDF5',borderRadius:4,height:6,overflow:'hidden'}}}}>
                      <div style={{{{height:'100%',width:Math.round(m.adv/MAX_STRONG_ADV*100)+'%',background:'linear-gradient(90deg,#10B981,#34D399)',borderRadius:4}}}}/>
                    </div>
                  </div>
                ))}}
              </div>
              <div className="card" style={{{{padding:20}}}}>
                <div style={{{{display:'flex',alignItems:'center',gap:8,marginBottom:4}}}}>
                  <div style={{{{width:30,height:30,borderRadius:8,background:'#FEE2E2',display:'flex',alignItems:'center',justifyContent:'center',fontSize:15}}}}>🔴</div>
                  <div style={{{{fontSize:14,fontWeight:700,color:'#1A1D2E'}}}}>Weak Markets</div>
                </div>
                <div style={{{{fontSize:12,color:'#94A3B8',marginBottom:18}}}}>Markets where Voye is most expensive vs competitors</div>
                {{WEAK_MARKETS.map((m,i) => (
                  <div key={{i}} style={{{{marginBottom:11}}}}>
                    <div style={{{{display:'flex',justifyContent:'space-between',marginBottom:4}}}}>
                      <span style={{{{fontSize:13,fontWeight:600,color:'#1A1D2E'}}}}>{{m.country}}</span>
                      <span style={{{{fontSize:12,color:'#DC2626',fontWeight:700}}}}>{{Math.abs(m.adv)}}% pricier · {{m.plans}} pkgs</span>
                    </div>
                    <div style={{{{background:'#FEF2F2',borderRadius:4,height:6,overflow:'hidden'}}}}>
                      <div style={{{{height:'100%',width:Math.round(Math.abs(m.adv)/MAX_WEAK_ADV*100)+'%',background:'linear-gradient(90deg,#EF4444,#FCA5A5)',borderRadius:4}}}}/>
                    </div>
                  </div>
                ))}}
              </div>
            </div>

            {{/* Top 5 / Bottom 5 */}}
            <div style={{{{display:'grid',gridTemplateColumns:'1fr 1fr',gap:20,marginBottom:20}}}}>
              <div className="card" style={{{{padding:20}}}}>
                <div style={{{{fontSize:14,fontWeight:700,color:'#1A1D2E',marginBottom:16}}}}>🏆 Top 5 — Strongest Edge</div>
                {{STRONG_MARKETS.slice(0,5).map((m,i) => (
                  <div key={{i}} style={{{{display:'flex',alignItems:'center',gap:12,padding:'10px 0',borderBottom:'1px solid #F1F5F9'}}}}>
                    <span style={{{{fontSize:15,fontWeight:800,color:'#D1FAE5',minWidth:20,textAlign:'center'}}}}>{{i+1}}</span>
                    <div style={{{{flex:1}}}}>
                      <div style={{{{fontSize:13,fontWeight:600,color:'#1A1D2E'}}}}>{{m.country}}</div>
                      <div style={{{{fontSize:11,color:'#94A3B8'}}}}>{{m.plans}} comparable packages</div>
                    </div>
                    <Pill cls="up" text={{m.adv+'% cheaper'}} />
                  </div>
                ))}}
              </div>
              <div className="card" style={{{{padding:20}}}}>
                <div style={{{{fontSize:14,fontWeight:700,color:'#1A1D2E',marginBottom:16}}}}>📉 Bottom 5 — Least Competitive</div>
                {{WEAK_MARKETS.slice(0,5).map((m,i) => (
                  <div key={{i}} style={{{{display:'flex',alignItems:'center',gap:12,padding:'10px 0',borderBottom:'1px solid #F1F5F9'}}}}>
                    <span style={{{{fontSize:15,fontWeight:800,color:'#FEE2E2',minWidth:20,textAlign:'center'}}}}>{{i+1}}</span>
                    <div style={{{{flex:1}}}}>
                      <div style={{{{fontSize:13,fontWeight:600,color:'#1A1D2E'}}}}>{{m.country}}</div>
                      <div style={{{{fontSize:11,color:'#94A3B8'}}}}>{{m.plans}} comparable packages</div>
                    </div>
                    <Pill cls="dn" text={{Math.abs(m.adv)+'% pricier'}} />
                  </div>
                ))}}
              </div>
            </div>

            {{/* Competitor Breakdown */}}
            <div className="card" style={{{{padding:20,marginBottom:20}}}}>
              <div style={{{{fontSize:14,fontWeight:700,color:'#1A1D2E',marginBottom:4}}}}>📊 Competitor Breakdown</div>
              <div style={{{{fontSize:12,color:'#94A3B8',marginBottom:20}}}}>Voye packages cheaper, equal, or pricier vs each competitor</div>
              <div style={{{{display:'grid',gridTemplateColumns:'repeat(4,1fr)',gap:16}}}}>
                {{COMP_BREAKDOWN.map((c,i) => (
                  <div key={{i}} style={{{{background:'#F8F9FC',borderRadius:12,padding:16,border:'1px solid #E8EBF0'}}}}>
                    <div style={{{{fontSize:13,fontWeight:700,marginBottom:14,color:COMP_COLORS[c.name]||'#64748B',textTransform:'capitalize'}}}}>{{c.name}}</div>
                    <div style={{{{display:'flex',justifyContent:'space-between',marginBottom:12}}}}>
                      <div style={{{{textAlign:'center'}}}}>
                        <div style={{{{fontSize:22,fontWeight:800,color:'#10B981'}}}}>{{c.cheaper}}</div>
                        <div style={{{{fontSize:10,color:'#94A3B8',marginTop:2}}}}>cheaper</div>
                      </div>
                      <div style={{{{textAlign:'center'}}}}>
                        <div style={{{{fontSize:22,fontWeight:800,color:'#94A3B8'}}}}>{{c.equal}}</div>
                        <div style={{{{fontSize:10,color:'#94A3B8',marginTop:2}}}}>equal</div>
                      </div>
                      <div style={{{{textAlign:'center'}}}}>
                        <div style={{{{fontSize:22,fontWeight:800,color:'#EF4444'}}}}>{{c.pricier}}</div>
                        <div style={{{{fontSize:10,color:'#94A3B8',marginTop:2}}}}>pricier</div>
                      </div>
                    </div>
                    {{c.total>0 && <div style={{{{display:'flex',height:6,borderRadius:3,overflow:'hidden',background:'#E2E8F0'}}}}>
                      <div style={{{{width:Math.round(c.cheaper/c.total*100)+'%',background:'#10B981'}}}}/>
                      <div style={{{{width:Math.round(c.equal/c.total*100)+'%',background:'#CBD5E1'}}}}/>
                      <div style={{{{width:Math.round(c.pricier/c.total*100)+'%',background:'#EF4444'}}}}/>
                    </div>}}
                  </div>
                ))}}
              </div>
            </div>

            {{/* Last Update + Significant Changes */}}
            <div style={{{{display:'grid',gridTemplateColumns:'1fr 1fr',gap:20,marginBottom:20}}}}>
              <div className="card" style={{{{padding:20}}}}>
                <div style={{{{display:'flex',alignItems:'center',gap:8,marginBottom:4}}}}>
                  <div style={{{{width:30,height:30,borderRadius:8,background:'#FEF3C7',display:'flex',alignItems:'center',justifyContent:'center',fontSize:15}}}}>🕐</div>
                  <div style={{{{fontSize:14,fontWeight:700,color:'#1A1D2E'}}}}>Last Price Update</div>
                </div>
                <div style={{{{fontSize:12,color:'#94A3B8',marginBottom:16}}}}>{{UPDATE_DATE}} · destinations with most plan changes</div>
                {{RECENT_UPDATES.map((m,i) => (
                  <div key={{i}} style={{{{display:'flex',justifyContent:'space-between',alignItems:'center',padding:'9px 0',borderBottom:'1px solid #F1F5F9'}}}}>
                    <span style={{{{fontSize:13,fontWeight:600,color:'#1A1D2E'}}}}>{{m.country}}</span>
                    <div style={{{{display:'flex',alignItems:'center',gap:8}}}}>
                      <span style={{{{fontSize:11,color:'#94A3B8'}}}}>{{m.changed}}/{{m.total}} plans</span>
                      <Pill cls="amber" text={{m.pct+'% updated'}} />
                    </div>
                  </div>
                ))}}
              </div>
              <div className="card" style={{{{padding:20}}}}>
                <div style={{{{fontSize:14,fontWeight:700,color:'#1A1D2E',marginBottom:16}}}}>⚡ Significant Changes</div>
                {{ALL_CHANGES.slice(0,10).map((c,i) => (
                  <div key={{i}} style={{{{display:'flex',justifyContent:'space-between',alignItems:'center',padding:'9px 0',borderBottom:'1px solid #F1F5F9'}}}}>
                    <div>
                      <div style={{{{fontSize:13,fontWeight:600,color:'#1A1D2E'}}}}>{{c.country}}</div>
                      <div style={{{{fontSize:11,color:'#94A3B8'}}}}>{{c.data}} · {{c.days||'?'}}d</div>
                    </div>
                    <div style={{{{textAlign:'right'}}}}>
                      <div style={{{{fontFamily:"'Roboto Mono',monospace",fontSize:12,color:'#64748B',marginBottom:3}}}}>${{c.old.toFixed(2)}} → <span style={{{{color:c.pct>0?'#EF4444':'#10B981',fontWeight:700}}}}>${{c.new.toFixed(2)}}</span></div>
                      <Pill cls={{c.pct>0?'dn':'up'}} text={{(c.pct>0?'+':'')+c.pct+'%'}} />
                    </div>
                  </div>
                ))}}
              </div>
            </div>

            {{/* Alerts */}}
            <div className="card" style={{{{padding:20}}}}>
              <div style={{{{display:'flex',alignItems:'center',gap:8,marginBottom:4}}}}>
                <div style={{{{width:30,height:30,borderRadius:8,background:'#FEF3C7',display:'flex',alignItems:'center',justifyContent:'center',fontSize:15}}}}>🚨</div>
                <div style={{{{fontSize:14,fontWeight:700,color:'#1A1D2E'}}}}>Alerts</div>
              </div>
              <div style={{{{fontSize:12,color:'#94A3B8',marginBottom:20}}}}>Packages and destinations requiring attention</div>
              <div style={{{{display:'grid',gridTemplateColumns:'1fr 1fr 1fr',gap:24}}}}>
                <div>
                  <div style={{{{display:'flex',alignItems:'center',gap:6,marginBottom:12}}}}>
                    <span style={{{{fontSize:12,fontWeight:700,color:'#1A1D2E'}}}}>🔺 Significantly Overpriced</span>
                    <span style={{{{marginLeft:'auto',background:'#FEE2E2',color:'#991B1B',borderRadius:20,padding:'2px 8px',fontSize:11,fontWeight:600}}}}>{{ALERTS_OVERPRICED.length}}</span>
                  </div>
                  {{ALERTS_OVERPRICED.length===0 && <div style={{{{fontSize:12,color:'#94A3B8'}}}}>No packages flagged</div>}}
                  {{ALERTS_OVERPRICED.map((a,i) => (
                    <div key={{i}} style={{{{padding:'8px 0',borderBottom:'1px solid #F1F5F9'}}}}>
                      <div style={{{{fontSize:12,fontWeight:600,color:'#1A1D2E'}}}}>{{a.country}}</div>
                      <div style={{{{fontSize:11,color:'#94A3B8'}}}}>{{a.data}}{{a.days?' · '+a.days+'d':''}}</div>
                      <div style={{{{fontSize:11,color:'#DC2626',fontWeight:600}}}}>+{{a.gap}}% above avg · ${{a.voye}} vs ${{a.avg_comp}}</div>
                    </div>
                  ))}}
                </div>
                <div>
                  <div style={{{{display:'flex',alignItems:'center',gap:6,marginBottom:12}}}}>
                    <span style={{{{fontSize:12,fontWeight:700,color:'#1A1D2E'}}}}>⚖️ Price Matches (≤1%)</span>
                    <span style={{{{marginLeft:'auto',background:'#F1F5F9',color:'#475569',borderRadius:20,padding:'2px 8px',fontSize:11,fontWeight:600}}}}>{{ALERTS_MATCHES.length}}</span>
                  </div>
                  {{ALERTS_MATCHES.length===0 && <div style={{{{fontSize:12,color:'#94A3B8'}}}}>No price matches found</div>}}
                  {{ALERTS_MATCHES.map((a,i) => (
                    <div key={{i}} style={{{{padding:'8px 0',borderBottom:'1px solid #F1F5F9'}}}}>
                      <div style={{{{fontSize:12,fontWeight:600,color:'#1A1D2E'}}}}>{{a.country}}</div>
                      <div style={{{{fontSize:11,color:'#94A3B8'}}}}>{{a.data}}{{a.days?' · '+a.days+'d':''}}</div>
                      <div style={{{{fontSize:11,color:'#64748B'}}}}>= {{a.comp}} · ${{a.comp_price}}</div>
                    </div>
                  ))}}
                </div>
                <div>
                  <div style={{{{display:'flex',alignItems:'center',gap:6,marginBottom:12}}}}>
                    <span style={{{{fontSize:12,fontWeight:700,color:'#1A1D2E'}}}}>🕓 Not Updated</span>
                    <span style={{{{marginLeft:'auto',background:'#F1F5F9',color:'#475569',borderRadius:20,padding:'2px 8px',fontSize:11,fontWeight:600}}}}>{{ALERTS_STALE.length}}</span>
                  </div>
                  {{ALERTS_STALE.length===0 && <div style={{{{fontSize:12,color:'#94A3B8'}}}}>All destinations updated</div>}}
                  <div style={{{{display:'flex',flexWrap:'wrap',gap:5}}}}>
                    {{ALERTS_STALE.slice(0,16).map((c,i) => (
                      <span key={{i}} style={{{{fontSize:11,background:'#F1F5F9',color:'#64748B',borderRadius:6,padding:'3px 9px',fontWeight:500}}}}>{{c}}</span>
                    ))}}
                    {{ALERTS_STALE.length>16 && <span style={{{{fontSize:11,color:'#94A3B8'}}}}>+{{ALERTS_STALE.length-16}} more</span>}}
                  </div>
                </div>
              </div>
            </div>
          </>}}

          {{/* ── CHANGES ── */}}
          {{tab === 'changes' && <>
            <div style={{{{display:'flex',gap:8,flexWrap:'wrap',marginBottom:16,alignItems:'center'}}}}>
              {{[['all','All Changes'],['up','↑ Increases'],['down','↓ Decreases']].map(([f,l]) => (
                <button key={{f}} className={{'fbtn'+(filterMode===f?' on':'')}} onClick={{() => setFilterMode(f)}}>{{l}}</button>
              ))}}
              <input style={{{{background:'#fff',border:'1px solid #E2E8F0',borderRadius:8,color:'#1A1D2E',padding:'7px 12px',fontSize:13,outline:'none',width:220,boxShadow:'0 1px 2px rgba(0,0,0,.04)'}}}} placeholder="Search country or data…" value={{searchQ}} onChange={{e => setSearchQ(e.target.value)}}/>
              <span style={{{{marginLeft:'auto'}}}}><Pill cls="info" text={{filteredChanges.length+' plans'}} /></span>
            </div>
            <div className="card" style={{{{overflow:'auto'}}}}>
              <table style={{{{width:'100%',borderCollapse:'collapse',minWidth:480}}}}>
                <thead><tr style={{{{background:'#F8F9FC',borderBottom:'2px solid #E8EBF0'}}}}>
                  {{['Country','Package','Days','Old Price','New Price','Change'].map((h,i) => (
                    <th key={{h}} style={{{{padding:'11px 16px',fontSize:11,color:'#64748B',fontWeight:600,letterSpacing:'.5px',textTransform:'uppercase',textAlign:i>=3?'right':'left'}}}}>{{h}}</th>
                  ))}}
                </tr></thead>
                <tbody>
                  {{filteredChanges.map((c,i) => (
                    <tr key={{i}} className="tbl-row" style={{{{borderBottom:'1px solid #F1F5F9'}}}}>
                      <td style={{{{padding:'11px 16px',fontWeight:600,fontSize:13,color:'#1A1D2E'}}}}>{{c.country}}</td>
                      <td style={{{{padding:'11px 16px',fontSize:13,color:'#64748B'}}}}>{{c.data||'—'}}</td>
                      <td style={{{{padding:'11px 16px',fontSize:13,color:'#94A3B8'}}}}>{{c.days||'—'}}d</td>
                      <td style={{{{padding:'11px 16px',textAlign:'right'}}}}><span style={{{{fontFamily:"'Roboto Mono',monospace",fontSize:12,color:'#94A3B8'}}}}>${{c.old.toFixed(2)}}</span></td>
                      <td style={{{{padding:'11px 16px',textAlign:'right'}}}}><span style={{{{fontFamily:"'Roboto Mono',monospace",fontSize:12,color:c.pct>0?'#10B981':'#EF4444',fontWeight:700}}}}>${{c.new.toFixed(2)}}</span></td>
                      <td style={{{{padding:'11px 16px',textAlign:'right'}}}}><Pill cls={{c.pct>0?'up':'dn'}} text={{(c.pct>0?'+':'')+c.pct+'%'}} /></td>
                    </tr>
                  ))}}
                </tbody>
              </table>
            </div>
          </>}}

          {{/* ── COMPARE ── */}}
          {{tab === 'compare' && <>
            <div style={{{{display:'flex',justifyContent:'space-between',alignItems:'flex-start',marginBottom:16}}}}>
              <div style={{{{display:'flex',gap:6,alignItems:'center',flexWrap:'wrap',flex:1}}}}>
                <span style={{{{fontSize:11,color:'#94A3B8',fontWeight:600,letterSpacing:'.5px',textTransform:'uppercase',marginRight:4}}}}>Destination</span>
                {{Object.keys(voyeState).sort().map(c => (
                  <button key={{c}} className={{'dbtn'+(c===selCountry?' on':'')}} onClick={{() => setSelCountry(c)}}>{{c}}</button>
                ))}}
              </div>
              <div style={{{{display:'flex',gap:8,alignItems:'center',marginLeft:16,flexShrink:0}}}}>
                {{saveStatus && <span style={{{{fontSize:12,color:'#10B981',fontWeight:600}}}}>{{saveStatus}}</span>}}
                <button className={{'fbtn'+(editMode?' on':'')}} onClick={{() => setEditMode(m => !m)}}>✏️ {{editMode ? 'Editing' : 'Edit Prices'}}</button>
                {{Object.keys(manualEdits).length > 0 && <>
                  <button className="fbtn on" onClick={{saveEdits}} style={{{{borderColor:'#A7F3D0',color:'#059669'}}}}>💾 Save</button>
                  <button className="fbtn" onClick={{resetEdits}} style={{{{color:'#EF4444'}}}}>↺ Reset</button>
                </>}}
              </div>
            </div>
            <div className="card" style={{{{overflow:'auto'}}}}>
              <table style={{{{width:'100%',borderCollapse:'collapse'}}}}>
                <thead><tr style={{{{background:'#F8F9FC',borderBottom:'2px solid #E8EBF0'}}}}>
                  <th style={{{{padding:'11px 16px',fontSize:11,color:'#64748B',fontWeight:600,letterSpacing:'.5px',textTransform:'uppercase',textAlign:'left'}}}}>Package</th>
                  <th style={{{{padding:'11px 16px',fontSize:11,color:'#64748B',fontWeight:600,letterSpacing:'.5px',textTransform:'uppercase',textAlign:'left'}}}}>Days</th>
                  <th style={{{{padding:'11px 16px',fontSize:11,color:'#6366F1',fontWeight:700,letterSpacing:'.5px',textTransform:'uppercase',textAlign:'right'}}}}>Voye</th>
                  {{COMPETITORS.map(comp => (
                    <th key={{comp}} style={{{{padding:'11px 16px',fontSize:11,fontWeight:700,letterSpacing:'.5px',textTransform:'uppercase',textAlign:'right',color:COMP_COLORS[comp]||'#64748B'}}}}>{{comp.charAt(0).toUpperCase()+comp.slice(1)}}</th>
                  ))}}
                  <th style={{{{padding:'11px 16px',fontSize:11,color:'#64748B',fontWeight:600,letterSpacing:'.5px',textTransform:'uppercase',textAlign:'right'}}}}>vs Market</th>
                </tr></thead>
                <tbody>
                  {{(voyeState[selCountry]||[]).map((p,i) => {{
                    const vKey = 'voye|'+selCountry+'|'+(p.data||'')+'|'+p.days;
                    const baseVoyeP = p.new_price || p.price;
                    const voyeP = manualEdits[vKey] !== undefined ? (parseFloat(manualEdits[vKey])||baseVoyeP) : baseVoyeP;
                    const compPrices = COMPETITORS.map((comp,j) => {{
                      const cKey = 'comp|'+comp+'|'+selCountry+'|'+(p.data||'')+'|'+p.days;
                      const cplans = (COMP_DATA[comp]||{{}})[selCountry] || [];
                      const match = cplans.find(cp => cp.data === p.data) || cplans.find(cp => cp.days === p.days);
                      const baseP = match ? match.price : null;
                      return manualEdits[cKey] !== undefined ? (parseFloat(manualEdits[cKey])||baseP) : baseP;
                    }});
                    const validPrices = compPrices.filter(x => x !== null && x > 0);
                    const avgComp = validPrices.length ? validPrices.reduce((a,b)=>a+b,0)/validPrices.length : null;
                    const posPct = avgComp && voyeP ? Math.round((voyeP - avgComp) / avgComp * 100) : null;
                    return (
                      <tr key={{i}} className="tbl-row" style={{{{borderBottom:'1px solid #F1F5F9'}}}}>
                        <td style={{{{padding:'11px 16px',fontWeight:600,fontSize:13,color:'#1A1D2E'}}}}>{{p.data||'—'}}</td>
                        <td style={{{{padding:'11px 16px',fontSize:13,color:'#94A3B8'}}}}>{{p.days||'—'}}d</td>
                        <td style={{{{padding:'11px 16px',textAlign:'right'}}}}>
                          {{editMode
                            ? <input type="number" step="0.01" style={{{{width:72,background:manualEdits[vKey]!==undefined?'#FFFBEB':'#F8F9FC',border:'1px solid '+(manualEdits[vKey]!==undefined?'#F59E0B':'#E2E8F0'),borderRadius:6,color:'#1A1D2E',padding:'4px 7px',fontSize:12,textAlign:'right',fontFamily:"'Roboto Mono',monospace",outline:'none'}}}} value={{manualEdits[vKey]!==undefined ? manualEdits[vKey] : (baseVoyeP||'').toString()}} onChange={{e => setManualEdits(prev => ({{...prev, [vKey]: e.target.value}}))}} />
                            : <span style={{{{fontFamily:"'Roboto Mono',monospace",fontSize:13,color:manualEdits[vKey]!==undefined?'#D97706':'#6366F1',fontWeight:700}}}}>${{voyeP!=null?voyeP.toFixed(2):'—'}}</span>
                          }}
                        </td>
                        {{COMPETITORS.map((comp,j) => {{
                          const cKey = 'comp|'+comp+'|'+selCountry+'|'+(p.data||'')+'|'+p.days;
                          const cplans = (COMP_DATA[comp]||{{}})[selCountry] || [];
                          const match = cplans.find(cp => cp.data === p.data) || cplans.find(cp => cp.days === p.days);
                          const baseCompP = match ? match.price : null;
                          const cp = compPrices[j];
                          const isEdited = manualEdits[cKey] !== undefined;
                          const cheaper = cp!=null && voyeP!=null && cp < voyeP;
                          const pricier = cp!=null && voyeP!=null && cp > voyeP;
                          return (
                            <td key={{j}} style={{{{padding:'11px 16px',textAlign:'right'}}}}>
                              {{editMode
                                ? (baseCompP !== null || isEdited)
                                  ? <input type="number" step="0.01" style={{{{width:72,background:isEdited?'#FFFBEB':'#F8F9FC',border:'1px solid '+(isEdited?'#F59E0B':'#E2E8F0'),borderRadius:6,color:isEdited?'#D97706':(cheaper?'#EF4444':pricier?'#10B981':'#64748B'),padding:'4px 7px',fontSize:12,textAlign:'right',fontFamily:"'Roboto Mono',monospace",outline:'none'}}}} value={{isEdited ? manualEdits[cKey] : (baseCompP||'').toString()}} onChange={{e => setManualEdits(prev => ({{...prev, [cKey]: e.target.value}}))}} />
                                  : <span style={{{{color:'#CBD5E1',fontSize:11}}}}>—</span>
                                : cp !== null
                                  ? <span style={{{{fontFamily:"'Roboto Mono',monospace",fontSize:13,color:isEdited?'#D97706':(cheaper?'#EF4444':pricier?'#10B981':'#94A3B8')}}}}>${{cp.toFixed(2)}}</span>
                                  : <span style={{{{color:'#CBD5E1',fontSize:11}}}}>—</span>
                              }}
                            </td>
                          );
                        }})}}
                        <td style={{{{padding:'11px 16px',textAlign:'right'}}}}>
                          {{posPct !== null ? <Pill cls={{posPct>10?'dn':posPct<-10?'up':'na'}} text={{(posPct>0?'+':'')+posPct+'%'}} /> : <span style={{{{color:'#CBD5E1',fontSize:11}}}}>—</span>}}
                        </td>
                      </tr>
                    );
                  }})}}
                </tbody>
              </table>
            </div>
            <div style={{{{marginTop:10,fontSize:11,color:'#94A3B8',display:'flex',gap:20,flexWrap:'wrap'}}}}>
              <span><span style={{{{fontFamily:"'Roboto Mono',monospace",color:'#EF4444'}}}}>$X.XX</span> = competitor cheaper than Voye</span>
              <span><span style={{{{fontFamily:"'Roboto Mono',monospace",color:'#10B981'}}}}>$X.XX</span> = competitor more expensive</span>
              <span>% = Voye vs market average</span>
              {{Object.keys(manualEdits).length > 0 && <span><span style={{{{fontFamily:"'Roboto Mono',monospace",color:'#D97706'}}}}>$X.XX</span> = edited ({{Object.keys(manualEdits).length}} unsaved)</span>}}
            </div>
          </>}}

          {{/* ── AI ── */}}
          {{tab === 'ai' && <>
            <div style={{{{display:'grid',gridTemplateColumns:'1fr 1fr',gap:20,marginBottom:20}}}}>
              <div className="card" style={{{{padding:24,borderTop:'3px solid #6366F1'}}}}>
                <div style={{{{fontSize:15,fontWeight:700,color:'#1A1D2E',marginBottom:10}}}}>🧠 Strategic Analysis</div>
                <p style={{{{fontSize:13,color:'#64748B',lineHeight:1.7,marginBottom:18}}}}>Claude analyzes all {{N_CHANGES}} price changes and gives pricing recommendations.</p>
                <button onClick={{runAI}} disabled={{aiLoading}} style={{{{background:'linear-gradient(135deg,#6366F1,#8B5CF6)',border:'none',color:'#fff',padding:'9px 22px',borderRadius:9,fontSize:13,fontWeight:600,cursor:'pointer',opacity:aiLoading?.5:1,boxShadow:'0 2px 8px rgba(99,102,241,.3)'}}}}>
                  {{aiLoading ? <span className="blink">Analyzing…</span> : 'Generate Analysis'}}
                </button>
              </div>
              <div className="card" style={{{{padding:24,borderTop:'3px solid #F59E0B'}}}}>
                <div style={{{{fontSize:15,fontWeight:700,color:'#1A1D2E',marginBottom:10}}}}>📊 Data Sources</div>
                <div style={{{{fontSize:13,color:'#64748B',lineHeight:2.2}}}}>
                  ✓ {{N_CHANGES}} Voye price changes — {{UPDATE_DATE}}<br/>
                  ✓ {{N_COUNTRIES}} destinations tracked<br/>
                  ✓ Competitors: {{COMPETITORS.join(', ')}}<br/>
                  ✓ Last scan: {{SCAN_DATE}}
                </div>
              </div>
            </div>
            <div className="card" style={{{{padding:24,borderTop:'3px solid #8B5CF6'}}}}>
              <div style={{{{fontSize:15,fontWeight:700,marginBottom:14,color:'#7C3AED'}}}}>Claude's Analysis</div>
              <div style={{{{background:'#F8F9FC',borderRadius:10,padding:20,fontSize:14,lineHeight:1.8,color:'#374151',whiteSpace:'pre-wrap',minHeight:100,border:'1px solid #E8EBF0'}}}}>
                {{aiText || 'Click "Generate Analysis" to get strategic insights.'}}
              </div>
            </div>
          </>}}

          {{/* ── REPORTS ── */}}
          {{tab === 'reports' && <>

            <div style={{{{marginBottom:20}}}}>
              <div style={{{{fontSize:13,color:'#64748B'}}}}>Generate and download Excel reports of price changes.</div>
              <div style={{{{display:'flex',gap:24,marginTop:6,flexWrap:'wrap'}}}}>
                <span style={{{{fontSize:12,color:'#94A3B8'}}}}>🔄 Competitor scan: {{DIFF_DATE}} · {{(DIFF_DATA.changes||[]).length}} changes</span>
                <span style={{{{fontSize:12,color:'#94A3B8'}}}}>📋 Voye update: {{UPDATE_DATE}} · {{N_CHANGES}} changes</span>
              </div>
            </div>

            {{/* Report type cards */}}
            <div style={{{{display:'grid',gridTemplateColumns:'1fr 1fr',gap:20,marginBottom:20}}}}>
              <div className="card" style={{{{padding:20,cursor:'pointer',border:reportType==='comp'?'2px solid #6366F1':'1px solid #E8EBF0',transition:'border-color .15s'}}}} onClick={{() => setReportType('comp')}}>
                <div style={{{{display:'flex',alignItems:'center',gap:10,marginBottom:12}}}}>
                  <div style={{{{width:38,height:38,borderRadius:10,background:'#DBEAFE',display:'flex',alignItems:'center',justifyContent:'center',fontSize:18}}}}>📊</div>
                  <div style={{{{flex:1}}}}>
                    <div style={{{{fontSize:14,fontWeight:700,color:'#1A1D2E'}}}}>Competitor Price Changes</div>
                    <div style={{{{fontSize:11,color:'#94A3B8'}}}}>{{(DIFF_DATA.changes||[]).length}} records from last scan</div>
                  </div>
                  {{reportType==='comp' && <span style={{{{background:'#EEF2FF',color:'#6366F1',padding:'2px 10px',borderRadius:20,fontSize:11,fontWeight:600}}}}>● Selected</span>}}
                </div>
                <div style={{{{fontSize:12,color:'#64748B',lineHeight:1.7,marginBottom:reportType==='comp'?12:0}}}}>Destination · Package · Days · Old Price · New Price · Change % · Direction</div>
                {{reportType === 'comp' && (
                  <div style={{{{paddingTop:12,borderTop:'1px solid #F1F5F9'}}}}>
                    <div style={{{{fontSize:12,fontWeight:600,color:'#374151',marginBottom:6}}}}>Filter by competitor</div>
                    <select value={{compFilter}} onChange={{e => setCompFilter(e.target.value)}} style={{{{width:'100%',padding:'7px 10px',border:'1px solid #E2E8F0',borderRadius:8,fontSize:13,color:'#1A1D2E',background:'#fff',outline:'none',fontFamily:"'Inter',sans-serif"}}}}>
                      <option value="all">All Competitors ({{(DIFF_DATA.changes||[]).length}} records)</option>
                      {{COMPETITORS.map(comp => (
                        <option key={{comp}} value={{comp}}>{{comp.charAt(0).toUpperCase()+comp.slice(1)}} ({{(DIFF_DATA.changes||[]).filter(c=>c.competitor===comp).length}} records)</option>
                      ))}}
                    </select>
                  </div>
                )}}
              </div>

              <div className="card" style={{{{padding:20,cursor:'pointer',border:reportType==='voye'?'2px solid #6366F1':'1px solid #E8EBF0',transition:'border-color .15s'}}}} onClick={{() => setReportType('voye')}}>
                <div style={{{{display:'flex',alignItems:'center',gap:10,marginBottom:12}}}}>
                  <div style={{{{width:38,height:38,borderRadius:10,background:'#D1FAE5',display:'flex',alignItems:'center',justifyContent:'center',fontSize:18}}}}>📋</div>
                  <div style={{{{flex:1}}}}>
                    <div style={{{{fontSize:14,fontWeight:700,color:'#1A1D2E'}}}}>Voye Price Changes</div>
                    <div style={{{{fontSize:11,color:'#94A3B8'}}}}>{{N_CHANGES}} records from last update</div>
                  </div>
                  {{reportType==='voye' && <span style={{{{background:'#EEF2FF',color:'#6366F1',padding:'2px 10px',borderRadius:20,fontSize:11,fontWeight:600}}}}>● Selected</span>}}
                </div>
                <div style={{{{fontSize:12,color:'#64748B',lineHeight:1.7}}}}>Destination · Package · Days · Old Price · New Price · Change $ · Change % · Date</div>
              </div>
            </div>

            {{/* Date range + generate */}}
            <div className="card" style={{{{padding:20,marginBottom:20}}}}>
              <div style={{{{fontSize:13,fontWeight:600,color:'#1A1D2E',marginBottom:12}}}}>Date Range <span style={{{{fontSize:11,fontWeight:400,color:'#94A3B8'}}}}>— included in the exported filename</span></div>
              <div style={{{{display:'flex',gap:16,alignItems:'center',flexWrap:'wrap'}}}}>
                <div style={{{{display:'flex',alignItems:'center',gap:8}}}}>
                  <span style={{{{fontSize:12,color:'#64748B',fontWeight:500}}}}>From</span>
                  <input type="date" value={{dateFrom}} onChange={{e => setDateFrom(e.target.value)}} style={{{{border:'1px solid #E2E8F0',borderRadius:8,padding:'6px 10px',fontSize:13,color:'#1A1D2E',background:'#fff',outline:'none'}}}}/>
                </div>
                <div style={{{{display:'flex',alignItems:'center',gap:8}}}}>
                  <span style={{{{fontSize:12,color:'#64748B',fontWeight:500}}}}>To</span>
                  <input type="date" value={{dateTo}} onChange={{e => setDateTo(e.target.value)}} style={{{{border:'1px solid #E2E8F0',borderRadius:8,padding:'6px 10px',fontSize:13,color:'#1A1D2E',background:'#fff',outline:'none'}}}}/>
                </div>
                <button onClick={{generatePreview}} style={{{{background:'linear-gradient(135deg,#6366F1,#8B5CF6)',border:'none',color:'#fff',padding:'8px 22px',borderRadius:8,fontSize:13,fontWeight:600,cursor:'pointer',boxShadow:'0 2px 8px rgba(99,102,241,.25)'}}}}>Generate Preview</button>
              </div>
              <div style={{{{fontSize:11,color:'#94A3B8',marginTop:10}}}}>ℹ Data reflects the latest available scan — date range applies to the filename only.</div>
            </div>

            {{/* Preview */}}
            {{previewRows.length > 0 && (
              <div className="card" style={{{{padding:20}}}}>
                <div style={{{{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:16}}}}>
                  <div>
                    <div style={{{{fontSize:14,fontWeight:700,color:'#1A1D2E'}}}}>Preview</div>
                    <div style={{{{fontSize:12,color:'#94A3B8'}}}}>Showing first {{previewRows.length}} of {{previewTotal}} records</div>
                  </div>
                  <button onClick={{downloadReport}} style={{{{background:'#10B981',border:'none',color:'#fff',padding:'8px 20px',borderRadius:8,fontSize:13,fontWeight:600,cursor:'pointer',display:'flex',alignItems:'center',gap:6,boxShadow:'0 2px 8px rgba(16,185,129,.25)'}}}}>⬇ Download .xlsx</button>
                </div>
                <div style={{{{overflow:'auto',borderRadius:10,border:'1px solid #E8EBF0'}}}}>
                  <table style={{{{width:'100%',borderCollapse:'collapse'}}}}>
                    <thead>
                      <tr style={{{{background:'#F8F9FC',borderBottom:'2px solid #E8EBF0'}}}}>
                        {{previewHeaders.map((h,i) => (
                          <th key={{i}} style={{{{padding:'9px 14px',fontSize:10,color:'#64748B',fontWeight:600,letterSpacing:'.5px',textTransform:'uppercase',textAlign:'left',whiteSpace:'nowrap'}}}}>{{h}}</th>
                        ))}}
                      </tr>
                    </thead>
                    <tbody>
                      {{previewRows.map((row,ri) => (
                        <tr key={{ri}} className="tbl-row" style={{{{borderBottom:'1px solid #F1F5F9'}}}}>
                          {{row.map((cell,ci) => (
                            <td key={{ci}} style={{{{padding:'9px 14px',fontSize:12,color:ci===0?'#1A1D2E':(ci===7&&cell==='▲ Up'?'#EF4444':ci===7&&cell==='▼ Down'?'#10B981':'#64748B'),fontWeight:ci===0?600:400,whiteSpace:'nowrap',fontFamily:ci>=3&&ci<=6?"'Roboto Mono',monospace":'inherit'}}}}>{{String(cell)}}</td>
                          ))}}
                        </tr>
                      ))}}
                    </tbody>
                  </table>
                </div>
                {{previewTotal > 10 && <div style={{{{marginTop:10,fontSize:12,color:'#94A3B8',textAlign:'center'}}}}>… and {{previewTotal-10}} more rows in the full download</div>}}
              </div>
            )}}

          </>}}

        </div>
      </div>
    </div>
  );
}}

ReactDOM.render(<App />, document.getElementById('root'));
</script>
</body>
</html>"""
    return html

def push_to_github(repo_url, token=None):
    """Push index.html to GitHub using git."""
    git_dir = BASE_DIR / ".git"
    if not git_dir.exists():
        print("Initializing git repo...")
        subprocess.run(["git", "init"], cwd=BASE_DIR, check=True)
        if repo_url:
            subprocess.run(["git", "remote", "add", "origin", repo_url], cwd=BASE_DIR)

    subprocess.run(["git", "add", "index.html"], cwd=BASE_DIR, check=True)
    msg = f"Update dashboard — {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    result = subprocess.run(["git", "commit", "-m", msg], cwd=BASE_DIR)
    if result.returncode != 0:
        print("Nothing to commit.")
        return
    subprocess.run(["git", "push", "origin", "main"], cwd=BASE_DIR, check=True)
    print("✓ Pushed to GitHub — Vercel will update in ~30 seconds")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-only", action="store_true")
    parser.add_argument("--github-repo", default=None, help="GitHub repo URL")
    args = parser.parse_args()

    print("Loading data...")
    comp_data, scraped_at = load_competitor_data()
    voye_data = load_voye_data()

    if not voye_data:
        print("WARNING: No Voye data found. Using competitor data only.")
        voye_data = {}

    print(f"Building dashboard — {len(voye_data)} Voye countries, {len(comp_data)} competitors")
    html = build_html(voye_data, comp_data, scraped_at)

    OUT_FILE.write_text(html, encoding='utf-8')
    print(f"✓ Built {OUT_FILE} ({len(html):,} chars)")

    if not args.build_only:
        repo = args.github_repo or os.environ.get("GITHUB_REPO_URL")
        if repo:
            push_to_github(repo)
        else:
            print("No GitHub repo URL — skipping push.")
            print("To push: python3 build_dashboard.py --github-repo https://github.com/Roei1121/voye-dashboard.git")

if __name__ == "__main__":
    main()
