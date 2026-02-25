# 🎯 OB1 RADAR™ - Football Intelligence System

**Copyright © 2024-2025 Mirko Tornani. All Rights Reserved.**

---

## ⚖️ INTELLECTUAL PROPERTY NOTICE

**OB1 Radar™** is proprietary software protected by:
- **Trademark** (pending registration with USBM San Marino)
- **Copyright** on all source code and algorithms
- **Trade secrets** on query patterns and scoring methodologies

**See**: `LICENSE.md` for full legal terms
**Contact**: mirko@matchanalysispro.online

🚨 **CONFIDENTIAL**: Do not share, replicate, or reverse engineer without written permission.

---

## 📋 System Overview

**Version:** 0.8.2 OPTIMIZED
**Status:** Production Ready ✅
**License:** Proprietary - Commercial Use Only

### What is OB1 Radar™?

A proprietary football intelligence system providing daily anomaly detection reports for youth talent (U19/U20) through:

- Multi-source intelligence gathering from global football sources
- Proprietary scoring algorithms for early talent detection
- Regional breakdown and performance insights
- Predictive analytics with 11-20 days advance notice

---

## 🚀 Quick Start

### Prerequisites

```bash
# Python 3.8+ required
python3 --version

# Install dependencies
pip install -r requirements.txt
```

### Basic Usage

```bash
# Run daily scan
python3 run_v0.8.2_optimized.py

# Check output
cat output/daily.json | jq '.items[0]'

# View logs
tail -f logs/ob1_radar.log
```

---

## 📊 Key Features

### 1. Intelligent Query System (PROPRIETARY)
- 17+ optimized query patterns developed over 18 months
- Multi-language coverage (English, Portuguese, Spanish, Japanese, Korean, Arabic, Thai)
- Regional tournament-specific targeting

### 2. Advanced Scoring Algorithm (TRADE SECRET)
- Proprietary anomaly detection weights
- Performance pattern recognition
- News velocity tracking
- Statistical significance scoring

### 3. Multi-Region Coverage
- Africa: CAF, COSAFA tournaments
- Asia: AFC, J-League, K-League focus
- South America: CONMEBOL, Brasileiro, Libertadores
- Europe: Top 5 leagues + academies

### 4. Robust Architecture
- Serper.dev API integration
- Intelligent caching system (7-day window)
- Concurrent processing (10 workers)
- Automatic retry with exponential backoff

---

## 📁 Project Structure

```
ob1-scout/
├── run_v0.8.2_optimized.py      ← Main system (PROPRIETARY)
├── ob1_config.py                ← Configuration
├── generate_report.py           ← HTML report generator
├── LICENSE.md                   ← Legal terms (READ THIS)
├── NDA_TEMPLATE_LEGAL.md        ← For client demos
├── TERMS_OF_SERVICE.md          ← Client terms
├── TRADEMARK_FILING_GUIDE.md    ← Marchio registration guide
├── data/
│   └── cache/                   ← URL cache
├── output/
│   └── daily.json               ← Anomaly reports
├── logs/
│   └── ob1_radar.log            ← System logs
└── reports/                     ← HTML reports
```

---

## 🔑 API Configuration

Edit `.env` or `ob1_config.py`:

```python
SERPER_API_KEY = 'your-key-here'  # Get from https://serper.dev
```

**Cost**: ~2500 free searches/month on Serper.dev free tier

---

## 📈 Performance Metrics

### Typical Run Statistics

```
⏱️  Time: 8-12 minutes
🔍 Queries: 17 (100% success expected)
📄 Candidates: 250-350 per run
🌐 Scraping: 85-95% success rate
✨ Anomalies: 10-15 high-quality signals
🌍 Regions: 4-6 regions covered
```

### Quality Thresholds

- **Min Anomaly Score**: 25 (high-quality only)
- **Min Text Length**: 400 characters
- **Cache Duration**: 7 days
- **Max Results per Query**: 20

---

## 🎯 Use Cases

### For Football Clubs
- Early talent detection (11-20 days before mainstream media)
- Multi-region scouting coverage
- Transfer target identification
- Competitive intelligence

### For Agents
- Client opportunity discovery
- Market timing optimization
- Performance tracking
- Valuation analysis

### For Federations
- Eligible player discovery (e.g., FSGC San Marino project)
- Youth system monitoring
- International talent pool expansion

---

## 🔒 Security & Confidentiality

### Before Sharing/Demoing

1. **Always use NDA**: See `NDA_TEMPLATE_LEGAL.md`
2. **Never share source code**: Demo via reports only
3. **Protect methodologies**: Don't explain scoring algorithm details
4. **Log access**: Track who sees what data

### For Client Presentations

```bash
# Generate clean report without revealing code
python3 generate_report.py

# Share only: reports/*.html
# Never share: run_*.py, ob1_*.py, or data/
```

---

## 📚 Documentation

- **`LICENSE.md`** - Legal terms and restrictions
- **`NDA_TEMPLATE_LEGAL.md`** - NDA for prospects/clients
- **`TERMS_OF_SERVICE.md`** - Client service terms
- **`TRADEMARK_FILING_GUIDE.md`** - How to register OB1 Radar™ trademark

---

## 🐛 Troubleshooting

### Common Issues

**Problem**: No anomalies found
```bash
# Solution: Lower min score temporarily
MIN_ANOMALY_SCORE = 15  # In ob1_config.py
```

**Problem**: Scraping failures
```bash
# Solution: Check logs for blocked domains
tail -100 logs/ob1_radar.log | grep "failed"
```

**Problem**: API quota exceeded
```bash
# Solution: Check Serper dashboard
# https://serper.dev/dashboard
```

---

## 📞 Support

### For Technical Issues
- Check logs: `logs/ob1_radar.log`
- Review output: `output/daily.json`
- Test configuration: `python3 ob1_config.py`

### For Business Inquiries
- **Email**: mirko@matchanalysispro.online
- **Website**: https://matchanalysispro.online
- **Pricing**: €2,500/month (subscription)

### For Legal/Licensing
- See `LICENSE.md` for terms
- Contact for partnership opportunities
- NDA required before detailed discussions

---

## 🚀 Deployment

### Production Checklist

- [ ] API keys configured in `.env`
- [ ] Test run completed successfully
- [ ] Logs directory writable
- [ ] Output directory accessible
- [ ] Cron job scheduled (daily 6:00 AM UTC)
- [ ] Monitoring alerts configured
- [ ] Client ToS signed (see `TERMS_OF_SERVICE.md`)
- [ ] NDA executed if applicable

### Cron Setup

```bash
# Daily run at 6:00 AM UTC
0 6 * * * cd /path/to/ob1-scout && python3 run_v0.8.2_optimized.py >> logs/cron.log 2>&1
```

---

## 📊 Success Metrics

### Validated Cases

1. **Gilberto Mora** (Club Tijuana)
   - OB1 detection: August 9, 2024
   - Mainstream coverage: August 20, 2024
   - **Lead time**: 11 days
   - Current value: €4.5M (Transfermarkt)

2. **Mateo Silvetti** (Inter Miami)
   - Transfer: €4M from Newell's
   - Recovery potential identified
   - Status: 4-month injury tracking

3. **Multiple U20 debuts** tracked
   - Kenya U20 AFCON squad
   - Senegal U20 convocations
   - Flamengo U20 champions

---

## 🎖️ Trademark Notice

**OB1 Radar™** is a trademark of Mirko Tornani.
Registration pending with USBM (Ufficio di Stato Brevetti e Marchi), Republic of San Marino.

Unauthorized use of the trademark is prohibited.

---

## ⚠️ Disclaimer

### For Scouting Use

OB1 Radar™ provides intelligence signals, not guarantees. Professional scouting judgment is required for all decisions. We are not liable for:

- Player performance outcomes
- Transfer decisions
- Market valuation changes
- Missed opportunities

### Data Accuracy

Intelligence is gathered from public sources. We do not guarantee:
- 100% accuracy of reports
- Complete coverage of all markets
- Real-time data updates
- Absence of false positives

See `TERMS_OF_SERVICE.md` for full disclaimer.

---

## 📜 Version History

### v0.8.2 OPTIMIZED (October 2025)
- Proprietary scoring algorithm v2.0
- Enhanced query optimization (17 patterns)
- Improved regional detection
- Better false positive filtering
- IP protection headers added

### v0.8.1 (September 2025)
- Serper.dev integration
- Cache system implementation
- Multi-threading optimization

### v0.7.0 (August 2025)
- Initial production release
- Multi-region support
- Basic anomaly detection

---

## 🤝 Partners & Clients

**Current discussions:**
- Juventus U23 (Pietro Chiellini)
- LAFC/AKKA (Giorgio Chiellini)
- FSGC San Marino (eligible player discovery)

**Status**: Confidential - NDA required for details

---

## 📖 Citation

If authorized to reference this system in reports:

```
OB1 Radar™ Football Intelligence System
Copyright © 2024-2025 Mirko Tornani
https://matchanalysispro.online
```

---

## 🌟 Commercial Licensing

**Standard License**: €2,500/month
- Daily anomaly reports
- Multi-region coverage
- Email support
- Report archives

**Enterprise License**: Custom pricing
- API access
- Custom regions
- Priority support
- White-label options

Contact: mirko@matchanalysispro.online

---

## 🔐 Final Notes

**This is proprietary software.**

- Source code is CONFIDENTIAL
- Algorithms are TRADE SECRETS
- Trademark is PROTECTED
- Commercial use requires LICENSE

**Unauthorized use, copying, or distribution is prohibited and may result in legal action.**

For legitimate business inquiries, partnership opportunities, or licensing:
📧 **mirko@matchanalysispro.online**

---

**OB1 Radar™** - Intelligence Before Headlines
*Copyright © 2024-2025 Mirko Tornani. All Rights Reserved.*
