"""
Live Trading Dashboard
========================
Flask web app showing real-time bot status, equity curve,
open positions, recent trades, and performance attribution.

Run: python dashboard.py
Open: http://localhost:5000
"""

import json
from flask import Flask, render_template_string, jsonify
from trade_database import TradeDatabase
from performance_monitor import PerformanceMonitor
from kill_switch import KillSwitch

app = Flask(__name__)
db = TradeDatabase()
perf = PerformanceMonitor()
ks = KillSwitch()

# -----------------------------------------------------------------------
# HTML template (single-file, no external deps — works offline)
# -----------------------------------------------------------------------
DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>XAU/USD Bot Dashboard</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { background: #0d1117; color: #e6edf3; font-family: 'Segoe UI', sans-serif; }
    .header { background: #161b22; padding: 16px 24px; border-bottom: 1px solid #30363d;
              display: flex; align-items: center; justify-content: space-between; }
    .header h1 { font-size: 18px; font-weight: 600; }
    .status-pill { padding: 4px 12px; border-radius: 12px; font-size: 12px; font-weight: 600; }
    .status-live { background: #1a4731; color: #3fb950; }
    .status-stopped { background: #3d1a1a; color: #f85149; }
    .status-paused { background: #3d3015; color: #d29922; }
    .grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; padding: 24px; }
    .card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 20px; }
    .card-title { font-size: 11px; color: #8b949e; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; }
    .card-value { font-size: 28px; font-weight: 700; }
    .card-value.green { color: #3fb950; }
    .card-value.red { color: #f85149; }
    .card-value.yellow { color: #d29922; }
    .section { padding: 0 24px 24px; }
    .section-title { font-size: 14px; font-weight: 600; color: #8b949e;
                     text-transform: uppercase; letter-spacing: 0.5px;
                     margin-bottom: 12px; padding-bottom: 8px;
                     border-bottom: 1px solid #21262d; }
    .chart-container { background: #161b22; border: 1px solid #30363d;
                       border-radius: 8px; padding: 16px; height: 260px;
                       display: flex; align-items: flex-end; gap: 2px; overflow: hidden; }
    .bar { background: #238636; border-radius: 2px 2px 0 0; flex: 1;
           min-height: 2px; transition: height 0.3s; }
    .bar.negative { background: #da3633; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th { text-align: left; padding: 8px 12px; color: #8b949e; font-weight: 500;
         border-bottom: 1px solid #21262d; font-size: 11px; text-transform: uppercase; }
    td { padding: 10px 12px; border-bottom: 1px solid #21262d; }
    tr:last-child td { border-bottom: none; }
    .badge { padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }
    .badge-buy { background: #1a4731; color: #3fb950; }
    .badge-sell { background: #3d1a1a; color: #f85149; }
    .badge-open { background: #1b2a3b; color: #58a6ff; }
    .two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
    .attr-row { display: flex; justify-content: space-between; align-items: center;
                padding: 8px 0; border-bottom: 1px solid #21262d; font-size: 13px; }
    .attr-row:last-child { border-bottom: none; }
    .attr-label { color: #8b949e; }
    .progress-bar { height: 6px; background: #21262d; border-radius: 3px; overflow: hidden; margin-top: 4px; }
    .progress-fill { height: 100%; background: #58a6ff; border-radius: 3px; }
    .controls { display: flex; gap: 8px; }
    button { padding: 6px 14px; border-radius: 6px; border: 1px solid #30363d;
             cursor: pointer; font-size: 13px; font-weight: 500; }
    .btn-danger { background: #3d1a1a; color: #f85149; border-color: #da3633; }
    .btn-warning { background: #3d3015; color: #d29922; border-color: #9e6a03; }
    .btn-success { background: #1a4731; color: #3fb950; border-color: #238636; }
    .refresh-note { color: #8b949e; font-size: 11px; }
    .equity-svg { width: 100%; height: 100%; }
    .metric-row { display: flex; justify-content: space-between; padding: 6px 0;
                  border-bottom: 1px solid #21262d; font-size: 13px; }
    .metric-row:last-child { border-bottom: none; }
  </style>
</head>
<body>
  <div class="header">
    <h1>⚡ XAU/USD Trading Bot</h1>
    <div style="display:flex;align-items:center;gap:16px;">
      <span class="refresh-note" id="last-update"></span>
      <div id="bot-status" class="status-pill status-live">LOADING...</div>
      <div class="controls">
        <button class="btn-warning" onclick="pauseBot()">Pause</button>
        <button class="btn-danger" onclick="stopBot()">Kill Switch</button>
        <button class="btn-success" onclick="refresh()">Refresh</button>
      </div>
    </div>
  </div>

  <!-- KPI Cards -->
  <div class="grid" id="kpi-grid">
    <div class="card"><div class="card-title">Net P&L (All Time)</div>
      <div class="card-value" id="kpi-pnl">—</div></div>
    <div class="card"><div class="card-title">Win Rate</div>
      <div class="card-value" id="kpi-wr">—</div></div>
    <div class="card"><div class="card-title">Profit Factor</div>
      <div class="card-value" id="kpi-pf">—</div></div>
    <div class="card"><div class="card-title">Total Trades</div>
      <div class="card-value" id="kpi-trades">—</div></div>
    <div class="card"><div class="card-title">Avg Win</div>
      <div class="card-value green" id="kpi-avgwin">—</div></div>
    <div class="card"><div class="card-title">Avg Loss</div>
      <div class="card-value red" id="kpi-avgloss">—</div></div>
    <div class="card"><div class="card-title">Open Trades</div>
      <div class="card-value yellow" id="kpi-open">—</div></div>
    <div class="card"><div class="card-title">Daily P&L</div>
      <div class="card-value" id="kpi-daily">—</div></div>
  </div>

  <!-- Equity Curve -->
  <div class="section">
    <div class="section-title">Equity Curve</div>
    <div class="chart-container" id="equity-chart">
      <svg class="equity-svg" id="equity-svg" viewBox="0 0 800 220" preserveAspectRatio="none">
        <defs>
          <linearGradient id="grad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="#58a6ff" stop-opacity="0.3"/>
            <stop offset="100%" stop-color="#58a6ff" stop-opacity="0.02"/>
          </linearGradient>
        </defs>
        <path id="equity-line" fill="none" stroke="#58a6ff" stroke-width="2"/>
        <path id="equity-fill" fill="url(#grad)"/>
      </svg>
    </div>
  </div>

  <!-- Attribution + Open Positions -->
  <div class="section">
    <div class="two-col">
      <div>
        <div class="section-title">Performance by Session</div>
        <div class="card" id="session-attr" style="padding:12px 16px;"></div>
      </div>
      <div>
        <div class="section-title">Performance by Regime</div>
        <div class="card" id="regime-attr" style="padding:12px 16px;"></div>
      </div>
    </div>
  </div>

  <!-- Open Positions -->
  <div class="section">
    <div class="section-title">Open Positions</div>
    <div class="card" style="padding:0;">
      <table>
        <thead><tr>
          <th>Type</th><th>Entry</th><th>SL</th><th>TP</th>
          <th>Volume</th><th>Session</th><th>Regime</th><th>ML Score</th>
        </tr></thead>
        <tbody id="open-positions-body">
          <tr><td colspan="8" style="text-align:center;color:#8b949e;padding:24px;">No open positions</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <!-- Recent Trades -->
  <div class="section">
    <div class="section-title">Recent Trades (Last 30)</div>
    <div class="card" style="padding:0;">
      <table>
        <thead><tr>
          <th>Time</th><th>Type</th><th>Entry</th><th>Exit</th>
          <th>P&L</th><th>Session</th><th>Regime</th><th>ML Score</th>
        </tr></thead>
        <tbody id="trades-body">
          <tr><td colspan="8" style="text-align:center;color:#8b949e;padding:24px;">Loading...</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <div style="height:40px;"></div>

<script>
function fmt(n, decimals=2) {
  if (n === null || n === undefined) return '—';
  return parseFloat(n).toFixed(decimals);
}
function colorClass(v) { return parseFloat(v) >= 0 ? 'green' : 'red'; }

function refresh() {
  fetch('/api/data').then(r => r.json()).then(data => {
    document.getElementById('last-update').textContent =
      'Updated: ' + new Date().toLocaleTimeString();

    // Status
    const st = data.status;
    const pill = document.getElementById('bot-status');
    if (st === 'active')  { pill.textContent='LIVE'; pill.className='status-pill status-live'; }
    else if (st === 'paused') { pill.textContent='PAUSED'; pill.className='status-pill status-paused'; }
    else { pill.textContent='STOPPED'; pill.className='status-pill status-stopped'; }

    // KPIs
    const s = data.stats;
    if (s) {
      const pnlEl = document.getElementById('kpi-pnl');
      pnlEl.textContent = '$' + fmt(s.net_pnl);
      pnlEl.className = 'card-value ' + colorClass(s.net_pnl);
      document.getElementById('kpi-wr').textContent = fmt(s.win_rate, 1) + '%';
      document.getElementById('kpi-pf').textContent = fmt(s.profit_factor);
      document.getElementById('kpi-trades').textContent = s.total_trades || 0;
      document.getElementById('kpi-avgwin').textContent = '$' + fmt(s.avg_win);
      document.getElementById('kpi-avgloss').textContent = '$' + fmt(s.avg_loss);
    }
    document.getElementById('kpi-open').textContent = data.open_count || 0;
    document.getElementById('kpi-daily').textContent =
      '$' + fmt(data.daily_pnl);
    const dailyEl = document.getElementById('kpi-daily');
    dailyEl.className = 'card-value ' + colorClass(data.daily_pnl);

    // Equity curve
    drawEquityCurve(data.equity_curve || []);

    // Session attribution
    renderAttr('session-attr', data.by_session || []);
    renderAttr('regime-attr', data.by_regime || []);

    // Open positions
    const opBody = document.getElementById('open-positions-body');
    if (data.open_trades && data.open_trades.length > 0) {
      opBody.innerHTML = data.open_trades.map(t => `
        <tr>
          <td><span class="badge badge-${t[3].toLowerCase()}">${t[3]}</span></td>
          <td>${fmt(t[5])}</td><td>${fmt(t[7])}</td><td>${fmt(t[8])}</td>
          <td>${fmt(t[9], 2)}</td>
          <td>${t[15] || '—'}</td><td>${t[16] || '—'}</td>
          <td>${fmt(t[19], 2)}</td>
        </tr>`).join('');
    } else {
      opBody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:#8b949e;padding:24px;">No open positions</td></tr>';
    }

    // Recent trades
    const tbody = document.getElementById('trades-body');
    if (data.trades && data.trades.length > 0) {
      tbody.innerHTML = data.trades.map(t => {
        const pnl = t[13] || t[10] || 0;
        return `<tr>
          <td style="color:#8b949e;font-size:12px;">${(t[1]||'').substring(0,16)}</td>
          <td><span class="badge badge-${(t[3]||'').toLowerCase()}">${t[3]||'—'}</span></td>
          <td>${fmt(t[5])}</td><td>${t[6] ? fmt(t[6]) : '—'}</td>
          <td style="color:${pnl>=0?'#3fb950':'#f85149'};font-weight:600;">
            ${pnl >= 0 ? '+' : ''}$${fmt(pnl)}</td>
          <td>${t[15] || '—'}</td><td>${t[16] || '—'}</td>
          <td>${t[19] ? fmt(t[19], 2) : '—'}</td>
        </tr>`;
      }).join('');
    } else {
      tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:#8b949e;padding:24px;">No trades yet</td></tr>';
    }
  }).catch(e => console.error('Refresh error:', e));
}

function drawEquityCurve(curve) {
  if (!curve || curve.length < 2) return;
  const vals = curve.map(p => p.cumulative_pnl);
  const minV = Math.min(...vals, 0);
  const maxV = Math.max(...vals, 0.01);
  const range = maxV - minV || 1;
  const W = 800, H = 220, PAD = 10;
  const toX = i => PAD + (i / (vals.length - 1)) * (W - 2 * PAD);
  const toY = v => PAD + (1 - (v - minV) / range) * (H - 2 * PAD);

  const pts = vals.map((v, i) => `${toX(i)},${toY(v)}`).join(' ');
  document.getElementById('equity-line').setAttribute('d', 'M ' + pts.split(' ').join(' L '));
  document.getElementById('equity-fill').setAttribute('d',
    'M ' + toX(0) + ',' + toY(minV) + ' L ' + pts.split(' ').join(' L ') +
    ' L ' + toX(vals.length - 1) + ',' + toY(minV) + ' Z');
}

function renderAttr(elemId, rows) {
  const el = document.getElementById(elemId);
  if (!rows || rows.length === 0) {
    el.innerHTML = '<div style="color:#8b949e;font-size:13px;padding:8px 0;">No data yet</div>';
    return;
  }
  el.innerHTML = rows.map(r => {
    const wr = r[1] > 0 ? Math.round(r[2] / r[1] * 100) : 0;
    const pnl = r[3] || 0;
    return `<div class="attr-row">
      <div>
        <div>${r[0] || '—'}</div>
        <div style="font-size:11px;color:#8b949e;">${r[1]} trades</div>
        <div class="progress-bar"><div class="progress-fill" style="width:${wr}%"></div></div>
      </div>
      <div style="text-align:right;">
        <div style="font-weight:600;">${wr}% WR</div>
        <div style="color:${pnl>=0?'#3fb950':'#f85149'};font-size:13px;">
          ${pnl>=0?'+':''}$${pnl}</div>
      </div>
    </div>`;
  }).join('');
}

function pauseBot() {
  fetch('/api/pause', {method:'POST'}).then(r => r.json())
    .then(d => { alert(d.message); refresh(); });
}
function stopBot() {
  if (confirm('Activate kill switch? Bot will stop trading immediately.')) {
    fetch('/api/kill', {method:'POST'}).then(r => r.json())
      .then(d => { alert(d.message); refresh(); });
  }
}

// Auto-refresh every 30 seconds
refresh();
setInterval(refresh, 30000);
</script>
</body>
</html>
"""


# -----------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------
@app.route('/')
def index():
    return render_template_string(DASHBOARD_HTML)


@app.route('/api/data')
def api_data():
    stats = db.get_stats_summary()
    equity_curve = db.get_equity_curve(limit=200)
    all_trades = db.get_all_trades(limit=30)
    open_trades = db.get_open_trades()
    by_session = db.get_performance_by_session()
    by_regime = db.get_performance_by_regime()

    # Daily P&L
    today_trades = db.get_daily_trades()
    daily_pnl = sum((t[13] or t[10] or 0) for t in today_trades)

    # Bot status
    if ks.is_active():
        status = 'stopped'
    elif ks.is_paused():
        status = 'paused'
    else:
        status = 'active'

    return jsonify({
        'status': status,
        'stats': stats,
        'equity_curve': equity_curve,
        'trades': [list(t) for t in all_trades],
        'open_trades': [list(t) for t in open_trades],
        'open_count': len(open_trades),
        'daily_pnl': round(daily_pnl, 2),
        'by_session': [list(r) for r in by_session],
        'by_regime': [list(r) for r in by_regime],
    })


@app.route('/api/metrics')
def api_metrics():
    metrics = perf.get_metrics()
    return jsonify(metrics or {})


@app.route('/api/pause', methods=['POST'])
def api_pause():
    import os
    pause_file = 'PAUSE_TRADING.txt'
    if os.path.exists(pause_file):
        os.remove(pause_file)
        return jsonify({'message': 'Bot resumed'})
    else:
        with open(pause_file, 'w') as f:
            f.write('Paused via dashboard')
        return jsonify({'message': 'Bot paused'})


@app.route('/api/kill', methods=['POST'])
def api_kill():
    ks.activate("Kill switch activated via dashboard")
    return jsonify({'message': 'Kill switch activated — bot stopped'})


@app.route('/api/health')
def api_health():
    return jsonify({'status': 'ok', 'db': db.db_path})


if __name__ == '__main__':
    print("Dashboard running at http://localhost:5000")
    app.run(host='0.0.0.0', port=5000, debug=False)
