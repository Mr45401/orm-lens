"""Regression tests for issue #266 — models in a sub-package of a Django app.

When an app splits models across sub-packages (for example
``io_storages/s3/models.py`` inside an ``io_storages`` app that holds
``apps.py`` / ``migrations/``), Django assigns the enclosing app's label
(``io_storages``). Both ``parse_models_file`` and ``scan_workspace`` must
resolve to ``io_storages`` rather than ``s3``.
"""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from orm_lens.cli import main
from orm_lens.drift import detect_drift
from orm_lens.parser import parse_models_file, scan_workspace

S3_MODELS = (
    "from django.db import models\n\n\n"
    "class S3ImportStorage(models.Model):\n"
    "    bucket = models.TextField(null=True, blank=True)\n"
    "    prefix = models.TextField(null=True, blank=True)\n"
)

S3_MIGRATION = (
    "from django.db import migrations, models\n\n\n"
    "class Migration(migrations.Migration):\n"
    "    initial = True\n"
    "    dependencies = []\n"
    "    operations = [\n"
    "        migrations.CreateModel(\n"
    '            name="S3ImportStorage",\n'
    "            fields=[\n"
    '                ("id", models.AutoField(primary_key=True, serialize=False)),\n'
    '                ("bucket", models.TextField(null=True, blank=True)),\n'
    '                ("prefix", models.TextField(null=True, blank=True)),\n'
    "            ],\n"
    "        ),\n"
    "    ]\n"
)


def _write(path: Path, text: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _run_cli(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class SubpackageAppLabelTest(unittest.TestCase):
    def test_issue_266_repro_list_describe_and_drift(self) -> None:
        """Exact #266 layout resolves to ``io_storages`` with zero drift."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "io_storages" / "__init__.py")
            _write(root / "io_storages" / "migrations" / "__init__.py")
            _write(root / "io_storages" / "s3" / "__init__.py")
            _write(
                root / "io_storages" / "models.py",
                "from .s3.models import S3ImportStorage  # noqa: F401\n",
            )
            s3_file = _write(root / "io_storages" / "s3" / "models.py", S3_MODELS)
            _write(
                root / "io_storages" / "migrations" / "0001_initial.py",
                S3_MIGRATION,
            )

            parsed = parse_models_file(str(s3_file), S3_MODELS)
            self.assertEqual([m.app_name for m in parsed], ["io_storages"])

            code, out, err = _run_cli(["list", "--path", tmp])
            self.assertEqual(code, 0, err)
            self.assertEqual(out.splitlines(), ["io_storages.S3ImportStorage"])

            code, out, err = _run_cli(["describe", "io_storages.S3ImportStorage", "--path", tmp])
            self.assertEqual(code, 0, err)
            self.assertIn("io_storages.S3ImportStorage", out)

            report = detect_drift(root)
            self.assertEqual(report.drifted, [])
            code, _out, err = _run_cli(["drift", "--path", tmp])
            self.assertEqual(code, 0, err)

    def test_apps_py_without_migrations_groups_multiple_subpackages(self) -> None:
        """An ``apps.py`` marker alone resolves nested sub-packages and ``models/``."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "io_storages" / "__init__.py")
            _write(root / "io_storages" / "apps.py", "from django.apps import AppConfig\n")
            _write(root / "io_storages" / "s3" / "__init__.py")
            _write(root / "io_storages" / "s3" / "models.py", S3_MODELS)
            _write(root / "io_storages" / "gcs" / "__init__.py")
            _write(root / "io_storages" / "gcs" / "models" / "__init__.py")
            _write(
                root / "io_storages" / "gcs" / "models" / "storage.py",
                "from django.db import models\n\n\n"
                "class GCSImportStorage(models.Model):\n"
                "    bucket = models.CharField(max_length=100)\n",
            )
            _write(root / "io_storages" / "cloud" / "__init__.py")
            _write(root / "io_storages" / "cloud" / "azure" / "__init__.py")
            _write(
                root / "io_storages" / "cloud" / "azure" / "models.py",
                "from django.db import models\n\n\n"
                "class AzureImportStorage(models.Model):\n"
                "    container = models.CharField(max_length=100)\n",
            )

            index = scan_workspace(tmp)
            self.assertEqual([a.name for a in index.apps], ["io_storages"])
            models = index.apps[0].models
            self.assertEqual(
                sorted((m.app_name, m.name) for m in models),
                [
                    ("io_storages", "AzureImportStorage"),
                    ("io_storages", "GCSImportStorage"),
                    ("io_storages", "S3ImportStorage"),
                ],
            )

    def test_plain_app_and_models_package_keep_their_labels(self) -> None:
        """``shop/models.py`` and ``billing/models/order.py`` keep their labels."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "shop" / "__init__.py")
            _write(root / "shop" / "apps.py")
            _write(
                root / "shop" / "models.py",
                "from django.db import models\n\n\n"
                "class Product(models.Model):\n"
                "    sku = models.CharField(max_length=32)\n",
            )
            _write(root / "billing" / "__init__.py")
            _write(root / "billing" / "migrations" / "__init__.py")
            _write(root / "billing" / "models" / "__init__.py")
            _write(
                root / "billing" / "models" / "order.py",
                "from django.db import models\n\n\n"
                "class Order(models.Model):\n"
                "    number = models.CharField(max_length=32)\n",
            )

            index = scan_workspace(tmp)
            by_app = {a.name: [m.name for m in a.models] for a in index.apps}
            self.assertEqual(by_app, {"billing": ["Order"], "shop": ["Product"]})
            for app in index.apps:
                for model in app.models:
                    self.assertEqual(model.app_name, app.name)

    def test_nested_app_with_own_marker_keeps_own_label(self) -> None:
        """A sub-directory that has its own ``apps.py`` is its own app."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "wagtail" / "__init__.py")
            _write(root / "wagtail" / "apps.py")
            _write(root / "wagtail" / "users" / "__init__.py")
            _write(root / "wagtail" / "users" / "apps.py")
            _write(
                root / "wagtail" / "users" / "models.py",
                "from django.db import models\n\n\n"
                "class UserProfile(models.Model):\n"
                "    avatar = models.CharField(max_length=100)\n",
            )

            index = scan_workspace(tmp)
            self.assertEqual([a.name for a in index.apps], ["users"])
            self.assertEqual(index.apps[0].models[0].app_name, "users")

    def test_walk_does_not_cross_non_package_parent(self) -> None:
        """A parent without ``__init__.py`` stops the walk even if ``migrations/`` exists."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Repo root has no __init__.py (not a Python package), e.g. top-level migrations/.
            (root / "migrations").mkdir()
            _write(root / "apps.py")
            _write(root / "s3" / "__init__.py")
            _write(root / "s3" / "models.py", S3_MODELS)

            index = scan_workspace(tmp)
            self.assertEqual([a.name for a in index.apps], ["s3"])
            self.assertEqual(index.apps[0].models[0].app_name, "s3")

    def test_no_markers_above_falls_back_to_todays_rule(self) -> None:
        """Without ``migrations/`` or ``apps.py`` anywhere above, today's label wins."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "pkg" / "__init__.py")
            _write(root / "pkg" / "s3" / "__init__.py")
            _write(root / "pkg" / "s3" / "models.py", S3_MODELS)
            _write(root / "pkg" / "orders" / "__init__.py")
            _write(root / "pkg" / "orders" / "models" / "__init__.py")
            _write(
                root / "pkg" / "orders" / "models" / "item.py",
                "from django.db import models\n\n\n"
                "class Item(models.Model):\n"
                "    name = models.CharField(max_length=50)\n",
            )

            index = scan_workspace(tmp)
            by_app = {a.name: [m.name for m in a.models] for a in index.apps}
            self.assertEqual(
                by_app,
                {"orders": ["Item"], "s3": ["S3ImportStorage"]},
            )

        bare = parse_models_file("models.py", S3_MODELS)
        self.assertEqual([m.app_name for m in bare], ["app"])

    def test_subpackage_model_prefers_same_app_abstract_base(self) -> None:
        """``_pick_base`` treats ``io_storages/s3/models.py`` as part of ``io_storages``."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "alpha" / "__init__.py")
            _write(root / "alpha" / "apps.py")
            _write(
                root / "alpha" / "abstract_models.py",
                "from django.db import models\n\n\n"
                "class AbstractStorage(models.Model):\n"
                "    wrong_field = models.CharField(max_length=20)\n\n"
                "    class Meta:\n"
                "        abstract = True\n",
            )
            _write(root / "io_storages" / "__init__.py")
            _write(root / "io_storages" / "apps.py")
            _write(
                root / "io_storages" / "abstract_models.py",
                "from django.db import models\n\n\n"
                "class AbstractStorage(models.Model):\n"
                "    shared_title = models.CharField(max_length=50)\n\n"
                "    class Meta:\n"
                "        abstract = True\n",
            )
            _write(root / "io_storages" / "s3" / "__init__.py")
            _write(
                root / "io_storages" / "s3" / "models.py",
                "from io_storages.abstract_models import AbstractStorage\n\n\n"
                "class S3ImportStorage(AbstractStorage):\n"
                "    pass\n",
            )

            index = scan_workspace(tmp)
            self.assertEqual([a.name for a in index.apps], ["io_storages"])
            model = index.apps[0].models[0]
            self.assertEqual([f.name for f in model.all_fields()], ["shared_title"])


if __name__ == "__main__":
    unittest.main()
