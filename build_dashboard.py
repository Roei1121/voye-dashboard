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
body{{font-family:'Inter',sans-serif;background:#0B0F1A;color:#E2E8F0;min-height:100vh}}
::-webkit-scrollbar{{width:5px;height:5px}}
::-webkit-scrollbar-track{{background:#0B0F1A}}
::-webkit-scrollbar-thumb{{background:#2D3748;border-radius:3px}}
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
</script>
<script type="text/babel">
const {{ useState, useMemo, useCallback }} = React;

function Pill({{cls, text}}) {{
  const s = {{
    info:  {{background:'rgba(99,102,241,.12)',color:'#A5B4FC',border:'1px solid rgba(99,102,241,.25)'}},
    up:    {{background:'rgba(74,222,128,.12)',color:'#4ADE80',border:'1px solid rgba(74,222,128,.25)'}},
    dn:    {{background:'rgba(248,113,113,.12)',color:'#F87171',border:'1px solid rgba(248,113,113,.25)'}},
    na:    {{background:'rgba(148,163,184,.1)',color:'#94A3B8',border:'1px solid rgba(148,163,184,.2)'}},
    amber: {{background:'rgba(245,166,35,.12)',color:'#F59E0B',border:'1px solid rgba(245,166,35,.25)'}},
  }};
  return <span style={{{{display:'inline-flex',alignItems:'center',gap:3,padding:'2px 9px',borderRadius:20,fontSize:11,fontWeight:600,...s[cls]}}}}>{{text}}</span>;
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
    const top = ALL_CHANGES.slice(0,10).map(c => `${{c.country}} ${{c.data}} ${{c.days}}d: $${{c.old}}→$${{c.new}} (${{c.pct>0?'+':''}}${{c.pct}}%)`).join('\\n');
    try {{
      const res = await fetch('https://api.anthropic.com/v1/messages', {{
        method:'POST', headers:{{'Content-Type':'application/json'}},
        body: JSON.stringify({{ model:'claude-sonnet-4-20250514', max_tokens:1000,
          messages:[{{role:'user',content:`You are a pricing strategist for Voye Global, an eSIM provider.\\n\\nPricing update (${{UPDATE_DATE}}):\\n- ${{N_CHANGES}} price changes across ${{N_COUNTRIES}} destinations\\n- ${{N_UP}} increases, ${{N_DN}} decreases\\n\\nBiggest changes:\\n${{top}}\\n\\nGive 5 sharp actionable strategic insights. English bullet points.`}}]
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

  return (
    <div style={{{{fontFamily:"'Inter',sans-serif",background:'#0B0F1A',minHeight:'100vh',color:'#E2E8F0'}}}}>
      <style>{{`
        button{{font-family:'Inter',sans-serif;cursor:pointer}}
        .tab-btn{{background:transparent;border:none;padding:9px 18px;border-radius:8px;font-size:13px;font-weight:500;color:#64748B;transition:all .15s}}
        .tab-btn:hover{{color:#94A3B8}}
        .tab-btn.on{{background:#1E293B;color:#E2E8F0}}
        .kpi{{background:#111827;border:1px solid #1E2A3B;border-radius:14px;padding:18px;border-top:3px solid}}
        .ccard{{border-radius:10px;padding:12px;cursor:pointer;transition:all .15s;border:1px solid transparent}}
        .ccard:hover{{transform:translateY(-1px);filter:brightness(1.1)}}
        .fbtn{{background:transparent;border:1px solid #1E2A3B;border-radius:7px;padding:5px 12px;font-size:12px;font-weight:500;color:#64748B;transition:all .15s}}
        .fbtn:hover{{border-color:#2D3748;color:#94A3B8}}
        .fbtn.on{{background:#1E293B;border-color:#6366F1;color:#A5B4FC}}
        .dbtn{{background:transparent;border:1px solid #1E2A3B;border-radius:7px;padding:5px 10px;font-size:11px;font-weight:500;color:#64748B;transition:all .15s}}
        .dbtn:hover{{border-color:#2D3748;color:#94A3B8}}
        .dbtn.on{{background:#1E293B;border-color:#8B5CF6;color:#A78BFA}}
        .cbtn{{border-radius:7px;padding:4px 10px;font-size:11px;font-weight:600;cursor:pointer;transition:all .15s;border:1px solid transparent}}
        tr:hover td{{background:rgba(255,255,255,.015)}}
        @keyframes fadeIn{{from{{opacity:0;transform:translateY(5px)}}to{{opacity:1;transform:translateY(0)}}}}
        .page{{animation:fadeIn .2s ease}}
        @keyframes blink{{0%,100%{{opacity:1}}50%{{opacity:.3}}}}
        .blink{{animation:blink 1.2s infinite}}
      `}}</style>

      {{/* NAV */}}
      <div style={{{{position:'sticky',top:0,zIndex:100,background:'rgba(7,8,15,.95)',backdropFilter:'blur(12px)',borderBottom:'1px solid #1E2A3B',display:'flex',alignItems:'center',justifyContent:'space-between',padding:'0 28px',height:58}}}}>
        <div style={{{{display:'flex',alignItems:'center',gap:10}}}}>
          <div style={{{{width:32,height:32,borderRadius:9,background:'linear-gradient(135deg,#6366F1,#8B5CF6)',display:'flex',alignItems:'center',justifyContent:'center',fontSize:15}}}}>📡</div>
          <div><span style={{{{fontSize:15,fontWeight:700}}}}>Voye</span><span style={{{{fontSize:12,color:'#64748B',marginLeft:6}}}}>Price Intelligence</span></div>
        </div>
        <div style={{{{display:'flex',gap:2}}}}>
          {{[['overview','📊 Overview'],['changes','🔄 Changes'],['compare','🔍 vs Competitors'],['ai','🤖 AI Analysis']].map(([id,label]) => (
            <button key={{id}} className={{`tab-btn${{tab===id?' on':''}}`}} onClick={{() => setTab(id)}}>{{label}}</button>
          ))}}
        </div>
        <div style={{{{display:'flex',gap:8,alignItems:'center'}}}}>
          <Pill cls="info" text={{`Updated ${{UPDATE_DATE}}`}} />
          <Pill cls="up" text={{`↑ ${{N_UP}} raised`}} />
          <Pill cls="dn" text={{`↓ ${{N_DN}} lowered`}} />
          <label style={{{{cursor:'pointer'}}}}>
            <input type="file" accept=".xlsx" style={{{{display:'none'}}}} onChange={{handleUpload}} />
            <span style={{{{background:'rgba(99,102,241,.15)',border:'1px solid rgba(99,102,241,.3)',color:'#A5B4FC',padding:'5px 12px',borderRadius:7,fontSize:12,fontWeight:600,cursor:'pointer',userSelect:'none'}}}}>↑ Upload Prices</span>
          </label>
          <span style={{{{fontSize:10,color:'#334155'}}}}>Scan: {{SCAN_DATE}}</span>
        </div>
      </div>

      {{uploadSummary && (
        <div style={{{{background:'rgba(34,197,94,.08)',borderBottom:'1px solid rgba(34,197,94,.2)',padding:'10px 28px',display:'flex',gap:20,alignItems:'center',fontSize:12}}}}>
          <span style={{{{color:'#4ADE80',fontWeight:700}}}}>✓ Upload complete</span>
          <span>{{uploadSummary.updated}} / {{uploadSummary.total}} prices updated</span>
          {{uploadSummary.skipped.length > 0 && <span style={{{{color:'#F59E0B'}}}}>Issues: {{uploadSummary.skipped.join(', ')}}</span>}}
          <button onClick={{() => setUploadSummary(null)}} style={{{{marginLeft:'auto',background:'transparent',border:'none',color:'#64748B',fontSize:18,lineHeight:1,cursor:'pointer'}}}}>×</button>
        </div>
      )}}
      <div style={{{{maxWidth:1280,margin:'0 auto',padding:28}}}} className="page" key={{tab}}>

        {{/* OVERVIEW */}}
        {{tab === 'overview' && <>
          {{/* KPI Cards */}}
          <div style={{{{display:'grid',gridTemplateColumns:'repeat(5,1fr)',gap:14,marginBottom:24}}}}>
            {{[
              {{icon:'🌍',val:N_COUNTRIES,label:'Total Destinations',sub:'markets covered',color:'#6366F1'}},
              {{icon:'📦',val:N_PLANS,label:'Total Packages',sub:'across all markets',color:'#3B82F6'}},
              {{icon:'✅',val:N_CHEAPER,label:'Packages Cheaper',sub:'vs avg competitor',color:'#22C55E'}},
              {{icon:'⚠️',val:N_PRICIER,label:'Packages Pricier',sub:'vs avg competitor',color:'#F87171'}},
              {{icon:'🎯',val:N_NEEDS_UPDATE,label:'Requiring Update',sub:'>10% above market',color:'#F59E0B'}},
            ].map((k,i) => (
              <div key={{i}} className="kpi" style={{{{borderTopColor:k.color}}}}>
                <div style={{{{fontSize:22,marginBottom:8}}}}>{{k.icon}}</div>
                <div style={{{{fontSize:30,fontWeight:800,color:k.color,letterSpacing:'-1px'}}}}>{{k.val}}</div>
                <div style={{{{fontSize:12,fontWeight:600,marginTop:4}}}}>{{k.label}}</div>
                <div style={{{{fontSize:11,color:'#64748B'}}}}>{{k.sub}}</div>
              </div>
            ))}}
          </div>

          {{/* Competitive Advantage + Weak Markets with bars */}}
          <div style={{{{display:'grid',gridTemplateColumns:'1fr 1fr',gap:16,marginBottom:16}}}}>
            <div style={{{{background:'#111827',border:'1px solid #1E2A3B',borderRadius:14,padding:20}}}}>
              <div style={{{{fontSize:14,fontWeight:700,marginBottom:4,color:'#4ADE80'}}}}>💪 Competitive Advantage Markets</div>
              <div style={{{{fontSize:11,color:'#64748B',marginBottom:14}}}}>Voye's biggest price advantages vs competitor average</div>
              {{STRONG_MARKETS.map((m,i) => (
                <div key={{i}} style={{{{marginBottom:10}}}}>
                  <div style={{{{display:'flex',justifyContent:'space-between',marginBottom:3}}}}>
                    <span style={{{{fontSize:12,fontWeight:600}}}}>{{m.country}}</span>
                    <span style={{{{fontSize:12,color:'#4ADE80',fontWeight:700}}}}>{{m.adv}}% cheaper · {{m.plans}} pkgs</span>
                  </div>
                  <div style={{{{background:'rgba(255,255,255,.05)',borderRadius:3,height:5,overflow:'hidden'}}}}>
                    <div style={{{{height:'100%',width:Math.round(m.adv/MAX_STRONG_ADV*100)+'%',background:'linear-gradient(90deg,#16A34A,#4ADE80)',borderRadius:3}}}}/>
                  </div>
                </div>
              ))}}
            </div>
            <div style={{{{background:'#111827',border:'1px solid #1E2A3B',borderRadius:14,padding:20}}}}>
              <div style={{{{fontSize:14,fontWeight:700,marginBottom:4,color:'#F87171'}}}}>🔴 Weak Markets</div>
              <div style={{{{fontSize:11,color:'#64748B',marginBottom:14}}}}>Markets where Voye is most expensive vs competitors</div>
              {{WEAK_MARKETS.map((m,i) => (
                <div key={{i}} style={{{{marginBottom:10}}}}>
                  <div style={{{{display:'flex',justifyContent:'space-between',marginBottom:3}}}}>
                    <span style={{{{fontSize:12,fontWeight:600}}}}>{{m.country}}</span>
                    <span style={{{{fontSize:12,color:'#F87171',fontWeight:700}}}}>{{Math.abs(m.adv)}}% pricier · {{m.plans}} pkgs</span>
                  </div>
                  <div style={{{{background:'rgba(255,255,255,.05)',borderRadius:3,height:5,overflow:'hidden'}}}}>
                    <div style={{{{height:'100%',width:Math.round(Math.abs(m.adv)/MAX_WEAK_ADV*100)+'%',background:'linear-gradient(90deg,#B91C1C,#F87171)',borderRadius:3}}}}/>
                  </div>
                </div>
              ))}}
            </div>
          </div>

          {{/* Top 5 / Bottom 5 */}}
          <div style={{{{display:'grid',gridTemplateColumns:'1fr 1fr',gap:16,marginBottom:16}}}}>
            <div style={{{{background:'#111827',border:'1px solid #1E2A3B',borderRadius:14,padding:20}}}}>
              <div style={{{{fontSize:14,fontWeight:700,marginBottom:14,color:'#4ADE80'}}}}>🏆 Top 5 — Strongest Competitive Edge</div>
              {{STRONG_MARKETS.slice(0,5).map((m,i) => (
                <div key={{i}} style={{{{display:'flex',alignItems:'center',gap:12,padding:'8px 0',borderBottom:'1px solid #0D1526'}}}}>
                  <span style={{{{fontSize:16,fontWeight:800,color:'rgba(74,222,128,.25)',minWidth:22,textAlign:'center'}}}}>{{i+1}}</span>
                  <div style={{{{flex:1}}}}>
                    <div style={{{{fontSize:13,fontWeight:600}}}}>{{m.country}}</div>
                    <div style={{{{fontSize:11,color:'#64748B'}}}}>{{m.plans}} comparable packages</div>
                  </div>
                  <Pill cls="up" text={{m.adv+'% cheaper'}} />
                </div>
              ))}}
            </div>
            <div style={{{{background:'#111827',border:'1px solid #1E2A3B',borderRadius:14,padding:20}}}}>
              <div style={{{{fontSize:14,fontWeight:700,marginBottom:14,color:'#F87171'}}}}>📉 Bottom 5 — Least Competitive</div>
              {{WEAK_MARKETS.slice(0,5).map((m,i) => (
                <div key={{i}} style={{{{display:'flex',alignItems:'center',gap:12,padding:'8px 0',borderBottom:'1px solid #0D1526'}}}}>
                  <span style={{{{fontSize:16,fontWeight:800,color:'rgba(248,113,113,.25)',minWidth:22,textAlign:'center'}}}}>{{i+1}}</span>
                  <div style={{{{flex:1}}}}>
                    <div style={{{{fontSize:13,fontWeight:600}}}}>{{m.country}}</div>
                    <div style={{{{fontSize:11,color:'#64748B'}}}}>{{m.plans}} comparable packages</div>
                  </div>
                  <Pill cls="dn" text={{Math.abs(m.adv)+'% pricier'}} />
                </div>
              ))}}
            </div>
          </div>

          {{/* Competitor Breakdown */}}
          <div style={{{{background:'#111827',border:'1px solid #1E2A3B',borderRadius:14,padding:20,marginBottom:16}}}}>
            <div style={{{{fontSize:14,fontWeight:700,marginBottom:4}}}}>📊 Competitor Breakdown</div>
            <div style={{{{fontSize:11,color:'#64748B',marginBottom:16}}}}>How many Voye packages are cheaper, equal, or pricier vs each competitor</div>
            <div style={{{{display:'grid',gridTemplateColumns:'repeat(4,1fr)',gap:16}}}}>
              {{COMP_BREAKDOWN.map((c,i) => (
                <div key={{i}} style={{{{background:'#0D1526',borderRadius:10,padding:16}}}}>
                  <div style={{{{fontSize:13,fontWeight:700,marginBottom:12,color:COMP_COLORS[c.name]||'#94A3B8',textTransform:'capitalize'}}}}>{{c.name}}</div>
                  <div style={{{{display:'flex',justifyContent:'space-between',marginBottom:10}}}}>
                    <div style={{{{textAlign:'center'}}}}>
                      <div style={{{{fontSize:22,fontWeight:800,color:'#4ADE80'}}}}>{{c.cheaper}}</div>
                      <div style={{{{fontSize:10,color:'#64748B',marginTop:2}}}}>cheaper</div>
                    </div>
                    <div style={{{{textAlign:'center'}}}}>
                      <div style={{{{fontSize:22,fontWeight:800,color:'#64748B'}}}}>{{c.equal}}</div>
                      <div style={{{{fontSize:10,color:'#64748B',marginTop:2}}}}>equal</div>
                    </div>
                    <div style={{{{textAlign:'center'}}}}>
                      <div style={{{{fontSize:22,fontWeight:800,color:'#F87171'}}}}>{{c.pricier}}</div>
                      <div style={{{{fontSize:10,color:'#64748B',marginTop:2}}}}>pricier</div>
                    </div>
                  </div>
                  {{c.total>0 && <div style={{{{display:'flex',height:6,borderRadius:3,overflow:'hidden'}}}}>
                    <div style={{{{width:Math.round(c.cheaper/c.total*100)+'%',background:'#22C55E'}}}}/>
                    <div style={{{{width:Math.round(c.equal/c.total*100)+'%',background:'#334155'}}}}/>
                    <div style={{{{width:Math.round(c.pricier/c.total*100)+'%',background:'#EF4444'}}}}/>
                  </div>}}
                </div>
              ))}}
            </div>
          </div>

          {{/* Last Price Update + Significant Changes */}}
          <div style={{{{display:'grid',gridTemplateColumns:'1fr 1fr',gap:16,marginBottom:16}}}}>
            <div style={{{{background:'#111827',border:'1px solid #1E2A3B',borderRadius:14,padding:20}}}}>
              <div style={{{{fontSize:14,fontWeight:700,marginBottom:4,color:'#F59E0B'}}}}>🕐 Last Price Update</div>
              <div style={{{{fontSize:11,color:'#64748B',marginBottom:14}}}}>Last updated: {{UPDATE_DATE}} · Destinations with most plan changes</div>
              {{RECENT_UPDATES.map((m,i) => (
                <div key={{i}} style={{{{display:'flex',justifyContent:'space-between',alignItems:'center',padding:'7px 0',borderBottom:'1px solid #0D1526'}}}}>
                  <span style={{{{fontSize:13,fontWeight:600}}}}>{{m.country}}</span>
                  <div style={{{{display:'flex',alignItems:'center',gap:8}}}}>
                    <span style={{{{fontSize:11,color:'#64748B'}}}}>{{m.changed}}/{{m.total}} plans</span>
                    <Pill cls="amber" text={{m.pct+'% updated'}} />
                  </div>
                </div>
              ))}}
            </div>
            <div style={{{{background:'#111827',border:'1px solid #1E2A3B',borderRadius:14,padding:20}}}}>
              <div style={{{{fontSize:14,fontWeight:700,marginBottom:14}}}}>⚡ Significant Changes</div>
              {{ALL_CHANGES.slice(0,10).map((c,i) => (
                <div key={{i}} style={{{{display:'flex',justifyContent:'space-between',alignItems:'center',padding:'7px 0',borderBottom:'1px solid #0D1526'}}}}>
                  <div>
                    <div style={{{{fontSize:13,fontWeight:600}}}}>{{c.country}}</div>
                    <div style={{{{fontSize:11,color:'#64748B'}}}}>{{c.data}} · {{c.days||'?'}}d</div>
                  </div>
                  <div style={{{{textAlign:'right'}}}}>
                    <div style={{{{fontFamily:"'Roboto Mono',monospace",fontSize:12,color:'#94A3B8'}}}}>${{c.old.toFixed(2)}} → <span style={{{{color:c.pct>0?'#F87171':'#4ADE80',fontWeight:700}}}}>${{c.new.toFixed(2)}}</span></div>
                    <Pill cls={{c.pct>0?'dn':'up'}} text={{(c.pct>0?'+':'')+c.pct+'%'}} />
                  </div>
                </div>
              ))}}
            </div>
          </div>

          {{/* Alerts Panel */}}
          <div style={{{{background:'#111827',border:'1px solid #1E2A3B',borderRadius:14,padding:20}}}}>
            <div style={{{{fontSize:14,fontWeight:700,marginBottom:4,color:'#F59E0B'}}}}>🚨 Alerts</div>
            <div style={{{{fontSize:11,color:'#64748B',marginBottom:16}}}}>Packages and destinations requiring attention</div>
            <div style={{{{display:'grid',gridTemplateColumns:'1fr 1fr 1fr',gap:20}}}}>
              <div>
                <div style={{{{display:'flex',alignItems:'center',gap:6,marginBottom:10}}}}>
                  <span style={{{{fontSize:12,fontWeight:700,color:'#F87171'}}}}>🔺 Significantly Overpriced</span>
                  <span style={{{{marginLeft:'auto',background:'rgba(248,113,113,.15)',color:'#F87171',borderRadius:10,padding:'1px 8px',fontSize:11,fontWeight:600}}}}>{{ALERTS_OVERPRICED.length}}</span>
                </div>
                {{ALERTS_OVERPRICED.length===0 && <div style={{{{fontSize:12,color:'#334155'}}}}>No packages flagged</div>}}
                {{ALERTS_OVERPRICED.map((a,i) => (
                  <div key={{i}} style={{{{padding:'6px 0',borderBottom:'1px solid #0D1526'}}}}>
                    <div style={{{{fontSize:12,fontWeight:600}}}}>{{a.country}}</div>
                    <div style={{{{fontSize:11,color:'#64748B'}}}}>{{a.data}}{{a.days?' · '+a.days+'d':''}}</div>
                    <div style={{{{fontSize:11,color:'#F87171',fontWeight:600}}}}>+{{a.gap}}% above avg · ${{a.voye}} vs ${{a.avg_comp}}</div>
                  </div>
                ))}}
              </div>
              <div>
                <div style={{{{display:'flex',alignItems:'center',gap:6,marginBottom:10}}}}>
                  <span style={{{{fontSize:12,fontWeight:700,color:'#94A3B8'}}}}>⚖️ Price Matches (≤1% diff)</span>
                  <span style={{{{marginLeft:'auto',background:'rgba(148,163,184,.15)',color:'#94A3B8',borderRadius:10,padding:'1px 8px',fontSize:11,fontWeight:600}}}}>{{ALERTS_MATCHES.length}}</span>
                </div>
                {{ALERTS_MATCHES.length===0 && <div style={{{{fontSize:12,color:'#334155'}}}}>No price matches found</div>}}
                {{ALERTS_MATCHES.map((a,i) => (
                  <div key={{i}} style={{{{padding:'6px 0',borderBottom:'1px solid #0D1526'}}}}>
                    <div style={{{{fontSize:12,fontWeight:600}}}}>{{a.country}}</div>
                    <div style={{{{fontSize:11,color:'#64748B'}}}}>{{a.data}}{{a.days?' · '+a.days+'d':''}}</div>
                    <div style={{{{fontSize:11,color:'#94A3B8'}}}}>= {{a.comp}} · ${{a.comp_price}}</div>
                  </div>
                ))}}
              </div>
              <div>
                <div style={{{{display:'flex',alignItems:'center',gap:6,marginBottom:10}}}}>
                  <span style={{{{fontSize:12,fontWeight:700,color:'#64748B'}}}}>🕓 Destinations Not Updated</span>
                  <span style={{{{marginLeft:'auto',background:'rgba(100,116,139,.15)',color:'#64748B',borderRadius:10,padding:'1px 8px',fontSize:11,fontWeight:600}}}}>{{ALERTS_STALE.length}}</span>
                </div>
                {{ALERTS_STALE.length===0 && <div style={{{{fontSize:12,color:'#334155'}}}}>All destinations updated</div>}}
                <div style={{{{display:'flex',flexWrap:'wrap',gap:4}}}}>
                  {{ALERTS_STALE.slice(0,16).map((c,i) => (
                    <span key={{i}} style={{{{fontSize:11,background:'rgba(100,116,139,.1)',color:'#64748B',borderRadius:6,padding:'2px 8px'}}}}>{{c}}</span>
                  ))}}
                  {{ALERTS_STALE.length>16 && <span style={{{{fontSize:11,color:'#475569'}}}}>+{{ALERTS_STALE.length-16}} more</span>}}
                </div>
              </div>
            </div>
          </div>
        </>}}

        {{/* CHANGES */}}
        {{tab === 'changes' && <>
          <div style={{{{display:'flex',gap:8,flexWrap:'wrap',marginBottom:16,alignItems:'center'}}}}>
            {{[['all','All Changes'],['up','↑ Increases'],['down','↓ Decreases']].map(([f,l]) => (
              <button key={{f}} className={{`fbtn${{filterMode===f?' on':''}}`}} onClick={{() => setFilterMode(f)}}>{{l}}</button>
            ))}}
            <input style={{{{background:'#0D1526',border:'1px solid #1E2A3B',borderRadius:8,color:'#E2E8F0',padding:'7px 12px',fontSize:13,outline:'none',width:220}}}} placeholder="Search country or data…" value={{searchQ}} onChange={{e => setSearchQ(e.target.value)}}/>
            <span style={{{{marginLeft:'auto'}}}}><Pill cls="info" text={{filteredChanges.length+' plans'}} /></span>
          </div>
          <div style={{{{overflow:'auto',borderRadius:14,border:'1px solid #1E2A3B'}}}}>
            <table style={{{{width:'100%',borderCollapse:'collapse',minWidth:480}}}}>
              <thead><tr style={{{{background:'#0D1526'}}}}>
                {{['Country','Package','Days','Old Price','New Price','Change'].map((h,i) => (
                  <th key={{h}} style={{{{padding:'10px 16px',fontSize:10,color:'#64748B',fontWeight:600,letterSpacing:'.6px',textTransform:'uppercase',textAlign:i>=3?'right':'left'}}}}>{{h}}</th>
                ))}}
              </tr></thead>
              <tbody>
                {{filteredChanges.map((c,i) => (
                  <tr key={{i}} style={{{{borderBottom:'1px solid #0D1526'}}}}>
                    <td style={{{{padding:'10px 16px',fontWeight:600,fontSize:13}}}}>{{c.country}}</td>
                    <td style={{{{padding:'10px 16px',fontSize:13,color:'#94A3B8'}}}}>{{c.data||'—'}}</td>
                    <td style={{{{padding:'10px 16px',fontSize:13,color:'#64748B'}}}}>{{c.days||'—'}}d</td>
                    <td style={{{{padding:'10px 16px',textAlign:'right'}}}}><span style={{{{fontFamily:"'Roboto Mono',monospace",fontSize:12,color:'#64748B'}}}}>${{c.old.toFixed(2)}}</span></td>
                    <td style={{{{padding:'10px 16px',textAlign:'right'}}}}><span style={{{{fontFamily:"'Roboto Mono',monospace",fontSize:12,color:c.pct>0?'#4ADE80':'#F87171',fontWeight:700}}}}>${{c.new.toFixed(2)}}</span></td>
                    <td style={{{{padding:'10px 16px',textAlign:'right'}}}}><Pill cls={{c.pct>0?'up':'dn'}} text={{`${{c.pct>0?'+':''}}${{c.pct}}%`}} /></td>
                  </tr>
                ))}}
              </tbody>
            </table>
          </div>
        </>}}

        {{/* COMPARE vs COMPETITORS */}}
        {{tab === 'compare' && <>
          <div style={{{{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:14}}}}>
            <div style={{{{display:'flex',gap:8,alignItems:'center',flexWrap:'wrap',flex:1}}}}>
              <span style={{{{fontSize:12,color:'#64748B',fontWeight:600}}}}>DESTINATION:</span>
              <div style={{{{display:'flex',gap:6,flexWrap:'wrap',flex:1}}}}>
                {{Object.keys(voyeState).sort().map(c => (
                  <button key={{c}} className={{`dbtn${{c===selCountry?' on':''}}`}} onClick={{() => setSelCountry(c)}}>{{c}}</button>
                ))}}
              </div>
            </div>
            <div style={{{{display:'flex',gap:8,alignItems:'center',marginLeft:16,flexShrink:0}}}}>
              {{saveStatus && <span style={{{{fontSize:12,color:'#4ADE80',fontWeight:600}}}}>{{saveStatus}}</span>}}
              <button className={{`fbtn${{editMode?' on':''}}`}} onClick={{() => setEditMode(m => !m)}}>✏️ {{editMode ? 'Editing' : 'Edit Prices'}}</button>
              {{Object.keys(manualEdits).length > 0 && <>
                <button className="fbtn on" onClick={{saveEdits}} style={{{{borderColor:'#22C55E',color:'#4ADE80'}}}}>💾 Save</button>
                <button className="fbtn" onClick={{resetEdits}} style={{{{color:'#F87171'}}}}>↺ Reset</button>
              </>}}
            </div>
          </div>
          <div style={{{{overflow:'auto',borderRadius:14,border:'1px solid #1E2A3B'}}}}>
            <table style={{{{width:'100%',borderCollapse:'collapse'}}}}>
              <thead><tr style={{{{background:'#0D1526'}}}}>
                <th style={{{{padding:'10px 16px',fontSize:10,color:'#64748B',fontWeight:600,letterSpacing:'.6px',textTransform:'uppercase',textAlign:'left'}}}}>Package</th>
                <th style={{{{padding:'10px 16px',fontSize:10,color:'#64748B',fontWeight:600,letterSpacing:'.6px',textTransform:'uppercase',textAlign:'left'}}}}>Days</th>
                <th style={{{{padding:'10px 16px',fontSize:10,color:'#A5B4FC',fontWeight:700,letterSpacing:'.6px',textTransform:'uppercase',textAlign:'right'}}}}>Voye</th>
                {{COMPETITORS.map(comp => (
                  <th key={{comp}} style={{{{padding:'10px 16px',fontSize:10,fontWeight:700,letterSpacing:'.6px',textTransform:'uppercase',textAlign:'right',color:COMP_COLORS[comp]||'#94A3B8'}}}}>{{comp.charAt(0).toUpperCase()+comp.slice(1)}}</th>
                ))}}
                <th style={{{{padding:'10px 16px',fontSize:10,color:'#64748B',fontWeight:600,letterSpacing:'.6px',textTransform:'uppercase',textAlign:'right'}}}}>vs Market</th>
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
                    <tr key={{i}} style={{{{borderBottom:'1px solid #0D1526'}}}}>
                      <td style={{{{padding:'10px 16px',fontWeight:600,fontSize:13}}}}>{{p.data||'—'}}</td>
                      <td style={{{{padding:'10px 16px',fontSize:13,color:'#64748B'}}}}>{{p.days||'—'}}d</td>
                      <td style={{{{padding:'10px 16px',textAlign:'right'}}}}>
                        {{editMode
                          ? <input type="number" step="0.01" style={{{{width:70,background:manualEdits[vKey]!==undefined?'rgba(245,158,11,.08)':'#0D1526',border:'1px solid '+(manualEdits[vKey]!==undefined?'#F59E0B':'#1E2A3B'),borderRadius:5,color:'#A5B4FC',padding:'3px 6px',fontSize:12,textAlign:'right',fontFamily:"'Roboto Mono',monospace",outline:'none'}}}} value={{manualEdits[vKey]!==undefined ? manualEdits[vKey] : (baseVoyeP||'').toString()}} onChange={{e => setManualEdits(prev => ({{...prev, [vKey]: e.target.value}}))}} />
                          : <span style={{{{fontFamily:"'Roboto Mono',monospace",fontSize:12,color:manualEdits[vKey]!==undefined?'#F59E0B':'#A5B4FC',fontWeight:700}}}}>${{voyeP!=null?voyeP.toFixed(2):'—'}}</span>
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
                          <td key={{j}} style={{{{padding:'10px 16px',textAlign:'right'}}}}>
                            {{editMode
                              ? (baseCompP !== null || isEdited)
                                ? <input type="number" step="0.01" style={{{{width:70,background:isEdited?'rgba(245,158,11,.08)':'#0D1526',border:'1px solid '+(isEdited?'#F59E0B':'#1E2A3B'),borderRadius:5,color:isEdited?'#F59E0B':(cheaper?'#F87171':pricier?'#4ADE80':'#64748B'),padding:'3px 6px',fontSize:12,textAlign:'right',fontFamily:"'Roboto Mono',monospace",outline:'none'}}}} value={{isEdited ? manualEdits[cKey] : (baseCompP||'').toString()}} onChange={{e => setManualEdits(prev => ({{...prev, [cKey]: e.target.value}}))}} />
                                : <span style={{{{color:'#2D3748',fontSize:11}}}}>—</span>
                              : cp !== null
                                ? <span style={{{{fontFamily:"'Roboto Mono',monospace",fontSize:12,color:isEdited?'#F59E0B':(cheaper?'#F87171':pricier?'#4ADE80':'#64748B')}}}}>${{cp.toFixed(2)}}</span>
                                : <span style={{{{color:'#2D3748',fontSize:11}}}}>—</span>
                            }}
                          </td>
                        );
                      }})}}
                      <td style={{{{padding:'10px 16px',textAlign:'right'}}}}>
                        {{posPct !== null ? <Pill cls={{posPct>10?'dn':posPct<-10?'up':'na'}} text={{(posPct>0?'+':'')+posPct+'%'}} /> : <span style={{{{color:'#2D3748',fontSize:11}}}}>—</span>}}
                      </td>
                    </tr>
                  );
                }})}}
              </tbody>
            </table>
          </div>
          <div style={{{{marginTop:10,fontSize:11,color:'#475569',display:'flex',gap:20,flexWrap:'wrap'}}}}>
            <span><span style={{{{fontFamily:"'Roboto Mono',monospace",color:'#F87171'}}}}>$X.XX</span> = competitor cheaper than Voye</span>
            <span><span style={{{{fontFamily:"'Roboto Mono',monospace",color:'#4ADE80'}}}}>$X.XX</span> = competitor more expensive</span>
            <span>% = Voye vs market average</span>
            {{Object.keys(manualEdits).length > 0 && <span><span style={{{{fontFamily:"'Roboto Mono',monospace",color:'#F59E0B'}}}}>$X.XX</span> = edited ({{Object.keys(manualEdits).length}} unsaved)</span>}}
          </div>
        </>}}

        {{/* AI */}}
        {{tab === 'ai' && <>
          <div style={{{{display:'grid',gridTemplateColumns:'1fr 1fr',gap:16,marginBottom:20}}}}>
            <div style={{{{background:'#111827',border:'1px solid #1E2A3B',borderTop:'3px solid #6366F1',borderRadius:14,padding:20}}}}>
              <div style={{{{fontSize:14,fontWeight:700,marginBottom:12}}}}>🧠 Strategic Analysis</div>
              <p style={{{{fontSize:13,color:'#64748B',lineHeight:1.7,marginBottom:16}}}}>Claude analyzes all {{N_CHANGES}} price changes and gives pricing recommendations.</p>
              <button onClick={{runAI}} disabled={{aiLoading}} style={{{{background:'linear-gradient(135deg,#6366F1,#8B5CF6)',border:'none',color:'#fff',padding:'9px 20px',borderRadius:9,fontSize:13,fontWeight:600,cursor:'pointer',opacity:aiLoading?.5:1}}}}>
                {{aiLoading ? <span className="blink">Analyzing…</span> : 'Generate Analysis'}}
              </button>
            </div>
            <div style={{{{background:'#111827',border:'1px solid #1E2A3B',borderTop:'3px solid #F59E0B',borderRadius:14,padding:20}}}}>
              <div style={{{{fontSize:14,fontWeight:700,marginBottom:12}}}}>📊 Data Sources</div>
              <div style={{{{fontSize:12,color:'#64748B',lineHeight:2.2}}}}>
                ✓ {{N_CHANGES}} Voye price changes — {{UPDATE_DATE}}<br/>
                ✓ {{N_COUNTRIES}} destinations tracked<br/>
                ✓ Competitors: {{COMPETITORS.join(', ')}}<br/>
                ✓ Last scan: {{SCAN_DATE}}
              </div>
            </div>
          </div>
          <div style={{{{background:'#111827',border:'1px solid #1E2A3B',borderTop:'3px solid #8B5CF6',borderRadius:14,padding:20}}}}>
            <div style={{{{fontSize:14,fontWeight:700,marginBottom:12,color:'#A78BFA'}}}}>Claude's Analysis</div>
            <div style={{{{background:'#0D1526',borderRadius:10,padding:20,fontSize:14,lineHeight:1.8,color:'#CBD5E0',whiteSpace:'pre-wrap',minHeight:100}}}}>
              {{aiText || 'Click "Generate Analysis" to get strategic insights.'}}
            </div>
          </div>
        </>}}

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
