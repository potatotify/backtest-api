from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import json
import time
import pandas as pd
import cloudinary
import cloudinary.uploader
import shutil
import requests

app = Flask(__name__)

CORS(app, resources={
    r"/*": {
        "origins": "*",
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"],
        "expose_headers": ["Content-Length"],
        "max_age": 3600
    }
})

cloudinary.config(
    cloud_name=os.getenv('CLOUDINARY_CLOUD_NAME'),
    api_key=os.getenv('CLOUDINARY_API_KEY'),
    api_secret=os.getenv('CLOUDINARY_API_SECRET')
)

UPLOAD_FOLDER = '/tmp/uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

@app.route('/', methods=['GET'])
def home():
    return jsonify({
        'message': 'Trading Backtest API',
        'status': 'running',
        'endpoints': ['/run-backtest']
    })

@app.route('/run-backtest', methods=['POST', 'OPTIONS'])
def run_backtest():
    if request.method == 'OPTIONS':
        response = jsonify({'status': 'ok'})
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type')
        response.headers.add('Access-Control-Allow-Methods', 'POST, OPTIONS')
        return response, 200
    
    try:
        data = request.json
        file_url = data.get('fileUrl')
        parameters = data.get('parameters', {})
        
        if not file_url:
            return jsonify({'error': 'fileUrl is required'}), 400
        
        # Download from Cloudinary
        print(f"Downloading file from: {file_url}")
        response = requests.get(file_url, timeout=60)
        
        # Save temporarily
        temp_csv = os.path.join(UPLOAD_FOLDER, f'temp_data_{int(time.time())}.csv')
        with open(temp_csv, 'wb') as f:
            f.write(response.content)
        print(f"File saved to: {temp_csv}")
        
        # Create config file
        config_path = os.path.join(UPLOAD_FOLDER, f'config_{int(time.time())}.json')
        config = {
            'filepath': temp_csv,
            **parameters
        }
        with open(config_path, 'w') as f:
            json.dump(config, f)
        
        # Run backtest
        import subprocess
        print("Running backtest...")
        result = subprocess.run(
            ['python', 'trail_backtesting.py', config_path],
            capture_output=True,
            text=True,
            timeout=600
        )
        
        if result.returncode != 0:
            print(f"Backtest error: {result.stderr}")
            return jsonify({'error': f'Backtest failed: {result.stderr}'}), 500
        
        print("Backtest completed successfully")
        
        # Read results
        trades_df = pd.read_csv('trades.csv')
        metrics_df = pd.read_csv('metrics.csv')
        trades = trades_df.to_dict('records')
        metrics = metrics_df.to_dict('records')[0]
        
        # Generate chart data
        chart_data = {'equity_curve': [], 'monthly_returns': []}
        if not trades_df.empty:
            equity_curve = []
            for idx, row in trades_df.iterrows():
                equity_curve.append({
                    'trade_number': int(idx + 1),
                    'date': str(row['exit_time']),
                    'balance': float(row['balance_after_trade']) if 'balance_after_trade' in row else 0
                })
            
            trades_df['month'] = pd.to_datetime(trades_df['exit_time']).dt.to_period('M').astype(str)
            monthly_pnl = trades_df.groupby('month')['pnl'].sum().reset_index()
            monthly_returns = [
                {'month': str(row['month']), 'pnl': float(row['pnl'])}
                for _, row in monthly_pnl.iterrows()
            ]
            
            chart_data = {
                'equity_curve': equity_curve,
                'monthly_returns': monthly_returns
            }
        
        # Upload result CSVs to Cloudinary
        now_id = f"{int(time.time())}"
        print("Uploading results to Cloudinary...")
        trades_upload = cloudinary.uploader.upload(
            'trades.csv',
            resource_type='raw',
            folder='backtest_reports',
            public_id=f'backtest_{now_id}_trades'
        )
        metrics_upload = cloudinary.uploader.upload(
            'metrics.csv',
            resource_type='raw',
            folder='backtest_reports',
            public_id=f'backtest_{now_id}_metrics'
        )
        
        # Upload HTML charts
        chart_files = []
        plots_folder = 'plots'
        if os.path.exists(plots_folder):
            html_files = sorted([f for f in os.listdir(plots_folder) if f.endswith('.html')])
            for html_file in html_files:
                html_path = os.path.join(plots_folder, html_file)
                try:
                    chart_upload = cloudinary.uploader.upload(
                        html_path,
                        resource_type='raw',
                        folder='backtest_charts',
                        public_id=f'backtest_{now_id}_{os.path.splitext(html_file)[0]}'
                    )
                    chart_files.append(chart_upload['secure_url'])
                except Exception as e:
                    print(f"Failed to upload {html_file}: {e}")
        
        download_links = {
            "trades_csv": trades_upload["secure_url"],
            "metrics_csv": metrics_upload["secure_url"]
        }
        
        # Clean up
        print("Cleaning up temporary files...")
        for file in [temp_csv, config_path, 'trades.csv', 'metrics.csv']:
            if os.path.exists(file):
                os.remove(file)
        
        if os.path.exists(plots_folder):
            shutil.rmtree(plots_folder)
        
        print("Backtest complete!")
        return jsonify({
            'success': True,
            'metrics': metrics,
            'trades': trades,
            'chart_data': chart_data,
            'chart_files': chart_files,
            'downloadLinks': download_links
        })
    
    except Exception as e:
        print(f"Error: {str(e)}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
