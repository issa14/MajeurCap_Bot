import pandas as pd
import numpy as np

def compute_metrics(trades_df: pd.DataFrame, initial_capital: float = 1000.0) -> dict:
    """
    Calcule les métriques de performance à partir d'un DataFrame de trades.
    Prend en compte le PnL, Winrate, Profit Factor, Drawdown, Sharpe et Calmar.
    """
    if trades_df.empty:
        return {
            "trades": 0, "winrate": 0, "profit_factor": 0, "pnl_total": 0,
            "avg_win": 0, "avg_loss": 0, "max_drawdown": 0, "sharpe": 0, "calmar": 0
        }

    win = trades_df[trades_df["pnl_pct"] > 0]
    loss = trades_df[trades_df["pnl_pct"] <= 0]
    trades = len(trades_df)
    
    winrate = len(win) / trades * 100 if trades else 0
    avg_win = win["pnl_pct"].mean() if not win.empty else 0
    avg_loss = loss["pnl_pct"].mean() if not loss.empty else 0
    pnl_total = trades_df["pnl_pct"].sum()
    
    gross_win = win["pnl_pct"].sum()
    gross_loss = abs(loss["pnl_pct"].sum())
    profit_factor = gross_win / gross_loss if gross_loss > 0 else float('inf')

    # Drawdown sur courbe equity réelle (plus précis que PnL cumulé brut)
    equity = initial_capital * (1 + trades_df["pnl_pct"].cumsum() / 100)
    running_max = equity.cummax()
    # Drawdown relatif : % de perte par rapport au pic d'équité précédent
    max_drawdown = ((running_max - equity) / running_max).max() * 100

    # Sharpe ratio: (mean / std_dev) * sqrt(number of trades)
    std_dev = trades_df["pnl_pct"].std()
    sharpe = (trades_df["pnl_pct"].mean() / std_dev * np.sqrt(trades)) if (std_dev > 0 and trades > 0) else 0

    # Calmar Ratio = PnL total (%) / Max Drawdown (%)
    calmar = (pnl_total / max_drawdown) if max_drawdown > 0 else float('inf')

    return {
        "trades": trades,
        "winrate": winrate,
        "profit_factor": profit_factor,
        "pnl_total": pnl_total,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "max_drawdown": max_drawdown,
        "sharpe": sharpe,
        "calmar": calmar
    }
