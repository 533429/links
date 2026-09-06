#!/usr/bin/env python3
import csv
import json
import re
import shutil
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "data" / "streams.csv"
DIST = ROOT / "dist"
ID_RE = re.compile(r"^tt\d+$")


def fail(message: str) -> None:
    raise SystemExit(f"ERROR: {message}")


def require_https(url: str, row_num: int) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        fail(f"Row {row_num}: url must be a valid HTTPS URL: {url}")


def parse_positive_int(value: str, field: str, row_num: int) -> int:
    try:
        number = int(value)
    except ValueError:
        fail(f"Row {row_num}: {field} must be a positive integer.")
    if number < 1:
        fail(f"Row {row_num}: {field} must be a positive integer.")
    return number


def clean_dist() -> None:
    if DIST.exists():
        shutil.rmtree(DIST)
    (DIST / "stream" / "movie").mkdir(parents=True)
    (DIST / "stream" / "series").mkdir(parents=True)


def load_rows():
    if not CSV_PATH.exists():
        fail(f"Missing {CSV_PATH}")

    required = {"type", "imdb_id", "season", "episode", "episode_count", "url"}
    rows = []
    seen = set()
    series_groups = defaultdict(list)

    with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            fail("CSV has no header row.")
        missing = required - set(reader.fieldnames)
        if missing:
            fail("CSV is missing columns: " + ", ".join(sorted(missing)))

        for row_num, row in enumerate(reader, start=2):
            if not any((v or "").strip() for v in row.values()):
                continue

            media_type = (row["type"] or "").strip().lower()
            imdb_id = (row["imdb_id"] or "").strip()
            season = (row["season"] or "").strip()
            episode = (row["episode"] or "").strip()
            episode_count = (row["episode_count"] or "").strip()
            url = (row["url"] or "").strip()

            if media_type not in {"movie", "series"}:
                fail(f"Row {row_num}: type must be 'movie' or 'series'.")
            if not ID_RE.fullmatch(imdb_id):
                fail(f"Row {row_num}: imdb_id must look like tt1234567.")
            require_https(url, row_num)

            if media_type == "movie":
                if any([season, episode, episode_count]):
                    fail(f"Row {row_num}: season/episode/episode_count must be blank for movies.")
                key = ("movie", imdb_id)
                if key in seen:
                    fail(f"Duplicate movie entry: {imdb_id}")
                seen.add(key)
                rows.append({
                    "type": media_type,
                    "imdb_id": imdb_id,
                    "url": url,
                })
            else:
                if not all([season, episode, episode_count]):
                    fail(f"Row {row_num}: series rows require season, episode, and episode_count.")
                s = parse_positive_int(season, "season", row_num)
                e = parse_positive_int(episode, "episode", row_num)
                ec = parse_positive_int(episode_count, "episode_count", row_num)

                if e > ec:
                    fail(f"Row {row_num}: episode {e} exceeds episode_count {ec}.")

                key = ("series", imdb_id, s, e)
                if key in seen:
                    fail(f"Duplicate series episode: {imdb_id} S{s:02d}E{e:02d}")
                seen.add(key)

                series_groups[(imdb_id, s)].append((e, ec))
                rows.append({
                    "type": media_type,
                    "imdb_id": imdb_id,
                    "season": s,
                    "episode": e,
                    "episode_count": ec,
                    "url": url,
                })

    # Validate each season has exactly episodes 1..episode_count.
    for (imdb_id, season), items in series_groups.items():
        counts = {count for _, count in items}
        if len(counts) != 1:
            fail(
                f"{imdb_id} season {season}: all rows must use the same episode_count."
            )
        expected = next(iter(counts))
        actual = sorted(e for e, _ in items)
        wanted = list(range(1, expected + 1))
        if actual != wanted:
            missing = sorted(set(wanted) - set(actual))
            extra = sorted(set(actual) - set(wanted))
            details = []
            if missing:
                details.append("missing " + ", ".join(map(str, missing)))
            if extra:
                details.append("unexpected " + ", ".join(map(str, extra)))
            fail(
                f"{imdb_id} season {season}: expected episodes 1-{expected}; "
                + "; ".join(details)
            )

    return rows


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    rows = load_rows()
    clean_dist()

    manifest = {
        "id": "com.example.directstreams",
        "version": "1.0.0",
        "name": "Direct Streams",
        "description": "Streams backed by direct HTTPS video URLs.",
        "resources": ["stream"],
        "types": ["movie", "series"],
        "idPrefixes": ["tt"],
        "catalogs": [],
    }
    write_json(DIST / "manifest.json", manifest)

    for row in rows:
        if row["type"] == "movie":
            target = DIST / "stream" / "movie" / f'{row["imdb_id"]}.json'
            data = {
                "streams": [
                    {
                        "name": "Direct",
                        "url": row["url"],
                    }
                ]
            }
        else:
            stream_id = f'{row["imdb_id"]}:{row["season"]}:{row["episode"]}'
            target = DIST / "stream" / "series" / f"{stream_id}.json"
            data = {
                "streams": [
                    {
                        "name": "Direct",
                        "url": row["url"],
                    }
                ]
            }

        write_json(target, data)

    # Prevent Jekyll from trying to transform generated JSON.
    (DIST / ".nojekyll").write_text("", encoding="utf-8")

    movie_count = sum(1 for r in rows if r["type"] == "movie")
    episode_count = sum(1 for r in rows if r["type"] == "series")
    print(f"Generated {movie_count} movie stream(s) and {episode_count} episode stream(s).")


if __name__ == "__main__":
    main()
