import pytest

import bronze_load as bl


def test_copy_sql_targets_exact_files_relative_to_stage():
    sql = bl.copy_sql(
        "RECALLS", 3,
        ["bronze/recalls_post_2010/load_date=2026-10-20/A.txt.gz",
         "bronze/recalls_pre_2010/load_date=2026-10-19/B.txt.gz"],
    )
    assert "SELECT $1, $2, $3, METADATA$FILENAME, CURRENT_TIMESTAMP()" in sql
    assert "FILES = ('recalls_post_2010/load_date=2026-10-20/A.txt.gz', " \
           "'recalls_pre_2010/load_date=2026-10-19/B.txt.gz')" in sql
    assert "FORCE = TRUE" in sql


def test_copy_sql_rejects_keys_outside_stage():
    with pytest.raises(ValueError, match="outside the stage"):
        bl.copy_sql("COMPLAINTS", 51, ["other/complaints/X.txt.gz"])


def test_compare_counts_passes_on_growth_and_first_load():
    result = bl.compare_counts({"COMPLAINTS": [2_257_600, 2_257_425], "RECALLS": [327_819]}, 0.01)
    assert result == {"COMPLAINTS": (2_257_425, 2_257_600)}


def test_compare_counts_fails_on_big_drop():
    with pytest.raises(RuntimeError, match="COMPLAINTS: 2,257,425 -> 2,000,000"):
        bl.compare_counts({"COMPLAINTS": [2_000_000, 2_257_425]}, 0.01)
