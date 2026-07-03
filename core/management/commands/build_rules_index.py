"""Management command: build the Living Rules Reference FTS5 retrieval index.

Reads the vendored corpus (``core/data/source/lrr/lrr_rules.json``) and writes a
standalone SQLite FTS5 index (``core/data/index/lrr_fts.sqlite3``). The index is
a build artifact — rebuild it whenever the corpus changes, and at deploy time
(cheap, like ``collectstatic``). It is not the app database.

    python manage.py build_rules_index
"""

import os

from django.core.management.base import BaseCommand

from core.service.rules_index import INDEX_PATH, SOURCE_PATH, build_index


class Command(BaseCommand):
    help = "Build the LRR FTS5 retrieval index from the vendored rules corpus."

    def add_arguments(self, parser):
        parser.add_argument("--source", default=str(SOURCE_PATH),
                            help="Corpus JSON path (default: vendored lrr_rules.json).")
        parser.add_argument("--out", default=str(INDEX_PATH),
                            help="Output index path (default: core/data/index/lrr_fts.sqlite3).")

    def handle(self, *args, **options):
        # Building only reads JSON + writes a standalone sqlite file; skip the
        # import-time reset_database() side effect.
        os.environ.setdefault("SKIP_DB_STARTUP", "1")

        stats = build_index(options["source"], options["out"])
        self.stdout.write(self.style.SUCCESS(f"Built rules index: {stats['index_path']}"))
        self.stdout.write(
            f"  chunks: {stats['chunks']}  (topics: {stats['topics']}, "
            f"rules: {stats['rules']})  source version: {stats['source_version']}"
        )
