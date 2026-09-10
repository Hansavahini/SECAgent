from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Run the SEC 8-K Watcher"

    def add_arguments(self, parser):
        parser.add_argument("identifier", type=str)

    def handle(self, *args, **options):
        identifier = options["identifier"]

        self.stdout.write(
            self.style.SUCCESS(
                f"Watcher started for: {identifier}"
            )
        )