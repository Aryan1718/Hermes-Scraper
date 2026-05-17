# VPS Deployment Checklist

## 1. Provision the VPS

Use Ubuntu 22.04 or 24.04. A small VPS is usually enough for one browser session.

## 2. SSH into the server and update packages

```bash
sudo apt-get update
sudo apt-get upgrade -y
```

## 3. Install system dependencies

```bash
sudo apt-get install -y \
  python3 python3-pip python3-venv \
  xvfb curl unzip wget git \
  libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
  libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
  libgbm1 libasound2 libpango-1.0-0 libcairo2 libx11-xcb1 \
  libxext6 libx11-6 libxcb1 libxrender1 libxtst6 libxi6
```

## 4. Copy the project to the VPS

```bash
git clone <your-repo-url>
cd Hermes
```

Or upload the folder manually.

## 5. Create and activate a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

## 6. Install Python dependencies

```bash
pip install --upgrade pip
pip install "scrapling[fetchers]" python-dotenv
```

## 7. Install Scrapling browser dependencies

```bash
scrapling install
```

## 8. Create the `.env` file

```bash
cat > .env <<'EOF'
JOBRIGHT_EMAIL=your_email_here
JOBRIGHT_PASSWORD=your_password_here
EOF
```

## 9. Verify that `Xvfb` exists

```bash
which Xvfb
```

Expected result:

```bash
/usr/bin/Xvfb
```

## 10. Run a first debug test with virtual display

```bash
python3 Test.py --xvfb --debug --wait 10000
```

## 11. If needed, try `xvfb-run` instead of the built-in flag

```bash
xvfb-run -a python3 Test.py --debug --wait 10000
```

## 12. Confirm success output

Check that:

- login succeeds
- the first job opens
- `jobright_first_job.json` is created

## 13. Inspect the output file

```bash
ls -la
sed -n '1,120p' jobright_first_job.json
```

## 14. If it fails, collect debug artifacts

Look for:

- `debug_after_login.png`
- `debug_recommend_page.html`
- `debug_no_job_links.png`
- `debug_job_detail.html`

## 15. Once stable, run without debug

```bash
python3 Test.py --xvfb
```

## Success Criteria

- No login error
- Final URL contains `/jobs/info/`
- `jobright_first_job.json` exists and contains populated job and company data

## Common Failure Cases

### Browser launch failure

Likely cause:

- missing Linux libraries
- browser dependencies not installed correctly

### Display failure

Likely cause:

- `Xvfb` not installed
- `Xvfb` not starting correctly

### Login works but no jobs open

Try increasing wait time:

```bash
python3 Test.py --xvfb --debug --wait 12000
```

### Job page opens but no JSON is extracted

Check and preserve the debug files, especially:

- `debug_job_detail.html`
- `debug_recommend_page.html`

