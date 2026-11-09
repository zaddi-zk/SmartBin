# SMARTBIN Deployment Guide

## Auto-Deploy Configuration

The `render.yaml` has been updated with auto-deploy settings:

```yaml
autoDeploy: true
branch: main
```

## How Auto-Deploy Works

1. **Push to main branch** → Render automatically detects the change
2. **Build** → Runs `buildCommand` from render.yaml
3. **Deploy** → Runs `startCommand` to launch your bot
4. **Live** → Your bot is running at your Render URL

## Deployment Methods

### Method 1: Automatic (Recommended)

Simply push to the `main` branch:

```bash
git add .
git commit -m "Update bot"
git push origin main
```

Render will automatically:
- Detect the push
- Build your application
- Deploy the new version
- Restart the service

### Method 2: Using Deploy Script

Use the included `deploy.sh` script:

```bash
./deploy.sh
```

This script:
- Checks for uncommitted changes
- Prompts to commit if needed
- Pushes to trigger auto-deploy
- Shows deployment status

### Method 3: Manual Deploy via Render Dashboard

1. Go to https://dashboard.render.com
2. Find your SmartBin service
3. Click "Manual Deploy"
4. Select the branch to deploy

## Render Dashboard Settings

Ensure these settings are configured in your Render dashboard:

### Environment Variables
| Variable | Value | Notes |
|----------|-------|-------|
| `BOT_TOKEN` | Your Telegram bot token | Required |
| `MINIAPP_BASE` | https://your-domain.onrender.com | Update with your actual URL |
| `DATABASE_URL` | (optional) | For Postgres instead of SQLite |

### Auto-Deploy Settings
- **Auto-Deploy**: ✅ Enabled
- **Branch**: main
- **Branch Autodeploy**: ✅ On

## Build & Start Commands

### Build Command
```bash
python -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### Start Command
```bash
cd /opt/render/project/src && . .venv/bin/activate && python bot.py
```

## Monitoring Deployment

### Check Deployment Status
1. Visit: https://dashboard.render.com
2. Click on your SmartBin service
3. View "Recent Deploys" tab

### View Logs
1. In the service dashboard
2. Click "Logs" tab
3. See real-time logs

### Common Log Messages
```
event=boot version=1.0.0 miniapp_port=8080 db=postgres lru=100
event=description_updated
```

## Troubleshooting

### Deployment Stuck on "Building"
- Check build logs for errors
- Verify `requirements.txt` is correct
- Ensure Python version is compatible

### Service Fails to Start
- Check `BOT_TOKEN` is set correctly
- Verify `MINIAPP_BASE` matches your Render URL
- Check logs for startup errors

### Auto-Deploy Not Triggering
- Ensure you're pushing to `main` branch
- Verify auto-deploy is enabled in dashboard
- Check that render.yaml has `autoDeploy: true`

### Database Connection Issues
- If using SQLite: Ensure disk is mounted at `/home/render`
- If using Postgres: Set `DATABASE_URL` environment variable

## Rollback

If a deployment fails:

1. Go to Render dashboard
2. Click on your service
3. Go to "Recent Deploys"
4. Click on a previous successful deploy
5. Click "Rollback"

## Environment Variables Management

### Add/Update Environment Variable
1. Dashboard → Your Service → Environment
2. Click "Add Environment Variable"
3. Enter key and value
4. Click "Save Changes"
5. Service will auto-redeploy

### View Current Variables
```bash
# In Render shell
printenv
```

## Post-Deployment Checklist

After deployment, verify:

- [ ] Bot responds to `/start` command
- [ ] Bot responds to `/check` command
- [ ] Logs show no errors
- [ ] Database is accessible
- [ ] Mini App URL is correct (if using)

## Quick Commands

```bash
# Deploy with auto-commit
./deploy.sh

# Manual git deploy
git add . && git commit -m "Deploy" && git push

# Check deployment status
curl https://your-bot-domain.onrender.com

# View Render logs (requires Render CLI)
render logs SmartBin
```

## Update After Deployment

To update the bot:

1. Make your changes locally
2. Test locally if possible
3. Commit and push:
   ```bash
   git add .
   git commit -m "Description of changes"
   git push
   ```
4. Wait for auto-deploy (usually 1-2 minutes)
5. Verify the bot is working

## Notes

- **Build Time**: ~1-2 minutes
- **Deploy Time**: ~30 seconds
- **Total Time**: ~2-3 minutes from push to live
- **Downtime**: Minimal (Render uses rolling deploys)
