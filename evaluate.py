"""Accuracy evaluator — compares output.json against ground_truth.json.

Usage:
    python evaluate.py              # Evaluate output.json vs ground_truth.json
    python evaluate.py --verbose    # Show per-email mismatches
"""

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Fix Windows console encoding for Unicode characters
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DATA_DIR = Path(__file__).resolve().parent.parent
OUTPUT_FILE = Path(__file__).resolve().parent / "output.json"
GROUND_TRUTH_FILE = DATA_DIR / "ground_truth.json"

EVALUATED_FIELDS = [
    "product_line",
    "origin_port_code",
    "origin_port_name",
    "destination_port_code",
    "destination_port_name",
    "incoterm",
    "cargo_weight_kg",
    "cargo_cbm",
    "is_dangerous",
]


# ---------------------------------------------------------------------------
# Comparison Logic
# ---------------------------------------------------------------------------

def compare_values(expected: Any, actual: Any, field: str) -> bool:
    """Compare two values according to the evaluation rules.
    
    Rules:
    - String comparisons: case-insensitive, whitespace trimmed
    - Float comparisons: exact match after rounding to 2 decimal places
    - Null comparisons: null only equals null
    - Boolean comparisons: exact match
    """
    # Both null
    if expected is None and actual is None:
        return True
    
    # One null, one not
    if expected is None or actual is None:
        return False
    
    # Boolean comparison
    if isinstance(expected, bool) or isinstance(actual, bool):
        return bool(expected) == bool(actual)
    
    # Float comparison
    if isinstance(expected, (int, float)) or isinstance(actual, (int, float)):
        try:
            return round(float(expected), 2) == round(float(actual), 2)
        except (ValueError, TypeError):
            return False
    
    # String comparison (case-insensitive, whitespace trimmed)
    return str(expected).strip().lower() == str(actual).strip().lower()


def evaluate(
    output: List[Dict], 
    ground_truth: List[Dict], 
    verbose: bool = False
) -> Dict[str, Any]:
    """Evaluate extraction accuracy.
    
    Returns:
        Dictionary with per-field accuracy, overall accuracy, and mismatch details.
    """
    # Index by id
    gt_map = {item["id"]: item for item in ground_truth}
    out_map = {item["id"]: item for item in output}
    
    # Track per-field stats
    field_correct = {f: 0 for f in EVALUATED_FIELDS}
    field_total = {f: 0 for f in EVALUATED_FIELDS}
    
    total_correct = 0
    total_fields = 0
    mismatches: List[Dict] = []
    
    # Evaluate each email
    for gt_item in ground_truth:
        email_id = gt_item["id"]
        out_item = out_map.get(email_id)
        
        if out_item is None:
            # Missing from output — all fields wrong
            for field in EVALUATED_FIELDS:
                field_total[field] += 1
                total_fields += 1
            if verbose:
                mismatches.append({
                    "id": email_id,
                    "issue": "MISSING from output.json"
                })
            continue
        
        email_mismatches = []
        
        for field in EVALUATED_FIELDS:
            expected = gt_item.get(field)
            actual = out_item.get(field)
            field_total[field] += 1
            total_fields += 1
            
            if compare_values(expected, actual, field):
                field_correct[field] += 1
                total_correct += 1
            else:
                email_mismatches.append({
                    "field": field,
                    "expected": expected,
                    "actual": actual,
                })
        
        if email_mismatches and verbose:
            mismatches.append({
                "id": email_id,
                "errors": email_mismatches,
            })
    
    # Calculate accuracies
    field_accuracy = {}
    for field in EVALUATED_FIELDS:
        if field_total[field] > 0:
            field_accuracy[field] = field_correct[field] / field_total[field]
        else:
            field_accuracy[field] = 0.0
    
    overall = total_correct / total_fields if total_fields > 0 else 0.0
    
    return {
        "field_accuracy": field_accuracy,
        "overall_accuracy": overall,
        "total_correct": total_correct,
        "total_fields": total_fields,
        "mismatches": mismatches,
        "emails_evaluated": len(ground_truth),
        "emails_in_output": len(output),
    }


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def print_results(results: Dict[str, Any], verbose: bool = False):
    """Print evaluation results in a readable format."""
    
    print("\n" + "=" * 60)
    print("  EXTRACTION ACCURACY REPORT")
    print("=" * 60)
    
    print(f"\n  Emails evaluated: {results['emails_evaluated']}")
    print(f"  Emails in output: {results['emails_in_output']}")
    
    # Per-field accuracy
    print(f"\n  {'Field':<25} {'Accuracy':>10} {'Correct':>10}")
    print("  " + "-" * 47)
    
    for field in EVALUATED_FIELDS:
        acc = results["field_accuracy"][field]
        correct = int(acc * results["emails_evaluated"])
        total = results["emails_evaluated"]
        bar = "#" * int(acc * 20) + "." * (20 - int(acc * 20))
        print(f"  {field:<25} {acc:>9.1%}  {correct:>3}/{total} {bar}")
    
    # Overall accuracy
    overall = results["overall_accuracy"]
    print(f"\n  {'OVERALL':.<25} {overall:>9.1%}  "
          f"({results['total_correct']}/{results['total_fields']})")
    
    # Rating
    if overall >= 0.90:
        rating = "[EXCEPTIONAL]"
    elif overall >= 0.80:
        rating = "[STRONG]"
    elif overall >= 0.70:
        rating = "[ACCEPTABLE]"
    else:
        rating = "[NEEDS IMPROVEMENT]"
    
    print(f"\n  Rating: {rating}")
    print("=" * 60)
    
    # Verbose: show mismatches
    if verbose and results["mismatches"]:
        print(f"\n\nDETAILED MISMATCHES ({len(results['mismatches'])} emails with errors):\n")
        for mismatch in results["mismatches"]:
            email_id = mismatch["id"]
            if "issue" in mismatch:
                print(f"  {email_id}: {mismatch['issue']}")
            else:
                print(f"  {email_id}:")
                for err in mismatch["errors"]:
                    print(f"    X {err['field']}: expected={err['expected']!r}, got={err['actual']!r}")
        print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    """Run evaluation."""
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    
    # Load files
    if not OUTPUT_FILE.exists():
        print(f"[ERROR] {OUTPUT_FILE} not found. Run extract.py first.")
        sys.exit(1)
    
    if not GROUND_TRUTH_FILE.exists():
        print(f"[ERROR] {GROUND_TRUTH_FILE} not found.")
        sys.exit(1)
    
    with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
        output = json.load(f)
    
    with open(GROUND_TRUTH_FILE, "r", encoding="utf-8") as f:
        ground_truth = json.load(f)
    
    # Run evaluation
    results = evaluate(output, ground_truth, verbose=verbose)
    print_results(results, verbose=verbose)
    
    return results


if __name__ == "__main__":
    main()
