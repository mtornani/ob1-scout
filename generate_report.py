#!/usr/bin/env python3
import json
from datetime import datetime
from pathlib import Path

def generate_report():
    input_file = Path("output/daily.json")
    if not input_file.exists():
        print("❌ No data found. Run scraper first.")
        return
    
    with open(input_file, encoding="utf-8") as f:
        data = json.load(f)
    
    items = data.get("items", [])
    breakdown = data.get("region_breakdown", {})
    
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>OB1 Scout Weekly Report</title>
    <style>
        body {{ 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; 
            max-width: 900px; margin: 40px auto; padding: 20px; 
            background: #0f172a; color: #e2e8f0; line-height: 1.6;
        }}
        .header {{ 
            background: linear-gradient(135deg, #1e293b, #334155); 
            padding: 30px; border-radius: 12px; margin-bottom: 30px; 
        }}
        h1 {{ margin: 0; font-size: 2.5rem; color: #60a5fa; }}
        .meta {{ color: #94a3b8; margin-top: 10px; font-size: 0.95rem; }}
        .summary {{ 
            background: #1e293b; padding: 20px; border-radius: 8px; 
            margin-bottom: 30px; border-left: 4px solid #34d399; 
        }}
        .breakdown {{ display: flex; gap: 15px; flex-wrap: wrap; margin-top: 15px; }}
        .stat {{ background: #374151; padding: 10px 15px; border-radius: 6px; }}
        .item {{ 
            background: #1e293b; padding: 20px; margin-bottom: 15px; 
            border-radius: 8px; border-left: 4px solid #3b82f6; 
        }}
        .title {{ font-size: 1.2rem; font-weight: 600; margin-bottom: 8px; color: #f8fafc; }}
        .score {{ color: #34d399; font-weight: bold; font-size: 1.1rem; }}
        .type {{ color: #a78bfa; font-weight: 500; }}
        .tags {{ margin-top: 12px; }}
        .tag {{ 
            display: inline-block; background: #374151; padding: 4px 12px; 
            border-radius: 4px; font-size: 0.85rem; margin-right: 8px; 
            margin-bottom: 6px; color: #94a3b8;
        }}
        .tag.youth {{ background: #065f46; color: #6ee7b7; }}
        .tag.mercato {{ background: #1e3a8a; color: #93c5fd; }}
        .tag.esordio {{ background: #7c2d12; color: #fdba74; }}
        .tag.recente {{ background: #581c87; color: #e9d5ff; }}
        a {{ color: #60a5fa; text-decoration: none; font-size: 0.9rem; }}
        a:hover {{ text-decoration: underline; }}
        .footer {{ 
            text-align: center; margin-top: 40px; padding-top: 20px; 
            border-top: 1px solid #374151; color: #64748b; font-size: 0.85rem; 
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>OB1 Scout Weekly Report</h1>
        <div class="meta">Generated: {datetime.now().strftime('%B %d, %Y at %H:%M CET')}</div>
        <div class="meta">Source: 50+ global youth football sources</div>
    </div>
    
    <div class="summary">
        <strong style="font-size:1.1rem; color:#f8fafc;">Total Signals: {len(items)}</strong>
        <div class="breakdown">
            <div class="stat">Africa: {breakdown.get('africa', 0)}</div>
            <div class="stat">Asia: {breakdown.get('asia', 0)}</div>
            <div class="stat">South America: {breakdown.get('south-america', 0)}</div>
            <div class="stat">International: {breakdown.get('international', 0)}</div>
        </div>
    </div>
"""
    
    for idx, item in enumerate(items, 1):
        tags_html = ""
        for tag in item.get('why', []):
            tag_class = f"tag {tag}" if tag in ['youth', 'mercato', 'esordio', 'recente'] else "tag"
            tags_html += f'<span class="{tag_class}">{tag}</span>'
        
        links_html = " | ".join([f'<a href="{link}" target="_blank">View Source</a>' for link in item.get('links', [])])
        
        html += f"""
    <div class="item">
        <div class="title">{idx}. {item.get('label', 'Unknown Player')}</div>
        <div>
            <span class="score">Score: {item.get('score', 0)}</span> | 
            <span class="type">{item.get('anomaly_type', 'N/A')}</span>
        </div>
        <div class="tags">{tags_html}</div>
        <div style="margin-top:10px">{links_html}</div>
    </div>
"""
    
    html += """
    <div class="footer">
        OB1 Scout © 2025 | info@matchanalysispro.online
    </div>
</body>
</html>
"""
    
    output_file = Path("output/weekly_report.html")
    output_file.write_text(html, encoding="utf-8")
    print(f"✅ Report saved to {output_file}")
    print(f"   Open in browser: file:///{output_file.absolute()}")

if __name__ == "__main__":
    generate_report()