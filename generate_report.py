"""Generate the weekly OB1 Scout report from daily signals."""

import json
from datetime import datetime
from pathlib import Path


# Load signal entries from the JSON export.
def load_signals(path: Path) -> list:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if isinstance(data, dict):
        return list(data.get("signals", []))
    return list(data)


# Build the markdown body with summary details.
def format_report(signals: list) -> str:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    header = ["# OB1 Scout Weekly Report", f"Generated: {timestamp}", ""]
    header.append(f"Total signals: {len(signals)}\n")
    top_items = signals[:20]
    lines = [
        f"{idx}. {item.get('title', 'Untitled')} — {item.get('source', 'Unknown source')} (Age {item.get('age', 'N/A')})"
        for idx, item in enumerate(top_items, start=1)
    ]
    return "\n".join(header + lines)


# Orchestrate file IO and error handling.
def main() -> None:
    data_path = Path("output/daily.json")
    if not data_path.exists():
        print("Error: output/daily.json not found. Run the daily scraper before generating the weekly report.")
        return
    try:
        signals = load_signals(data_path)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"Error reading {data_path}: {exc}")
        return
    report = format_report(signals)
    output_path = Path("output/weekly_report.txt")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")
    print(f"Weekly report saved to {output_path.resolve()}")


if __name__ == "__main__":
    main()
