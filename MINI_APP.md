# 📱 SMARTBIN Mini App - Professional Gold Edition

## Overview
A **professional, gold-themed Telegram Mini App** designed specifically for carders. Built with clean HTML/CSS/JS (no frameworks) for maximum performance and compatibility.

## Features

### 🏠 Home Dashboard
- **Service Cards**: Quick access to all features
- **Kill Card Highlight**: Primary action prominently displayed
- **Quick Stats**: Total checks and success rate
- **Credit Display**: Real-time credit balance

### 🃏 BIN Lookup
- Enter 6-8 digit BIN
- Full metadata display:
  - Brand, Bank, Country, ISO2
  - Currency, Phone Format
  - Level, Prepaid Status
  - 2FA Availability

### ⚡ Check Card
- Single card verification
- Custom amount support
- Real-time results with:
  - Live/Declined status
  - Verdict display
  - Response time (latency)
  - Full message

### 💀 Kill Card (Star Feature)
- **5 auth attempts** per operation
- **Costs 5 credits**
- Detailed breakdown:
  - Each attempt result
  - Individual latency
  - Approved/Declined count
  - Average speed
  - Final verdict (LIVE/DECLINED)

### 📦 Bulk Check
- Paste multiple cards (one per line)
- Format: `number|MM|YY|CVV`
- Processes up to 20 cards
- Live/Dead indicators
- Summary statistics

### 🔧 BIN Doctor
- Submit BIN corrections
- Fields available:
  - Bank, Brand, Country
  - Currency, Level, Prepaid
- Earn credits on approval

### 💎 Shop
- **Credit Packs**:
  - 100 Credits - 25 XTR
  - 500 Credits - 120 XTR
  - 1000 Credits - 230 XTR
- **Premium Plans**:
  - 1 Day - 30 XTR
  - 30 Days - 250 XTR

### 📊 Status
- Real-time system health
- API status
- Average latency
- Canary success rate (24h)

## Design System

### Colors
```css
--bg-primary: #0a0a0f        /* Deep black-blue */
--bg-secondary: #12121a      /* Card backgrounds */
--bg-tertiary: #1a1a25       /* Elevated surfaces */
--gold-primary: #ffd700      /* Pure gold */
--gold-secondary: #ffb700    /* Darker gold */
--gold-dark: #b8860b         /* Accent gold */
--gold-gradient: linear-gradient(135deg, #ffd700, #ffb700, #ffd700)
```

### Status Colors
- **Success**: #00ff88 (Live/Approved)
- **Danger**: #ff4757 (Declined/Dead)
- **Warning**: #ffa500 (Mixed/Partial)

### Typography
- **Font**: Inter (Google Fonts)
- **Weights**: 400, 500, 600, 700, 800
- **Sizes**: 11px (desc) to 24px (headers)

## Support Button

### Floating Action Button (FAB)
- **Position**: Fixed bottom-right
- **Icon**: 💬 (speech bubble)
- **Action**: Opens Telegram to @SmartBin_info
- **Design**: Gold gradient with shadow

## API Integration

### Endpoints Used
```javascript
GET  /api/bin?bin=45717360     // BIN lookup
POST /api/check                // Single check
GET  /api/status               // User status & credits
GET  /api/health               // System health
```

### Request/Response Format
```javascript
// Check Request
{
  "card": "4242424242424242|12|2030|123",
  "amount": 1.00
}

// Check Response
{
  "live": true,
  "verdict": "APPROVED",
  "latency_ms": 245,
  "message": "Approved",
  "credits": 45
}
```

## User Experience

### Toast Notifications
- Success confirmations
- Error messages
- Status updates
- Auto-dismiss after 3 seconds

### Loading States
- Spinner animation
- Descriptive text
- Covers entire section

### Card Masking
- Format: `4242••••••4242`
- Shows first 4 + last 4 digits
- Protects card privacy

## Technical Details

### File Structure
```
smartbin/miniapp/
├── server.py          # Flask server & API
└── static/
    └── index.html     # Complete Mini App (874 lines)
```

### Technologies
- **HTML5**: Semantic markup
- **CSS3**: Custom properties, flexbox, grid
- **JavaScript**: Vanilla ES6+
- **Telegram WebApp SDK**: Native integration

### Performance
- **No frameworks**: ~50KB total (uncompressed)
- **Single file**: HTML + CSS + JS
- **Lazy loading**: Sections load on demand
- **Optimized animations**: CSS transitions

### Responsive Design
- **Mobile-first**: Optimized for phones
- **Max width**: 480px (tablet friendly)
- **Sticky header**: Always accessible
- **Scrollable tabs**: Horizontal navigation

## Telegram Integration

### WebApp SDK Features
```javascript
const tg = window.Telegram.WebApp;

tg.expand();                          // Full screen
tg.backgroundColor                    // Theme colors
tg.initDataUnsafe.user                // User data
tg.openTelegramLink(url)              // Open Telegram
```

### User Data
- User ID
- Username
- First Name
- Real-time credits
- Premium status

## Navigation

### Tab System
```
🏠 Home | 🃏 BIN | ⚡ Check | 💀 Kill | 📦 Bulk | 🔧 Doctor | 💎 Shop | 📊 Status
```

### Active State
- Gold gradient background
- Black text
- Shadow effect
- Smooth transitions

## Deployment

### Access URL
```
https://smartbin-eurr.onrender.com/miniapp
```

### Bot Integration
- Mini App button in menu
- Opens in Telegram WebApp view
- Seamless user experience

## Pro Features for Carders

### 1. Kill Card Workflow
```
1. Enter card: 4737034075902651|12|28|263
2. Tap "💀 Execute Kill (5 credits)"
3. Watch 5 attempts in real-time
4. See detailed breakdown
5. Get final verdict (LIVE/DECLINED)
```

### 2. Bulk Processing
```
Paste cards:
4242424242424242|12|2030|123
5178008141113983|08|2027|456

Get instant results with ✅/❌ indicators
```

### 3. Credit Management
- Real-time balance display
- Automatic deduction
- Premium user highlighting
- Cost warnings before operations

### 4. Speed Metrics
- Per-attempt latency
- Average response time
- System health monitoring
- Canary success rates

## Future Enhancements

### Planned Features
- [ ] Crypto payment integration
- [ ] Export results to CSV
- [ ] Saved BIN history
- [ ] Favorite cards
- [ ] Dark/Light theme toggle
- [ ] Sound effects
- [ ] Haptic feedback
- [ ] Offline mode

### Optimization Targets
- Sub-100ms UI response
- <50KB bundle size
- 60fps animations
- PWA support

## Screenshots

### Home Screen
```
┌─────────────────────────────────┐
│ 💎 SMARTBIN          💰 50     │
│   Card Intelligence             │
├─────────────────────────────────┤
│ 🏠 🃏 ⚡ 💀 📦 🔧 💎 📊        │
├─────────────────────────────────┤
│      💀 KILL CARD              │
│   Multi-attempt drain (5x)     │
│                                 │
│  🃏 BIN    ⚡ Check             │
│  📦 Bulk   🔧 Doctor           │
└─────────────────────────────────┘
```

### Kill Result
```
┌─────────────────────────────────┐
│ 💀 Kill Operation   🟢 LIVE    │
├─────────────────────────────────┤
│ 🎴 Card: 473703••••••2651      │
│ 🔄 Attempts: 5                  │
│ ✅ Approved: 2                  │
│ ❌ Declined: 3                  │
│ ⚡ Avg Speed: 220ms             │
├─────────────────────────────────┤
│ 1. DO-NOT-HONOR    ❌ 215ms    │
│ 2. APPROVED        ✅ 198ms    │
│ 3. DO-NOT-HONOR    ❌ 245ms    │
│ ...                             │
└─────────────────────────────────┘
```

## Support

### Contact
- **Telegram**: @SmartBin_info
- **Bot**: @SmartBlN_Bot
- **Mini App**: Built-in support button

### Documentation
- This file (MINI_APP.md)
- KILL_CARD_FEATURE.md
- UPGRADE_SUMMARY.md

---

## Version Info
- **Version**: 1.0.0
- **Build**: Professional Gold Edition
- **Lines of Code**: 874
- **Bundle Size**: ~50KB
- **Last Updated**: 2026-11-10

**Made with 💎 for carders, by carders.**
