import json
import logging
import pandas as pd
from datetime import datetime, timezone
from src.core.database import get_db_connection, execute_db_write_with_retry

logger = logging.getLogger(__name__)

class CounterfactualTracker:
    """
    Counterfactual Shadow Agent Tracking Engine.
    Tracks candidate setups rejected by specific account filter combinations
    and simulates their trade outcome to evaluate filter efficiency.
    """
    def __init__(self):
        pass

    def register_shadow_trade(
        self,
        setup: dict,
        account_key: str,
        strategy_mode: str,
        rejection_reasons: list
    ) -> bool:
        """Logs a rejected setup as a counterfactual shadow trade."""
        if not setup or not account_key or not rejection_reasons:
            return False

        try:
            symbol = setup.get("symbol", "BTC/USD")
            direction = setup.get("direction", setup.get("bias", "BUY")).upper()
            pattern = setup.get("pattern_type", setup.get("pattern", "UNKNOWN"))
            entry_price = float(setup.get("price", setup.get("entry_price", 0.0)))
            stop_loss = float(setup.get("stop_loss", 0.0))
            tp1 = float(setup.get("take_profit", setup.get("take_profit_1", 0.0)))
            tp2 = float(setup.get("take_profit_2", 0.0))

            now_iso = datetime.now(timezone.utc).isoformat()
            reasons_json = json.dumps(rejection_reasons)

            regime_type = setup.get("regime", "UNKNOWN")
            entry_hurst = float(setup.get("hurst", setup.get("hurst_exponent", 0.5)))
            adx_at_entry = float(setup.get("adx", 20.0))
            vol_percentile = float(setup.get("vol_percentile", 50.0))

            query = """
                INSERT INTO counterfactual_trades (
                    timestamp, account_key, symbol, direction, pattern, strategy_mode,
                    entry_price, stop_loss, take_profit_1, take_profit_2,
                    rejection_reasons, status, outcome, simulated_pnl, simulated_r,
                    regime_type, entry_hurst, adx_at_entry, vol_percentile
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', 'PENDING', 0.0, 0.0, ?, ?, ?, ?)
            """
            params = (
                now_iso, account_key, symbol, direction, pattern, strategy_mode,
                entry_price, stop_loss, tp1, tp2, reasons_json,
                regime_type, entry_hurst, adx_at_entry, vol_percentile
            )
            execute_db_write_with_retry(query, params)
            logger.info(f"👻 Counterfactual Shadow Agent registered for {account_key} ({symbol} {direction}): {reasons_json}")
            return True
        except Exception as e:
            logger.error(f"Failed to register counterfactual shadow trade: {e}")
            return False
        finally:
            if 'conn' in locals() and conn:
                try:
                    conn.close()
                except Exception:
                    pass

    def evaluate_open_shadow_trades(self, scanner) -> int:
        """
        Evaluates open counterfactual shadow trades against live price action.
        Returns the number of resolved trades.
        """
        try:
            conn = get_db_connection()
            open_trades = conn.execute(
                "SELECT * FROM counterfactual_trades WHERE status = 'OPEN'"
            ).fetchall()

            if not open_trades:
                conn.close()
                return 0

            resolved_count = 0
            now_utc = datetime.now(timezone.utc)

            for row in open_trades:
                t = dict(row)
                t_id = t["id"]
                symbol = t["symbol"]
                direction = t["direction"]
                entry = float(t.get("entry_price") or 0.0)
                sl = float(t.get("stop_loss") or 0.0)
                tp = float(t.get("take_profit_1") or 0.0)

                created_at = datetime.fromisoformat(t["timestamp"].replace("Z", "+00:00"))
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)

                age_hours = (now_utc - created_at).total_seconds() / 3600.0

                # Fetch recent bars for symbol
                df = None
                try:
                    if scanner and hasattr(scanner, 'fetch_data'):
                        df = scanner.fetch_data(symbol, "5m", limit=288)
                except Exception as _f_err:
                    logger.debug(f"Data fetch error during shadow evaluation for {symbol}: {_f_err}")

                outcome = None
                pnl = 0.0
                r_mult = 0.0

                if df is not None and not df.empty:
                    df_post = df.copy()
                    if "timestamp" in df_post.columns:
                        if pd.api.types.is_datetime64_any_dtype(df_post["timestamp"]):
                            if df_post["timestamp"].dt.tz is not None:
                                df_post = df_post[df_post["timestamp"] >= created_at]
                            else:
                                df_post = df_post[df_post["timestamp"] >= created_at.replace(tzinfo=None)]
                        else:
                            try:
                                dt_series = pd.to_datetime(df_post["timestamp"])
                                if dt_series.dt.tz is not None:
                                    df_post = df_post[dt_series >= created_at]
                                else:
                                    df_post = df_post[dt_series >= created_at.replace(tzinfo=None)]
                            except Exception:
                                pass

                    if not df_post.empty:
                        for _, candle in df_post.iterrows():
                            c_high = float(candle.get("high", 0.0))
                            c_low = float(candle.get("low", 0.0))

                            if direction in ["BUY", "LONG"]:
                                hit_sl = (sl > 0 and c_low <= sl)
                                hit_tp = (tp > 0 and c_high >= tp)
                                if hit_sl and hit_tp:
                                    outcome = "HIT_SL"
                                    r_mult = -1.0
                                    pnl = -100.0
                                    break
                                elif hit_sl:
                                    outcome = "HIT_SL"
                                    r_mult = -1.0
                                    pnl = -100.0
                                    break
                                elif hit_tp:
                                    outcome = "HIT_TP"
                                    r_mult = 2.5
                                    pnl = 250.0
                                    break
                            else:  # SELL / SHORT
                                hit_sl = (sl > 0 and c_high >= sl)
                                hit_tp = (tp > 0 and c_low <= tp)
                                if hit_sl and hit_tp:
                                    outcome = "HIT_SL"
                                    r_mult = -1.0
                                    pnl = -100.0
                                    break
                                elif hit_sl:
                                    outcome = "HIT_SL"
                                    r_mult = -1.0
                                    pnl = -100.0
                                    break
                                elif hit_tp:
                                    outcome = "HIT_TP"
                                    r_mult = 2.5
                                    pnl = 250.0
                                    break

                # Auto-expire stale shadow trades after 48h regardless of data fetch
                if not outcome and age_hours > 48.0:
                    outcome = "EXPIRED"
                    r_mult = 0.0
                    pnl = 0.0

                if outcome:
                    resolved_count += 1
                    closed_iso = now_utc.isoformat()
                    conn.execute(
                        """
                        UPDATE counterfactual_trades
                        SET status = 'CLOSED', outcome = ?, simulated_pnl = ?, simulated_r = ?, closed_at = ?
                        WHERE id = ?
                        """,
                        (outcome, pnl, r_mult, closed_iso, t_id)
                    )
                    logger.info(f"🏁 Counterfactual Trade #{t_id} [{t.get('account_key', 'SHADOW')}] Resolved: {outcome} (${pnl:+.2f}, {r_mult:+.1f}R)")

                    # ── Real-Time Continuous Bayesian Retraining & Tournament Recording ──
                    if outcome in ["HIT_TP", "HIT_SL"]:
                        self._update_bayesian_weight_realtime(t.get("pattern", ""), outcome, r_mult)
                        try:
                            from src.engines.champion_challenger_lab import ChampionChallengerLab
                            var_id = self._map_pattern_to_variant_id(t.get("pattern", ""))
                            if var_id:
                                ChampionChallengerLab().record_tournament_outcome(var_id, is_win=(outcome == "HIT_TP"), r_mult=r_mult)
                        except Exception as _tourn_err:
                            logger.debug(f"Tournament record error: {_tourn_err}")

            conn.commit()
            return resolved_count
        except Exception as e:
            logger.error(f"Error evaluating open shadow trades: {e}")
            return 0
        finally:
            if 'conn' in locals() and conn:
                try:
                    conn.close()
                except Exception:
                    pass

    def _update_bayesian_weight_realtime(self, pattern: str, outcome: str, r_mult: float):
        """Instant per-trade Bayesian posterior weight update."""
        try:
            weights_path = os.path.join(os.getcwd(), "data", "learned_strategy_weights.json")
            if not os.path.exists(weights_path):
                return

            strat_key = self._map_pattern_to_strategy_key(pattern)
            with open(weights_path, "r") as f:
                data = json.load(f)

            if strat_key not in data:
                data[strat_key] = {
                    "samples": 0, "wins": 0, "losses": 0, "win_rate": 50.0,
                    "posterior_win_rate": 0.5, "expectancy_r": 0.0, "bayes_weight": 0.5
                }

            rec = data[strat_key]
            rec["samples"] += 1
            if outcome == "HIT_TP":
                rec["wins"] += 1
            elif outcome == "HIT_SL":
                rec["losses"] += 1

            total = rec["wins"] + rec["losses"]
            if total > 0:
                rec["win_rate"] = round((rec["wins"] / total) * 100.0, 1)
                # Bayesian Beta-Binomial conjugate update with Beta(2, 2) uninformative prior
                alpha_prior = 2.0
                beta_prior = 2.0
                rec["posterior_win_rate"] = round((rec["wins"] + alpha_prior) / (total + alpha_prior + beta_prior), 3)
                rec["bayes_weight"] = round(min(max(rec["posterior_win_rate"], 0.1), 1.5), 3)

            rec["last_calibrated"] = datetime.now(timezone.utc).isoformat()

            with open(weights_path, "w") as f:
                json.dump(data, f, indent=2)

            logger.info(f"📈 [REAL-TIME RETRAINING] {strat_key}: Samples={rec['samples']} | WinRate={rec.get('win_rate', 50.0)}% | BayesWeight={rec['bayes_weight']}")
        except Exception as err:
            logger.debug(f"Real-time weight update error: {err}")

    @staticmethod
    def _map_pattern_to_strategy_key(pattern: str) -> str:
        """Maps free-text pattern string to canonical strategy enum key."""
        p = (pattern or "").upper()
        if "JUDAS" in p or "INDUCEMENT" in p:
            return "STRATEGY_9_JUDAS_INDUCEMENT"
        elif "ASIAN" in p or "FADE" in p:
            return "STRATEGY_1_ASIAN_FADE"
        elif "TREND" in p or "EXPANSION" in p:
            return "STRATEGY_2_TREND_EXPANSION"
        elif "TURTLE" in p or "SOUP" in p or "SWEEP" in p:
            return "STRATEGY_8_TURTLE_SOUP_SWEEP"
        elif "SMT" in p:
            return "STRATEGY_3_SMT_DIVERGENCE"
        elif "BREAKER" in p:
            return "STRATEGY_4_BREAKER_BLOCK"
        elif "50%" in p or "EQUILIBRIUM" in p:
            return "STRATEGY_5_50PCT_CE_MT"
        else:
            return "STRATEGY_2_TREND_EXPANSION"

    @staticmethod
    def _map_pattern_to_variant_id(pattern: str) -> Optional[str]:
        """Maps free-text pattern string to tournament variant ID."""
        p = (pattern or "").upper()
        if "CHALLENGER_LOCAL_MLX" in p or "LOCAL_MLX" in p:
            return "CHALLENGER_LOCAL_MLX"
        elif "CHALLENGER_LOCAL_OLLAMA" in p or "LOCAL_OLLAMA" in p:
            return "CHALLENGER_LOCAL_OLLAMA"
        elif "CHRONOS" in p:
            if "TURTLE" in p or "SOUP" in p:
                return "STRAT_8_CHRONOS_SHADOW_CHALLENGER"
            elif "SMT" in p:
                return "STRAT_3_CHRONOS_SHADOW_CHALLENGER"
            else:
                return "STRAT_9_CHRONOS_SHADOW_CHALLENGER"
        elif "VISUAL" in p:
            if "TURTLE" in p or "SOUP" in p:
                return "STRAT_8_VISUAL_VECTOR_CHALLENGER"
            elif "SMT" in p:
                return "STRAT_3_VISUAL_VECTOR_CHALLENGER"
            else:
                return "STRAT_9_VISUAL_VECTOR_CHALLENGER"
        elif "JUDAS" in p or "INDUCEMENT" in p:
            return "STRAT_9_CHALLENGER"
        elif "ASIAN" in p or "FADE" in p:
            return "STRAT_1_CHALLENGER"
        elif "TURTLE" in p or "SOUP" in p or "SWEEP" in p:
            return "STRAT_8_CHALLENGER"
        elif "SMT" in p:
            return "STRAT_3_SMT_DIVERGENCE_CHALLENGER"
        elif "50%" in p or "CE" in p or "EQUILIBRIUM" in p:
            if "CHAMPION" in p or "GOLD" in p or "XAU" in p or "STRAT_5_XAU" in p:
                return "STRAT_5_XAU_GOLD_50PCT_CE_LONG"
            return "STRAT_5_50PCT_CE_MT_CHALLENGER"
        elif "VALUE_AREA" in p or "AMT" in p:
            return "STRAT_AMT_VALUE_REJECTION"
        elif "AVWAP" in p or "SNAPBACK" in p:
            return "STRAT_AVWAP_SIGMA_SNAPBACK"
        elif "ICEBERG" in p or "DELTA_ABSORPTION" in p:
            return "STRAT_DELTA_ABSORPTION"
        elif "WYCKOFF" in p or "SPRING" in p or "UPTHRUST" in p:
            return "STRAT_WYCKOFF_VSA_SPRING"
        elif "SOL" in p:
            return "SOL_SHADOW_CHALLENGER"
        return None



    @staticmethod
    def get_counterfactual_summary() -> dict:

        """Aggregates counterfactual performance metrics across accounts and filter types."""
        try:
            conn = get_db_connection()
            rows = conn.execute("SELECT * FROM counterfactual_trades").fetchall()
            conn.close()

            summary = {
                "total_shadow_trades": len(rows),
                "by_account": {},
                "by_filter": {}
            }

            for row in rows:
                r = dict(row)
                acc = r["account_key"]
                outcome = r["outcome"]
                pnl = float(r["simulated_pnl"] or 0.0)
                reasons = json.loads(r["rejection_reasons"] or "[]")

                if acc not in summary["by_account"]:
                    summary["by_account"][acc] = {"total": 0, "hits_tp": 0, "hits_sl": 0, "net_pnl": 0.0}

                summary["by_account"][acc]["total"] += 1
                if outcome == "HIT_TP":
                    summary["by_account"][acc]["hits_tp"] += 1
                elif outcome == "HIT_SL":
                    summary["by_account"][acc]["hits_sl"] += 1
                summary["by_account"][acc]["net_pnl"] += pnl

                for reason in reasons:
                    # Clean reason name e.g. HURST_CHAOS_GATE
                    filter_name = reason.split("(")[0].strip()
                    if filter_name not in summary["by_filter"]:
                        summary["by_filter"][filter_name] = {"total": 0, "hits_tp": 0, "hits_sl": 0, "net_pnl": 0.0}
                    summary["by_filter"][filter_name]["total"] += 1
                    if outcome == "HIT_TP":
                        summary["by_filter"][filter_name]["hits_tp"] += 1
                    elif outcome == "HIT_SL":
                        summary["by_filter"][filter_name]["hits_sl"] += 1
                    summary["by_filter"][filter_name]["net_pnl"] += pnl

            return summary
        except Exception as e:
            logger.error(f"Failed to generate counterfactual summary: {e}")
            return {}
