"""Generate a Pareto chart from analysis/bug_log.csv.

Only rows marked ACTUAL_RESULT and having a positive occurrence_count are
included in the default chart. TEMPLATE rows and illustrative examples are
never treated as observed defects.
"""

import argparse
import csv
from pathlib import Path

import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parent
DEFAULT_INPUT = ROOT / "bug_log.csv"
DEFAULT_OUTPUT = ROOT / "pareto_chart.png"


def read_defect_counts(csv_path, include_illustrative=False):
    """Return category counts from the documented bug-log CSV format."""
    counts = {}
    with Path(csv_path).open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            record_type = row.get("record_type", "").strip().upper()
            if record_type == "TEMPLATE":
                continue
            if record_type == "ILLUSTRATIVE" and not include_illustrative:
                continue
            if record_type not in {"ACTUAL_RESULT", "ILLUSTRATIVE"}:
                continue
            try:
                frequency = int(row.get("occurrence_count", "").strip())
            except (AttributeError, ValueError):
                continue
            if frequency <= 0:
                continue
            category = row.get("category", "").strip() or "Uncategorised"
            counts[category] = counts.get(category, 0) + frequency
    return counts


def create_pareto_chart(csv_path=DEFAULT_INPUT, output_path=DEFAULT_OUTPUT,
                        include_illustrative=False):
    """Create a frequency-bar/cumulative-percentage Pareto chart."""
    counts = read_defect_counts(csv_path, include_illustrative)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    labels = list(sorted(counts, key=counts.get, reverse=True))
    frequencies = [counts[label] for label in labels]
    total = sum(frequencies)
    cumulative = []
    running = 0
    for frequency in frequencies:
        running += frequency
        cumulative.append(running * 100 / total if total else 0)

    figure, axis = plt.subplots(figsize=(9, 5))
    if labels:
        positions = range(len(labels))
        axis.bar(positions, frequencies, color="#4C78A8")
        axis.set_xticks(list(positions), labels, rotation=30, ha="right")
    else:
        axis.text(0.5, 0.5, "No positive actual defect frequencies recorded",
                  ha="center", va="center", transform=axis.transAxes)
        axis.set_xticks([])
    axis.set_ylabel("Frequency")
    axis.set_xlabel("Defect category")
    axis.set_title("Pareto Chart of Recorded Defects")
    axis.grid(axis="y", alpha=0.25)

    cumulative_axis = axis.twinx()
    if labels:
        cumulative_axis.plot(list(range(len(labels))), cumulative,
                             color="#F58518", marker="o")
    cumulative_axis.axhline(80, color="red", linestyle="--",
                            label="80% reference")
    cumulative_axis.set_ylim(0, 105)
    cumulative_axis.set_ylabel("Cumulative percentage")
    cumulative_axis.legend(loc="lower right")

    figure.tight_layout()
    figure.savefig(output_path, dpi=150)
    plt.close(figure)
    return output_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--include-illustrative", action="store_true",
                        help="Include rows explicitly marked ILLUSTRATIVE.")
    args = parser.parse_args()
    result = create_pareto_chart(args.input, args.output,
                                 args.include_illustrative)
    print(f"Pareto chart written to {result}")


if __name__ == "__main__":
    main()
