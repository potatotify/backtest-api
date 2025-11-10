from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import json
import time
import pandas as pd
from werkzeug.utils import secure_filename
import cloudinary
import cloudinary.uploader
import shutil

app = Flask(__name__)
CORS(app)

# Configure for large uploads (200MB)
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024

# Configure Cloudinary
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
        'endpoints': ['/upload', '/run-backtest']
    })

@app.route('/upload-and-backtest', methods=['POST'])
def upload_and_backtest():
    """Upload CSV, run backtest, return results - all in one endpoint"""
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400
        
        file = request.files['file']
        
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400
        
        if not file.filename.endswith('.csv'):
            return jsonify({'error': 'Only CSV files allowed'}), 400
        
        # Get parameters from form data
        parameters_json = request.form.get('parameters', '{}')
        parameters = json.loads(parameters_json)
        
        # Save CSV temporarily
        filename = secure_filename(file.filename)
        temp_csv = os.path.join(UPLOAD_FOLDER, f"{int(time.time())}_{filename}")
        file.save(temp_csv)
        
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
        result = subprocess.run(
            ['python', 'trail_backtesting.py', config_path],
            capture_output=True,
            text=True,
            timeout=600
        )
        
        if result.returncode != 0:
            return jsonify({'error': f'Backtest failed: {result.stderr}'}), 500
        
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
        
        # Upload only result CSVs to Cloudinary (these are small)
        now_id = f"{int(time.time())}"
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
        
        # Clean up ALL temporary files
        for file in [temp_csv, config_path, 'trades.csv', 'metrics.csv']:
            if os.path.exists(file):
                os.remove(file)
        
        if os.path.exists(plots_folder):
            shutil.rmtree(plots_folder)
        
        return jsonify({
            'success': True,
            'metrics': metrics,
            'trades': trades,
            'chart_data': chart_data,
            'chart_files': chart_files,
            'downloadLinks': download_links
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
