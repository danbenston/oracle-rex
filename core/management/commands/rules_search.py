"""Management command: search the LRR rules index (debug/inspect CLI).

The deterministic-retrieval equivalent of an "inspect" window: type a question,
see which rule chunks BM25 returns and their scores, before any model is
involved. Handy for tuning chunking / aliases and for eyeballing golden-set
labels.

    python manage.py rules_search "can I retreat if I have no ships left?"
    python manage.py rules_search -k 5 "space cannon offense timing"
"""

import os
import textwrap

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Search the LRR rules index and print the top matches (debug CLI)."

    def add_arguments(self, parser):
        parser.add_argument("query", nargs="+", help="The rules question to search for.")
        parser.add_argument("-k", type=int, default=8, help="Number of results (default 8).")
        parser.add_argument("--full", action="store_true",
                            help="Print each rule's full text instead of a snippet.")

    def handle(self, *args, **options):
        os.environ.setdefault("SKIP_DB_STARTUP", "1")
        from core.service.rules_index import RulesIndexError, retrieve

        question = " ".join(options["query"])
        try:
            results = retrieve(question, k=options["k"])
        except RulesIndexError as exc:
            self.stderr.write(self.style.ERROR(str(exc)))
            raise SystemExit(1)

        self.stdout.write(self.style.HTTP_INFO(f'Query: "{question}"'))
        if not results:
            self.stdout.write("  (no matches)")
            return

        for rank, r in enumerate(results, 1):
            head = f"{rank:>2}. [{r.score:6.2f}] LRR {r.rule_id} — {r.topic}"
            self.stdout.write(self.style.SUCCESS(head))
            body = r.text if options["full"] else textwrap.shorten(r.text, width=160, placeholder=" …")
            for line in textwrap.wrap(body, width=88):
                self.stdout.write(f"      {line}")
