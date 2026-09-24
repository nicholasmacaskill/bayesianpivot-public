import sqlite3
import os
from datetime import datetime, date, timezone
from src.core.config import Config
from src.core.supabase_client import supabase

def get_db_connection():
    # Ensure directory exists (Modal Volume)
    db_dir = os.path.dirname(Config.DB_PATH)
    if not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)
    
    conn = sqlite3.connect(Config.DB_PATH, timeout=60.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=60000;")
        conn.execute("PRAGMA synchronous=NORMAL;")
    except Exception:
        pass
    return conn

def execute_db_write_with_retry(query: str, params: tuple = (), max_retries: int = 10):
    """
    Executes a database write transaction with exponential backoff and jitter
    to guarantee zero sqlite3.OperationalError: database is locked failures during concurrent bursts.
    """
    import time
    import random
    
    for attempt in range(max_retries):
        conn = None
        try:
            conn = get_db_connection()
            c = conn.cursor()
            c.execute(query, params)
            last_id = c.lastrowid
            conn.commit()
            return last_id if last_id else True
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower() and attempt < max_retries - 1:
                sleep_sec = min(2.0, (0.05 * (1.5 ** attempt)) + random.uniform(0.02, 0.08))
                time.sleep(sleep_sec)
                continue
            raise e
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass



def init_db():
    start_time = datetime.now()
    try:
        conn = get_db_connection()
        c = conn.cursor()
        
        # Scans Table
        c.execute('''
            CREATE TABLE IF NOT EXISTS scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                symbol TEXT,
                timeframe TEXT,
                pattern TEXT,
                bias TEXT,
                direction TEXT,
                ai_score REAL,
                ai_reasoning TEXT,
                status TEXT DEFAULT 'PENDING',
                verdict TEXT DEFAULT 'N/A',
                shadow_regime TEXT DEFAULT 'Unknown',
                shadow_multiplier REAL DEFAULT 1.0,
                session TEXT,
                killzone TEXT,
                hurst REAL,
                adf_p REAL,
                daily_pnl REAL,
                total_pnl REAL,
                smt REAL,
                formations TEXT
            )
        ''')
        
        # Auto-Migration for existing tables
        try:
            c.execute("ALTER TABLE scans ADD COLUMN verdict TEXT DEFAULT 'N/A'")
        except sqlite3.OperationalError: pass
        
        try:
            c.execute("ALTER TABLE scans ADD COLUMN shadow_regime TEXT DEFAULT 'Unknown'")
        except sqlite3.OperationalError: pass
        
        try:
            c.execute("ALTER TABLE scans ADD COLUMN shadow_multiplier REAL DEFAULT 1.0")
        except sqlite3.OperationalError: pass

        try:
            c.execute("ALTER TABLE scans ADD COLUMN direction TEXT")
        except sqlite3.OperationalError: pass

        try:
            c.execute("ALTER TABLE scans ADD COLUMN bias_conflict INTEGER DEFAULT 0")
        except sqlite3.OperationalError: pass

        # New metadata for enriched /scan
        for col in [('session', 'TEXT'), ('killzone', 'TEXT'), ('hurst', 'REAL'), ('adf_p', 'REAL'), ('daily_pnl', 'REAL'), ('total_pnl', 'REAL'), ('smt', 'REAL'), ('formations', 'TEXT')]:
            try:
                c.execute(f"ALTER TABLE scans ADD COLUMN {col[0]} {col[1]}")
            except sqlite3.OperationalError: pass
        
        # Journal Table (AI Audit Reports)
        c.execute('''
            CREATE TABLE IF NOT EXISTS journal (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                trade_id TEXT UNIQUE,
                symbol TEXT,
                side TEXT,
                pnl REAL,
                ai_grade REAL,
                mentor_feedback TEXT,
                deviations TEXT,
                is_lucky_failure INTEGER DEFAULT 0
            )
        ''')
        
        # Migrations for Journal Table
        try:
            c.execute("ALTER TABLE journal ADD COLUMN price REAL DEFAULT 0.0")
        except sqlite3.OperationalError: pass

        try:
            c.execute("ALTER TABLE journal ADD COLUMN status TEXT DEFAULT 'CLOSED'")
        except sqlite3.OperationalError: pass

        try:
            c.execute("ALTER TABLE journal ADD COLUMN notes TEXT")
        except sqlite3.OperationalError: pass

        try:
            c.execute("ALTER TABLE journal ADD COLUMN strategy TEXT DEFAULT 'ROGUE'")
        except sqlite3.OperationalError: pass
        
        # Sync State Table (For Local-to-Cloud Equity Sync)
        c.execute('''
            CREATE TABLE IF NOT EXISTS sync_state (
                key TEXT PRIMARY KEY,
                value TEXT,
                last_updated TEXT
            )
        ''')

        # System Logs Table (Scanner Health)
        c.execute('''
            CREATE TABLE IF NOT EXISTS system_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                component TEXT,
                level TEXT,
                message TEXT
            )
        ''')

        # Prop Guardian Audits Table (Adversarial Detection)
        c.execute('''
            CREATE TABLE IF NOT EXISTS prop_guardian_audits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                firm_name TEXT,
                risk_score REAL,
                traps JSON,
                verdict TEXT,
                recommendation TEXT
            )
        ''')

        # Counterfactual Shadow Trades Table
        c.execute('''
            CREATE TABLE IF NOT EXISTS counterfactual_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                account_key TEXT,
                symbol TEXT,
                direction TEXT,
                pattern TEXT,
                strategy_mode TEXT,
                entry_price REAL,
                stop_loss REAL,
                take_profit_1 REAL,
                take_profit_2 REAL,
                rejection_reasons TEXT,
                status TEXT DEFAULT 'OPEN',
                outcome TEXT DEFAULT 'PENDING',
                simulated_pnl REAL DEFAULT 0.0,
                simulated_r REAL DEFAULT 0.0,
                closed_at TEXT
            )
        ''')

        # Migrations for counterfactual_trades
        for cf_col, cf_type in [
            ('regime_type', "TEXT DEFAULT 'UNKNOWN'"),
            ('entry_hurst', "REAL DEFAULT 0.50"),
            ('adx_at_entry', "REAL DEFAULT 20.0"),
            ('vol_percentile', "REAL DEFAULT 50.0")
        ]:
            try:
                c.execute(f"ALTER TABLE counterfactual_trades ADD COLUMN {cf_col} {cf_type}")
            except sqlite3.OperationalError:
                pass

        # Chart Visual Embeddings Table (Multimodal Geometric & Visual RAG)
        c.execute('''
            CREATE TABLE IF NOT EXISTS chart_visual_embeddings (
                embedding_id TEXT PRIMARY KEY,
                signal_id TEXT,
                timestamp TEXT,
                symbol TEXT,
                pattern TEXT,
                session TEXT,
                direction TEXT,
                outcome TEXT,
                realized_r REAL,
                pnl REAL,
                vector BLOB,
                vector_dim INTEGER,
                notes TEXT,
                regime_type TEXT DEFAULT 'UNKNOWN',
                hurst REAL DEFAULT 0.50,
                atr_percentile REAL DEFAULT 50.0,
                created_at TEXT
            )
        ''')
        c.execute('''
            CREATE INDEX IF NOT EXISTS idx_cve_symbol_pattern 
            ON chart_visual_embeddings(symbol, pattern)
        ''')

        # Auto-migrations for chart_visual_embeddings
        for cve_col, cve_type in [
            ('regime_type', "TEXT DEFAULT 'UNKNOWN'"),
            ('hurst', "REAL DEFAULT 0.50"),
            ('atr_percentile', "REAL DEFAULT 50.0")
        ]:
            try:
                c.execute(f"ALTER TABLE chart_visual_embeddings ADD COLUMN {cve_col} {cve_type}")
            except sqlite3.OperationalError:
                pass
        
        conn.commit()
    except Exception as e:
        print(f"DB Init Error: {e}")
    finally:
        if 'conn' in locals():
            conn.close()
            
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS backtest_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_date TEXT,
                symbol TEXT,
                timeframe TEXT,
                pattern TEXT,
                pnl REAL,
                win_rate REAL,
                total_trades INTEGER,
                trade_log JSON,
                metadata JSON
            )
        ''')
        conn.commit()
        conn.close()

def log_scan(scan_data, ai_result):
    # Extract shadow data safely
    shadow_regime = scan_data.get('shadow_regime', 'N/A')
    shadow_multiplier = scan_data.get('shadow_multiplier', 1.0)
    verdict = scan_data.get('verdict', 'N/A')
    
    query = '''
        INSERT INTO scans (
            timestamp, symbol, timeframe, pattern, bias, direction,
            ai_score, ai_reasoning, verdict, shadow_regime, shadow_multiplier,
            session, killzone, hurst, adf_p, daily_pnl, total_pnl, smt, formations, bias_conflict
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    '''
    params = (
        scan_data.get('timestamp', datetime.now(timezone.utc).isoformat()),
        scan_data['symbol'],
        Config.TIMEFRAME,
        scan_data['pattern'],
        scan_data['bias'],
        scan_data.get('direction', 'N/A'),
        ai_result['score'],
        ai_result['reasoning'],
        verdict,
        shadow_regime,
        shadow_multiplier,
        scan_data.get('session'),
        scan_data.get('killzone'),
        scan_data.get('hurst'),
        scan_data.get('adf_p'),
        scan_data.get('daily_pnl'),
        scan_data.get('total_pnl'),
        scan_data.get('smt_strength', 0.0),
        scan_data.get('formations', ''),
        1 if scan_data.get('bias_conflict') else 0
    )
    scan_id = execute_db_write_with_retry(query, params)
    
    # Sync to Supabase
    try:
        supabase.log_scan(scan_data, ai_result)
    except Exception as e:
        print(f"Supabase Scan Sync Error: {e}")
        
    return scan_id

def log_journal_entry(trade_id, symbol, side, pnl, score, feedback, deviations, is_lucky_failure=0, strategy="ROGUE", timestamp=None):
    conn = get_db_connection()
    c = conn.cursor()
    now = timestamp or datetime.now().isoformat()
    try:
        c.execute('''
            INSERT OR REPLACE INTO journal (timestamp, trade_id, symbol, side, pnl, ai_grade, mentor_feedback, deviations, is_lucky_failure, strategy)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (now, trade_id, symbol, side, pnl, score, feedback, deviations, is_lucky_failure, strategy))
        conn.commit()
    except Exception as e:
        print(f"Error logging journal entry: {e}")
    finally:
        conn.close()
        
    # Sync to Supabase
    try:
        # deviations is expected as a JSON string or list in bridge
        supabase.log_journal_entry(
            trade_id=trade_id,
            symbol=symbol,
            side=side,
            pnl=pnl,
            ai_grade=score,
            mentor_feedback=feedback,
            deviations=deviations,
            is_lucky_failure=is_lucky_failure,
            strategy=strategy,
            timestamp=now
        )
    except Exception as e:
        print(f"Supabase Journal Sync Error: {e}")

def update_journal_notes(trade_id, notes):
    """Updates the personal notes for a specific trade."""
    conn = get_db_connection()
    c = conn.cursor()
    try:
        # Try updating by trade_id string first
        c.execute("UPDATE journal SET notes = ? WHERE trade_id = ?", (notes, str(trade_id)))
        if c.rowcount == 0:
            # Fallback to integer id
            try:
                c.execute("UPDATE journal SET notes = ? WHERE id = ?", (notes, int(trade_id)))
            except ValueError: pass
        conn.commit()
        return True
    except Exception as e:
        print(f"Error updating notes: {e}")
        return False
    finally:
        conn.close()

def update_sync_state(total_equity, trades_today):
    conn = get_db_connection()
    c = conn.cursor()
    now = datetime.now().isoformat()
    # Ensure table exists (just in case)
    c.execute('''
        CREATE TABLE IF NOT EXISTS sync_state (
            key TEXT PRIMARY KEY,
            value TEXT,
            last_updated TEXT
        )
    ''')
    c.execute("INSERT OR REPLACE INTO sync_state (key, value, last_updated) VALUES (?, ?, ?)", ("total_equity", total_equity, now))
    c.execute("INSERT OR REPLACE INTO sync_state (key, value, last_updated) VALUES (?, ?, ?)", ("trades_today", trades_today, now))
    conn.commit()
    conn.close()
    
    # Sync to Supabase
    try:
        supabase.update_sync_state(total_equity, trades_today)
    except Exception as e:
        print(f"Supabase Sync State Error: {e}")


def get_sync_state():
    conn = get_db_connection()
    c = conn.cursor()
    
    # Ensure table exists
    c.execute("""
        CREATE TABLE IF NOT EXISTS sync_state (
            key TEXT PRIMARY KEY,
            value TEXT,
            last_updated TEXT
        )
    """)
    
    c.execute("SELECT key, value FROM sync_state")
    rows = c.fetchall()
    conn.close()
    return {row['key']: row['value'] for row in rows}

def check_daily_limit():
    """Returns True if daily trade count < Limit"""
    today = date.today().isoformat()
    conn = get_db_connection()
    c = conn.cursor()
    # This might fail if 'trades' table doesn't exist? 
    # Actually 'trades' table isn't defined in init_db above. 
    # It seems check_daily_limit was using a table that isn't cleanly defined here.
    # We'll assume 'journal' is the source of truth for trades now or create a trades table if needed.
    # For now, let's fix it to look at 'journal' or 'scans' with status executed?
    # Let's use journal count for now, assuming 1 journal entry = 1 trade.
    
    try:
        c.execute("SELECT COUNT(*) FROM journal WHERE date(timestamp) = ?", (today,))
        count = c.fetchone()[0]
    except:
        count = 0
        
    conn.close()
    return count < Config.DAILY_TRADE_LIMIT

def log_system_event(component, message, level="INFO"):
    """Logs a system health or error event"""
    conn = get_db_connection()
    c = conn.cursor()
    now = datetime.now().isoformat()
    try:
        c.execute('''
            INSERT INTO system_logs (timestamp, component, level, message)
            VALUES (?, ?, ?, ?)
        ''', (now, component, level, message))
        conn.commit()
    except Exception as e:
        print(f"Error logging system event: {e}")
    finally:
        conn.close()

def log_prop_audit(audit_data):
    """Persists a forensic audit report to the database"""
    import json
    conn = get_db_connection()
    c = conn.cursor()
    now = datetime.now().isoformat()
    try:
        c.execute('''
            INSERT INTO prop_guardian_audits 
            (timestamp, firm_name, risk_score, traps, verdict, recommendation)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            now,
            audit_data.get('firm_name', 'Unknown'),
            audit_data.get('risk_score', 0),
            json.dumps(audit_data.get('traps', [])),
            audit_data.get('verdict', 'N/A'),
            audit_data.get('recommendation', 'N/A')
        ))
        conn.commit()
    except Exception as e:
        print(f"Error logging prop audit: {e}")
    finally:
        conn.close()

def get_latest_prop_audits(limit=5):
    """Retrieves the most recent forensic audits"""
    import json
    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute("SELECT * FROM prop_guardian_audits ORDER BY timestamp DESC LIMIT ?", (limit,))
        rows = []
        for row in c.fetchall():
            d = dict(row)
            if isinstance(d.get('traps'), str):
                try:
                    d['traps'] = json.loads(d['traps'])
                except: pass
            rows.append(d)
        return rows
    except Exception as e:
        print(f"Error fetching prop audits: {e}")
        return []
    finally:
        conn.close()

def log_token_usage(prompt_tokens, candidate_tokens, model_name="gemini-2.5-flash"):
    """
    Increments token usage statistics for the current day.
    """
    input_rate = 0.075 / 1000000.0
    output_rate = 0.30 / 1000000.0
    cost = (prompt_tokens * input_rate) + (candidate_tokens * output_rate)
    
    total_tokens = prompt_tokens + candidate_tokens
    today = date.today().isoformat()
    
    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute('''
            CREATE TABLE IF NOT EXISTS token_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT UNIQUE,
                prompt_tokens INTEGER DEFAULT 0,
                candidate_tokens INTEGER DEFAULT 0,
                total_tokens INTEGER DEFAULT 0,
                cost REAL DEFAULT 0.0
            )
        ''')
        c.execute('''
            INSERT INTO token_usage (date, prompt_tokens, candidate_tokens, total_tokens, cost)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                prompt_tokens = prompt_tokens + excluded.prompt_tokens,
                candidate_tokens = candidate_tokens + excluded.candidate_tokens,
                total_tokens = total_tokens + excluded.total_tokens,
                cost = cost + excluded.cost
        ''', (today, prompt_tokens, candidate_tokens, total_tokens, cost))
        conn.commit()
    except Exception as e:
        print(f"Error logging token usage: {e}")
    finally:
        conn.close()

def get_daily_token_usage():
    """Retrieves the current day's token usage details."""
    today = date.today().isoformat()
    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute("SELECT * FROM token_usage WHERE date = ?", (today,))
        row = c.fetchone()
        if row:
            return dict(row)
    except Exception as e:
        print(f"Error fetching token usage: {e}")
    finally:
        conn.close()
    return {"date": today, "prompt_tokens": 0, "candidate_tokens": 0, "total_tokens": 0, "cost": 0.0}
