# CSM TTS Server Deployment Guide (RunPod)

Complete step-by-step guide to deploy CSM (Sesame's Conversational Speech Model) as a standalone TTS HTTP API on a RunPod GPU instance.

---

## What This Is

CSM is a **text-to-speech only** model. It uses Llama 3.2 1B as a transformer backbone and Mimi as an audio codec. It takes text → produces 24kHz audio. It **cannot** do STT or conversation.

This deployment gives you a simple HTTP API:
- `POST /tts` — Generate speech from text (returns WAV)
- `GET /health` — Health check
- `GET /voices` — List available speaker voices

---

## Prerequisites

- RunPod account with **A40 or A100** GPU pod (A40 recommended — CSM only needs ~4-6GB VRAM)
- HuggingFace account with access to:
  - `meta-llama/Llama-3.2-1B` (accept license at https://huggingface.co/meta-llama/Llama-3.2-1B)
  - `sesame/csm-1b` (accept license at https://huggingface.co/sesame/csm-1b)
- HuggingFace token from https://huggingface.co/settings/tokens
- RunPod Volume Disk enabled (15GB recommended)

---

## Step 1: Install System Packages

```bash
# Update package list
apt-get update

# Install required packages
apt-get install -y \
  python3 \
  python3-pip \
  python3-venv \
  git \
  curl \
  wget \
  nginx \
  ffmpeg

# Verify installations
python3 --version
nginx -v
ffmpeg -version
```

---

## Step 2: Clone CSM Repository

```bash
# Navigate to workspace (persistent volume)
cd /workspace

# Clone repository
git clone https://github.com/ardevpk/quickcalls-tts.git

# Enter directory
cd quickcalls-tts
```

---

## Step 3: Set Up Python Virtual Environment

```bash
# Create virtual environment
python3 -m venv venv

# Activate virtual environment
source venv/bin/activate

# Upgrade pip
pip install --upgrade pip

# Install CSM dependencies
pip install -r requirements.txt

# Install server dependencies
pip install -r requirements_server.txt
```

---

## Step 4: Configure HuggingFace Authentication

```bash
# Set HuggingFace token (replace YOUR_TOKEN_HERE with your actual token)
export HF_TOKEN="YOUR_TOKEN_HERE"

# Persist HF cache to workspace volume (survives pod restarts)
export HF_HOME=/workspace/.cache/huggingface

# Make it persistent across sessions
echo 'export HF_TOKEN="YOUR_TOKEN_HERE"' >> ~/.bashrc
echo 'export HF_HOME=/workspace/.cache/huggingface' >> ~/.bashrc

# Verify
echo $HF_TOKEN
```

---

## Step 5: Copy Server File

Copy `server.py` and `requirements_server.txt` into the CSM directory:

```bash
# If you cloned the quickcalls repo, copy from there:
cp /path/to/quickcalls/apps/models/csm/server.py /workspace/csm/server.py
cp /path/to/quickcalls/apps/models/csm/requirements_server.txt /workspace/csm/requirements_server.txt

# Or create server.py manually by pasting the contents from the repo
```

---

## Step 6: Configure nginx Reverse Proxy

```bash
# Stop nginx if running
nginx -s stop 2>/dev/null || true

# Create nginx configuration (no SSL — RunPod proxy handles HTTPS)
cat > /etc/nginx/sites-available/csm << 'EOF'
server {
    listen 8998;
    server_name _;

    # Increase timeouts for TTS generation (can take 10-30s for long text)
    proxy_connect_timeout 120s;
    proxy_send_timeout 120s;
    proxy_read_timeout 120s;
    send_timeout 120s;

    # Increase max body size for potential context audio
    client_max_body_size 50M;

    location / {
        proxy_pass http://127.0.0.1:8999;
        proxy_http_version 1.1;

        # Headers
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Disable buffering for streaming responses
        proxy_buffering off;
    }
}
EOF

# Enable the site
ln -sf /etc/nginx/sites-available/csm /etc/nginx/sites-enabled/csm

# Remove default site
rm -f /etc/nginx/sites-enabled/default

# Test nginx configuration
nginx -t

# Start nginx
nginx
```

---

## Step 7: Start CSM Server

```bash
# Navigate to CSM directory
cd /workspace/csm

# Activate virtual environment
source venv/bin/activate

# Set environment variables
export HF_TOKEN="YOUR_TOKEN_HERE"
export HF_HOME=/workspace/.cache/huggingface
export NO_TORCH_COMPILE=1

# Start server with nohup (runs on localhost:8999, nginx proxies to 8998)
nohup uvicorn server:app --host 127.0.0.1 --port 8999 > /workspace/csm/server.log 2>&1 &

# Save the process ID
echo $! > /workspace/csm/server.pid

# Verify it's running
ps aux | grep "uvicorn server"
```

**First startup** will take ~5 minutes as it downloads:
- `sesame/csm-1b` model weights
- `meta-llama/Llama-3.2-1B` tokenizer
- Mimi audio codec
- Speaker prompt WAV files

Subsequent startups take ~20-30 seconds.

---

## Step 8: Expose Port via RunPod

1. Go to RunPod dashboard
2. Click on your pod → **"Edit"**
3. Under **"HTTP Ports"**, add port **8998**
4. Save configuration
5. Go to **"Connect"** section
6. Copy the public URL (e.g., `https://xyz-8998.proxy.runpod.net`)

---

## Step 9: Verify Deployment

```bash
# Check server logs (wait for "Application startup complete")
tail -f /workspace/csm/server.log

# Check if ports are listening
ss -tlnp | grep 8999
ss -tlnp | grep 8998

# Health check
curl http://127.0.0.1:8998/health

# List voices
curl http://127.0.0.1:8998/voices

# Generate test audio
curl -X POST http://127.0.0.1:8998/tts \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello, this is a test of the CSM text to speech server.", "speaker": 0}' \
  --output test.wav

# Check the output file
ls -la test.wav

# Monitor GPU usage
watch -n 2 nvidia-smi
```

From your local machine (replace with your RunPod URL):
```bash
curl -X POST https://xyz-8998.proxy.runpod.net/tts \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello from CSM!", "speaker": 0}' \
  --output test.wav
```

---

## Management Commands

### View Logs
```bash
# Real-time logs
tail -f /workspace/csm/server.log

# Last 100 lines
tail -n 100 /workspace/csm/server.log
```

### Stop Server
```bash
# Kill using PID file
kill $(cat /workspace/csm/server.pid)

# Or find and kill manually
ps aux | grep "uvicorn server"
kill <PID>
```

### Restart Server
```bash
# Stop
kill $(cat /workspace/csm/server.pid) 2>/dev/null || true
sleep 2

# Start
cd /workspace/csm
source venv/bin/activate
export HF_TOKEN="YOUR_TOKEN_HERE"
export HF_HOME=/workspace/.cache/huggingface
export NO_TORCH_COMPILE=1

nohup uvicorn server:app --host 127.0.0.1 --port 8999 > server.log 2>&1 &
echo $! > server.pid
```

### Restart nginx
```bash
# Test config
nginx -t

# Reload
nginx -s reload

# Full restart
nginx -s stop && nginx
```

---

## Quick Restart Script

Save this as `/workspace/csm/restart.sh`:

```bash
#!/bin/bash
set -e

echo "Stopping CSM server..."
kill $(cat /workspace/csm/server.pid) 2>/dev/null || true
sleep 2

echo "Starting CSM server..."
cd /workspace/csm
source venv/bin/activate
export HF_TOKEN="YOUR_TOKEN_HERE"
export HF_HOME=/workspace/.cache/huggingface
export NO_TORCH_COMPILE=1

nohup uvicorn server:app --host 127.0.0.1 --port 8999 > server.log 2>&1 &
echo $! > server.pid

echo "CSM started with PID: $(cat server.pid)"
echo "Check logs: tail -f /workspace/csm/server.log"
```

Make it executable:
```bash
chmod +x /workspace/csm/restart.sh
```

---

## Full Startup Script (For Pod Restarts)

Save this as `/workspace/startup-csm.sh`:

```bash
#!/bin/bash
set -e

echo "=== CSM TTS Startup Script ==="

# Install system packages (needed after pod restart)
echo "Installing system packages..."
apt-get update -qq && apt-get install -y -qq nginx ffmpeg > /dev/null 2>&1

# Configure nginx
echo "Configuring nginx..."
cat > /etc/nginx/sites-available/csm << 'NGINX_EOF'
server {
    listen 8998;
    server_name _;
    proxy_connect_timeout 120s;
    proxy_send_timeout 120s;
    proxy_read_timeout 120s;
    send_timeout 120s;
    client_max_body_size 50M;
    location / {
        proxy_pass http://127.0.0.1:8999;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;
    }
}
NGINX_EOF
ln -sf /etc/nginx/sites-available/csm /etc/nginx/sites-enabled/csm
rm -f /etc/nginx/sites-enabled/default
nginx -t && nginx

# Start CSM server
echo "Starting CSM server..."
cd /workspace/csm
source venv/bin/activate
export HF_TOKEN="YOUR_TOKEN_HERE"
export HF_HOME=/workspace/.cache/huggingface
export NO_TORCH_COMPILE=1

nohup uvicorn server:app --host 127.0.0.1 --port 8999 > server.log 2>&1 &
echo $! > server.pid

echo "=== Startup Complete ==="
echo "CSM PID: $(cat server.pid)"
echo "Public URL: Check RunPod dashboard for https://xxx-8998.proxy.runpod.net"
echo "Logs: tail -f /workspace/csm/server.log"
echo "GPU: nvidia-smi"
```

Make it executable:
```bash
chmod +x /workspace/startup-csm.sh
```

Run on every pod start:
```bash
/workspace/startup-csm.sh
```

---

## Troubleshooting

### Server won't start
```bash
# Check logs
tail -n 50 /workspace/csm/server.log

# Check if port is already in use
ss -tlnp | grep 8999

# Verify HF token
echo $HF_TOKEN

# Test Python environment
source /workspace/csm/venv/bin/activate
python -c "import torch; print(f'CUDA: {torch.cuda.is_available()}')"
python -c "from generator import load_csm_1b; print('CSM imports OK')"
```

### HuggingFace authentication errors
```bash
# Make sure you accepted the license for both models:
# - https://huggingface.co/meta-llama/Llama-3.2-1B
# - https://huggingface.co/sesame/csm-1b

# Verify token works
pip install huggingface_hub
huggingface-cli whoami
```

### nginx errors
```bash
# Check nginx logs
tail /var/log/nginx/error.log

# Test configuration
nginx -t

# Check if nginx is running
ps aux | grep nginx
```

### Port not accessible from RunPod URL
```bash
# Verify nginx is listening on 8998
ss -tlnp | grep 8998

# Verify backend is running on 8999
ss -tlnp | grep 8999

# Check RunPod dashboard for port mapping status
```

### GPU not detected
```bash
# Check GPU
nvidia-smi

# Verify CUDA in Python
source /workspace/csm/venv/bin/activate
python -c "import torch; print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0))"
```

### Generation too slow
```bash
# Check GPU utilization during generation
nvidia-smi

# CSM generation speed depends on text length:
# - Short text (~10 words): 2-5 seconds
# - Medium text (~50 words): 5-15 seconds
# - Long text (~200 words): 15-30 seconds
```

---

## Expected Resource Usage

- **VRAM**: ~4-6GB (CSM 1B + Mimi codec + watermarker)
- **RAM**: ~3GB
- **Disk**: ~10GB (models + HF cache)
- **Startup time**:
  - First run: ~5 min (model download)
  - Subsequent: ~20-30 sec
- Much lighter than PersonaPlex (14-18GB VRAM)

---

## API Reference

### POST /tts

Generate speech from text.

**Request:**
```json
{
  "text": "Hello, how are you?",
  "speaker": 0,
  "max_audio_length_ms": 10000,
  "temperature": 0.9,
  "topk": 50
}
```

| Field | Type | Default | Description |
|---|---|---|---|
| `text` | string | required | Text to synthesize (1-2000 chars) |
| `speaker` | int | 0 | Speaker ID: 0 (voice A) or 1 (voice B) |
| `max_audio_length_ms` | float | 10000 | Max audio length in ms (1000-90000) |
| `temperature` | float | 0.9 | Sampling temperature (0.0-2.0) |
| `topk` | int | 50 | Top-k sampling (1-1000) |

**Response:** `audio/wav` binary (24kHz, mono)

**Headers:** `X-Audio-Duration` — duration in seconds

### GET /health

**Response:**
```json
{
  "status": "ok",
  "model_loaded": true,
  "device": "cuda",
  "sample_rate": 24000,
  "gpu_available": true
}
```

### GET /voices

**Response:**
```json
[
  {"id": "conversational_a", "speaker": 0, "description": "Conversational voice A (female)"},
  {"id": "conversational_b", "speaker": 1, "description": "Conversational voice B (male)"}
]
```

---

## Cost Optimization

- **Stop pod** when not in use
- **Volume storage**: ~$1.50/month (15GB)
- **Runtime**: ~$0.40/hr (A40) — CSM is light enough for smaller GPUs too
- **Total**: Storage + usage hours

---

## Future: Full Self-Hosted Voice Pipeline

Once CSM is deployed and working as TTS, the next step is:
1. Add **faster-whisper** (local STT) to the same RunPod instance
2. Add **Ollama + Llama 3.2 3B** (local LLM) to the same instance
3. Create a LiveKit agent adapter in `apps/agent/src/csm/` (like PersonaPlex adapter)
4. Wire it all together: STT → LLM → CSM TTS — fully self-hosted, no API keys

---

## Notes

- All data in `/workspace` persists across pod stops/starts
- nginx and system packages need reinstallation on pod restart (handled by startup script)
- HuggingFace models are cached in `/workspace/.cache/huggingface` (persists)
- CSM applies an imperceptible watermark to all generated audio to identify it as AI-generated
