import pandas as pd
import numpy as np
import plotly.graph_objects as go
from scipy.stats import norm
import datetime
import os
from itertools import product
from tqdm import tqdm


CONFIG = {
    'starting_balance': 100000,
    'risk_percentage': 0.01,           # New risk parameter
    'tick_size': 0.25,
    'tick_value': 5,
    'commission_per_trade': 5,
    'slippage_ticks': 1,
    'tp_ticks': 20,
    'sl_ticks': 20,
    'trailing_stop': False,
    'trailing_stop_ticks': 5,
    'contract_margin': 13000           # Updated margin
}


def load_minute_data(filepath):
    data = pd.read_csv(filepath, parse_dates=['date_time'])
    data.rename(columns={'date_time': 'datetime'}, inplace=True)
    data['datetime'] = data['datetime'].dt.tz_localize(None)  # Optional: remove timezone info
    data.sort_values('datetime', inplace=True)
    return data


def calculate_ema(data, span=9):
    data['ema9'] = data['close'].ewm(span=span, adjust=False).mean()
    return data


def detect_signals(data):
    data['signal'] = 0 
    
    for i in range(3, len(data)):
        last3 = data.iloc[i-3:i]
        current = data.iloc[i]
        
        if all(last3['close'] < last3['open']) and all(last3['close'] < last3['ema9']):
            if data.iloc[i]['close'] > data.iloc[i]['ema9']:
                data.at[i, 'signal'] = 1

        if all(last3['close'] > last3['open']) and all(last3['close'] > last3['ema9']):
            if data.iloc[i]['close'] < data.iloc[i]['ema9']:
                data.at[i, 'signal'] = -1

    return data


def simulate_trades(data, config):
    balance = config['starting_balance']
    open_trade = None
    trades = []
    
    for i in tqdm(range(4, len(data)), desc="Simulating Trades", leave=False):
        row = data.iloc[i]
        
        max_contracts_margin = balance // config['contract_margin']
        risk_per_trade = balance * config['risk_percentage']
        max_contracts_risk = risk_per_trade // (config['sl_ticks'] * config['tick_value'])
        qty = 1

        if open_trade:
            if open_trade['type'] == 'long':
                tp_price = open_trade['entry_price'] + config['tp_ticks'] * config['tick_size']
                sl_price = open_trade['entry_price'] - config['sl_ticks'] * config['tick_size']

                if config['trailing_stop']:
                    max_price = max(open_trade['max_price'], row['high'])
                    new_sl = max_price - config['trailing_stop_ticks'] * config['tick_size']
                    sl_price = max(sl_price, new_sl)
                    open_trade['max_price'] = max_price

                if row['high'] >= tp_price:
                    exit_price = tp_price
                    outcome = 'TP'
                elif row['low'] <= sl_price:
                    exit_price = sl_price
                    outcome = 'SL'
                else:
                    continue

            elif open_trade['type'] == 'short':
                tp_price = open_trade['entry_price'] - config['tp_ticks'] * config['tick_size']
                sl_price = open_trade['entry_price'] + config['sl_ticks'] * config['tick_size']

                if config['trailing_stop']:
                    min_price = min(open_trade['min_price'], row['low'])
                    new_sl = min_price + config['trailing_stop_ticks'] * config['tick_size']
                    sl_price = min(sl_price, new_sl)
                    open_trade['min_price'] = min_price

                if row['low'] <= tp_price:
                    exit_price = tp_price
                    outcome = 'TP'
                elif row['high'] >= sl_price:
                    exit_price = sl_price
                    outcome = 'SL'
                else:
                    continue
            
            qty = open_trade['quantity']
            pnl = (exit_price - open_trade['entry_price']) * qty * config['tick_value'] / config['tick_size']
            if open_trade['type'] == 'short':
                pnl = -pnl

            total_cost = config['commission_per_trade'] + config['slippage_ticks'] * config['tick_value'] * 2
            pnl -= total_cost
            balance += pnl

            trades.append({
                'entry_time': open_trade['entry_time'],
                'exit_time': row['datetime'],
                'position': open_trade['type'],
                'entry_price': open_trade['entry_price'],
                'exit_price': exit_price,
                'quantity': qty,
                'pnl': pnl,
                'exit_reason': outcome,
                'balance_after_trade': balance,
                'sl_price': sl_price,
                'tp_price': tp_price
            })
            open_trade = None

        if row['signal'] != 0 and open_trade is None:
            if qty < 1:
                continue
            open_trade = {
                'entry_time': row['datetime'],
                'entry_price': row['close'],
                'quantity': qty,
                'type': 'long' if row['signal'] == 1 else 'short',
                'max_price': row['close'],
                'min_price': row['close']
            }

    trades_df = pd.DataFrame(trades)
    return trades_df


def analyze_performance(trades_df, initial_balance=CONFIG['starting_balance']):
    if trades_df.empty:
        return {}

    win_rate = (trades_df['pnl'] > 0).mean()
    total_trades = len(trades_df)
    avg_win = trades_df[trades_df['pnl'] > 0]['pnl'].mean() if not trades_df[trades_df['pnl'] > 0].empty else 0
    avg_loss = trades_df[trades_df['pnl'] < 0]['pnl'].mean() if not trades_df[trades_df['pnl'] < 0].empty else 0
    total_pnl = trades_df['pnl'].sum()
    profit_percentage = (total_pnl / initial_balance) * 100
    trades_df['cumulative_pnl'] = trades_df['pnl'].cumsum()
    trades_df['cumulative_balance'] = initial_balance + trades_df['cumulative_pnl']
    max_drawdown = (trades_df['cumulative_balance'].cummax() - trades_df['cumulative_balance']).max()
    returns = trades_df['pnl'] / initial_balance
    sharpe_ratio = float(returns.mean() / returns.std() * np.sqrt(252*24*60)) if returns.std() != 0 else None

    metrics = {
        'total_trades': total_trades,
        'win_rate': float(win_rate),
        'avg_win': float(avg_win),
        'avg_loss': float(avg_loss),
        'total_pnl': float(total_pnl),
        'profit_percentage': profit_percentage,
        'max_drawdown': max_drawdown,
        'sharpe_ratio': sharpe_ratio,
        'best_trade': float(trades_df['pnl'].max()) if not trades_df.empty else 0,
        'worst_trade': float(trades_df['pnl'].min()) if not trades_df.empty else 0
    }
    return metrics


def save_trades(trades_df, path='trades.csv'):
    columns = ['entry_time', 'position', 'entry_price', 'sl_price', 'tp_price',
               'exit_time', 'exit_reason', 'pnl', 'cumulative_pnl']
    for col in columns:
        if col not in trades_df.columns:
            trades_df[col] = None
    trades_df.to_csv(path, index=False)


def save_metrics(metrics, path='metrics.csv'):
    df = pd.DataFrame([metrics])
    df.to_csv(path, index=False)


def plot_trades(data, trades_df, output_folder='plots', months_per_plot=3):
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)
    
    start_date = data['datetime'].min()
    end_date = data['datetime'].max()
    
    current_start = start_date
    plot_idx = 1
    
    while current_start < end_date:
        current_end = current_start + pd.DateOffset(months=months_per_plot)
        
        chunk_data = data[(data['datetime'] >= current_start) & (data['datetime'] < current_end)]
        chunk_trades = trades_df[(trades_df['entry_time'] >= current_start) & (trades_df['entry_time'] < current_end)]
        
        fig = go.Figure()

        fig.add_trace(go.Candlestick(
            x=chunk_data['datetime'],
            open=chunk_data['open'],
            high=chunk_data['high'],
            low=chunk_data['low'],
            close=chunk_data['close'],
            name='Candles'
        ))

        fig.add_trace(go.Scatter(
            x=chunk_data['datetime'], 
            y=chunk_data['ema9'], 
            mode='lines',
            line=dict(color='orange', width=1),
            name='EMA9'
        ))

        for _, trade in chunk_trades.iterrows():
            color = 'green' if trade['position'] == 'long' else 'red'
            fig.add_trace(go.Scatter(
                x=[trade['entry_time']], 
                y=[trade['entry_price']],
                mode='markers',
                marker=dict(color=color, size=10, symbol='arrow-up' if trade['position']=='long' else 'arrow-down'),
                name=f"Entry ({trade['position']})"
            ))
            fig.add_trace(go.Scatter(
                x=[trade['exit_time']], 
                y=[trade['exit_price']],
                mode='markers',
                marker=dict(color='blue', size=8, symbol='x'),
                name="Exit"
            ))

        fig.update_layout(
            title=f"Strategy Backtest ({current_start.date()} to {current_end.date()})",
            xaxis_title="Time",
            yaxis_title="Price",
            xaxis_rangeslider_visible=False,
            template="plotly_dark"
        )
        
        filename = f"{output_folder}/strategy_candles_{plot_idx:03d}.html"
        fig.write_html(filename)
        print(f"Saved: {filename}")

        plot_idx += 1
        current_start = current_end

    print("All plots saved.")


# You can still keep optimize_parameters unchanged, or use it as-is.

def run_backtest(filepath, generate_plots=True):
    data = load_minute_data(filepath)
    data = calculate_ema(data)
    data = detect_signals(data)
    trades_df = simulate_trades(data, CONFIG)
    metrics = analyze_performance(trades_df)

    save_trades(trades_df)
    save_metrics(metrics)
    
    if generate_plots:
        plot_trades(data, trades_df)
    
    return trades_df, metrics


if __name__ == "__main__":
    import sys
    import json
    
    if len(sys.argv) > 1:
        config_path = sys.argv[1]
        print(f"Loading config from: {config_path}")
        
        with open(config_path, 'r') as f:
            user_config = json.load(f)
        
        CONFIG.update({
            'starting_balance': user_config.get('starting_balance', 100000),
            'risk_percentage': user_config.get('risk_percentage', 1) / 100,
            'tick_size': user_config.get('tick_size', 0.25),
            'tick_value': user_config.get('tick_value', 5),
            'commission_per_trade': user_config.get('commission_per_trade', 5),
            'slippage_ticks': user_config.get('slippage_ticks', 1),
            'tp_ticks': user_config.get('tp_ticks', 20),
            'sl_ticks': user_config.get('sl_ticks', 20),
            'trailing_stop': user_config.get('trailing_stop', False),
            'trailing_stop_ticks': user_config.get('trailing_stop_ticks', 5),
            'contract_margin': user_config.get('contract_margin', 13000),
        })
        
        input_data = user_config.get('filepath')
        print(f"Running backtest on: {input_data}")
        
    else:
        input_data = "nq_ohlcv_minute_combined_2020_2025.csv"
    
    print("Starting backtest...")
    trades, stats = run_backtest(input_data, generate_plots=True)
    print(json.dumps(stats, default=str))
    print("Backtest completed!")
