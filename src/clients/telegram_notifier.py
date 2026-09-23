from __future__ import annotations
import os
import json
import html
import logging
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv
load_dotenv('.env.local')

logger = logging.getLogger(__name__)


def _format_time_ago(minutes):
    if not isinstance(minutes, (int, float)) or minutes < 0:
        return "?"
    days = int(minutes // 1440)
    hours = int((minutes % 1440) // 60)
    mins = int(minutes % 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if mins or not parts:
        parts.append(f"{mins}m")
    return " ".join(parts) + " ago"


def _teal(text: any) -> str:
    """Formats text with Telegram sleek teal link accent (clean, zero code pills)."""
    clean_val = html.escape(str(text))
    return f'<a href="https://t.me/bayesianpivot_bot">{clean_val}</a>'


class TelegramNotifier:
    def __init__(self, bot_token=None, chat_id=None):
        self.bot_token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat_id   = chat_id   or os.environ.get("TELEGRAM_CHAT_ID")
        self.base_url  = f"https://api.telegram.org/bot{self.bot_token}"
        logger.info(f"📤 Notifier Initialized | Chat ID: {self.chat_id}")

        self.last_alerts    = {}   # deduplication tracker
        self.COOLDOWN_MINUTES = 60

    # ──────────────────────────────────────────────────────────────────────────
    # V3 SOVEREIGN ALERT
    # ──────────────────────────────────────────────────────────────────────────

    def send_alert(self, symbol, timeframe, pattern, ai_score, reasoning,
                   verdict="N/A", risk_calc=None, buttons=None,
                   regime_result=None,
                   health_report=None,
                   bias_data=None,
                   liquidity_targets=None,
                   session_info=None,
                   shadow_insights=None,
                   security_status=None,
                   psych_data=None,
                   direction=None):
        """V3 Hierarchy of Edge alert — strict HTML, mobile-first, agent-readable."""
        if not self.bot_token or not self.chat_id:
            logger.warning("Telegram credentials not set. Skipping alert.")
            return

        # ── Deduplication ────────────────────────────────────────────────────
        now      = datetime.now()
        alert_key = f"{symbol}_{pattern}"
        if alert_key in self.last_alerts:
            elapsed = (now - self.last_alerts[alert_key]).total_seconds() / 60
            if elapsed < self.COOLDOWN_MINUTES:
                logger.info(f"🤫 Suppressing duplicate {symbol} ({elapsed:.0f}m ago)")
                return
        self.last_alerts[alert_key] = now

        # ── 1. HEADER ─────────────────────────────────────────────────────────
        hr          = health_report or {}
        dd_pct      = hr.get('daily_drawdown', 0.0) * 100
        equity_buf  = hr.get('equity_buffer_usd', 0.0)
        sess_phase  = session_info.get('phase', 'Unknown') if session_info else 'Unknown'
        kz_name     = session_info.get('name', 'OFF-HOURS') if session_info else 'OFF-HOURS'

        badge = '⚠️ [WARNING]' if dd_pct >= 3.0 else '🛡️ [SECURE]'
        header = (
            f"{badge} | 🏁 <b>{kz_name} — {sess_phase}</b> | "
            f"📉 Buffer: {_teal(f'${equity_buf:,.0f}')}"
        )

        # ── 2. BIAS CONFLUENCE ────────────────────────────────────────────────
        bd  = bias_data or {}
        confluence = (
            f"📐 <b>BIAS CONFLUENCE</b>\n"
            f"• Daily: {_teal(bd.get('daily','N/A'))} | "
            f"HTF: {_teal(bd.get('htf','N/A'))} | "
            f"Intermarket: {_teal(bd.get('dxy_trend','N/A'))}"
        )

        # ── 3. LIQUIDITY EDGE ─────────────────────────────────────────────────
        lt = liquidity_targets or {}
        draw_px   = lt.get('target_price', 'N/A')
        draw_type = lt.get('target_type', 'N/A')
        dist_pips = lt.get('distance_pips', 'N/A')
        if isinstance(draw_px, float):   draw_px   = f"{draw_px:,.4f}"
        if isinstance(dist_pips, float): dist_pips = f"{dist_pips:.1f}"
        liquidity = (
            f"🎯 <b>LIQUIDITY EDGE</b>\n"
            f"• Draw on Liquidity: {_teal(draw_px)} <i>({draw_type})</i>\n"
            f"• Gravity: {_teal(f'{dist_pips} pips')}"
        )

        # ── 4. THE HUNT ───────────────────────────────────────────────────────
        safe_reasoning = html.escape(str(reasoning or ''))
        hunt = (
            f"🦅 <b>THE HUNT</b>\n"
            f"• Active Strategy: {_teal(pattern)} (<b>{ai_score}/10</b>)\n"
            f"• Hunt Logic: <i>{safe_reasoning}</i>"
        )

        # ── 5. SYSTEM STATE ───────────────────────────────────────────────────
        pd          = psych_data or {}
        mood        = pd.get('mood', 'N/A')
        alpha_mult  = regime_result.suggested_size_mult if regime_result and hasattr(regime_result, 'suggested_size_mult') else 'N/A'
        atr_pct_ile = regime_result.atr_percentile      if regime_result and hasattr(regime_result, 'atr_percentile')      else 'N/A'
        slip        = (shadow_insights or {}).get('slippage_estimate', 'N/A')

        if isinstance(alpha_mult, float): alpha_mult = f"{alpha_mult:.2f}"

        system_state = (
            f"🔬 <b>SYSTEM STATE</b>\n"
            f"• Mood: {_teal(mood)} | Alpha Persistence: {_teal(f'{alpha_mult}x')}\n"
            f"• Volatility: {_teal(f'{atr_pct_ile}th %ile')} | Slip: {_teal(slip)}"
        )

        # ── 6. EXECUTION ──────────────────────────────────────────────────────
        exec_block = ""
        if risk_calc:
            entry = risk_calc.get('entry', 0)
            sl    = risk_calc.get('stop_loss', 0)
            lots  = risk_calc.get('position_size', 0)
            pos_val = risk_calc.get('position_value', 0)
            tp    = risk_calc.get('take_profit', 'OPEN')
            tp_str = f"${tp:,.4f}" if isinstance(tp, (int, float)) else str(tp)
            
            val_str = f" | Position Value: {_teal(f'${pos_val:,.2f}')}" if pos_val > 0 else ""
            
            exec_block = (
                f"\n💷 <b>EXECUTION</b>\n"
                f"• Entry: {_teal(f'${entry:,.4f}')} | SL: {_teal(f'${sl:,.4f}')} | TP: {_teal(tp_str)}\n"
                f"• Position Size: {_teal(lots)}{val_str}"
            )

        # ── 7. AGENT-READABLE JSON SPOILER ────────────────────────────────────
        agent_payload = {
            "symbol":  symbol,
            "regime":  regime_result.regime.value if regime_result and hasattr(regime_result, 'regime') else None,
            "bias":    {"daily": bd.get('daily'), "htf": bd.get('htf'), "dxy": bd.get('dxy_trend')},
            "draw":    {"price": lt.get('target_price'), "type": lt.get('target_type'), "pips": lt.get('distance_pips')},
            "risk":    {"size_mult": regime_result.suggested_size_mult if regime_result and hasattr(regime_result, 'suggested_size_mult') else None,
                        "dd_pct": round(dd_pct, 2), "buffer_usd": equity_buf},
        }
        spoiler_block = (
            f"\n\n<tg-spoiler>agent_data: {json.dumps(agent_payload, default=str)}</tg-spoiler>"
        )

        # ── TradingView Link ──────────────────────────────────────────────────
        tv_sym  = symbol.replace("/", "")
        tv_link = f"https://www.tradingview.com/chart/?symbol=BINANCE:{tv_sym}"
        # Derive direction from the explicit signal direction first; fall back to
        # case-insensitive pattern matching so labels like "FVG_BULLISH" are handled.
        dir_upper = (direction or "").upper()
        is_long = (
            dir_upper == "LONG"
            or "LONG" in (pattern or "").upper()
            or "BULLISH" in (pattern or "").upper()
        )
        emoji   = "🟢" if is_long else "🔴"
        grade   = "🦄 UNICORN" if ai_score >= 8.5 else ("🦅 HIGH ALPHA" if ai_score >= 7.5 else "⚠️ MED ALPHA")

        # ── ASSEMBLE ──────────────────────────────────────────────────────────
        warning_block = ""
        if is_long:
            warning_block = (
                f"\n\n⚠️ <b>HISTORICAL RISK ALERT:</b> Long trades represent your largest manual draw. "
                f"Ensure strict limit execution and 50% risk reduction ($50 USD max risk)."
            )
        
        if (bias_data or {}).get('bias_conflict'):
            conflict_warning = (
                f"\n\n⚠️ <b>BIAS CONFLICT:</b> Multi-timeframe bias is conflicted (1D vs 4H/1H divergence). "
                f"Trade size reduced to 50% of normal. Monitor closely for structural break."
            )
            warning_block = (warning_block or "") + conflict_warning

        security_block = ""
        if security_status:
            if "CLEAN" in str(security_status).upper() or "SECURE" in str(security_status).upper():
                status_formatted = "🛡️ *Security:* Environment confirmed as secure"
            else:
                status_formatted = f"⚠️ *Security:* {security_status}"
            security_block = f"\n\n{status_formatted}"

        msg = (
            f"{emoji} <b>{grade}: {symbol}</b>\n"
            f"{header}\n\n"
            f"{hunt}\n\n"
            f"{confluence}\n\n"
            f"{liquidity}\n\n"
            f"{system_state}"
            f"{exec_block}"
            f"{warning_block}"
            f"{security_block}\n\n"
            f"📊 <a href='{tv_link}'>View on TradingView</a>"
            f"{spoiler_block}"
        )
        self._send_message(msg, buttons=buttons)

    # ──────────────────────────────────────────────────────────────────────────
    # V3 /scan BRIEFING
    # ──────────────────────────────────────────────────────────────────────────

    def send_scan_briefing(self, header_data: dict, account_data: dict,
                           performance_data: dict, confluence_data: dict,
                           market_rows: list, latest_setup: dict | None,
                           latest_rejected: dict | None = None,
                           strategic_directive: str | None = None):
        """
        Sends the full V3 Sovereign Briefing on /scan command.
        All ASCII tables are replaced with HTML lists for mobile readability.
        """
        # ── HEADER ────────────────────────────────────────────────────────────
        badge      = '⚠️ <b>[INTEGRITY: WARNING]</b>' if header_data.get('dd_pct', 0) >= 3.0 else '🛡️ <b>[INTEGRITY: SECURE]</b>'
        trust      = header_data.get('trust', 100)
        kz_name    = header_data.get('kz_name', 'OFF-HOURS')
        sess_phase = header_data.get('sess_phase', 'Unknown')
        dd_pct     = header_data.get('dd_pct', 0.0)
        buf_usd    = header_data.get('equity_buffer_usd', 0.0)
        uptime     = header_data.get('uptime', 'N/A')
        cycle      = header_data.get('cycle', 0)
        security   = header_data.get('security', 'N/A')

        header = (
            f"🔍 <b>BAYESIAN PIVOT BRIEFING v3</b>\n"
            f"{badge} | Trust: {_teal(f'{trust}/100')}\n"
            f"🏁 <b>{kz_name}</b> — {sess_phase}\n"
            f"📉 DD: {_teal(f'{dd_pct:.1f}%')} | Buffer: {_teal(f'${buf_usd:,.0f}')}\n"
            f"🕒 Uptime: {_teal(uptime)} | Cycle {_teal(f'#{cycle}')}\n"
            f"🔐 {_teal(security)}"
        )

        # ── ACCOUNT ───────────────────────────────────────────────────────────
        equity    = account_data.get('equity', 0)
        acct_block = f"💰 <b>Account</b>\n• Equity: {_teal(f'${equity:,.2f}')}"

        # ── OPEN POSITIONS ────────────────────────────────────────────────────
        positions = account_data.get('positions', [])
        if positions:
            pos_lines = []
            for p in positions:
                pnl  = p.get('pnl', 0)
                icon = '🟢' if pnl >= 0 else '🔴'
                side = 'BUY' if p.get('side','').upper() == 'BUY' else 'SELL'
                px = p.get('price', 0)
                pos_lines.append(f"  {icon} {_teal(p.get('symbol','N/A'))} {side} @ {_teal(f'{px:.4f}')} → {_teal(f'{pnl:+.2f}')}")
            pos_block = f"📂 <b>Open ({len(positions)})</b>\n" + "\n".join(pos_lines)
        else:
            pos_block = "📂 <b>Open Positions</b>\n  <i>None</i>"

        # ── PERFORMANCE ───────────────────────────────────────────────────────
        n_trades  = performance_data.get('total_trades', 0)
        win_rate  = performance_data.get('win_rate', 0)
        avg_rr    = performance_data.get('avg_rr', 0)
        avg_win   = performance_data.get('avg_win', 0)
        avg_loss  = performance_data.get('avg_loss', 0)
        recent    = performance_data.get('recent', [])

        perf_block = (
            f"📈 <b>Performance ({n_trades} trades)</b>\n"
            f"• Win Rate: {_teal(f'{win_rate:.1f}%')} | Avg RR: {_teal(f'{avg_rr:.2f}')}\n"
            f"• Avg Win: {_teal(f'${avg_win:+.2f}')} | Avg Loss: {_teal(f'-${avg_loss:.2f}')}"
        )
        if recent:
            perf_block += "\n\n🕔 <b>Last 5 Closed</b>"
            for t in recent:
                pnl  = t.get('pnl', 0)
                icon = '🟢' if pnl >= 0 else '🔴'
                ts   = t.get('close_time', '')[:10]
                perf_block += f"\n  {icon} {_teal(t.get('symbol','?'))} {t.get('side','')} {ts} → {_teal(f'{pnl:+.2f}')}"

        # ── BIAS CONFLUENCE ───────────────────────────────────────────────────
        dxy = confluence_data.get('dxy', {})
        nq  = confluence_data.get('nq', {})
        tnx = confluence_data.get('tnx', {})
        dxy_chg = dxy.get('change_ltf', 0)
        nq_chg = nq.get('change_ltf', 0)
        tnx_chg = tnx.get('change_ltf', 0)
        alpha_mult      = confluence_data.get('alpha_mult', 1.0)
        alpha_reasoning = confluence_data.get('alpha_reasoning', 'N/A')

        confluence_block = (
            f"📐 <b>Confluence (Intermarket)</b>\n"
            f"• DXY: {_teal(dxy.get('trend','N/A'))} ({_teal(f'{dxy_chg:+.2f}%')})\n"
            f"• NQ: {_teal(nq.get('trend','N/A'))} ({_teal(f'{nq_chg:+.2f}%')})\n"
            f"• TNX: {_teal(tnx.get('trend','N/A'))} ({_teal(f'{tnx_chg:+.2f}%')})\n"
            f"✨ Alpha: {_teal(f'{alpha_mult:.2f}x')} — <i>{alpha_reasoning}</i>"
        )

        # ── MARKET STATE (HTML list, no ASCII table) ──────────────────────────
        if market_rows:
            market_block = "📊 <b>Market State</b>\n"
            for row in market_rows:
                sym    = row.get('symbol','?').split('/')[0]
                bias   = row.get('bias','N/A')
                regime = row.get('regime','N/A')
                h      = row.get('hurst', 0.5)
                strat  = 'Turtle Soup' if h < 0.45 else ('Trend Align' if h > 0.55 else 'Structure')
                draw   = row.get('draw', None)
                draw_str = f" | Draw: {_teal(draw)}" if draw else ""
                market_block += f"• <b>{sym}</b> — {_teal(bias)} | {regime} | H:<b>{h:.2f}</b> [{strat}]{draw_str}\n"
        else:
            market_block = "📊 <b>Market State</b>\n<i>No scan data yet.</i>"

        # ── LATEST SETUPS (Call vs Rejected) ──────────────────────────────────
        setup_block = ''
        if latest_setup:
            mins_ago = latest_setup.get('mins_ago', '?')
            setup_block += (
                f"💎 <b>Latest Call</b>: {_teal(latest_setup.get('symbol','?'))} ({_format_time_ago(mins_ago)})\n"
                f"  • Formation: {_teal(latest_setup.get('pattern','N/A'))} | AI: <b>{latest_setup.get('ai_score','N/A')}/10</b>\n"
            )
        
        if latest_rejected:
            mins_ago_rej = latest_rejected.get('mins_ago', '?')
            rej_ai = latest_rejected.get('ai_score', 'N/A')
            if setup_block: setup_block += "\n"
            setup_block += (
                f"❌ <b>Latest Rejected</b>: {_teal(latest_rejected.get('symbol','?'))} ({_format_time_ago(mins_ago_rej)})\n"
                f"  • Formation: {_teal(latest_rejected.get('pattern','N/A'))} | AI: {_teal(f'{rej_ai}/10')}\n"
            )

        if not setup_block:
            setup_block = "🔭 <b>Setups Today</b>\n  <i>No signals detected this session.</i>"

        # ── STRATEGIC DIRECTIVE ───────────────────────────────────────────────
        directive_block = ""
        if strategic_directive:
            directive_block = f"🧠 <b>Strategic Directive</b>\n• {strategic_directive}"

        # ── AGENT SPOILER ─────────────────────────────────────────────────────
        agent_payload = {
            "equity": equity,
            "dd_pct": dd_pct,
            "buffer_usd": buf_usd,
            "win_rate": win_rate,
            "avg_rr": avg_rr,
            "dxy": dxy.get('trend'),
            "kz": kz_name,
            "sess_phase": sess_phase,
            "market": [{"sym": r.get('symbol','').split('/')[0], "bias": r.get('bias'), "hurst": r.get('hurst')} for r in market_rows],
        }
        spoiler = f"\n<tg-spoiler>agent_data: {json.dumps(agent_payload, default=str)}</tg-spoiler>"

        # ── ASSEMBLE ──────────────────────────────────────────────────────────
        msg = f"{header}\n\n"
        if directive_block:
            msg += f"{directive_block}\n\n"
        msg += (
            f"{acct_block}\n\n"
            f"{pos_block}\n\n"
            f"{perf_block}\n\n"
            f"{confluence_block}\n\n"
            f"{market_block}\n"
            f"{setup_block}"
            f"{spoiler}"
        )
        self._send_message(msg)

    # ──────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────────────────────

    def send_security_alert(self, title: str, summary: str, severity: str = "HIGH"):
        icon = {"CRITICAL": "🚨", "HIGH": "⚠️", "MEDIUM": "🟡"}.get(severity, "⚠️")
        msg = (
            f"{icon} <b>BAYESIAN PIVOT GUARD — {severity}</b>\n\n"
            f"🛡️ <b>{title}</b>\n\n{summary}\n\n"
            f"⏰ {_teal(datetime.now().strftime('%H:%M:%S UTC'))}"
        )
        self._send_message(msg)

    def send_kill_switch(self, reason):
        self._send_message(
            f"⚠️ <b>CIRCUIT BREAKER TRIGGERED</b>\n\n"
            f"🛑 Reason: {reason}\n\n"
            f"Trading suspended until manual reset or 00:00 UTC."
        )

    def send_system_error(self, component, error):
        self._send_message(
            f"🆘 <b>CRITICAL ERROR</b>\n\n"
            f"📍 Component: {_teal(component)}\n"
            f"❌ Error: <i>{str(error)[:300]}</i>\n\n"
            f"Check local logs for details."
        )

    def _send_message(self, text, buttons=None):
        if not self.bot_token or not self.chat_id:
            return
        payload = {
            "chat_id": self.chat_id,
            "text": text if text else "",
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if buttons:
            payload["reply_markup"] = {"inline_keyboard": buttons}
        try:
            r = requests.post(f"{self.base_url}/sendMessage", json=payload, timeout=5)
            logger.info(f"📤 TG ({r.status_code}): {r.text[:80]}")
            r.raise_for_status()
        except Exception as e:
            logger.error(f"❌ Telegram send failed: {e}")
            # Robust fallback: If HTML entity parsing fails, strip tags and deliver plaintext
            try:
                import re
                plain_text = re.sub(r'<[^>]+>', '', text or '')
                payload["text"] = plain_text
                payload.pop("parse_mode", None)
                r_fb = requests.post(f"{self.base_url}/sendMessage", json=payload, timeout=5)
                r_fb.raise_for_status()
                logger.info(f"📤 TG Fallback Plaintext ({r_fb.status_code}): Delivered successfully.")
            except Exception as fb_err:
                logger.error(f"❌ Telegram fallback plaintext also failed: {fb_err}")

    def get_latest_message(self, since_timestamp=None):
        try:
            r = requests.get(f"{self.base_url}/getUpdates",
                             params={"limit": 100, "allowed_updates": ["message"]}, timeout=5)
            r.raise_for_status()
            msgs = []
            for upd in r.json().get("result", []):
                msg = upd.get("message")
                if not msg or str(msg.get("chat", {}).get("id")) != str(self.chat_id):
                    continue
                txt = msg.get("text")
                ts  = msg.get("date")
                if txt and (not since_timestamp or ts > since_timestamp):
                    msgs.append({"text": txt, "timestamp": ts})
            return sorted(msgs, key=lambda x: x["timestamp"])[-1] if msgs else None
        except Exception as e:
            logger.error(f"TG getUpdates failed: {e}")
            return None

    def send_executive_report_to_telegram(self):
        """Generates and sends the daily executive portfolio report directly to Telegram."""
        from src.clients.tl_client import TradeLockerClient
        from src.engines.multi_account_funnel import MultiAccountFunnelManager
        from src.engines.counterfactual_tracker import CounterfactualTracker
        from src.engines.qa_quant_agent import QAQuantAgent

        tl = TradeLockerClient()
        funnel = MultiAccountFunnelManager()
        tracker = CounterfactualTracker()
        qa = QAQuantAgent()

        total_equity = tl.get_total_equity()
        open_positions = tl.get_open_positions()
        summary = tracker.get_counterfactual_summary()
        health = qa.audit_portfolio_health(total_equity, open_positions)

        prev_loss = summary.get('prevented_losses_usd', 0.0)
        net_impact = summary.get('net_filter_impact_usd', 0.0)

        msg = (
            f"📊 <b>BAYESIAN PIVOT — EXECUTIVE REPORT</b>\n"
            f"⏰ {_teal(datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC'))}\n\n"
            f"🏛️ <b>Total Portfolio NAV:</b> {_teal(f'${total_equity:,.2f}')}\n"
            f"📈 <b>Active Open Trades:</b> {_teal(len(open_positions))}\n"
            f"🛡️ <b>Portfolio Health:</b> {_teal(health['status'])}\n\n"
            f"👻 <b>COUNTERFACTUAL SHADOW ENGINE</b>\n"
            f"• Trades Audited: {_teal(summary.get('total_shadow_trades', 0))}\n"
            f"• Prevented Losses: {_teal(f'${prev_loss:,.2f}')}\n"
            f"• Net Filter Impact: {_teal(f'${net_impact:,.2f}')}\n\n"
            f"⚡ <i>Active Fleet Mandates Provisioned & Hardened.</i>"
        )
        
        buttons = [
            [{"text": "🚨 EMERGENCY KILL SWITCH", "callback_data": "btn_kill"}]
        ]
        self._send_message(msg, buttons=buttons)

    def poll_updates_and_dispatch(self, offset=None):
        """
        Polls Telegram updates for commands (/report, /kill, /status) or inline buttons,
        and executes requested actions.
        """
        if not self.bot_token or not self.chat_id:
            return offset

        try:
            params = {"limit": 50, "allowed_updates": ["message", "callback_query"]}
            if offset:
                params["offset"] = offset

            resp = requests.get(f"{self.base_url}/getUpdates", params=params, timeout=5)
            if resp.status_code != 200:
                return offset

            results = resp.json().get("result", [])
            new_offset = offset

            for upd in results:
                upd_id = upd.get("update_id")
                if upd_id:
                    new_offset = upd_id + 1

                # Check text message
                msg = upd.get("message", {})
                chat_id = str(msg.get("chat", {}).get("id"))
                text = str(msg.get("text", "")).strip().lower()

                # Check callback query (button click)
                cb = upd.get("callback_query", {})
                cb_data = str(cb.get("data", "")).strip().lower()
                cb_chat = str(cb.get("message", {}).get("chat", {}).get("id"))

                if (chat_id == str(self.chat_id) and text) or (cb_chat == str(self.chat_id) and cb_data):
                    target_cmd = text if text else cb_data

                    if target_cmd in ["/report", "btn_report", "/status", "btn_status"]:
                        logger.info("📱 Telegram Command Received: /report. Generating Executive Summary...")
                        self.send_executive_report_to_telegram()

                    elif target_cmd in ["/kill", "btn_kill", "/emergency_kill"]:
                        logger.warning("🚨 Telegram Emergency Kill Command Received! Executing portfolio wipe...")
                        from scripts.maintenance.emergency_kill_switch import execute_emergency_kill_switch
                        self._send_message("🚨 <b>EMERGENCY KILL SWITCH TRIGGERED VIA TELEGRAM</b>\nLiquidating all open positions across the entire account fleet...")
                        execute_emergency_kill_switch()
                        self._send_message("✅ <b>EMERGENCY LIQUIDATION COMPLETE.</b> All open positions closed across all account mandates.")

                    elif target_cmd.startswith("scale_"):
                        logger.info(f"🚀 Telegram Scale-In Command Received: {target_cmd}")
                        try:
                            # format: scale_BTCUSD_buy_78648.0_77371.0_79048.0 (scale_<SYM>_<SIDE>_<ENTRY>_<SL>_<TP>)
                            parts = target_cmd.split("_")
                            raw_sym = parts[1].upper()
                            sym = f"{raw_sym[:3]}/{raw_sym[3:]}" if len(raw_sym) == 6 else raw_sym
                            side = parts[2].lower()
                            entry_p = float(parts[3])
                            sl = float(parts[4])
                            tp = float(parts[5])
                            
                            self._send_message(f"🚀 <b>DISPATCHING TRANCHE 2 (SCALE-IN):</b> Adding remaining 50% size on {sym} {side.upper()} across all accounts...")
                            from src.clients.tl_client import TradeLockerClient
                            from src.core.config import Config
                            res = TradeLockerClient().execute_trade_across_all_accounts(
                                symbol=sym,
                                side=side,
                                entry_price=entry_p,
                                stop_loss=sl,
                                take_profit=tp,
                                risk_scale=getattr(Config, 'SCALE_IN_TRANCHE_2_SCALE', 0.50),
                                tranche_label="TRANCHE_2_SCALE_IN"
                            )
                            if res.get('success'):
                                self._send_message(f"✅ <b>SCALE-IN COMPLETE!</b> Tranche 2 (+50% Size) filled on {res.get('filled_count')}/{res.get('total_accounts')} accounts. Position is now at 100% Full Size! 🚀")
                            else:
                                self._send_message("⚠️ <b>SCALE-IN NOTICE:</b> Failed to fill Tranche 2 or accounts rejected order.")
                        except Exception as scale_err:
                            logger.error(f"Error executing scale-in from Telegram: {scale_err}")
                            self._send_message(f"❌ <b>SCALE-IN ERROR:</b> {scale_err}")

            return new_offset

        except Exception as e:
            logger.error(f"Telegram polling error: {e}")
            return offset

    def send_photo(self, photo_path, caption=None):

        try:
            with open(photo_path, 'rb') as f:
                payload = {'chat_id': self.chat_id}
                if caption:
                    payload.update({'caption': caption, 'parse_mode': 'HTML'})
                requests.post(f"{self.base_url}/sendPhoto",
                              data=payload, files={'photo': f}, timeout=10)
        except Exception as e:
            logger.error(f"TG photo failed: {e}")


    def send_high_confluence_alert(self, symbol: str, direction: str, entry: float,
                                   stop_loss: float, target: float, ai_score: float,
                                   smt_strength: float, session_name: str,
                                   price_quartile: float, buttons: list = None):
        """
        Pushes an elite Golden Confluence Payout Alert when a setup passes all 5 rigid edge criteria:
          1. NY AM Session (07:00-10:00 EST)
          2. SMT Strength >= 0.50
          3. Q2 Manipulation Window
          4. Deep Discount (<0.25) or Deep Premium (>0.75)
          5. AI Validator Score >= 8.5
        """
        dir_str = str(direction or "LONG").upper()
        is_long = dir_str == "LONG" or dir_str == "BUY"
        emoji = "🟢" if is_long else "🔴"
        risk_dist = abs(entry - stop_loss)
        reward_dist = abs(target - entry)
        rr = reward_dist / risk_dist if risk_dist > 0 else 2.5
        target_pnl = 150.0 * rr  # Estimated PnL at standard $150 risk

        msg = (
            f"🏆 <b>GOLDEN CONFLUENCE ALERT (PAYOUT PLAY)</b>\n"
            f"{emoji} <b>{symbol} {dir_str} @ ${entry:,.2f}</b>\n"
            f"🤖 <b>AI Score: {ai_score}/10 (FLOW_GO)</b>\n\n"
            f"🔥 <b>RIGID DATA CONFLUENCE MET:</b>\n"
            f"• 🏁 <b>{session_name}</b> (Killzone Confirmed — 66.7% Win Rate)\n"
            f"• ⚡ <b>SMT Sponsorship ({smt_strength:.2f})</b> — 66.4% Win Rate\n"
            f"• ⏳ <b>Q2 Judas Window</b> (90-Min Manipulation)\n"
            f"• 💰 <b>Quartile Position ({price_quartile:.2f})</b> — 66.7% Win Rate\n\n"
            f"🛑 <b>Stop Loss:</b> ${stop_loss:,.2f}\n"
            f"🎯 <b>Full Target ({rr:.1f}R):</b> ${target:,.2f} (Est. +${target_pnl:,.2f} PnL)\n\n"
            f"⚠️ <b>PAYOUT DIRECTIVE:</b> This trade meets all 5 rigid statistical edge criteria. "
            f"Do NOT take early micro-exits. Allow trade to run to full {rr:.1f}R target."
        )
        self._send_message(msg, buttons=buttons)

    def send_deadzone_alert(self, symbol: str, direction: str, utc_hour: int):
        """
        Pushes a Dead-Zone Warning Alert when a trade is placed during historical loss hours,
        including the exact end time and when the next prime window opens.
        """
        deadzone_map = {
            7:  {"ast": "04:00 AM", "ends_at": "05:00 AM AST", "next_prime": "07:00 AM AST (NY AM Session — +$1,565 PnL)", "loss": 2104.43, "wr": "26.7%"},
            14: {"ast": "11:00 AM", "ends_at": "12:00 PM AST", "next_prime": "03:00 PM AST (NY Close — 62.5% WR)", "loss": 1030.64, "wr": "31.2%"},
            17: {"ast": "02:00 PM", "ends_at": "03:00 PM AST", "next_prime": "03:00 PM AST (NY Close — 62.5% WR)", "loss": 1582.72, "wr": "20.0%"},
            22: {"ast": "07:00 PM", "ends_at": "08:00 PM AST", "next_prime": "09:00 PM AST (Asian Open — +$594 PnL)", "loss": 1289.39, "wr": "42.9%"},
        }
        info = deadzone_map.get(utc_hour, {
            "ast": f"{utc_hour}:00 UTC",
            "ends_at": f"{(utc_hour+1)%24}:00 UTC",
            "next_prime": "Next Prime Killzone",
            "loss": 1000.0,
            "wr": "30%"
        })
        dir_display = str(direction or "UNKNOWN").upper()
        msg = (
            f"🛑 <b>HISTORICAL DEAD-ZONE WARNING ({info['ast']} AST)</b>\n"
            f"⚠️ <b>{symbol} {dir_display} Trade Detected</b>\n\n"
            f"<i>Database Audit Warning: Trading during {info['ast']} AST (UTC {utc_hour:02d}:00) has generated "
            f"<b>-${info['loss']:,.2f}</b> in historical losses (Win Rate: {info['wr']}).\n\n"
            f"⏳ <b>Dead Zone Ends At:</b> {info['ends_at']}\n"
            f"🟢 <b>Next Prime Window:</b> {info['next_prime']}\n\n"
            f"Execution is strongly discouraged. Wait for the next prime window.</i>"
        )
        self._send_message(msg)


# ── Standalone helpers ────────────────────────────────────────────────────────

def send_alert(symbol, timeframe, pattern, ai_score, reasoning, verdict="N/A",
               risk_calc=None, buttons=None, shadow_insights=None, security_status=None,
               regime_result=None, health_report=None, bias_data=None,
               liquidity_targets=None, session_info=None, psych_data=None,
               direction=None):
    TelegramNotifier().send_alert(
        symbol=symbol, timeframe=timeframe, pattern=pattern,
        ai_score=ai_score, reasoning=reasoning, verdict=verdict,
        risk_calc=risk_calc, buttons=buttons, shadow_insights=shadow_insights,
        security_status=security_status, regime_result=regime_result,
        health_report=health_report, bias_data=bias_data,
        liquidity_targets=liquidity_targets, session_info=session_info,
        psych_data=psych_data, direction=direction,
    )

def send_high_confluence_alert(symbol, direction, entry, stop_loss, target,
                               ai_score, smt_strength, session_name,
                               price_quartile, buttons=None):
    TelegramNotifier().send_high_confluence_alert(
        symbol=symbol, direction=direction, entry=entry,
        stop_loss=stop_loss, target=target, ai_score=ai_score,
        smt_strength=smt_strength, session_name=session_name,
        price_quartile=price_quartile, buttons=buttons
    )

def send_deadzone_alert(symbol, direction, utc_hour):
    TelegramNotifier().send_deadzone_alert(symbol=symbol, direction=direction, utc_hour=utc_hour)

def send_system_error(component, error):
    TelegramNotifier().send_system_error(component, error)

def send_message(text, buttons=None):
    TelegramNotifier()._send_message(text, buttons=buttons)
