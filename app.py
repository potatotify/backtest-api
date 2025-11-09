from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import json
import pandas as pd
from werkzeug.utils import secure_filename
import cloudinary
import cloudinary.uploader
from cloudinary.utils import cloudinary_url

app = Flask(__name__)
CORS(app)

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

@app.route('/upload', methods=['POST'])
def upload_file():
    """Upload CSV file to Cloudinary"""
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400
        
        file = request.files['file']
        
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400
        
        if not file.filename.endswith('.csv'):
            return jsonify({'error': 'Only CSV files allowed'}), 400
        
        # Save temporarily
        filename = secure_filename(file.filename)
        temp_path = os.path.join(UPLOAD_FOLDER, filename)
        file.save(temp_path)
        
        # Upload to Cloudinary
        upload_result = cloudinary.uploader.upload(
            temp_path,
            resource_type='raw',  # For CSV files
            folder='backtest_uploads',
            public_id=f"{os.path.splitext(filename)[0]}_{int(time.time())}"
        )
        
        # Clean up temp file
        os.remove(temp_path)
        
        return jsonify({
            'success': True,
            'file_url': upload_result['secure_url'],
            'public_id': upload_result['public_id'],
            'filename': filename
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/run-backtest', methods=['POST'])
def run_backtest():
    """Run backtest with uploaded file"""
    try:
        data = request.json
        file_url = data.get('file_url')
        parameters = data.get('parameters', {})
        
        if not file_url:
            return jsonify({'error': 'file_url is required'}), 400
        
        # Download CSV from Cloudinary
        import requests
        response = requests.get(file_url)
        
        # Save temporarily
        temp_csv = os.path.join(UPLOAD_FOLDER, 'temp_data.csv')
        with open(temp_csv, 'wb') as f:
            f.write(response.content)
        
        # Create config file
        config_path = os.path.join(UPLOAD_FOLDER, 'config.json')
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
            timeout=600  # 10 minute timeout
        )
        
        if result.returncode != 0:
            return jsonify({'error': f'Backtest failed: {result.stderr}'}), 500
        
        # Read results
        trades_df = pd.read_csv('trades.csv')
        metrics_df = pd.read_csv('metrics.csv')
        
        trades = trades_df.to_dict('records')
        metrics = metrics_df.to_dict('records')[0]
        
        # Clean up
        for file in [temp_csv, config_path, 'trades.csv', 'metrics.csv']:
            if os.path.exists(file):
                os.remove(file)
        
        return jsonify({
            'success': True,
            'trades': trades,
            'metrics': metrics
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
