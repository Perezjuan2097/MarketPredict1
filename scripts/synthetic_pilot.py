"""Generate an explicitly synthetic retail CSV. This script uses no database or network."""
import argparse
import csv
from datetime import date, timedelta
from random import Random
import sys


def generate(output, days=120):
    csvfile = csv.writer(output)
    csvfile.writerow(["fecha", "producto", "cantidad", "precio", "categoria", "tx"])
    random = Random(527)
    start = date(2025, 1, 1)
    for i in range(days):
        day = start + timedelta(days=i)
        weekend = 1.25 if day.weekday() >= 5 else 1
        for j, (product, price, category) in enumerate((
            ("DEMO Cafe 250g", "8.25", "DEMO Alimentos"), ("DEMO Te verde", "6.50", "DEMO Alimentos"),
            ("DEMO Taza", "12.90", "DEMO Hogar"),
        )):
            quantity = max(1, round((11 + j * 4 + i / 20) * weekend + random.gauss(0, 2)))
            csvfile.writerow([day, product, quantity, price, category, f"DEMO-{i}-{j}"])
            if i % 19 == j:
                csvfile.writerow([day, product, -1, price, category, f"DEMO-RET-{i}-{j}"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=120, choices=range(80, 366), metavar="80..365")
    args = parser.parse_args()
    generate(sys.stdout, args.days)


if __name__ == "__main__":
    main()
