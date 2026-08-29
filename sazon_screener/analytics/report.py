"""Screening analytics dashboard.

Usage:
    python -m sazon_screener.analytics.report                # text table
    python -m sazon_screener.analytics.report --html          # generate HTML file
    python -m sazon_screener.analytics.report --serve         # HTTP server on :8766
"""

import argparse
import http.server
import json
import os
import socketserver
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .data_loader import compute_all


# ── Text formatter ────────────────────────────────────────────────────


def _line(char: str = "-", width: int = 60) -> str:
    return char * width


def _header(title: str) -> str:
    return f"\n{_line('=')}\n  {title}\n{_line('=')}"


def _pct_bar(pct: float, width: int = 40) -> str:
    filled = int(pct / 100 * width)
    return "[" + "#" * filled + "." * (width - filled) + f"] {pct:.1f}%"


def format_text(data: dict[str, Any]) -> str:
    """Generate plain-text report."""
    lines: list[str] = []
    qs = data.get("qual_stats", {})
    funnel = data.get("funnel", [])
    city_dist = data.get("city_dist", [])
    daily = data.get("daily_trends", [])
    lang = data.get("lang_split", [])
    ts = data.get("trace_stats", {})

    dt = data.get("generated_at", "")
    lines.append(f"Sazón Screener — Analytics Report")
    lines.append(f"Generated: {dt}")
    lines.append(_line())

    # Overview
    lines.append(_header("Overview"))
    lines.append(f"  Total screenings:     {qs.get('total', 0)}")
    lines.append(f"  Qualified:            {qs.get('qualified', 0)} ({qs.get('qual_pct', 0)}%)")
    lines.append(f"  Disqualified:         {qs.get('disqualified', 0)} ({qs.get('disq_pct', 0)}%)")

    # Disqualification by reason
    reasons = qs.get("disq_by_reason", [])
    if reasons:
        lines.append(_header("Disqualification by Reason"))
        for r in reasons:
            lines.append(f"  {r['reason']:<50} {r['count']}")

    # Funnel
    if funnel:
        lines.append(_header("Funnel — Completion by Stage"))
        for f in funnel:
            stage = f["stage"].capitalize()
            pct = f["pct"]
            reached = f["reached"]
            bar = _pct_bar(pct)
            lines.append(f"  {stage:<15} {reached:>3} candidates  {bar}")

    # City distribution
    if city_dist:
        lines.append(_header("City Distribution"))
        for c in city_dist:
            bar = _pct_bar(c["pct"])
            lines.append(f"  {c['city']:<25} {c['count']:>3}  {bar}")

    # Language split
    if lang:
        lines.append(_header("Language Split"))
        for l in lang:
            bar = _pct_bar(l["pct"])
            lines.append(f"  {l['language']:<10} {l['count']:>3}  {bar}")

    # Daily trends
    if daily:
        lines.append(_header("Daily Trends"))
        for d in daily:
            lines.append(f"  {d['date']}  {d['count']} screenings")

    # Cost / traces
    if ts.get("total_events", 0) > 0:
        lines.append(_header("Trace Stats"))
        lines.append(f"  Total events:    {ts['total_events']}")
        lines.append(f"  LLM calls:       {ts['total_llm_calls']}")
        lines.append(f"  Total tokens:    {ts['total_tokens']}")
        lines.append(f"  Est. cost:       ${ts['estimated_cost']:.4f}")
        lines.append(f"  Unique runs:     {ts['unique_runs']}")

    if qs.get("total", 0) == 0 and not funnel and ts.get("total_events", 0) == 0:
        lines.append(_line())
        lines.append("  No data yet. Run the agent first to generate screening records.")

    lines.append(_line())
    return "\n".join(lines)


# ── HTML formatter ────────────────────────────────────────────────────


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sazón Screener — Analytics</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: #0d1117; color: #c9d1d9; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; padding: 32px; max-width: 960px; margin: 0 auto; }}
  h1 {{ color: #58a6ff; font-size: 24px; margin-bottom: 4px; }}
  .sub {{ color: #8b949e; font-size: 13px; margin-bottom: 24px; }}
  h2 {{ color: #f0f6fc; font-size: 16px; margin: 28px 0 12px; border-bottom: 1px solid #21262d; padding-bottom: 6px; }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 20px; }}
  .card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; }}
  .card .value {{ font-size: 28px; font-weight: 700; color: #f0f6fc; }}
  .card .label {{ font-size: 12px; color: #8b949e; text-transform: uppercase; letter-spacing: 0.5px; margin-top: 4px; }}
  .bar {{ display: flex; align-items: center; gap: 8px; margin: 4px 0; }}
  .bar-track {{ flex: 1; height: 20px; background: #21262d; border-radius: 4px; overflow: hidden; }}
  .bar-fill {{ height: 100%; border-radius: 4px; transition: width 0.3s; }}
  .bar-label {{ min-width: 100px; font-size: 13px; }}
  .bar-pct {{ min-width: 50px; text-align: right; font-size: 12px; color: #8b949e; }}
  .bar-fill.green {{ background: #3fb950; }}
  .bar-fill.orange {{ background: #d29922; }}
  .bar-fill.red {{ background: #f85149; }}
  .bar-fill.blue {{ background: #58a6ff; }}
  .table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  .table th {{ text-align: left; padding: 8px 12px; border-bottom: 2px solid #30363d; color: #8b949e; font-weight: 600; text-transform: uppercase; font-size: 11px; letter-spacing: 0.5px; }}
  .table td {{ padding: 8px 12px; border-bottom: 1px solid #21262d; }}
  .table tr:hover {{ background: #161b22; }}
  .empty {{ color: #8b949e; font-style: italic; padding: 24px; text-align: center; }}
  .updated {{ font-size: 11px; color: #484f58; text-align: center; margin-top: 40px; }}
  @media (max-width: 640px) {{ body {{ padding: 16px; }} }}
</style>
</head>
<body>
  <h1>Sazón Screener</h1>
  <div class="sub">Screening Analytics &mdash; {{generated_at}}</div>

  <div class="cards">
    <div class="card"><div class="value">{{total}}</div><div class="label">Total Screenings</div></div>
    <div class="card"><div class="value" style="color:#3fb950">{{qualified}}</div><div class="label">Qualified</div></div>
    <div class="card"><div class="value" style="color:#f85149">{{disqualified}}</div><div class="label">Disqualified</div></div>
    <div class="card"><div class="value">{{lang_es}}%</div><div class="label">Spanish</div></div>
  </div>

  {{funnel_html}}

  {{disq_html}}

  {{city_html}}

  {{daily_html}}

  {{cost_html}}

  <div class="updated">Last updated: {{generated_at}}</div>
</body>
</html>"""


def _bar_html(items: list[dict], label_key: str, value_key: str, pct_key: str,
               bar_color: str = "blue", max_label_width: int = 30) -> str:
    if not items:
        return ""
    rows = []
    for item in items:
        label = str(item.get(label_key, ""))[:max_label_width]
        pct = item.get(pct_key, 0)
        val = item.get(value_key, 0)
        rows.append(
            f'<div class="bar">'
            f'<div class="bar-label">{label}</div>'
            f'<div class="bar-track"><div class="bar-fill {bar_color}" style="width:{pct}%"></div></div>'
            f'<div class="bar-pct">{val}</div>'
            f'</div>'
        )
    return "\n".join(rows)


def format_html(data: dict[str, Any]) -> str:
    """Generate standalone HTML report."""
    qs = data.get("qual_stats", {})
    funnel = data.get("funnel", [])
    city_dist = data.get("city_dist", [])
    daily = data.get("daily_trends", [])
    lang = data.get("lang_split", [])
    ts = data.get("trace_stats", {})

    total = qs.get("total", 0)
    qual = qs.get("qualified", 0)
    disq = qs.get("disqualified", 0)

    es_pct = "0"
    if lang:
        for l in lang:
            if l["language"] == "es":
                es_pct = str(l["pct"])
                break

    # Funnel section
    if funnel:
        funnel_html = "<h2>Funnel — Completion by Stage</h2>"
        f_items = []
        for f in funnel:
            f_items.append({
                "label_key": "stage",
                "value_key": "reached",
                "pct_key": "pct",
                "label": f["stage"].capitalize(),
                "value": f["reached"],
                "pct": f["pct"],
            })
        funnel_html += _bar_html(
            [{"label": f["stage"].capitalize(), "value": f["reached"], "pct": f["pct"]} for f in funnel],
            "label", "value", "pct", "green",
        )
    else:
        funnel_html = ""

    # Disqualification section
    reasons = qs.get("disq_by_reason", [])
    if reasons:
        disq_html = "<h2>Disqualification by Reason</h2>"
        disq_html += _bar_html(
            [{"label": r["reason"][:50], "value": r["count"], "pct": round(r["count"] / disq * 100, 1) if disq else 0} for r in reasons],
            "label", "value", "pct", "red",
        )
    else:
        disq_html = ""

    # City distribution
    if city_dist:
        city_html = "<h2>City Distribution</h2>"
        city_html += "<table class='table'><tr><th>City</th><th>Count</th><th>%</th></tr>"
        for c in city_dist:
            city_html += f"<tr><td>{c['city']}</td><td>{c['count']}</td><td>{c['pct']}%</td></tr>"
        city_html += "</table>"
    else:
        city_html = ""

    # Daily trends
    if daily:
        daily_html = "<h2>Daily Trends</h2>"
        daily_html += "<table class='table'><tr><th>Date</th><th>Screenings</th></tr>"
        for d in daily:
            daily_html += f"<tr><td>{d['date']}</td><td>{d['count']}</td></tr>"
        daily_html += "</table>"
    else:
        daily_html = ""

    # Cost/trace stats
    if ts.get("total_events", 0) > 0:
        cost_html = "<h2>Trace & Cost</h2>"
        cost_html += "<table class='table'>"
        cost_html += f"<tr><td>Total events</td><td>{ts['total_events']}</td></tr>"
        cost_html += f"<tr><td>LLM calls</td><td>{ts['total_llm_calls']}</td></tr>"
        cost_html += f"<tr><td>Total tokens</td><td>{ts['total_tokens']}</td></tr>"
        cost_html += f"<tr><td>Est. cost</td><td>${ts['estimated_cost']:.4f}</td></tr>"
        cost_html += f"<tr><td>Unique runs</td><td>{ts['unique_runs']}</td></tr>"
        cost_html += "</table>"
    else:
        cost_html = ""

    dt = data.get("generated_at", "")

    if total == 0 and not funnel and ts.get("total_events", 0) == 0:
        funnel_html = '<div class="empty">No data yet. Run the agent to generate screening records.</div>'

    return _HTML_TEMPLATE \
        .replace("{{total}}", str(total)) \
        .replace("{{qualified}}", str(qual)) \
        .replace("{{disqualified}}", str(disq)) \
        .replace("{{lang_es}}", es_pct) \
        .replace("{{funnel_html}}", funnel_html) \
        .replace("{{disq_html}}", disq_html) \
        .replace("{{city_html}}", city_html) \
        .replace("{{daily_html}}", daily_html) \
        .replace("{{cost_html}}", cost_html) \
        .replace("{{generated_at}}", dt)


# ── HTTP server ───────────────────────────────────────────────────────


_HTML_CACHE: str = ""


def _build_html() -> str:
    data = compute_all()
    return format_html(data)


class AnalyticsHandler(http.server.BaseHTTPRequestHandler):
    """Serves the analytics HTML page."""

    def do_GET(self) -> None:
        if self.path == "/" or self.path == "/index.html":
            html = _build_html()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))
        elif self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[analytics] {fmt % args}", file=sys.stderr)


def serve(port: int = 8766) -> None:
    """Start analytics HTTP server."""
    print(f"Sazón Screener Analytics → http://0.0.0.0:{port}")
    with socketserver.TCPServer(("0.0.0.0", port), AnalyticsHandler) as httpd:
        httpd.serve_forever()


# ── CLI ────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Sazón Screener Analytics")
    parser.add_argument("--html", action="store_true", help="Generate HTML report")
    parser.add_argument("--serve", action="store_true", help="Start HTTP server")
    parser.add_argument("--port", type=int, default=8766, help="Server port (default 8766)")
    parser.add_argument("--output", type=str, default="analytics_report.html",
                        help="Output file for --html")
    args = parser.parse_args()

    if args.serve:
        serve(args.port)
        return

    data = compute_all()

    if args.html:
        html = format_html(data)
        Path(args.output).write_text(html, encoding="utf-8")
        print(f"Report written to {args.output}")
    else:
        text = format_text(data)
        print(text)


if __name__ == "__main__":
    main()